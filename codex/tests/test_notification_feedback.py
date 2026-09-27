"""A notification must not become a new memory; human replies still must."""

from __future__ import annotations

import pytest

from memento.email_notify import is_memento_notification
from memento.google_intake import normalize_gbrain_event
from memento.memory import digest, render_memory
from memento.providers import DecisionAnswer, DecisionBatch
from memento.runtime import Runtime
from memento.settings import Settings
from memento.storage import Store


OWNER = "owner@example.test"


class SpyEvaluator:
    def __init__(self):
        self.calls = []

    async def decide(self, state, questions):
        self.calls.append(state)
        return DecisionBatch(
            model="fixture", elapsed_ms=0,
            answers={key: DecisionAnswer(kind="noul", value=False, probability=0.01)
                     for key in questions},
        )


class ForbiddenWriter:
    def __init__(self):
        self.calls = []

    async def write(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("An outgoing notification must not enter the memory writer")


@pytest.fixture
def runtime(tmp_path):
    store = Store(tmp_path / "feedback.sqlite3")
    app = Runtime(
        Settings(data_dir=tmp_path, email_recipient=OWNER), store,
        evaluator=SpyEvaluator(), writer=ForbiddenWriter(),
    )
    yield app
    store.close()


def native_thread(*, reply_subject=None, reply_sender=OWNER, citation=True):
    """Match upstream: title is first subject; author/id/body are the latest."""
    latest_id = "aabbccddeeff0002" if reply_subject else "aabbccddeeff0001"
    latest_subject = reply_subject or "[Memento] Flight tomorrow"
    source = (
        f'[Source: email "{latest_subject}", 2026-09-27]'
        f'(https://mail.google.com/mail/u/?authuser={OWNER}#inbox/{latest_id})'
    ) if citation else ""
    latest_body = "Actually UA123 now departs at 06:30." if reply_subject else "Flight departs tomorrow."
    earlier = (
        f"## → Memento <{OWNER}> · 2026-09-27 21:00\n\n"
        '[Source: email "[Memento] Flight tomorrow", 2026-09-27]'
        f'(https://mail.google.com/mail/u/?authuser={OWNER}#inbox/aabbccddeeff0001)'
        f"\n\nTo: {OWNER}\n\nFlight departs tomorrow.\n\n"
    ) if reply_subject else ""
    return {
        "slug": "emails/2026/09/2026-09-27-memento-flight-thread",
        "source_id": "google", "revision": "fixture-r2", "type": "email",
        "title": "[Memento] Flight tomorrow",
        "frontmatter": {
            "account": OWNER, "from": f"Owner <{reply_sender}>", "to": [OWNER],
            "thread_id": "aabbccddeeff0010", "message_id": latest_id,
            "date": "2026-09-27T22:00:00Z",
        },
        "body": (
            "# [Memento] Flight tomorrow\n\n"
            f"{earlier}"
            f"## → Owner <{reply_sender}> · 2026-09-27 22:00\n\n"
            f"{source}\n\nTo: {OWNER}\n\n{latest_body}\n"
        ),
    }


def test_normalized_self_notification_is_recognized():
    envelope = normalize_gbrain_event(native_thread())
    assert envelope["payload"]["subject"] == "[Memento] Flight tomorrow"
    assert envelope["payload"]["memento_notification"] is True


def test_human_self_reply_uses_latest_subject_not_inherited_thread_title():
    envelope = normalize_gbrain_event(native_thread(
        reply_subject="Re: [Memento] Flight tomorrow",
    ))
    assert envelope["payload"]["title"] == "[Memento] Flight tomorrow"
    assert envelope["payload"]["subject"] == "Re: [Memento] Flight tomorrow"
    assert not envelope["payload"]["memento_notification"]
    assert "06:30" in envelope["payload"]["text"]


def test_inherited_title_without_latest_citation_is_not_enough_to_suppress():
    envelope = normalize_gbrain_event(native_thread(
        reply_subject="Re: [Memento] Flight tomorrow", citation=False,
    ))
    assert not envelope["payload"]["memento_notification"]


@pytest.mark.parametrize("sender,subject,expected", [
    (OWNER, "[Memento] Flight tomorrow", True),
    (f"Owner <{OWNER.upper()}>", "[Memento] Flight tomorrow", True),
    (OWNER, "Re: [Memento] Flight tomorrow", False),
    (OWNER, "Fwd: [Memento] Flight tomorrow", False),
    (OWNER, "My notes on [Memento]", False),
    ("someone@example.test", "[Memento] Flight tomorrow", False),
    (f'"{OWNER}" <someone@example.test>', "[Memento] Flight tomorrow", False),
])
def test_marker_filter_requires_real_self_sender_and_anchored_latest_subject(sender, subject, expected):
    assert is_memento_notification({
        "from": sender, "to": [OWNER], "subject": subject,
        "subject_is_latest": True,
    }, own_address=OWNER) is expected


def test_known_gmail_message_id_is_reliable_even_without_subject_metadata():
    assert is_memento_notification(
        {"message_id": "aabbccddeeff0001"},
        own_address=OWNER, sent_message_ids={"aabbccddeeff0001"},
    )
    assert not is_memento_notification(
        {"message_id": "aabbccddeeff0002"},
        own_address=OWNER, sent_message_ids={"aabbccddeeff0001"},
    )


@pytest.mark.asyncio
async def test_suppression_happens_before_classification_or_recovered_writer_work(runtime):
    envelope = normalize_gbrain_event(native_thread())
    event_id = await runtime.submit(**envelope)
    runtime.store.set_setting(f"writer_plan:{digest(event_id)}", [
        ({"id": event_id, "source": "email", "text": "Flight tomorrow"}, None),
    ])
    await runtime.process_event(runtime.store.event(event_id))
    await runtime.wait_idle()
    assert not runtime.evaluator.calls
    assert not runtime.writer.calls
    assert runtime.store.event(event_id)["status"] == "done"
    assert any(row["kind"] == "suppressed" for row in runtime.store.traces())
    assert not runtime.store.ready_events()


@pytest.mark.asyncio
async def test_human_reply_to_notification_reaches_jev(runtime):
    envelope = normalize_gbrain_event(native_thread(
        reply_subject="Re: [Memento] Flight tomorrow",
    ))
    event_id = await runtime.submit(**envelope)
    await runtime.process_event(runtime.store.event(event_id))
    assert len(runtime.evaluator.calls) == 1
    assert "06:30" in runtime.evaluator.calls[0]["event"]["text"]
    assert runtime.store.event(event_id)["status"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [
    {"message_id": "aabbccddeeff0001"},
    {"frontmatter": {"message_id": "aabbccddeeff0001"}},
])
async def test_persisted_sent_message_id_prevents_feedback_after_restart(runtime, tmp_path, identity):
    memory = await runtime.register_markdown(
        "memories/flight", render_memory("Flight", "UA123", "", ["fixture"]),
    )
    runtime.store.enqueue(
        notification_id="notification-1", memory=memory,
        kind="alert", text="Flight tomorrow",
    )
    runtime.store.notification_status(
        "notification-1", "delivered", delivery_id="aabbccddeeff0001",
    )
    store = Store(tmp_path / "feedback.sqlite3")
    restarted = Runtime(runtime.settings, store, evaluator=SpyEvaluator())
    try:
        event_id = await restarted.submit("email", {
            **identity, "text": "Flight tomorrow",
        }, event_id="fixture:received-again")
        await restarted.process_event(store.event(event_id))
        assert not restarted.evaluator.calls
        assert store.event(event_id)["status"] == "done"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_suppression_marker_never_swallows_chat_or_calendar(runtime):
    for source in ("chat", "calendar"):
        event_id = await runtime.submit(source, {
            "memento_notification": True, "text": "Actual user input",
        }, event_id=f"fixture:{source}")
        await runtime.process_event(runtime.store.event(event_id))
    assert len(runtime.evaluator.calls) == 2
