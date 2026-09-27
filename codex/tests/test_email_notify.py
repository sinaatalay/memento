from contextlib import asynccontextmanager
import base64
from email import message_from_bytes
import json

import pytest

from memento.email_notify import EmailNotifier, build_notification
from memento.gbrain import GBrainError


class FakeBrain:
    def __init__(self, home):
        self.home = home
        self.command = ["bun", "/fake/checkout/src/cli.ts"]
        self.timeout = 10

    @asynccontextmanager
    async def _serialized(self):
        yield


def test_mime_routes_only_to_configured_owner_and_marks_output():
    raw, message_id, intent = build_notification(
        "owner@example.test", "Your flight is tomorrow.\nTo: attacker@example.test",
        subject="Upcoming flight", notification_id="notification-1",
    )
    message = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert message["To"] == "owner@example.test"
    assert message["Subject"] == "[Memento] Upcoming flight"
    assert message["Auto-Submitted"] == "auto-generated"
    assert message["X-Memento-Notification"]
    assert message["Message-ID"] == message_id
    assert len(intent) == 64
    assert message_id == build_notification("owner@example.test", "different text", subject="Test", notification_id="notification-1")[1]


@pytest.mark.parametrize("recipient,subject", [
    ("owner@example.test,attacker@example.test", "Test"),
    ("owner@example.test\nBcc: attacker@example.test", "Test"),
    ("owner@example.test", "Test\r\nBcc: attacker@example.test"),
])
def test_header_injection_rejected(recipient, subject):
    with pytest.raises(ValueError):
        build_notification(recipient, "text", subject=subject, notification_id="test")


async def test_send_once_with_stable_receipt_and_reject_changed_intent(tmp_path):
    notifier = EmailNotifier(FakeBrain(tmp_path), "owner@example.test")
    calls = []

    async def request(mode, **payload):
        calls.append(mode)
        return {"connected": True} if mode == "connect" else {"message_id": "gmail-id" if mode == "send" else None}

    notifier._request = request
    first = await notifier.send("Remember your flight", notification_id="one")
    second = await notifier.send("Remember your flight", notification_id="one")
    assert first == second == "gmail-id"
    assert calls.count("send") == 1
    assert notifier.ready
    with pytest.raises(GBrainError, match="different_content"):
        await notifier.send("Different message", notification_id="one")


async def test_ambiguous_send_is_reconciled_without_duplicate_post(tmp_path):
    notifier = EmailNotifier(FakeBrain(tmp_path), "owner@example.test")
    posts = 0
    indexed = False

    async def request(mode, **payload):
        nonlocal posts
        if mode == "connect":
            return {"connected": True}
        if mode == "find":
            return {"message_id": "already-sent" if indexed else None}
        posts += 1
        raise GBrainError("send", "timeout_outcome_unknown")

    notifier._request = request
    with pytest.raises(GBrainError, match="timeout"):
        await notifier.send("Remember", notification_id="one")
    with pytest.raises(GBrainError, match="reconciliation"):
        await notifier.send("Remember", notification_id="one")
    indexed = True
    assert await notifier.send("Remember", notification_id="one") == "already-sent"
    assert posts == 1
    assert next(iter(json.loads(notifier.ledger_path.read_text()).values()))["state"] == "sent"


async def test_connect_missing_send_consent_stays_not_ready(tmp_path):
    notifier = EmailNotifier(FakeBrain(tmp_path), "owner@example.test")

    async def request(mode, **payload):
        raise GBrainError("connect", "scope_missing_gmail_send")

    notifier._request = request
    with pytest.raises(GBrainError, match="scope_missing"):
        await notifier.connect()
    assert not notifier.ready
    assert not notifier.ledger_path.exists()


async def test_definite_quota_rejection_can_retry_without_ambiguous_wedge(tmp_path):
    notifier = EmailNotifier(FakeBrain(tmp_path), "owner@example.test")
    posts = 0

    async def request(mode, **payload):
        nonlocal posts
        if mode == "connect":
            return {"connected": True}
        if mode == "find":
            return {"message_id": None}
        posts += 1
        if posts == 1:
            raise GBrainError("send", "gmail_send_http_429")
        return {"message_id": "sent-after-backoff"}

    notifier._request = request
    with pytest.raises(GBrainError, match="429"):
        await notifier.send("Remember", notification_id="quota")
    assert next(iter(json.loads(notifier.ledger_path.read_text()).values()))["state"] == "rejected"
    assert await notifier.send("Remember", notification_id="quota") == "sent-after-backoff"
    assert posts == 2
