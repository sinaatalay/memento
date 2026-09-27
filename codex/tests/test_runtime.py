"""Runtime guarantees tested with real SQLite/recipes and fake external IO."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memento.collector import RecipeError, collect
from memento.memory import render_memory
from memento.providers import DecisionAnswer, DecisionBatch
from memento.runtime import Runtime, StaleMemoryError, safe_page_slug
from memento.settings import Settings
from memento.storage import Store
from memento.writer import MemoryDraft


NOW = datetime(2026, 9, 27, 21, 0, tzinfo=timezone.utc)
REMINDER = '''\
from memento import remind, at
remind(at("2026-09-27T20:00Z"), "Leave for the airport.")
'''
WATCH = '''\
from memento import on, email, noul, alert
on(email, when=noul("Did the flight change?"), do=alert("Flight changed."))
'''
REWRITE = '''\
from memento import on, email, noul, alert, rewrite, this
on(
    email, when=noul("Did the flight change?"),
    do=[alert("Flight changed."), rewrite(this)],
)
'''


class FakeEvaluator:
    def __init__(self, *, trigger=0.99, gate=0.01, fail=False):
        self.trigger, self.gate, self.fail = trigger, gate, fail
        self.calls = []

    async def decide(self, state, questions):
        self.calls.append((state, questions))
        if self.fail:
            raise RuntimeError("Jev temporary outage")
        return DecisionBatch(
            model="fixture", elapsed_ms=10,
            answers={key: DecisionAnswer(
                kind="noul", value=(self.gate if key == "remember" else self.trigger) >= 0.5,
                probability=self.gate if key == "remember" else self.trigger,
            ) for key in questions},
        )


class FakeWriter:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []
        self.closed = False

    async def write(self, event, existing_memory=None, **kwargs):
        self.calls.append((event, existing_memory, kwargs))
        if self.fail:
            raise RuntimeError("River temporary outage")
        return MemoryDraft(
            title="A remembered plan", body="A concrete future commitment.",
            recipe="", sources=[event["id"]],
        )

    async def close(self):
        self.closed = True


class FakeEmail:
    ready = True
    recipient = "owner@example.test"

    def __init__(self):
        self.sent = []
        self.calls = []
        self.closed = False

    async def send(self, text, *, subject="Memento", notification_id=None):
        self.calls.append({"text": text, "subject": subject, "notification_id": notification_id})
        # Yield to expose two concurrent deliveries reading the same pending row.
        await asyncio.sleep(0.005)
        self.sent.append(text)
        return str(len(self.sent))

    async def connect(self):
        self.ready = True
        return {"ready": True}

    async def close(self):
        self.closed = True


class FakeBrain:
    def __init__(self, pages):
        self.pages = pages
        self.gets = []
        self.syncs = []

    async def sync(self, **kwargs):
        self.syncs.append(kwargs)

    async def list_pages(self, **kwargs):
        return list(self.pages.values())

    async def get_page(self, slug, source="default"):
        self.gets.append((source, slug))
        return self.pages[slug]


@pytest.fixture
def runtime(tmp_path):
    settings = Settings(data_dir=tmp_path)
    store = Store(tmp_path / "ledger.sqlite3")
    store.set_setting("email_enabled_since", "1970-01-01T00:00:00+00:00")
    runtime = Runtime(settings, store)
    runtime.now = lambda demo=False: NOW + timedelta(
        seconds=store.setting("demo_clock_offset", 0) if demo else 0
    )
    yield runtime
    store.close()


async def register(runtime, *, memory_id="memories/flight", recipe=WATCH, body="Flight UA123"):
    return await runtime.register_markdown(
        memory_id, render_memory("Flight", body, recipe, ["fixture:flight"])
    )


async def process(runtime, *, event_id="fixture:1", demo=False, text="New departure time"):
    await runtime.submit("email", {"text": text, "demo": demo}, event_id=event_id)
    await runtime.process_event(runtime.store.event(event_id))


@pytest.mark.asyncio
async def test_reminder_is_not_repeated_after_restart(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    settings = Settings(data_dir=tmp_path)
    notifier = FakeEmail()
    first_store = Store(path)
    first = Runtime(settings, first_store, notifier=notifier)
    first.now = lambda demo=False: NOW
    await register(first, recipe=REMINDER)
    await first.tick()
    assert len(notifier.sent) == 1
    first_store.close()

    second_store = Store(path)
    second = Runtime(settings, second_store, notifier=notifier)
    second.now = lambda demo=False: NOW
    try:
        await second.tick()
        assert len(notifier.sent) == 1
        assert second_store.notifications()[0]["status"] == "delivered"
    finally:
        second_store.close()


@pytest.mark.asyncio
async def test_concurrent_delivery_sends_once(runtime):
    runtime.notifier = FakeEmail()
    await register(runtime, recipe=REMINDER)
    await asyncio.gather(runtime.tick(), runtime.tick(), runtime.deliver())
    assert runtime.notifier.sent == ["Leave for the airport."]
    delivered = runtime.store.notifications()[0]
    assert delivered["delivery_id"] == "1"
    assert runtime.notifier.calls[0]["notification_id"] == delivered["id"]
    assert runtime.notifier.calls[0]["subject"] == "Memento · Flight"


@pytest.mark.asyncio
async def test_email_enablement_keeps_old_backlog_local_and_sends_new_work(tmp_path):
    store = Store(tmp_path / "ledger.sqlite3")
    settings = Settings(data_dir=tmp_path)
    before = Runtime(settings, store)
    before.now = lambda demo=False: NOW
    await register(before, recipe=REMINDER, memory_id="memories/demo/old")
    await before.tick()
    assert store.notifications()[0]["status"] == "pending"
    notifier = FakeEmail()
    after = Runtime(settings, store, notifier=notifier)
    after.now = lambda demo=False: NOW
    try:
        cutoff = store.setting("email_enabled_since")
        await after.tick()
        assert notifier.sent == []
        assert store.notifications()[0]["status"] == "local"
        await register(after, recipe=REMINDER, memory_id="memories/new")
        await after.tick()
        assert len(notifier.sent) == 1
        restarted = Runtime(settings, store, notifier=notifier)
        assert store.setting("email_enabled_since") == cutoff
        await restarted.deliver()
        assert len(notifier.sent) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_email_enablement_does_not_turn_old_unprocessed_events_into_mail(tmp_path):
    store = Store(tmp_path / "ledger.sqlite3")
    settings = Settings(data_dir=tmp_path)
    before = Runtime(settings, store)
    await register(before)
    await before.submit("email", {"text": "Old update"}, event_id="old-event")
    after = Runtime(settings, store, notifier=FakeEmail(), evaluator=FakeEvaluator())
    try:
        await after.process_event(store.event("old-event"))
        assert store.notifications()[0]["status"] == "local"
        assert after.notifier.sent == []
        await after.submit("email", {"text": "New update"}, event_id="new-event")
        await after.process_event(store.event("new-event"))
        assert after.notifier.sent == ["Flight changed."]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_surface_remains_local_while_alert_is_emailed(runtime):
    runtime.notifier = FakeEmail()
    runtime.evaluator = FakeEvaluator()
    await register(runtime, recipe='''\
from memento import on, chat, noul, surface, alert, this
on(chat, when=noul("Flight relevant?"),
   do=[surface(this), alert("Flight plans need attention.")])
''')
    event_id = await runtime.submit("chat", {"text": "What about my flight?"})
    await runtime.process_event(runtime.store.event(event_id))
    notifications = {n["kind"]: n for n in runtime.store.notifications()}
    assert notifications["surface"]["status"] == "local"
    assert notifications["alert"]["status"] == "delivered"
    assert runtime.notifier.sent == ["Flight plans need attention."]
    assert runtime.evaluator.calls[0][0]["relevant_memories"][0]["body"] == "Flight UA123"


@pytest.mark.asyncio
async def test_update_decisions_include_only_matching_memory_context(runtime):
    runtime.evaluator = FakeEvaluator(trigger=0.01)
    await register(runtime, body="Flight UA123 departs at 08:05 on September 28.")
    await register(runtime, memory_id="memories/demo/flight", body="Synthetic flight")
    await register(runtime, memory_id="memories/chat-only", recipe='''\
from memento import on, chat, noul, surface, this
on(chat, when=noul("Relevant?"), do=surface(this))
''')
    await process(runtime, text="UA123 now departs at 06:30 instead of 08:05.")
    state, _ = runtime.evaluator.calls[0]
    assert state["relevant_memories"] == [{
        "id": "memories/flight", "title": "Flight",
        "body": "Flight UA123 departs at 08:05 on September 28.",
    }]


@pytest.mark.asyncio
async def test_demo_email_is_clearly_labeled_without_changing_live_text(runtime):
    runtime.notifier = FakeEmail()
    await register(runtime, recipe=REMINDER, memory_id="memories/demo/synthetic")
    await runtime.tick()
    sent = runtime.notifier.calls[0]
    assert sent["subject"].startswith("DEMO · ")
    assert sent["text"].startswith("DEMO — synthetic example.")
    assert "Leave for the airport." in sent["text"]


@pytest.mark.asyncio
async def test_email_failure_backs_off_outbox_and_reuses_notification_identity(runtime):
    class FlakyEmail(FakeEmail):
        fail = True

        async def send(self, *args, **kwargs):
            if self.fail:
                self.calls.append(kwargs)
                raise RuntimeError("Email provider temporarily unavailable")
            return await super().send(*args, **kwargs)

    runtime.notifier = FlakyEmail()
    await register(runtime, recipe=REMINDER, memory_id="memories/first")
    await register(runtime, recipe=REMINDER, memory_id="memories/second")
    await runtime.tick()
    first_id = runtime.notifier.calls[0]["notification_id"]
    await runtime.tick()
    await runtime.deliver()
    assert len(runtime.notifier.calls) == 1  # Global outage backoff, not one row per tick.
    failed = next(n for n in runtime.store.notifications() if n["id"] == first_id)
    assert failed["attempts"] == 1 and failed["retry_at"]
    runtime.notifier.fail = False
    runtime.store.set_setting("email_send_retry_at", None)
    runtime.store.db.execute("UPDATE notifications SET retry_at=NULL WHERE id=?", (first_id,))
    runtime.store.db.commit()
    await runtime.deliver()
    assert len(runtime.notifier.sent) == 2
    assert runtime.notifier.calls[1]["notification_id"] == first_id
    assert all(n["status"] == "delivered" for n in runtime.store.notifications())


@pytest.mark.asyncio
async def test_email_retry_keeps_frozen_payload_when_its_rewrite_changes_title(runtime):
    class IntentCheckingEmail(FakeEmail):
        intents = {}

        async def send(self, text, *, subject="Memento", notification_id=None):
            intent = (subject, text)
            if notification_id not in self.intents:
                self.intents[notification_id] = intent
                raise RuntimeError("Email outcome temporarily unknown")
            assert self.intents[notification_id] == intent
            return await super().send(text, subject=subject, notification_id=notification_id)

    runtime.notifier = IntentCheckingEmail()
    runtime.evaluator = FakeEvaluator()
    await register(runtime)
    await process(runtime)
    pending = runtime.store.notifications()[0]
    assert pending["delivery_subject"] == "Memento · Flight"
    assert pending["delivery_text"] == "Flight changed."

    # Same-event rewrite retains the alert but can change its memory's title.
    await runtime.register_markdown(
        "memories/flight", render_memory("Rescheduled flight", "New departure", "", []),
        preserve_event_id="fixture:1",
    )
    runtime.store.set_setting("email_send_retry_at", None)
    runtime.store.db.execute("UPDATE notifications SET retry_at=NULL")
    runtime.store.db.commit()
    await runtime.deliver()
    assert runtime.store.notifications()[0]["status"] == "delivered"
    assert runtime.notifier.calls[0]["subject"] == "Memento · Flight"
    assert runtime.notifier.calls[0]["notification_id"] == pending["id"]

    # A second preparation, including after reload, cannot alter saved bytes.
    frozen = runtime.store.prepare_delivery(pending["id"], subject="Changed", text="Changed")
    assert frozen["delivery_subject"] == "Memento · Flight"
    assert frozen["delivery_text"] == "Flight changed."


@pytest.mark.asyncio
async def test_missing_consent_does_not_attempt_sends_or_repeat_connect(runtime):
    class NeedsConsent(FakeEmail):
        ready = False
        connect_calls = 0

        async def connect(self):
            self.connect_calls += 1
            raise RuntimeError("gmail.send is not granted")

    runtime.notifier = NeedsConsent()
    await register(runtime, recipe=REMINDER)
    await runtime.tick()
    await runtime.tick()
    assert runtime.notifier.calls == []
    assert runtime.notifier.connect_calls == 1
    assert runtime.status["email"] == "needs_consent"
    assert runtime.store.notifications()[0]["attempts"] == 0
    assert runtime.store.notifications()[0]["status"] == "pending"


def test_old_delivery_ledger_migrates_without_losing_acknowledgments(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE notifications (
            id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, memory_revision TEXT NOT NULL,
            event_id TEXT, kind TEXT NOT NULL, text TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT,
            telegram_message_id TEXT, error TEXT
        );
        INSERT INTO notifications VALUES (
            'legacy', 'memories/old', 'r1', NULL, 'alert', 'Old alert',
            'delivered', '2026-09-27T20:00Z', '2026-09-27T20:01Z', 'old-ack', NULL
        );
    ''')
    connection.close()
    store = Store(path)
    try:
        row = store.notifications()[0]
        assert row["delivery_id"] == "old-ack"
        assert row["status"] == "delivered"
        assert row["attempts"] == 0 and row["retry_at"] is None
        assert row["delivery_subject"] is None and row["delivery_text"] is None
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "replace", "invalid"])
async def test_changed_memory_cancels_pending_old_behavior(runtime, change):
    memory = await register(runtime, recipe=REMINDER)
    await runtime.tick()
    assert runtime.store.notifications()[0]["status"] == "pending"
    if change == "delete":
        runtime.store.deactivate(memory["id"])
    elif change == "replace":
        await register(runtime, recipe="", body="The trip is canceled.")
    else:
        with pytest.raises(RecipeError):
            await register(runtime, recipe="import os")
        assert runtime.store.memory(memory["id"])["error"]
    runtime.notifier = FakeEmail()
    await runtime.tick()
    assert runtime.notifier.sent == []
    assert runtime.store.notifications()[0]["status"] == "canceled"


@pytest.mark.asyncio
async def test_reverting_invalid_edit_restores_valid_memory(runtime):
    first = await register(runtime, recipe=REMINDER)
    with pytest.raises(RecipeError):
        await register(runtime, recipe="import os")
    assert not runtime.store.memory(first["id"])["active"]
    restored = await register(runtime, recipe=REMINDER)
    assert restored["active"]
    assert not runtime.store.memory(first["id"])["error"]


@pytest.mark.asyncio
async def test_body_edit_preserves_pending_reminder_but_not_delivered_duplicate(runtime):
    await register(runtime, recipe=REMINDER)
    await runtime.tick()
    edited = await register(runtime, recipe=REMINDER, body="Added seat 12A.")
    await runtime.tick()
    assert runtime.store.notifications()[0]["status"] == "pending"
    assert runtime.store.notifications()[0]["memory_revision"] == edited["revision"]
    runtime.notifier = FakeEmail()
    await runtime.deliver()
    await register(runtime, recipe=REMINDER, body="Added gate B2.")
    await runtime.tick()
    assert len(runtime.notifier.sent) == 1


@pytest.mark.asyncio
async def test_canonical_front_matter_changes_do_not_invalidate_behavior(runtime):
    original = await register(runtime, recipe=REMINDER)
    await runtime.tick()
    canonical = original["markdown"].replace(
        "---\n", "---\ntype: memory\nprovenance: gbrain\n", 1
    )
    changed = await runtime.register_markdown(original["id"], canonical)
    assert changed["revision"] == original["revision"]
    assert runtime.store.notifications()[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_external_metadata_only_edit_persists_without_rotating_behavior(runtime):
    memory_id = "projects/acme.v1"
    original = await register(runtime, recipe=REMINDER, memory_id=memory_id)
    await runtime.tick()
    revised = original["markdown"].replace(
        "---\n", "---\ntags: [pilot, important]\nowner: Maya\n", 1
    )
    updated = await runtime.register_markdown(memory_id, revised)
    assert updated["revision"] == original["revision"]
    assert updated["markdown"] == revised
    assert runtime.store.memory(memory_id)["markdown"] == revised
    assert (runtime.settings.data_dir / f"{memory_id}.md").read_text() == revised
    notification = runtime.store.notifications()[0]
    assert notification["memory_revision"] == original["revision"]
    assert notification["status"] == "pending"
    assert len(runtime.store.memories()) == 1


@pytest.mark.asyncio
async def test_demo_events_and_clock_are_isolated_from_live_memories(runtime):
    runtime.evaluator = FakeEvaluator()
    await register(runtime, memory_id="memories/live-flight")
    await register(runtime, memory_id="memories/demo/flight")
    await process(runtime, demo=True)
    assert {n["memory_id"] for n in runtime.store.notifications()} == {"memories/demo/flight"}
    assert len(runtime.evaluator.calls[0][1]) == 2  # demo watch + gate
    await process(runtime, event_id="fixture:live", demo=False)
    assert len(runtime.evaluator.calls[1][1]) == 2
    assert len(runtime.store.notifications()) == 2

    future = 'from memento import remind, at\nremind(at("2026-09-28T20:00Z"), "Future")'
    await register(runtime, memory_id="memories/live-reminder", recipe=future)
    await register(runtime, memory_id="memories/demo/reminder", recipe=future)
    runtime.store.set_setting("demo_clock_offset", 86400)
    await runtime.tick()
    reminders = [n for n in runtime.store.notifications() if n["kind"] == "reminder"]
    assert {n["memory_id"] for n in reminders} == {"memories/demo/reminder"}


def test_score_uses_normalized_value_and_choice_requires_matching_winner():
    recipe = collect('''\
from memento import on, email, score, choice, alert
on(email, when=score("Urgency", ["Low", "Med", "High"]),
   do=alert("Urgent"), threshold=0.9)
on(email, when=choice("Kind", travel="Travel", other="Other"),
   do=alert("Travel"), match="travel", threshold=0.8)
''')
    score, choice = recipe.triggers
    assert not Runtime._matches(score, DecisionAnswer(kind="score", value=1.6, probability=0.8))
    assert Runtime._matches(score, DecisionAnswer(kind="score", value=1.9, probability=0.95))
    assert not Runtime._matches(choice, DecisionAnswer(kind="choice", value="other", probability=0.99))
    assert not Runtime._matches(choice, DecisionAnswer(kind="choice", value="travel", probability=0.7))
    assert Runtime._matches(choice, DecisionAnswer(kind="choice", value="travel", probability=0.95))


@pytest.mark.asyncio
async def test_all_relevant_watches_and_gate_share_one_call(runtime):
    runtime.evaluator = FakeEvaluator()
    await register(runtime, memory_id="memories/flight")
    await register(runtime, memory_id="memories/second-flight")
    filtered = '''\
from memento import on, email, noul, alert
on(email, when=noul("Update?"), do=alert("Update"), sender="airline")
'''
    await register(runtime, memory_id="memories/filter", recipe=filtered)
    await process(runtime, text="Actual update\nOn yesterday wrote:\nold message")
    assert len(runtime.evaluator.calls) == 1
    state, questions = runtime.evaluator.calls[0]
    assert len(questions) == 3
    assert "remember" in questions
    assert state["event"]["text"] == "Actual update"
    assert len(runtime.store.notifications()) == 2
    # Reprocessing the same event is inert, including writer/gate work.
    await runtime.process_event(runtime.store.event("fixture:1"))
    assert len(runtime.evaluator.calls) == 1


@pytest.mark.asyncio
async def test_writer_failure_keeps_event_retryable_then_recovers(runtime):
    runtime.evaluator = FakeEvaluator(gate=0.99)
    runtime.writer = FakeWriter(fail=True)
    await process(runtime)
    assert runtime.store.event("fixture:1")["status"] == "writing"
    await runtime.wait_idle()
    failed = runtime.store.event("fixture:1")
    assert failed["status"] == "failed"
    assert failed["retry_at"] is not None
    assert "River temporary outage" in failed["error"]
    trace = next(t for t in runtime.store.traces() if t["kind"] == "retry")
    assert trace["detail"]["attempt"] == 1
    assert trace["detail"]["retry_at"] == failed["retry_at"]
    assert runtime.store.ready_events() == []  # Backoff, not a busy retry loop.
    ready = runtime.store.ready_events(now="9999-01-01T00:00:00+00:00")
    assert len(ready) == 1
    runtime.writer.fail = False
    await runtime.process_event(ready[0])
    await runtime.wait_idle()
    assert runtime.store.event("fixture:1")["status"] == "done"
    assert len(runtime.store.memories()) == 1
    assert len(runtime.evaluator.calls) == 1  # Retry saved writer work only.


@pytest.mark.asyncio
async def test_partial_rewrite_failure_does_not_repeat_successful_work(runtime):
    await register(runtime, memory_id="memories/first", recipe=REWRITE)
    await register(runtime, memory_id="memories/second", recipe=REWRITE)
    runtime.evaluator = FakeEvaluator()

    class PartialWriter(FakeWriter):
        attempts = {}

        async def write(self, event, existing_memory=None, **kwargs):
            memory_id = existing_memory["id"]
            self.attempts[memory_id] = self.attempts.get(memory_id, 0) + 1
            if memory_id == "memories/second" and self.attempts[memory_id] == 1:
                raise RuntimeError("Temporary writer outage for the second memory")
            return MemoryDraft(
                title="Updated flight", body="New time; keep watching.",
                recipe=REWRITE, sources=[event["id"]],
            )

    runtime.writer = PartialWriter()
    runtime.notifier = FakeEmail()
    await process(runtime)
    await runtime.wait_idle()
    assert runtime.store.event("fixture:1")["status"] == "failed"
    await runtime.process_event(runtime.store.event("fixture:1"))
    await runtime.wait_idle()
    assert runtime.store.event("fixture:1")["status"] == "done"
    assert runtime.writer.attempts == {"memories/first": 1, "memories/second": 2}
    assert len(runtime.notifier.sent) == 2
    assert len(runtime.evaluator.calls) == 1


@pytest.mark.asyncio
async def test_jev_failure_retries_without_losing_event(runtime):
    runtime.evaluator = FakeEvaluator(fail=True)
    await process(runtime)
    event = runtime.store.event("fixture:1")
    assert event["status"] == "failed"
    assert event["attempts"] == 1
    runtime.evaluator.fail = False
    await runtime.process_event(event)
    assert runtime.store.event("fixture:1")["status"] == "done"
    assert runtime.store.event("fixture:1")["attempts"] == 2


def test_interrupted_evaluation_and_writes_requeue_on_restart(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    store = Store(path)
    for state in ("processing", "writing", "done"):
        store.add_event(state, "email", {"text": "fixture"})
        store.set_event_status(state, state)
    store.close()
    reopened = Store(path)
    try:
        assert {event["id"] for event in reopened.ready_events()} == {"processing", "writing"}
        assert reopened.event("done")["status"] == "done"
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_rewrite_does_not_clobber_edit_during_writer_call(runtime):
    original = await register(runtime)
    started, release = asyncio.Event(), asyncio.Event()

    class WaitingWriter(FakeWriter):
        async def write(self, *args, **kwargs):
            started.set()
            await release.wait()
            return await super().write(*args, **kwargs)

    runtime.writer = WaitingWriter()
    task = asyncio.create_task(runtime.write_memory({"id": "update"}, existing=original))
    await started.wait()
    newer = await register(runtime, recipe="", body="Canceled by the user.")
    release.set()
    assert await task is None
    assert runtime.store.memory(original["id"])["revision"] == newer["revision"]


@pytest.mark.asyncio
async def test_revision_is_checked_inside_registration_lock(runtime):
    original = await register(runtime)
    await runtime.memory_lock.acquire()
    task = asyncio.create_task(runtime.register_markdown(
        original["id"], render_memory("Stale", "Old result", "", []),
        expected_revision=original["revision"],
    ))
    await asyncio.sleep(0)
    # Simulate a deletion winning while the rewrite waits for the lock.
    runtime.store.deactivate(original["id"])
    runtime.memory_lock.release()
    with pytest.raises(StaleMemoryError):
        await task
    assert not runtime.store.memory(original["id"])["active"]


@pytest.mark.asyncio
async def test_rewrite_and_gate_do_not_create_duplicate_memory(runtime):
    await register(runtime, recipe=REWRITE)
    runtime.evaluator = FakeEvaluator(gate=0.99)
    runtime.writer = FakeWriter()
    await process(runtime)
    await runtime.wait_idle()
    assert len(runtime.writer.calls) == 1
    assert runtime.writer.calls[0][1]["id"] == "memories/flight"
    assert len(runtime.store.memories()) == 1
    assert runtime.store.event("fixture:1")["status"] == "done"
    # The memory rewrite must not cancel its own already-triggered alert while
    # Email is disconnected or not paired yet.
    notifications = runtime.store.notifications()
    assert len(notifications) == 1
    assert notifications[0]["status"] == "pending"
    assert notifications[0]["memory_revision"] == runtime.store.memory("memories/flight")["revision"]


@pytest.mark.asyncio
async def test_writer_receives_demo_clock_and_timezone(runtime):
    runtime.writer = FakeWriter()
    runtime.store.set_setting("demo_clock_offset", 86400)
    result = await runtime.write_memory({"id": "demo:1", "demo": True})
    assert result["id"].startswith("memories/demo/")
    _, _, kwargs = runtime.writer.calls[0]
    assert kwargs["now"] == NOW + timedelta(days=1)
    assert kwargs["timezone_name"] == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_expiration_stops_behavior_and_cancels_pending(runtime):
    recipe = REMINDER + 'from memento import expires\nexpires(at("2026-09-27T22:00Z"))'
    await register(runtime, recipe=recipe)
    await runtime.tick()
    runtime.now = lambda demo=False: NOW + timedelta(hours=2)
    runtime.notifier = FakeEmail()
    await runtime.tick()
    assert runtime.store.notifications()[0]["status"] == "canceled"
    assert not runtime.store.memory("memories/flight")["active"]
    assert runtime.notifier.sent == []


@pytest.mark.asyncio
async def test_memory_only_sync_never_ingests_google_pages(runtime):
    runtime.brain = FakeBrain({
        "emails/private": {
            "slug": "emails/private", "source_id": "google",
            "updated_at": "2026-09-27T21:00:00Z", "content": "Private email",
        },
        "memories/synced": {
            "slug": "memories/synced", "source_id": "default",
            "updated_at": "2026-09-27T21:00:00Z",
            "content": render_memory("Synced", "A fact", "", []),
        },
    })
    await runtime.sync_brain()
    assert runtime.brain.gets == [("default", "memories/synced")]
    assert runtime.brain.syncs == [{"source": "default", "working_tree": True}]
    assert runtime.store.events() == []
    assert runtime.store.memory("memories/synced") is not None


@pytest.mark.asyncio
async def test_delete_and_restore_without_updated_at_change_are_reconciled(runtime):
    slug = "projects/lifecycle"
    page = {"slug": slug, "source_id": "default", "updated_at": "2026-09-27T20:00:00Z",
            "revision": "r1", "content": render_memory("Lifecycle", "A plan", REMINDER, [])}

    class TimestampFilteredBrain(FakeBrain):
        async def list_pages(self, updated_after=None, **kwargs):
            # Match upstream's problematic semantics, rather than returning
            # every row unconditionally and accidentally hiding the defect.
            return [p for p in self.pages.values()
                    if updated_after is None or p["updated_at"] > updated_after]

    runtime.brain = TimestampFilteredBrain({slug: page})
    await runtime.sync_brain()
    assert runtime.store.memory(slug)["active"]
    await runtime.tick()
    assert runtime.store.notifications()[0]["status"] == "pending"
    # Other pages moved the cursor past this memory before it was deleted.
    runtime.store.set_setting("gbrain_cursor", "2026-09-27T21:00:00Z")
    page["deleted_at"], page["revision"] = "2026-09-27T21:01:00Z", "r2"
    await runtime.sync_brain()
    assert not runtime.store.memory(slug)["active"]
    assert runtime.store.notifications()[0]["status"] == "canceled"
    assert len(runtime.brain.gets) == 1  # A tombstone needs no body read.
    page["deleted_at"], page["revision"] = None, "r3"
    await runtime.sync_brain()
    assert runtime.store.memory(slug)["active"]
    assert runtime.store.setting(f"gbrain_revision:{slug}") == "r3"
    assert len(runtime.brain.gets) == 2
    await runtime.sync_brain()
    assert len(runtime.brain.gets) == 2  # Stable metadata does not re-read bodies.


@pytest.mark.parametrize("slug", ["notes/trip", "projects/v1.0", "people/my_file", "_index", "生活/明日"])
def test_ordinary_canonical_page_slugs_are_safe(slug):
    assert safe_page_slug(slug)


@pytest.mark.parametrize("slug", ["../outside", "notes/../outside", "/absolute", "notes//gap", "notes/.hidden", "notes/back\\slash", "notes/%2e%2e", "notes/white space", "a" * 256])
def test_page_slugs_cannot_escape_memory_mirror(slug):
    assert not safe_page_slug(slug)


@pytest.mark.asyncio
async def test_external_owner_page_lifecycle_never_creates_a_duplicate_capture(runtime):
    slug = "notes/trip.v1"
    page = {
        "slug": slug, "source_id": "default", "updated_at": "2026-09-27T20:00:00Z",
        "revision": "r1", "content": "# A normal note\nAn ordinary saved preference.",
    }
    runtime.brain = FakeBrain({slug: page})
    runtime.evaluator, runtime.writer = FakeEvaluator(gate=0.99), FakeWriter()
    await runtime.sync_brain()
    assert runtime.store.memory(slug)["compiled"] == {"reminders": [], "triggers": [], "expires_at": None}
    assert runtime.brain.syncs == [{"source": "default", "working_tree": True}]

    # Another tool adds the optional recipe to its existing page in place.
    page.update(content=render_memory("A normal note", "Flight tomorrow.", REMINDER, []),
                revision="r2", updated_at="2026-09-27T20:01:00Z")
    await runtime.sync_brain()
    await runtime.tick()
    assert len(runtime.store.memory(slug)["compiled"]["reminders"]) == 1
    assert runtime.store.notifications()[0]["status"] == "pending"

    # Removing just the optional recipe keeps the page and stops its behavior.
    page.update(content="# A normal note\nThe flight was canceled.",
                revision="r3", updated_at="2026-09-27T20:02:00Z")
    await runtime.sync_brain()
    assert runtime.store.notifications()[0]["status"] == "canceled"
    assert runtime.store.memory(slug)["compiled"]["reminders"] == []
    assert [m["id"] for m in runtime.store.memories()] == [slug]
    assert runtime.store.events() == []
    assert runtime.evaluator.calls == [] and runtime.writer.calls == []


@pytest.mark.asyncio
async def test_only_default_owner_pages_are_registered_and_authored_paths_are_not_invented(runtime):
    pages = [
        {"slug": "chats/an-interest", "source_id": "default"},
        {"slug": "projects/a-plan", "source_id": "default"},
        {"slug": "projects/other-account", "source_id": "other"},
        {"slug": "events/gmail/import", "source_id": "default"},
    ]
    runtime.brain = FakeBrain({p["slug"]: {
        **p, "updated_at": "2026-09-27T20:00:00Z", "revision": "r1",
        "content": render_memory("A page", "Useful information", "", []),
    } for p in pages})
    await runtime.sync_brain()
    assert {m["id"] for m in runtime.store.memories()} == {"chats/an-interest", "projects/a-plan"}
    assert runtime.store.events() == []
    assert {slug for _, slug in runtime.brain.gets} == {"chats/an-interest", "projects/a-plan"}


@pytest.mark.asyncio
async def test_enabled_google_sync_uses_normalized_deduplicated_events(runtime):
    runtime.settings.gbrain_enabled = True
    native = {
        "slug": "emails/thread", "source_id": "google", "type": "email",
        "updated_at": "2026-09-27T21:00:00Z", "revision": "email-r1",
        "frontmatter": {"message_id": "same-message", "account": "fixture",
                        "from": "Airline <notice@example.test>"},
        "content": "## Me · 2026-09-26 08:00\nOld fact\n"
                   "## Airline · 2026-09-27 09:00\nNew departure time",
    }
    supplement = {
        **native, "slug": "events/gmail/same-message", "source_id": "default",
        "content": "New departure time",
    }
    calendar = {
        "slug": "calendar/event", "source_id": "google", "type": "meeting",
        "updated_at": "2026-09-27T21:00:01Z", "revision": "calendar-r1",
        "frontmatter": {"event_id": "calendar-event", "start": "2026-09-28T11:00Z"},
        "content": "Flight on the calendar",
    }
    runtime.brain = FakeBrain({p["slug"]: p for p in [native, supplement, calendar]})

    class Intake:
        polled = 0

        async def poll(self):
            assert runtime.brain.syncs  # Native sync occurs first.
            self.polled += 1
            return []

    runtime.google_intake = Intake()
    await runtime.sync_brain()
    events = runtime.store.events()
    assert runtime.google_intake.polled == 1
    assert runtime.brain.syncs == [{"source": "default", "working_tree": True}, {"source": "google"}]
    assert len(events) == 2  # Same Gmail message across both sources, once.
    email = next(e for e in events if e["source"] == "email")
    assert email["payload"]["text"] == "New departure time"
    assert email["payload"]["from_address"] == "notice@example.test"
    assert next(e for e in events if e["source"] == "calendar")["payload"]["start"] == "2026-09-28T11:00Z"


@pytest.mark.asyncio
async def test_remote_rewrite_uses_observed_revision_not_latest_revision(runtime):
    original = await register(runtime)
    runtime.store.set_setting("gbrain_revision:memories/flight", "observed-r1")

    class RemoteBrain:
        calls = []

        async def get_page(self, *args, **kwargs):
            pytest.fail("fresh remote revision must not bless a stale rewrite")

        async def put_page(self, *args, **kwargs):
            self.calls.append(kwargs)
            assert kwargs["expected_revision"] == "observed-r1"
            raise RuntimeError("revision_conflict")

    runtime.brain, runtime.writer = RemoteBrain(), FakeWriter()
    result = await runtime.write_memory({"id": "change"}, existing=original)
    assert result is None
    assert runtime.store.memory(original["id"])["revision"] == original["revision"]
    assert len(runtime.brain.calls) == 1


@pytest.mark.asyncio
async def test_unconfirmed_brain_write_reuses_intent_and_never_claims_commit(runtime):
    class PendingBrain:
        calls = []

        async def put_page(self, *args, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {"state": "pending"}
            return {"state": "committed", "revision": "committed-r1"}

    runtime.brain = PendingBrain()
    markdown = render_memory("Plan", "A sourced plan", "", [])
    with pytest.raises(RuntimeError, match="pending"):
        await runtime.register_markdown("memories/plan", markdown, write_brain=True)
    assert runtime.store.memory("memories/plan") is None
    await runtime.register_markdown("memories/plan", markdown, write_brain=True)
    assert runtime.brain.calls[0] == runtime.brain.calls[1]
    assert runtime.store.setting("gbrain_revision:memories/plan") == "committed-r1"
    assert runtime.store.memory("memories/plan") is not None


@pytest.mark.asyncio
async def test_invalid_synced_memory_does_not_block_other_pages(runtime):
    runtime.brain = FakeBrain({
        "memories/invalid": {
            "slug": "memories/invalid", "source_id": "default",
            "updated_at": "2026-09-27T21:00:00Z",
            "content": render_memory("Invalid", "A fact", "import os", []),
        },
        "memories/valid": {
            "slug": "memories/valid", "source_id": "default",
            "updated_at": "2026-09-27T21:00:01Z",
            "content": render_memory("Valid", "A fact", "", []),
        },
    })
    await runtime.sync_brain()
    assert runtime.store.memory("memories/valid") is not None
    assert runtime.store.setting("gbrain_cursor") is not None


@pytest.mark.asyncio
async def test_clock_is_independent_of_slow_evaluation_and_stop_closes_clients(runtime):
    started, release = asyncio.Event(), asyncio.Event()

    class SlowEvaluator(FakeEvaluator):
        async def decide(self, *args, **kwargs):
            started.set()
            await release.wait()
            return await super().decide(*args, **kwargs)

    runtime.evaluator, runtime.writer = SlowEvaluator(), FakeWriter()
    runtime.notifier = FakeEmail()
    await register(runtime, recipe=REMINDER)
    await runtime.submit("email", {"text": "A test event"})
    await runtime.start()
    await started.wait()
    for _ in range(100):
        if runtime.notifier.sent:
            break
        await asyncio.sleep(0.005)
    assert runtime.notifier.sent == ["Leave for the airport."]
    await runtime.stop()
    assert runtime.writer.closed and runtime.notifier.closed
    assert runtime.worker_tasks == []
    assert not runtime.tasks
