"""Event-driven, durable local runtime for active Markdown memories."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .collector import collect
from .dsl import EventTrigger, Question, Recipe
from .memory import digest, parse_memory, render_memory
from .providers import memory_gate
from .settings import Settings
from .storage import Store


class StaleMemoryError(ValueError):
    """An asynchronous rewrite no longer owns the current memory revision."""


def trim_email(text: str) -> str:
    text = re.split(r"\nOn .{0,200}wrote:|\n-{2,}\s*Forwarded message|\n--\s*\n", text, maxsplit=1)[0]
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))[:16_000]


class Runtime:
    def __init__(self, settings: Settings, store: Store, *, evaluator=None, writer=None, brain=None, telegram=None):
        self.settings, self.store = settings, store
        self.evaluator, self.writer, self.brain, self.telegram = evaluator, writer, brain, telegram
        self.tasks: set[asyncio.Task] = set()
        self.worker_tasks: list[asyncio.Task] = []
        self.event_lock = asyncio.Lock()
        self.memory_lock = asyncio.Lock()
        self.delivery_lock = asyncio.Lock()
        self.stopping = False
        self.status = {"jev": "configured" if evaluator else "unconfigured", "river": "configured" if writer else "unconfigured", "gbrain": "configured" if brain else "disabled", "google": "waiting" if settings.gbrain_enabled else "disabled", "telegram": "configured" if telegram else "unconfigured"}

    def now(self, demo=False) -> datetime:
        now = datetime.now(timezone.utc)
        return now + timedelta(seconds=self.store.setting("demo_clock_offset", 0)) if demo else now

    def memory_now(self, memory: dict) -> datetime:
        return self.now("/demo/" in memory["id"])

    def active_memories(self) -> list[tuple[dict, Recipe]]:
        active = []
        for memory in self.store.memories(active_only=True):
            recipe = Recipe.model_validate(memory["compiled"])
            if recipe.expires_at and recipe.expires_at <= self.memory_now(memory):
                self.store.deactivate(memory["id"])
                self.store.trace("expired", memory["title"], {"memory_id": memory["id"]})
                continue
            active.append((memory, recipe))
        return active

    async def register_markdown(self, memory_id: str, markdown: str, *, write_brain=False, expected_revision: str | None = None, preserve_event_id: str | None = None) -> dict:
        if not re.fullmatch(r"memories/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+", memory_id):
            raise ValueError("memory id must be a safe slug below memories/")
        async with self.memory_lock:
            current = self.store.memory(memory_id)
            def check_revision() -> None:
                if expected_revision is not None:
                    latest = self.store.memory(memory_id)
                    if not latest or not latest["active"] or latest["revision"] != expected_revision:
                        raise StaleMemoryError("memory changed while its rewrite was being prepared")
            check_revision()
            revision = digest(markdown)
            if current and current["active"] and current["revision"] == revision and not current.get("error"):
                return current
            try:
                fields = parse_memory(markdown)
                compiled = await asyncio.to_thread(collect, fields["recipe"])
            except Exception as exc:
                check_revision()
                if current:
                    self.store.deactivate(memory_id, error=self._safe_error(exc))
                self.store.trace("error", "Recipe rejected; old behavior stopped", {"memory_id": memory_id, "error": str(exc)})
                raise
            check_revision()
            if write_brain and self.brain:
                await self._put_brain(memory_id, markdown)
            check_revision()
            record = {"id": memory_id, **fields, "markdown": markdown, "compiled": compiled.model_dump(mode="json"), "revision": revision, "active": True}
            self.store.save_memory(record, preserve_event_id=preserve_event_id)
            mirror = self.settings.data_dir / f"{memory_id}.md"
            mirror.parent.mkdir(parents=True, exist_ok=True)
            temp = mirror.with_suffix(".tmp")
            temp.write_text(markdown)
            temp.chmod(0o600)
            temp.replace(mirror)
            self.store.trace("memory", f"{'Updated' if current else 'Remembered'}: {fields['title']}", {"memory_id": memory_id, "reminders": len(compiled.reminders), "triggers": len(compiled.triggers)})
            return record

    async def _put_brain(self, memory_id: str, markdown: str) -> None:
        expected = None
        try:
            page = await self.brain.get_page(memory_id, source="default")
            expected = page.get("revision")
        except Exception:
            # Create-only writes still reject conflicts and outages; no force writes.
            pass
        await self.brain.put_page(memory_id, markdown, source="default", expected_revision=expected, request_id=str(uuid.uuid4()))
        self.status["gbrain"] = "connected"

    async def submit(self, source: str, payload: dict, *, event_id: str | None = None) -> str:
        if source not in {"email", "calendar", "chat"}:
            raise ValueError("source must be email, calendar, or chat")
        event_id = event_id or f"{'demo' if payload.get('demo') else source}:{uuid.uuid4()}"
        if self.store.add_event(event_id, source, payload):
            self.store.trace("event", payload.get("title") or f"New {source}", {"event_id": event_id, "source": source, "demo": payload.get("demo", False)})
        return event_id

    @staticmethod
    def _filters_match(trigger: EventTrigger, event: dict) -> bool:
        for field, expected in trigger.filters.items():
            actual = event.get(field)
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _matches(trigger: EventTrigger, answer: Any) -> bool:
        if trigger.question.kind == "choice":
            return answer.value == trigger.match and answer.probability >= trigger.threshold
        if trigger.question.kind == "score":
            return answer.probability >= trigger.threshold
        return answer.probability >= trigger.threshold

    async def process_event(self, event: dict) -> None:
        async with self.event_lock:
            event_id, source = event["id"], event["source"]
            current_event = self.store.event(event_id)
            if current_event and current_event["status"] in {"done", "writing"}:
                return
            payload = dict(event["payload"])
            self.store.set_event_status(event_id, "processing")
            saved_writes = self.store.setting(f"writer_plan:{digest(event_id)}")
            if saved_writes is not None:
                # Recover the exact remaining writer work instead of classifying
                # against recipes that earlier successful writes have changed.
                self.store.set_event_status(event_id, "writing")
                self._spawn(self._finish_event_writes(event_id, saved_writes))
                await self.deliver()
                return
            if source == "email" and "text" in payload:
                payload["text"] = trim_email(payload["text"])
            state = {"event": {"id": event_id, "source": source, **payload}, "today": self.now(payload.get("demo", False)).astimezone(ZoneInfo(self.settings.timezone)).isoformat(), "timezone": self.settings.timezone}
            triggers: dict[str, tuple[dict, EventTrigger]] = {}
            questions = {}
            for memory, recipe in self.active_memories():
                # Simulated events never fire watches over personal live data.
                if bool(payload.get("demo")) != ("/demo/" in memory["id"]):
                    continue
                for index, trigger in enumerate(recipe.triggers):
                    if trigger.source != source or not self._filters_match(trigger, payload):
                        continue
                    key = f"t_{digest(memory['id'] + str(index))}"
                    triggers[key] = (memory, trigger)
                    questions[key] = trigger.question
            questions["remember"] = Question(**memory_gate(source))
            if not self.evaluator:
                self.store.set_event_status(event_id, "failed", "Jev is not configured")
                return
            try:
                batch = await self.evaluator.decide(state, questions)
                self.status["jev"] = "connected"
                self.store.trace("jev", f"{len(questions)} conditions · {batch.elapsed_ms:.0f} ms", {"event_id": event_id, "state": state, "questions": {k: v.model_dump(mode="json") for k, v in questions.items()}, "result": batch.model_dump(mode="json")})
                rewritten = set()
                writes = []
                for key, (memory, trigger) in triggers.items():
                    answer = batch.answers.get(key)
                    if answer is None or not self._matches(trigger, answer):
                        continue
                    # Re-check after network latency in case the memory was replaced.
                    current = self.store.memory(memory["id"])
                    if not current or not current["active"] or current["revision"] != memory["revision"]:
                        continue
                    for index, action in enumerate(trigger.actions):
                        # An event retried after a partial writer failure must
                        # not repeat alerts simply because a rewrite succeeded.
                        action_id = digest(f"{event_id}:{memory['id']}:{key}:{index}")
                        if action.kind == "rewrite":
                            if memory["id"] not in rewritten:
                                rewritten.add(memory["id"])
                                writes.append((state["event"], memory))
                        else:
                            text = action.text if action.kind == "alert" else f"{memory['title']}\n\n{memory['body']}"
                            if self.store.enqueue(notification_id=action_id, memory=memory, kind=action.kind, text=text, event_id=event_id):
                                self.store.trace("fired", f"{action.kind}: {memory['title']}", {"question": trigger.question.question, "probability": answer.probability, "event_id": event_id})
                gate = batch.answers.get("remember")
                if gate and gate.probability >= 0.8 and not rewritten:
                    writes.append(({**state["event"], "today": state["today"]}, None))
                if writes:
                    self.store.set_setting(f"writer_plan:{digest(event_id)}", writes)
                    self.store.set_event_status(event_id, "writing")
                    self._spawn(self._finish_event_writes(event_id, writes))
                else:
                    self.store.set_event_status(event_id, "done")
                await self.deliver()
            except Exception as exc:
                error = self._safe_error(exc)
                self.status["jev"] = "error"
                self.store.set_event_status(event_id, "failed", error)
                self.store.trace("error", "Event processing failed", {"event_id": event_id, "error": error})

    async def _finish_event_writes(self, event_id: str, writes: list[tuple[dict, dict | None]]) -> None:
        async def finish_one(event: dict, memory: dict | None) -> None:
            identity = memory["id"] if memory else "new"
            key = f"writer_job:{digest(event_id)}:{identity}"
            if self.store.setting(key):
                return
            await self.write_memory(event, existing=memory, raise_errors=True)
            self.store.set_setting(key, True)

        outcomes = await asyncio.gather(
            *(finish_one(event, memory) for event, memory in writes),
            return_exceptions=True,
        )
        failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        if failures:
            self.store.set_event_status(event_id, "failed", self._safe_error(failures[0]))
            retry = self.store.event(event_id)
            self.store.trace("retry", "Memory work will retry", {
                "event_id": event_id, "attempt": retry["attempts"],
                "retry_at": retry["retry_at"], "error": retry["error"],
            })
        else:
            self.store.set_event_status(event_id, "done")

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def wait_idle(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def write_memory(self, event: dict, *, existing: dict | None = None, raise_errors=False) -> dict | None:
        if not self.writer:
            self.store.trace("error", "Memory needs a writer", {"event_id": event.get("id"), "reason": "River is not configured"})
            if raise_errors:
                raise RuntimeError("River is not configured")
            return None
        try:
            self.store.trace("writing", "Updating a memory" if existing else "Writing a memory", {"event_id": event.get("id")})
            event = {**event, "today": event.get("today") or self.now(event.get("demo", False)).astimezone(ZoneInfo(self.settings.timezone)).isoformat(), "timezone": self.settings.timezone}
            draft = await self.writer.write(
                event, existing_memory=existing,
                now=self.now(event.get("demo", False)),
                timezone_name=self.settings.timezone,
            )
            if existing:
                current = self.store.memory(existing["id"])
                if not current or not current["active"] or current["revision"] != existing["revision"]:
                    self.store.trace("stale", "Discarded an outdated rewrite", {"memory_id": existing["id"]})
                    return None
                memory_id = existing["id"]
            else:
                # Stable across model retries even if its generated title changes.
                memory_id = f"memories/{'demo/' if event.get('demo') else ''}event-{digest(event.get('id', json.dumps(event)))[:16]}"
            markdown = render_memory(draft.title, draft.body, draft.recipe, draft.sources)
            memory = await self.register_markdown(
                memory_id, markdown, write_brain=True,
                expected_revision=existing["revision"] if existing else None,
                preserve_event_id=event.get("id") if existing else None,
            )
            self.status["river"] = "connected"
            await self.tick()
            return memory
        except StaleMemoryError:
            self.store.trace("stale", "Discarded an outdated rewrite", {"memory_id": existing["id"] if existing else None})
            return None
        except Exception as exc:
            self.status["river"] = "error"
            self.store.trace("error", "Memory writer failed", {"event_id": event.get("id"), "error": self._safe_error(exc)})
            if raise_errors:
                raise
            return None

    async def tick(self) -> None:
        for memory, recipe in self.active_memories():
            now = self.memory_now(memory)
            for reminder in recipe.reminders:
                if reminder.at <= now:
                    key = digest(f"reminder:{memory['id']}:{reminder.at.isoformat()}:{reminder.text}")
                    if self.store.enqueue(notification_id=key, memory=memory, kind="reminder", text=reminder.text):
                        self.store.trace("fired", f"Reminder: {memory['title']}", {"at": reminder.at.isoformat(), "demo": "/demo/" in memory["id"]})
        await self.deliver()

    async def deliver(self) -> None:
        async with self.delivery_lock:
            await self._deliver_pending()

    async def _deliver_pending(self) -> None:
        for notification in reversed(self.store.notifications(pending_only=True)):
            memory = self.store.memory(notification["memory_id"])
            if not memory or not memory["active"] or memory["revision"] != notification["memory_revision"]:
                self.store.notification_status(notification["id"], "canceled")
                continue
            if not self.telegram or not self.telegram.chat_id:
                # Remains visible locally; send to the paired owner after pairing.
                continue
            try:
                message_id = await self.telegram.send(notification["text"])
                self.store.notification_status(notification["id"], "delivered", telegram_id=message_id)
                self.status["telegram"] = "connected"
            except Exception as exc:
                self.status["telegram"] = "error"
                self.store.notification_status(notification["id"], "pending", error=self._safe_error(exc))
                break

    async def sync_brain(self) -> None:
        if not self.brain:
            return
        try:
            if self.settings.gbrain_enabled:
                await self.brain.sync(source="google")
                self.status["google"] = "connected"
            cursor = self.store.setting("gbrain_cursor")
            pages = await self.brain.list_pages(updated_after=cursor)
            newest = cursor
            for row in pages:
                slug, source = row["slug"], row.get("source_id", "default")
                if source == "google" and not self.settings.gbrain_enabled:
                    continue
                updated = row.get("updated_at")
                if updated and (newest is None or updated > newest):
                    newest = updated
                seen_key = f"page:{source}:{slug}"
                if self.store.setting(seen_key) == updated:
                    continue
                if row.get("deleted_at"):
                    if slug.startswith("memories/") and source == "default":
                        self.store.deactivate(slug)
                    self.store.set_setting(seen_key, updated)
                    continue
                page = await self.brain.get_page(slug, source=source)
                content = page.get("content") or page.get("body") or ""
                if slug.startswith("memories/") and source == "default":
                    try:
                        await self.register_markdown(slug, content)
                    except Exception:
                        # Invalid memory stops its old behavior; other pages
                        # must still progress past this page in the same sync.
                        self.store.set_setting(seen_key, updated)
                        continue
                elif source == "google":
                    kind = "calendar" if slug.startswith("calendar/") else "email"
                    await self.submit(kind, {"text": content, "title": page.get("title", row.get("title", "")), "slug": slug, "source_id": source, "page_revision": page.get("revision"), "frontmatter": page.get("frontmatter", {})}, event_id=f"gbrain:{source}:{slug}:{page.get('revision', updated)}")
                self.store.set_setting(seen_key, updated)
            if newest:
                # Inclusive overlap avoids losing pages that share a timestamp.
                overlap = datetime.fromisoformat(newest.replace("Z", "+00:00")) - timedelta(milliseconds=1)
                self.store.set_setting("gbrain_cursor", overlap.isoformat())
            self.status["gbrain"] = "connected"
        except Exception as exc:
            self.status["gbrain"] = "error"
            self.store.trace("error", "GBrain sync failed", {"error": self._safe_error(exc)})

    def _safe_error(self, exc: Exception) -> str:
        text = str(exc)
        for secret in (self.settings.jev_api_key, self.settings.river_api_key, self.settings.telegram_token):
            if secret:
                text = text.replace(secret, "[redacted]")
        return text[:2000]

    async def _worker(self):
        while not self.stopping:
            for event in self.store.ready_events():
                await self.process_event(event)
            await asyncio.sleep(1)

    async def _clock_loop(self):
        while not self.stopping:
            await self.tick()
            await asyncio.sleep(1)

    async def _sync_loop(self):
        while not self.stopping:
            await self.sync_brain()
            await asyncio.sleep(self.settings.sync_seconds)

    async def _telegram_loop(self):
        while not self.stopping:
            try:
                if not self.telegram.username:
                    await self.telegram.connect()
                await self.telegram.poll()
                self.status["telegram"] = "connected" if self.telegram.chat_id else "pairing"
            except Exception as exc:
                self.status["telegram"] = "error"
                self.store.trace("error", "Telegram connection failed", {"error": self._safe_error(exc)})
                await asyncio.sleep(5)

    async def start(self):
        if self.worker_tasks:
            return
        self.stopping = False
        self.worker_tasks.append(asyncio.create_task(self._worker()))
        self.worker_tasks.append(asyncio.create_task(self._clock_loop()))
        if self.brain:
            self.worker_tasks.append(asyncio.create_task(self._sync_loop()))
        if self.telegram:
            self.worker_tasks.append(asyncio.create_task(self._telegram_loop()))

    async def stop(self):
        self.stopping = True
        for task in self.worker_tasks:
            task.cancel()
        await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.worker_tasks.clear()
        await self.wait_idle()
        if self.telegram:
            await self.telegram.close()
        if self.writer and hasattr(self.writer, "close"):
            await self.writer.close()

    def snapshot(self) -> dict:
        active = self.active_memories()
        traces = self.store.traces()
        last_jev = next((t for t in traces if t["kind"] == "jev"), None)
        return {
            "name": "memento", "status": self.status,
            "now": self.now().isoformat(), "demo_now": self.now(True).isoformat(),
            "demo_clock_offset": self.store.setting("demo_clock_offset", 0),
            "memories": self.store.memories(), "events": self.store.events(limit=30),
            "notifications": self.store.notifications(), "traces": traces,
            "counts": {"memories": len(active), "triggers": sum(len(r.triggers) + len(r.reminders) for _, r in active), "last_jev_ms": last_jev["detail"]["result"]["elapsed_ms"] if last_jev else None},
            "telegram": {"username": self.telegram.username, "paired": bool(self.telegram.chat_id), "pair_code": self.telegram.pair_code if not self.telegram.chat_id else None} if self.telegram else None,
        }
