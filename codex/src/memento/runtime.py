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
from .gbrain import GBrain
from .google_intake import GoogleAutomatedIntake, normalize_gbrain_event
from .memory import digest, parse_memory, render_memory
from .providers import annotate_calendar, memory_gate
from .settings import Settings
from .storage import Store, stamp


class StaleMemoryError(ValueError):
    """An asynchronous rewrite no longer owns the current memory revision."""


def trim_email(text: str) -> str:
    text = re.split(r"\nOn .{0,200}wrote:|\n-{2,}\s*Forwarded message|\n--\s*\n", text, maxsplit=1)[0]
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))[:16_000]


def safe_page_slug(slug: str) -> bool:
    """Mirror GBrain's letter/number/underscore-led, relative page segments."""
    return isinstance(slug, str) and 0 < len(slug) <= 255 and bool(
        re.fullmatch(r"\w[\w.-]*(?:/\w[\w.-]*)*", slug)
    )


class Runtime:
    def __init__(self, settings: Settings, store: Store, *, evaluator=None, writer=None, brain=None, notifier=None, google_intake=None):
        self.settings, self.store = settings, store
        self.evaluator, self.writer, self.brain, self.notifier = evaluator, writer, brain, notifier
        if notifier is not None:
            self.store.enable_email_delivery()
        self.google_intake = google_intake
        if self.google_intake is None and settings.gbrain_enabled and isinstance(brain, GBrain):
            self.google_intake = GoogleAutomatedIntake(brain)
        self.tasks: set[asyncio.Task] = set()
        self.worker_tasks: list[asyncio.Task] = []
        self.event_lock = asyncio.Lock()
        self.memory_lock = asyncio.Lock()
        self.delivery_lock = asyncio.Lock()
        self.writer_slots = asyncio.Semaphore(2)
        self.stopping = False
        self.status = {"jev": "configured" if evaluator else "unconfigured", "river": "configured" if writer else "unconfigured", "gbrain": "configured" if brain else "disabled", "google": "waiting" if settings.gbrain_enabled else "disabled", "email": "configured" if notifier else "unconfigured"}

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
        if not safe_page_slug(memory_id) or memory_id.startswith("events/gmail/"):
            raise ValueError("memory id must be a safe owner-page slug outside events/gmail/")
        async with self.memory_lock:
            current = self.store.memory(memory_id)
            def check_revision() -> None:
                if expected_revision is not None:
                    latest = self.store.memory(memory_id)
                    if not latest or not latest["active"] or latest["revision"] != expected_revision:
                        raise StaleMemoryError("memory changed while its rewrite was being prepared")
            check_revision()
            if current and current["active"] and current["markdown"] == markdown and not current.get("error"):
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
            # GBrain canonicalizes front matter and adds provenance metadata.
            # Equivalent serialization must not invalidate scheduled behavior.
            revision = digest(json.dumps({
                "title": fields["title"], "body": fields["body"],
                "sources": sorted(fields["sources"]),
                "compiled": compiled.model_dump(mode="json"),
            }, sort_keys=True))
            if current and current["active"] and current["revision"] == revision and not current.get("error"):
                if write_brain and self.brain:
                    await self._put_brain(memory_id, markdown)
                check_revision()
                self.store.refresh_memory_markdown(memory_id, markdown, recipe=fields["recipe"])
                self._write_mirror(memory_id, markdown)
                return self.store.memory(memory_id)
            if write_brain and self.brain:
                await self._put_brain(memory_id, markdown)
            check_revision()
            record = {"id": memory_id, **fields, "markdown": markdown, "compiled": compiled.model_dump(mode="json"), "revision": revision, "active": not self.store.setting(f"manual_stop:{memory_id}", False)}
            self.store.save_memory(record, preserve_event_id=preserve_event_id)
            self._write_mirror(memory_id, markdown)
            self.store.trace("memory", f"{'Updated' if current else 'Remembered'}: {fields['title']}", {"memory_id": memory_id, "reminders": len(compiled.reminders), "triggers": len(compiled.triggers)})
            return record

    def _write_mirror(self, memory_id: str, markdown: str) -> None:
        mirror = self.settings.data_dir / f"{memory_id}.md"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        temp = mirror.with_suffix(".tmp")
        temp.write_text(markdown)
        temp.chmod(0o600)
        temp.replace(mirror)

    async def _put_brain(self, memory_id: str, markdown: str) -> None:
        intent_key = f"gbrain_write:{memory_id}:{digest(markdown)}"
        intent = self.store.setting(intent_key)
        if intent is None:
            intent = {
                "request_id": str(uuid.uuid4()),
                # This is the revision observed when we read/wrote the memory,
                # not a freshly fetched revision that could bless a stale edit.
                "expected_revision": self.store.setting(f"gbrain_revision:{memory_id}"),
            }
            self.store.set_setting(intent_key, intent)
        receipt = await self.brain.put_page(
            memory_id, markdown, source="default",
            expected_revision=intent["expected_revision"],
            request_id=intent["request_id"],
        )
        if receipt.get("state") != "committed" or not receipt.get("revision"):
            raise RuntimeError(
                f"GBrain write is {receipt.get('state', 'unconfirmed')} "
                f"(request {intent['request_id']})"
            )
        self.store.set_setting(f"gbrain_revision:{memory_id}", receipt["revision"])
        self.store.set_setting(intent_key, None)
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
            message_id = payload.get("message_id") or (payload.get("frontmatter") or {}).get("message_id")
            if source == "email" and (payload.get("memento_notification") or self.store.sent_delivery(message_id)):
                self.store.set_event_status(event_id, "done")
                self.store.trace("suppressed", "Ignored Memento's outgoing notification", {"event_id": event_id})
                return
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
            state = annotate_calendar({"event": {"id": event_id, "source": source, **payload}, "today": self.now(payload.get("demo", False)).astimezone(ZoneInfo(self.settings.timezone)).isoformat(), "timezone": self.settings.timezone})
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
            # Conditions compare the incoming event with stored facts. Include
            # those facts for email/calendar updates as well as chat references
            # such as "my flight"; the condition alone is not the memory.
            relevant = {memory["id"]: memory for memory, _ in triggers.values()}
            state["relevant_memories"] = [
                {"id": m["id"], "title": m["title"], "body": m["body"][:1200]}
                for m in list(relevant.values())[:50]
            ]
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
            async with self.writer_slots:
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
            markdown = render_memory(
                draft.title, draft.body, draft.recipe, draft.sources,
                original_markdown=existing["markdown"] if existing else None,
            )
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
                        cutoff = self.store.setting("email_enabled_since")
                        if (cutoff and "/demo/" in memory["id"]
                                and memory["updated_at"] < cutoff
                                and reminder.at <= datetime.fromisoformat(cutoff)):
                            # Old demo deadlines may not yet have been ticked;
                            # enabling email must not turn them into real mail.
                            self.store.notification_status(key, "local")
                        self.store.trace("fired", f"Reminder: {memory['title']}", {"at": reminder.at.isoformat(), "demo": "/demo/" in memory["id"]})
        await self.deliver()

    async def deliver(self) -> None:
        async with self.delivery_lock:
            await self._deliver_pending()

    async def _deliver_pending(self) -> None:
        cutoff = self.store.enable_email_delivery() if self.notifier else None
        send_retry_at = self.store.setting("email_send_retry_at")
        if send_retry_at and send_retry_at > stamp():
            return
        for notification in self.store.ready_notifications():
            if notification["kind"] == "surface" or (cutoff and notification["created_at"] < cutoff):
                self.store.notification_status(notification["id"], "local")
                continue
            memory = self.store.memory(notification["memory_id"])
            if not memory or not memory["active"] or memory["revision"] != notification["memory_revision"]:
                self.store.notification_status(notification["id"], "canceled")
                continue
            if not self.notifier:
                continue
            if not self.notifier.ready and not await self._connect_notifier():
                break
            try:
                demo = "/demo/" in memory["id"]
                subject = f"{'DEMO · ' if demo else ''}Memento · {memory['title']}"
                text = f"DEMO — synthetic example.\n\n{notification['text']}" if demo else notification["text"]
                prepared = self.store.prepare_delivery(notification["id"], subject=subject, text=text)
                self.store.delivery_attempt(notification["id"])
                delivery_id = await self.notifier.send(
                    prepared["delivery_text"], subject=prepared["delivery_subject"],
                    notification_id=notification["id"],
                )
                self.store.notification_status(notification["id"], "delivered", delivery_id=delivery_id)
                self.store.set_setting("email_send_retry_at", None)
                self.status["email"] = "connected"
            except Exception as exc:
                self.status["email"] = "error" if self.notifier.ready else "needs_consent"
                self.store.notification_status(notification["id"], "pending", error=self._safe_error(exc))
                retry = self.store.notification(notification["id"])
                self.store.set_setting("email_send_retry_at", retry["retry_at"])
                self.store.trace("retry", "Email delivery will retry", {
                    "notification_id": notification["id"], "error": self._safe_error(exc),
                    "attempt": retry["attempts"], "retry_at": retry["retry_at"],
                })
                break

    async def _connect_notifier(self) -> bool:
        if not self.notifier:
            return False
        if self.notifier.ready:
            self.status["email"] = "connected"
            return True
        retry_at = self.store.setting("email_connect_retry_at")
        if retry_at and retry_at > stamp():
            self.status["email"] = "needs_consent"
            return False
        try:
            await self.notifier.connect()
            if self.notifier.ready:
                self.store.set_setting("email_connect_retry_at", None)
                self.status["email"] = "connected"
                return True
            error = "Email sending permission has not been granted"
        except Exception as exc:
            error = self._safe_error(exc)
        self.status["email"] = "needs_consent"
        self.store.set_setting("email_connect_retry_at", (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat())
        self.store.trace("email", "Email sending needs consent", {"error": error})
        return False

    async def sync_brain(self) -> None:
        if not self.brain:
            return
        try:
            # An editor or another agent can author ordinary Markdown directly.
            # Import uncommitted saves into the page index before reconciling it.
            await self.brain.sync(source="default", working_tree=True)
            if self.settings.gbrain_enabled:
                await self.brain.sync(source="google")
                if self.google_intake:
                    try:
                        await self.google_intake.poll()
                    except Exception as exc:
                        self.store.trace("error", "Automated Gmail intake failed", {"error": self._safe_error(exc)})
                self.status["google"] = "connected"
            # A process upgraded from the first prototype may have local
            # memories without their observed GBrain revision. Re-read those
            # pages before allowing any rewrite to use a CAS precondition.
            missing_revisions = {
                m["id"] for m in self.store.memories()
                if not self.store.setting(f"gbrain_revision:{m['id']}")
            }
            # GBrain soft-delete/restore change deleted_at without advancing
            # updated_at. Reconcile the small metadata inventory so neither
            # transition is hidden by a timestamp cursor; fetch bodies only
            # when the content/lifecycle fingerprint changes.
            pages = await self.brain.list_pages(include_deleted=True)
            newest = self.store.setting("gbrain_cursor")
            for row in pages:
                slug, source = row["slug"], row.get("source_id", "default")
                google_event = source == "google" or (source == "default" and slug.startswith("events/gmail/"))
                owner_page = source == "default" and not google_event and safe_page_slug(slug)
                if google_event and not self.settings.gbrain_enabled:
                    continue
                if not owner_page and not google_event:
                    continue
                updated = row.get("updated_at")
                changed = max(filter(None, (updated, row.get("deleted_at"))), default=None)
                if changed and (newest is None or changed > newest):
                    newest = changed
                seen_key = f"page:{source}:{slug}"
                fingerprint = {"updated_at": updated, "deleted_at": row.get("deleted_at")}
                if self.store.setting(seen_key) == fingerprint and slug not in missing_revisions:
                    continue
                if row.get("deleted_at"):
                    if source == "default" and self.store.memory(slug):
                        self.store.deactivate(slug)
                    self.store.set_setting(seen_key, fingerprint)
                    continue
                page = await self.brain.get_page(slug, source=source)
                content = page.get("content") or page.get("body") or ""
                if owner_page:
                    try:
                        await self.register_markdown(slug, content)
                        self.store.set_setting(f"gbrain_revision:{slug}", page.get("revision"))
                    except Exception:
                        # Invalid memory stops its old behavior; other pages
                        # must still progress past this page in the same sync.
                        self.store.set_setting(seen_key, fingerprint)
                        continue
                elif google_event:
                    envelope = normalize_gbrain_event({**row, **page})
                    await self.submit(envelope["source"], envelope["payload"], event_id=envelope["event_id"])
                self.store.set_setting(seen_key, fingerprint)
            if newest:
                # Informational watermark; lifecycle reconciliation is unfiltered.
                overlap = datetime.fromisoformat(newest.replace("Z", "+00:00")) - timedelta(milliseconds=1)
                self.store.set_setting("gbrain_cursor", overlap.isoformat())
            self.status["gbrain"] = "connected"
        except Exception as exc:
            self.status["gbrain"] = "error"
            self.store.trace("error", "GBrain sync failed", {"error": self._safe_error(exc)})

    def _safe_error(self, exc: Exception) -> str:
        text = str(exc)
        for secret in (self.settings.jev_api_key, self.settings.river_api_key, self.settings.gbrain_mcp_token):
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

    async def start(self):
        if self.worker_tasks:
            return
        self.stopping = False
        if self.notifier:
            self.store.enable_email_delivery()
            await self._connect_notifier()
        self.worker_tasks.append(asyncio.create_task(self._worker()))
        self.worker_tasks.append(asyncio.create_task(self._clock_loop()))
        if self.brain:
            self.worker_tasks.append(asyncio.create_task(self._sync_loop()))

    async def stop(self):
        self.stopping = True
        for task in self.worker_tasks:
            task.cancel()
        await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.worker_tasks.clear()
        await self.wait_idle()
        if self.notifier:
            await self.notifier.close()
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
            "email": {"recipient": self.notifier.recipient, "ready": self.notifier.ready, "enabled_since": self.store.setting("email_enabled_since")} if self.notifier else None,
        }
