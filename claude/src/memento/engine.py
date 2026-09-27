"""The runtime: memories register their recipes; every new item gets one Jev call.

- A memory is any ordinary GBrain page with a `recipe:` in its frontmatter,
  whoever wrote it (you, an agent over MCP, or Memento from an email). Pages
  are read from GBrain's markdown files; each recipe is collected into triggers.
- Every ~20 s GBrain syncs Gmail and Calendar; each new email or event, and
  every chat message, becomes Jev's state, and the conditions of all active
  triggers on that source are the questions of ONE request.
- A clock fires time triggers. The LLM only runs to write or rewrite memories.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from . import gbrain, jev, river, writer
from .collect import collect
from .dsl import TZ, RecipeError
from .model import STATE_KEY, EventTrigger, Noul, Recipe, Source
from .notify import notify

SYNC_EVERY = 10  # seconds between Gmail/Calendar syncs (one incremental sync takes ~2 s)
RUN = os.environ.get("MEMENTO_RUN", "")  # namespace for Memento-written pages in a clean demo run
STATE_FILE = gbrain.ROOT / f"memento-state-{RUN or 'default'}.json"  # fired triggers + demo clock
# gate probability above which a new memory gets written
WRITE_AT = {Source.email: 0.6, Source.calendar: 0.7, Source.chat: 0.85}
# Another agent in this repo sends its own demo emails to the same inbox with this prefix.
IGNORE_SUBJECT_PREFIX = "[Memento]"
KIND = {Source.email: "email", Source.calendar: "calendar event", Source.chat: "chat message"}

# One extra question rides along with every Jev call: is this worth a memory?
GATES = {
    Source.email: Noul(
        question="Does `email` contain something the user must remember or act on later "
        "(a trip, appointment, reservation, deadline, bill, promise or personal date), "
        "rather than a newsletter, receipt, promotion or routine notification?"
    ),
    Source.calendar: Noul(
        question="Is `event` an appointment, trip, meeting or occasion the user should prepare for or be reminded of?"
    ),
    Source.chat: Noul(
        question="Is `message` the user stating a decided plan, appointment, promise or date to remember (not a question or an idea)?"
    ),
}


@dataclass
class Memory:
    slug: str  # unique key: the page slug, prefixed with its source unless it's in `mem`
    source: str  # GBrain source id
    path: str  # the page's slug inside that source
    origin: str  # "memento" (written from an email) or "you" (a person or agent wrote the page)
    frontmatter: dict
    page_body: str  # the page body as stored, for rewrites that must keep it
    title: str
    body: str
    recipe_src: str
    sources: list[str]
    mtime: float
    updated_at: datetime
    recipe: Recipe | None = None
    error: str | None = None
    missed: set[str] = field(default_factory=set)  # time triggers already past when registered


def _strip_title(body: str) -> str:
    return re.sub(r"\A#\s+.*\n?", "", body).strip()


def _clean_email_text(body: str) -> str:
    lines = []
    for line in body.splitlines():
        if line.startswith(">") or line.startswith("[Source:") or line.startswith("# "):
            continue
        if re.match(r"On .+ wrote:\s*$", line):
            break
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:3000]


def render(item: dict | str) -> str:
    if isinstance(item, str):
        return item
    return "\n".join(f"{k}: {v}" for k, v in item.items() if k != "text") + "\n\n" + str(item.get("text", ""))


class Engine:
    def __init__(self) -> None:
        self.memories: dict[str, Memory] = {}
        self.fired: set[str] = set()
        self.offset = timedelta()
        self.feed: deque[dict] = deque(maxlen=200)
        self.listeners: set[asyncio.Queue] = set()
        self.event_mtimes: dict[Path, float] = {}
        self.page_mtimes: dict[Path, float] = {}
        self.page_keys: dict[Path, str] = {}
        self.history: list[ModelMessage] = []
        self.tasks: list[asyncio.Task] = []
        self._sync_now = asyncio.Event()
        self._chat_agent: Agent[None, str] | None = None

    # ---- clock and feed ----

    def now(self) -> datetime:
        return datetime.now(TZ) + self.offset

    def today(self) -> str:
        return self.now().strftime("%A, %B %-d, %Y, %-I:%M %p")

    def publish(self, event: dict) -> None:
        event.setdefault("at", self.now().isoformat())
        routine = event["type"] == "sync" and event.get("status") != "error" and not event.get("new_events")
        if not routine:  # streamed live, but kept out of the stored feed so history survives a reload
            self.feed.append(event)
        for queue in list(self.listeners):
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.listeners.discard(queue)

    def advance(self, minutes: int) -> None:
        self.offset += timedelta(minutes=minutes)
        self._save_state()
        self.publish({"type": "clock", "now": self.now().isoformat(), "offset_minutes": self.offset_minutes})

    def reset_clock(self) -> None:
        self.offset = timedelta()
        self._save_state()
        self.publish({"type": "clock", "now": self.now().isoformat(), "offset_minutes": 0})

    @property
    def offset_minutes(self) -> int:
        return int(self.offset.total_seconds() // 60)

    def _load_state(self) -> None:
        if STATE_FILE.exists():
            saved = json.loads(STATE_FILE.read_text())
            self.fired = set(saved.get("fired", []))
            self.offset = timedelta(minutes=saved.get("offset_minutes", 0))

    def _save_state(self) -> None:
        STATE_FILE.write_text(json.dumps({"fired": sorted(self.fired), "offset_minutes": self.offset_minutes}))

    # ---- lifecycle ----

    async def start(self) -> None:
        self._load_state()
        await self.scan_memories()
        self.event_mtimes = self._event_files()  # mail that is already here is history, not news
        self.tasks = [
            asyncio.create_task(self._loop(self.scan_memories, 1.0)),
            asyncio.create_task(self._loop(self.tick, 1.0)),
            asyncio.create_task(self._sync_loop()),
        ]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()

    async def _loop(self, fn, every: float) -> None:
        while True:
            try:
                await fn()
            except Exception as e:  # keep the demo alive
                self.publish({"type": "sync", "status": "error", "error": f"{fn.__name__}: {e}"})
            await asyncio.sleep(every)

    # ---- memories ----

    async def scan_memories(self) -> None:
        """Register every page with a recipe; unregister pages that lost it or were deleted."""
        seen: set[Path] = set()
        for source in gbrain.SOURCES:
            if not source.root.exists():
                continue
            for path in source.root.rglob("*.md"):
                if ".git" in path.parts:
                    continue
                slug = gbrain.slug_of(path, source.root)
                if slug.startswith(gbrain.MEMORY_PREFIX) and not path.name.startswith(RUN):
                    continue  # Memento-written pages from another demo run
                seen.add(path)
                mtime = path.stat().st_mtime
                if self.page_mtimes.get(path) == mtime:
                    continue
                self.page_mtimes[path] = mtime
                fm, body = gbrain.read_page(path)
                if fm.get("recipe"):
                    await self.register(path, source, slug, fm, body)
                elif path in self.page_keys:
                    self.memories.pop(self.page_keys.pop(path), None)
        for path in [p for p in self.page_keys if p not in seen]:
            self.memories.pop(self.page_keys.pop(path), None)
            self.page_mtimes.pop(path, None)

    async def register(self, path: Path, source, slug: str, fm: dict, body: str) -> Memory:
        key = slug if source.id == gbrain.MEMORY_SOURCE else f"{source.id}:{slug}"
        memory = Memory(
            slug=key,
            source=source.id,
            path=slug,
            origin="memento" if fm.get("memento") else "you",
            frontmatter=fm,
            page_body=body,
            title=str(fm.get("title") or slug),
            body=_strip_title(body),
            recipe_src=str(fm.get("recipe") or ""),
            sources=list(fm.get("sources") or []),
            mtime=path.stat().st_mtime,
            updated_at=self.now(),
        )
        try:
            memory.recipe = await asyncio.to_thread(collect, memory.recipe_src, memory=key)
        except RecipeError as e:
            memory.error = str(e)
        if memory.recipe:
            now = self.now()
            memory.missed = {t.id for t in memory.recipe.triggers if t.kind == "time" and t.fire_at <= now}
        change = "updated" if key in self.memories else "added"
        self.memories[key] = memory
        self.page_keys[path] = key
        self.publish({"type": "memory", "memory": self.view(memory), "change": change})
        return memory

    def active(self, memory: Memory) -> bool:
        if memory.recipe is None or memory.error:
            return False
        return memory.recipe.expires is None or memory.recipe.expires > self.now()

    def event_triggers(self, source: Source) -> list[tuple[Memory, EventTrigger]]:
        return [
            (m, t)
            for m in self.memories.values()
            if self.active(m)
            for t in m.recipe.triggers
            if isinstance(t, EventTrigger) and t.source == source
        ]

    # ---- time ----

    async def tick(self) -> None:
        now = self.now()
        for memory in list(self.memories.values()):
            if not self.active(memory):
                continue
            for t in memory.recipe.triggers:
                if t.kind == "time" and t.fire_at <= now and t.id not in self.fired and t.id not in memory.missed:
                    self.fired.add(t.id)
                    self._save_state()
                    await notify(memory.title, t.action.text)
                    self.publish(
                        {"type": "fired", "trigger_id": t.id, "memory": memory.slug, "title": memory.title,
                         "action": "alert", "text": t.action.text}
                    )  # fmt: skip

    # ---- incoming items ----

    async def handle(self, source: Source, title: str, item: dict | str, origin: str | None = None):
        """One Jev call: this item against every active trigger on its source, plus the write gate."""
        event_id = uuid.uuid4().hex[:8]
        self.publish({"type": "event_in", "id": event_id, "source": source.value, "title": title})
        pairs = self.event_triggers(source)
        questions: dict = {t.id: t.when for _, t in pairs}
        questions["__gate__"] = GATES[source]
        state = {"today": self.today(), STATE_KEY[source]: item}
        try:
            probs, stats = await jev.ask(state, questions)
        except Exception as e:
            self.publish({"type": "sync", "status": "error", "error": f"Jev: {e}"})
            return []
        ranked = sorted(((probs[t.id], m, t) for m, t in pairs), key=lambda r: -r[0])
        fired = [(m, t, p) for p, m, t in ranked if p >= t.threshold]
        self.publish(
            {
                "type": "jev",
                "event_id": event_id,
                "source": source.value,
                **stats,
                "n_triggers": len(pairs),
                "results": [
                    {"trigger_id": t.id, "memory": m.slug, "title": m.title, "question": t.when.question,
                     "p": round(p, 3), "threshold": t.threshold, "fired": p >= t.threshold}
                    for p, m, t in ranked[:8]
                ],
            }
        )  # fmt: skip
        rewriting = False
        for memory, trigger, p in fired:
            for action in trigger.actions:
                event = {"type": "fired", "trigger_id": trigger.id, "memory": memory.slug, "title": memory.title,
                         "action": action.kind, "p": round(p, 3)}  # fmt: skip
                if action.kind == "alert":
                    event["text"] = action.text
                    await notify(memory.title, action.text)
                elif action.kind == "rewrite":
                    rewriting = True
                    asyncio.create_task(self.rewrite(memory, source, item))
                self.publish(event)
        p_write = probs["__gate__"]
        decision = "write" if p_write >= WRITE_AT[source] and not rewriting else "skip"
        self.publish(
            {"type": "gate", "event_id": event_id, "kind": "worth remembering", "p": round(p_write, 3),
             "decision": decision}
        )  # fmt: skip
        if decision == "write":
            asyncio.create_task(self.write_new(source, item, origin))
        return fired

    async def write_new(self, source: Source, item: dict | str, origin: str | None) -> None:
        self.publish({"type": "writer", "status": "start"})
        started = time.perf_counter()
        prompt = writer.new_item_prompt(self.today(), KIND[source], render(item), [m.title for m in self.memories.values()])
        try:
            draft, requests = await writer.write(prompt)
            if draft is None:
                self.publish({"type": "writer", "status": "done", "ms": self._ms(started), "attempts": requests})
                return
            markdown = gbrain.memory_markdown(draft.title, draft.body, draft.recipe, [origin] if origin else [])
            slug = self._free_slug(gbrain.slugify(draft.title, RUN))
            try:
                await gbrain.put_page(gbrain.MEMORY_SOURCE, slug, markdown)
            except RuntimeError:  # GBrain refuses a slug whose file was removed by hand: take a fresh one
                slug = f"{slug}-{uuid.uuid4().hex[:4]}"
                await gbrain.put_page(gbrain.MEMORY_SOURCE, slug, markdown)
        except Exception as e:
            self.publish({"type": "writer", "status": "error", "error": str(e)[:300]})
            return
        self.publish({"type": "writer", "status": "done", "memory": slug, "title": draft.title,
                      "ms": self._ms(started), "attempts": requests})  # fmt: skip
        await self.scan_memories()

    async def rewrite(self, memory: Memory, source: Source, item: dict | str) -> None:
        self.publish({"type": "writer", "status": "start", "memory": memory.slug, "title": memory.title})
        started = time.perf_counter()
        current = f"# {memory.title}\n{memory.body}\n\n```python\n{memory.recipe_src}```"
        try:
            draft, requests = await writer.write(writer.rewrite_prompt(self.today(), current, KIND[source], render(item)))
            if draft is None:
                raise RuntimeError("the writer returned NOTHING for a rewrite")
            fm = dict(memory.frontmatter)
            fm["recipe"] = draft.recipe
            if memory.origin == "memento":  # Memento's own note: rewrite it whole
                fm["title"] = draft.title
                body = f"# {draft.title}\n\n{draft.body}"
            else:  # someone else's page: keep every word, append what changed
                body = f"{memory.page_body}\n\n## Update, {self.now():%b %-d %-I:%M %p} (memento)\n\n{draft.body}"
            await gbrain.put_page(memory.source, memory.path, gbrain.page_markdown(fm, body))
        except Exception as e:
            self.publish({"type": "writer", "status": "error", "memory": memory.slug, "error": str(e)[:300]})
            return
        title = draft.title if memory.origin == "memento" else memory.title
        self.publish({"type": "writer", "status": "done", "memory": memory.slug, "title": title,
                      "ms": self._ms(started), "attempts": requests})  # fmt: skip
        await self.scan_memories()

    def _free_slug(self, slug: str) -> str:
        candidate, n = slug, 2
        while candidate in self.memories:
            candidate, n = f"{slug}-{n}", n + 1
        return candidate

    @staticmethod
    def _ms(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)

    # ---- Gmail and Calendar, through GBrain ----

    def _event_files(self) -> dict[Path, float]:
        return {p: p.stat().st_mtime for p in gbrain.GOOGLE_DIR.rglob("*.md")} if gbrain.GOOGLE_DIR.exists() else {}

    def request_sync(self) -> None:
        self._sync_now.set()

    async def _sync_loop(self) -> None:
        while True:
            try:
                await self.sync_once()
            except Exception as e:
                self.publish({"type": "sync", "status": "error", "error": str(e)[:300]})
            try:
                await asyncio.wait_for(self._sync_now.wait(), SYNC_EVERY)
            except TimeoutError:
                pass
            self._sync_now.clear()

    async def sync_once(self) -> None:
        self.publish({"type": "sync", "status": "start"})
        code, out = await gbrain.sync_google()
        current = self._event_files()
        new = [p for p, mtime in current.items() if self.event_mtimes.get(p) != mtime]
        self.event_mtimes = current
        fresh = [p for p in sorted(new, key=lambda p: current[p]) if self._is_fresh(p)]
        status = {"type": "sync", "status": "done" if code == 0 else "error", "new_events": len(fresh)}
        if code != 0:
            status["error"] = out.strip().splitlines()[-1][:300] if out.strip() else "sync failed"
        self.publish(status)
        for path in fresh:
            await self.on_page(path)

    def _is_fresh(self, path: Path) -> bool:
        """Only react to mail from the last day and events in the next two weeks, not the backfill."""
        fm, _ = gbrain.read_page(path)
        if str(fm.get("title") or "").startswith(IGNORE_SUBJECT_PREFIX):
            return False
        real_now = datetime.now(TZ)
        if fm.get("type") == "email":
            when = _parse_time(fm.get("date"))
            return when is not None and real_now - when < timedelta(days=1)
        when = _parse_time(fm.get("start"))
        return when is not None and timedelta(0) < when - real_now < timedelta(days=14)

    async def on_page(self, path: Path) -> None:
        fm, body = gbrain.read_page(path)
        origin = gbrain.slug_of(path, gbrain.GOOGLE_DIR)
        if fm.get("type") == "email":
            item = {"from": fm.get("from"), "subject": fm.get("title"), "date": fm.get("date"),
                    "text": _clean_email_text(body)}  # fmt: skip
            await self.handle(Source.email, str(fm.get("title") or "email"), item, origin)
        else:
            item = {"title": fm.get("title"), "start": fm.get("start"), "end": fm.get("end"),
                    "location": fm.get("location"), "attendees": fm.get("attendees"), "text": body[:1500]}  # fmt: skip
            await self.handle(Source.calendar, str(fm.get("title") or "event"), item, origin)

    # ---- chat ----

    def chat_agent(self) -> Agent[None, str]:
        if self._chat_agent is None:
            self._chat_agent = Agent(
                river.model(river.FAST),
                output_type=str,
                instructions=(
                    "You are the user's personal assistant; the user talks to you about their own life. "
                    "Be brief and concrete (1-3 sentences). "
                    "Some of the user's memories may be attached to their message because they matter "
                    "right now: use them, and point out conflicts or things to prepare."
                ),
            )
        return self._chat_agent

    async def chat(self, message: str) -> dict:
        self.publish({"type": "chat", "role": "user", "text": message})
        fired = await self.handle(Source.chat, message[:80], message)
        surfaced: dict[str, tuple[Memory, float]] = {}
        for memory, trigger, p in fired:
            if any(a.kind == "surface" for a in trigger.actions):
                surfaced[memory.slug] = (memory, max(p, surfaced.get(memory.slug, (memory, 0.0))[1]))
        notes = "\n\n".join(f"Memory: {m.title}\n{m.body}" for m, _ in surfaced.values())
        prompt = f"Now: {self.today()}.\n" + (f"\n{notes}\n\n" if notes else "\n") + f"User: {message}"
        try:
            result = await self.chat_agent().run(prompt, message_history=self.history[-12:])
            reply = result.output
            self.history = result.all_messages()
        except Exception as e:
            reply = f"(assistant unavailable: {e})"
        chips = [{"slug": m.slug, "title": m.title, "p": round(p, 3)} for m, p in surfaced.values()]
        self.publish({"type": "chat", "role": "assistant", "text": reply, "surfaced": chips})
        return {"reply": reply, "surfaced": chips}

    # ---- views ----

    def view(self, memory: Memory) -> dict:
        triggers = []
        if memory.recipe:
            for t in memory.recipe.triggers:
                if t.kind == "time":
                    triggers.append({"id": t.id, "kind": "time", "fire_at": t.fire_at.isoformat(),
                                     "fired": t.id in self.fired or t.id in memory.missed,
                                     "actions": [f"alert: {t.action.text}"]})  # fmt: skip
                else:
                    actions = [f"alert: {a.text}" if a.kind == "alert" else a.kind for a in t.actions]
                    triggers.append({"id": t.id, "kind": "event", "source": t.source.value,
                                     "question": t.when.question, "threshold": t.threshold, "actions": actions})  # fmt: skip
        expires = memory.recipe.expires.isoformat() if memory.recipe and memory.recipe.expires else None
        status = "error" if memory.error else ("active" if self.active(memory) else "expired")
        return {
            "slug": memory.slug,
            "path": memory.path,
            "source": memory.source,
            "origin": memory.origin,
            "title": memory.title,
            "body": memory.body,
            "recipe": memory.recipe_src,
            "sources": memory.sources,
            "expires": expires,
            "status": status,
            "error": memory.error,
            "triggers": triggers,
            "updated_at": memory.updated_at.isoformat(),
        }

    def snapshot(self) -> dict:
        memories = sorted(self.memories.values(), key=lambda m: m.updated_at, reverse=True)
        return {
            "now": self.now().isoformat(),
            "offset_minutes": self.offset_minutes,
            "memories": [self.view(m) for m in memories],
            "feed": list(self.feed),
        }


def _parse_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=TZ)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)
