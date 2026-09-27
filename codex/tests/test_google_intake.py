import json

import pytest
import yaml

from memento.gbrain import GBrainError
from memento.google_intake import GoogleAutomatedIntake, normalize_gbrain_event


def message():
    return {
        "id": "abcdef1234567890", "threadId": "112233aabbccdd00",
        "account": "owner@example.test", "subject": "Flight confirmation",
        "from": "Airline <noreply@example.test>", "fromAddress": "noreply@example.test",
        "to": ["owner@example.test"], "cc": [],
        "dateIso": "2026-09-27T22:00:00.000Z", "labelIds": ["INBOX"],
        "bodyText": "Flight departs tomorrow at 08:00.",
    }


def test_native_thread_uses_newest_message_and_ignores_quoted_headers():
    page = {
        "slug": "emails/thread", "source_id": "google", "type": "email",
        "title": "Flight", "revision": "r1",
        "frontmatter": {"message_id": "abcdef1234567890", "account": "owner@example.test",
                        "from": "Airline <noreply@example.test>", "date": "2026-09-27T22:00:00Z"},
        "compiled_truth": """# Flight

## Airline · 2026-09-27 20:00

[Source: email "Flight", 2026-09-27](https://example.test/old)

Flight moved to 09:00.

## Airline · 2026-09-27 22:00

[Source: email "Flight", 2026-09-27](https://example.test/new)

To: owner@example.test

Your seat is 2A.
## Baggage allowance
One bag.
> ## Airline · 2026-09-27 20:00
> Flight moved to 09:00.

On Sunday someone wrote:
Flight moved to 09:00.
""",
    }
    envelope = normalize_gbrain_event(page)
    assert envelope["source"] == "email"
    assert envelope["payload"]["from_address"] == "noreply@example.test"
    assert envelope["payload"]["text"] == "Your seat is 2A.\n## Baggage allowance\nOne bag."
    page["revision"] = "label-only-change"
    assert normalize_gbrain_event(page)["event_id"] == envelope["event_id"]


def test_native_and_supplemental_message_share_identity():
    native = {"slug": "emails/thread", "type": "email", "source_id": "google",
              "frontmatter": {"account": "owner@example.test", "message_id": message()["id"]},
              "compiled_truth": "Hello"}
    supplemental = {**native, "slug": f"events/gmail/{message()['id']}", "source_id": "default"}
    assert normalize_gbrain_event(native)["event_id"] == normalize_gbrain_event(supplemental)["event_id"]


def test_real_gbrain_citation_strips_notification_brackets_but_reply_survives():
    page = {"slug": "emails/notification", "type": "email", "source_id": "google",
            "title": "[Memento] Email connected",
            "frontmatter": {"account": "owner@example.test", "from": "Memento <owner@example.test>",
                            "to": ["owner@example.test"], "message_id": "abcdef1234567890"},
            "compiled_truth": '## → Memento <owner@example.test> · 2026-09-27 22:00\n\n'
                              '[Source: email "Memento Email connected", 2026-09-27](https://example.test)\n\n'
                              'To: owner@example.test\n\nConnected.'}
    normalized = normalize_gbrain_event(page)["payload"]
    assert normalized["subject"] == "[Memento] Email connected"
    assert normalized["memento_notification"] is True
    page["compiled_truth"] += '\n\n## Owner · 2026-09-27 23:00\n\n[Source: email "Re: Memento Email connected", 2026-09-27](https://example.test/reply)\n\nActually change my reminder.'
    normalized = normalize_gbrain_event(page)["payload"]
    assert normalized["subject"] == "Re: Memento Email connected"
    assert normalized["memento_notification"] is False


def test_calendar_exposes_dates_and_changes_observation_on_revision():
    page = {"slug": "calendar/meeting", "type": "meeting", "source_id": "google",
            "revision": "r1", "compiled_truth": "Meet at airport",
            "frontmatter": {"start": "2026-09-28T08:00:00-07:00", "end": "2026-09-28T09:00:00-07:00",
                            "event_id": "event-1", "all_day": False, "location": "SFO"}}
    envelope = normalize_gbrain_event(page)
    assert envelope["source"] == "calendar"
    assert envelope["payload"]["start"] == page["frontmatter"]["start"]
    assert envelope["payload"]["location"] == "SFO"
    page["revision"] = "r2"
    assert normalize_gbrain_event(page)["event_id"] != envelope["event_id"]


class FakeBrain:
    def __init__(self, home):
        self.home, self.source = home, "default"
        self.command = ["bun", "/fake/checkout/src/cli.ts"]
        self.pages = {}
        self.requests = []
        self.pending = False

    async def list_pages(self, **kwargs):
        return list(self.pages.values())

    async def put_page(self, slug, markdown, request_id):
        self.requests.append(request_id)
        if self.pending:
            return {"state": "pending"}
        _, frontmatter, body = markdown.split("---", 2)
        fm = yaml.safe_load(frontmatter)
        self.pages[slug] = {"slug": slug, "source_id": "default", "type": "email",
                            "title": fm["title"], "frontmatter": fm, "compiled_truth": body,
                            "revision": "r1"}
        return {"state": "committed", "revision": "r1"}

    async def get_page(self, slug):
        return self.pages[slug]


async def test_poll_writes_once_and_resumes_only_after_commit(tmp_path):
    brain = FakeBrain(tmp_path)
    intake = GoogleAutomatedIntake(brain, limit=15)

    async def fetch(state):
        return {"messages": [message()], "next_page_token": "next-batch"}

    intake._fetch = fetch
    result = await intake.poll()
    assert len(result) == 1
    assert result[0]["payload"]["message_id"] == message()["id"]
    assert result[0]["payload"]["text"] == message()["bodyText"]
    assert json.loads(intake.state_path.read_text())["page_token"] == "next-batch"
    assert await intake.poll() == []
    assert len(brain.requests) == 1
    # A user-deleted page is still a persistent deduplication receipt.
    next(iter(brain.pages.values()))["deleted_at"] = "2026-09-27T23:00:00Z"
    assert await intake.poll() == []
    assert len(brain.requests) == 1


async def test_pending_write_replays_same_intent_and_keeps_cursor(tmp_path):
    brain = FakeBrain(tmp_path)
    brain.pending = True
    intake = GoogleAutomatedIntake(brain)

    async def fetch(state):
        return {"messages": [message()], "next_page_token": "next-batch"}

    intake._fetch = fetch
    with pytest.raises(GBrainError, match="write_not_committed"):
        await intake.poll()
    assert not intake.state_path.exists()
    brain.pending = False
    assert len(await intake.poll()) == 1
    assert brain.requests[0] == brain.requests[1]


def test_intake_enforces_small_batch_and_fixed_24h_snapshot(tmp_path):
    brain = FakeBrain(tmp_path)
    with pytest.raises(ValueError):
        GoogleAutomatedIntake(brain, limit=26)
    intake = GoogleAutomatedIntake(brain, limit=25)
    state = intake._state()
    assert f"after:{state['started_at'] - 86400}" in state["query"]
    assert f"before:{state['started_at'] + 1}" in state["query"]
    state["page_token"] = "continue"
    intake.state_path.write_text(json.dumps(state))
    assert intake._state() == state
