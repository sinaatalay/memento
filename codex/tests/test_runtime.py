"""Runtime guarantees tested with real SQLite/recipes and fake external IO."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memento.collector import RecipeError, collect
from memento.memory import render_memory
from memento.providers import DecisionAnswer, DecisionBatch
from memento.runtime import Runtime, StaleMemoryError
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


class FakeTelegram:
    chat_id = "owner"
    username = "memento_fixture"

    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, text):
        # Yield to expose two concurrent deliveries reading the same pending row.
        await asyncio.sleep(0.005)
        self.sent.append(text)
        return str(len(self.sent))

    async def poll(self):
        await asyncio.sleep(10)

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
    telegram = FakeTelegram()
    first_store = Store(path)
    first = Runtime(settings, first_store, telegram=telegram)
    first.now = lambda demo=False: NOW
    await register(first, recipe=REMINDER)
    await first.tick()
    assert len(telegram.sent) == 1
    first_store.close()

    second_store = Store(path)
    second = Runtime(settings, second_store, telegram=telegram)
    second.now = lambda demo=False: NOW
    try:
        await second.tick()
        assert len(telegram.sent) == 1
        assert second_store.notifications()[0]["status"] == "delivered"
    finally:
        second_store.close()


@pytest.mark.asyncio
async def test_concurrent_delivery_sends_once(runtime):
    runtime.telegram = FakeTelegram()
    await register(runtime, recipe=REMINDER)
    await asyncio.gather(runtime.tick(), runtime.tick(), runtime.deliver())
    assert runtime.telegram.sent == ["Leave for the airport."]


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
    runtime.telegram = FakeTelegram()
    await runtime.tick()
    assert runtime.telegram.sent == []
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
    runtime.telegram = FakeTelegram()
    await runtime.deliver()
    await register(runtime, recipe=REMINDER, body="Added gate B2.")
    await runtime.tick()
    assert len(runtime.telegram.sent) == 1


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
    runtime.telegram = FakeTelegram()
    await process(runtime)
    await runtime.wait_idle()
    assert runtime.store.event("fixture:1")["status"] == "failed"
    await runtime.process_event(runtime.store.event("fixture:1"))
    await runtime.wait_idle()
    assert runtime.store.event("fixture:1")["status"] == "done"
    assert runtime.writer.attempts == {"memories/first": 1, "memories/second": 2}
    assert len(runtime.telegram.sent) == 2
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
    # Telegram is disconnected or not paired yet.
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
    runtime.telegram = FakeTelegram()
    await runtime.tick()
    assert runtime.store.notifications()[0]["status"] == "canceled"
    assert not runtime.store.memory("memories/flight")["active"]
    assert runtime.telegram.sent == []


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
    assert runtime.brain.syncs == []
    assert runtime.store.events() == []
    assert runtime.store.memory("memories/synced") is not None


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
    runtime.telegram = FakeTelegram()
    await register(runtime, recipe=REMINDER)
    await runtime.submit("email", {"text": "A test event"})
    await runtime.start()
    await started.wait()
    for _ in range(100):
        if runtime.telegram.sent:
            break
        await asyncio.sleep(0.005)
    assert runtime.telegram.sent == ["Leave for the airport."]
    await runtime.stop()
    assert runtime.writer.closed and runtime.telegram.closed
    assert runtime.worker_tasks == []
    assert not runtime.tasks
