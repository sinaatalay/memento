"""HTTP API and live stream for the memento UI (contract: API.md)."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE.parents[2] / ".context" / ".env"  # honiara/.context/.env (gitignored)


def _load_env() -> None:
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, _, value = line.partition("=")
            if key and value:
                os.environ.setdefault(key.strip(), value.strip())


_load_env()

from .engine import Engine  # noqa: E402  (keys must be in the environment first)
from .model import Source  # noqa: E402

engine = Engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(title="memento", lifespan=lifespan)


class ChatIn(BaseModel):
    message: str


class ClockIn(BaseModel):
    advance_minutes: int | None = None
    reset: bool = False


class EmailIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sender: str = Field("someone@example.com", alias="from")
    subject: str
    body: str


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/state")
async def state() -> dict:
    return engine.snapshot()


@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    queue = engine.subscribe()

    async def events():
        try:
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), 15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            engine.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/chat")
async def chat(body: ChatIn) -> dict:
    return await engine.chat(body.message)


@app.post("/api/clock")
async def clock(body: ClockIn) -> dict:
    if body.reset:
        engine.reset_clock()
    elif body.advance_minutes:
        engine.advance(body.advance_minutes)
    return {"now": engine.now().isoformat(), "offset_minutes": engine.offset_minutes}


@app.post("/api/sync")
async def sync() -> dict:
    engine.request_sync()
    return {"ok": True}


class GmailIn(BaseModel):
    demo: str


@app.post("/api/gmail/send")
async def gmail_send(body: GmailIn) -> dict:
    """Send one of the demo emails as a real Gmail message to the connected account."""
    from . import gmail

    name, subject, text = gmail.DEMO[body.demo]
    message_id = await asyncio.to_thread(gmail.send, subject, text, name)
    engine.request_sync()
    return {"ok": True, "id": message_id, "subject": subject}


@app.post("/api/simulate/email")
async def simulate_email(body: EmailIn) -> dict:
    item = {"from": body.sender, "subject": body.subject, "date": engine.now().isoformat(), "text": body.body}
    asyncio.create_task(engine.handle(Source.email, body.subject, item))
    return {"ok": True}


def main() -> None:
    import uvicorn

    uvicorn.run("memento.server:app", host="127.0.0.1", port=int(os.environ.get("MEMENTO_PORT", "8765")))
