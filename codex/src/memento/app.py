"""Local dashboard and explicit demo controls."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .runtime import Runtime


class EventInput(BaseModel):
    source: str = "chat"
    text: str = Field(min_length=1, max_length=50_000)
    title: str = ""
    demo: bool = True
    event_id: str | None = None


class MemoryInput(BaseModel):
    id: str
    markdown: str = Field(max_length=100_000)


class ClockInput(BaseModel):
    hours: float = Field(default=0, ge=-8760, le=8760)
    reset: bool = False


def create_app(runtime: Runtime, *, run_workers: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        if run_workers:
            await runtime.start()
        yield
        if run_workers:
            await runtime.stop()

    app = FastAPI(title="Memento", lifespan=lifespan)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return JSONResponse({"error": "Memento listens locally only"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.url.netloc:
                return JSONResponse({"error": "Cross-origin changes are disabled"}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def home():
        return Path(__file__).with_name("dashboard.html").read_text()

    @app.get("/api/state")
    async def state():
        return runtime.snapshot()

    @app.get("/api/health")
    async def health():
        return {"ok": True, "integrations": runtime.status}

    @app.post("/api/events", status_code=202)
    async def event(data: EventInput):
        try:
            event_id = await runtime.submit(data.source, {"text": data.text, "title": data.title, "demo": data.demo}, event_id=data.event_id)
            return {"event_id": event_id, "status": "queued"}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/memories")
    async def memory(data: MemoryInput):
        try:
            return await runtime.register_markdown(data.id, data.markdown, write_brain=True)
        except Exception as exc:
            raise HTTPException(400, runtime._safe_error(exc)) from exc

    @app.post("/api/memories/{memory_id:path}/stop")
    async def stop_memory(memory_id: str):
        if not runtime.store.memory(memory_id):
            raise HTTPException(404, "Memory not found")
        runtime.store.deactivate(memory_id)
        runtime.store.trace("stopped", "Stopped watching", {"memory_id": memory_id})
        return {"stopped": memory_id}

    @app.post("/api/events/{event_id:path}/retry")
    async def retry(event_id: str):
        runtime.store.set_event_status(event_id, "queued")
        return {"queued": event_id}

    @app.post("/api/clock")
    async def clock(data: ClockInput):
        seconds = 0 if data.reset else runtime.store.setting("demo_clock_offset", 0) + data.hours * 3600
        runtime.store.set_setting("demo_clock_offset", seconds)
        runtime.store.trace("clock", "Demo clock changed", {"offset_hours": seconds / 3600, "scope": "memories/demo/ only"})
        await runtime.tick()
        return {"demo_now": runtime.now(True).isoformat(), "offset_hours": seconds / 3600}

    @app.post("/api/sync")
    async def sync():
        await runtime.sync_brain()
        return {"status": runtime.status}

    return app
