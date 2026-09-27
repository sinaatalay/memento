"""Verify an ordinary assistant-authored GBrain page becomes an active memory.

Run against the restarted local Memento server. This pauses existing demo
watches and sends ONE clearly labeled DEMO email through the configured runtime.
It never reads SQLite or writes through Memento's memory-registration endpoint.
Use --dry-run to validate the fixture without contacting services or sending mail.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import shutil
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import yaml

from memento.collector import collect
from memento.dsl import Question
from memento.gbrain_mcp import GBrainMCP
from memento.memory import LiteralDumper, parse_memory, split_markdown
from memento.providers import JevEvaluator, annotate_calendar
from memento.settings import Settings


ROADMAP = (
    "DEMO internal product roadmap discussion: our SSO feature is still under "
    "development and unavailable to customers. The team hopes to release it "
    "next quarter, but this is only an estimate, not a confirmed release. "
    "There is no request or new commitment for the recipient."
)
SHIPPED = (
    "DEMO internal release confirmation: our SSO feature has now shipped to "
    "production and is available to all customers today, including the Acme "
    "pilot. Customers can enable SSO now. This is a completed release, not "
    "a roadmap estimate. Nobody has contacted Maya yet; the recipient still "
    "needs to follow up with her."
)


def load_fixture(path: Path) -> tuple[str, str, dict[str, Any], str]:
    full = path.read_text()
    metadata, body = split_markdown(full)
    fields = parse_memory(full)
    recipe = collect(fields["recipe"])
    if not recipe.triggers or recipe.reminders:
        raise ValueError("Fixture must have an event recipe and no timed reminders")
    alerts = [action for trigger in recipe.triggers for action in trigger.actions if action.kind == "alert"]
    if len(alerts) != 1:
        raise ValueError("Fixture must have exactly one alert action")
    if not all(key in metadata for key in ("type", "tags", "custom")):
        raise ValueError("Fixture needs type, tags, and custom metadata to verify preservation")
    if not all(term in body.lower() for term in ("maya", "acme", "pricing")):
        raise ValueError("Fixture needs the Maya/Acme promise and original pricing context")
    passive_metadata = {key: value for key, value in metadata.items() if key != "recipe"}
    passive = "---\n" + yaml.dump(
        passive_metadata, Dumper=LiteralDumper, sort_keys=False, allow_unicode=True,
    ) + "---\n" + body + "\n"
    return full, passive, passive_metadata, body


def assert_metadata(markdown: str, expected: dict[str, Any]) -> None:
    actual, _ = split_markdown(markdown)
    for key, value in expected.items():
        if key not in {"title", "sources"}:
            assert actual.get(key) == value, f"Original metadata changed: {key}"


class Demo:
    def __init__(self, client: httpx.AsyncClient, timeout: float):
        self.client, self.timeout = client, timeout

    async def state(self) -> dict:
        response = await self.client.get("/api/state")
        response.raise_for_status()
        return response.json()

    async def post(self, path: str, payload: dict | None = None) -> dict:
        response = await self.client.post(path, json=payload or {})
        response.raise_for_status()
        return response.json()

    async def wait(self, predicate, label: str):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            state = await self.state()
            value = predicate(state)
            if value:
                return value, state
            await asyncio.sleep(0.5)
        raise RuntimeError(f"Timed out waiting for {label}; inspect the local trace")

    async def event(self, event_id: str, text: str) -> dict:
        await self.post("/api/events", {
            "source": "email", "demo": True, "event_id": event_id,
            "title": "DEMO: Acme SSO page verification", "text": text,
        })

        def done(state):
            event = next((event for event in state["events"] if event["id"] == event_id), None)
            if event and event["status"] == "failed":
                raise RuntimeError("Demo event failed; inspect its local trace")
            return event if event and event["status"] == "done" else None

        _, state = await self.wait(done, "synthetic event completion")
        return state


async def audit_rewrite(settings: Settings, memory: dict) -> dict:
    """Evaluate retained meaning and replay conditions without causing actions."""
    recipe = collect(memory["recipe"])
    questions = {
        "promise_pending": Question(
            kind="noul", question="Reading current_memory.body ONLY, does it retain an outstanding follow-up with Maya at Acme? Ignore facts stated only in event.",
            options={
                "true": "The memory says the user still needs to follow up with Maya; notifying the user did not complete it.",
                "false": "The promise is absent, canceled, or described as already completed or already sent to Maya.",
            },
        ),
        "pricing_preserved": Question(
            kind="noul", question="Reading current_memory.body ONLY, does it still retain the original pricing-discussion context? Ignore event.",
            options={
                "true": "The memory retains a mention of the original pricing discussion.",
                "false": "The original pricing discussion is omitted or contradicted.",
            },
        ),
    }
    conditions = {}
    for index, trigger in enumerate(recipe.triggers):
        if trigger.source == "email":
            conditions[f"remaining_{index}"] = trigger
            questions[f"remaining_{index}"] = trigger.question
    state = annotate_calendar({
        "today": datetime.now(ZoneInfo(settings.timezone)).isoformat(),
        "timezone": settings.timezone,
        "event": {"source": "email", "text": SHIPPED, "demo": True},
        "current_memory": {"body": memory["body"], "title": memory["title"]},
    })
    evaluator = JevEvaluator(api_key=settings.jev_api_key)
    result = await evaluator.decide(state, questions)
    for key in ("promise_pending", "pricing_preserved"):
        assert result.answers[key].probability >= 0.8, f"Rewritten memory failed semantic audit: {key}"
    for key, trigger in conditions.items():
        answer = result.answers[key]
        matches = answer.probability >= trigger.threshold
        if trigger.question.kind == "choice":
            matches = matches and answer.value == trigger.match
        assert not matches, "Rewritten recipe still fires on the already-satisfied shipment condition"
    assert not recipe.reminders, "Rewrite invented a timed reminder without a requested deadline"
    return {
        "elapsed_ms": round(result.elapsed_ms, 2),
        "probabilities": {key: answer.probability for key, answer in result.answers.items()},
        "pending_promise_preserved": True, "pricing_preserved": True,
        "shipment_watch_retired": True,
    }


async def run(args, report: dict) -> None:
    full, passive, metadata, body = load_fixture(args.page_file)
    expected_recipe = collect(parse_memory(full)["recipe"]).model_dump(mode="json")
    settings = Settings.from_env(args.env_file)
    assert settings.gbrain_mcp_url, "Configure the shared GBrain MCP URL first"
    assert settings.jev_api_key, "Jev is required for event matching and final read-only audit"
    bun = shutil.which("bun")
    assert bun, "Bun is required for the configured GBrain CLI helpers"
    brain = GBrainMCP(
        [bun, str(settings.gbrain_checkout / "src/cli.ts")], settings.gbrain_home,
        endpoint=settings.gbrain_mcp_url, token=settings.gbrain_mcp_token,
        credentials_file=settings.gbrain_mcp_credentials,
    )
    memory_id = f"projects/demo/{uuid4().hex}"
    report["memory_id"] = memory_id
    run_id = uuid4().hex

    def find(state):
        return next((memory for memory in state["memories"] if memory["id"] == memory_id), None)

    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout, trust_env=False) as client:
        demo = Demo(client, args.timeout)
        initial = await demo.state()
        assert (initial.get("email") or {}).get("ready"), "Configured Gmail notifier must be ready before this one-email verification"
        initial_demo_ids = {memory["id"] for memory in initial["memories"] if "/demo/" in memory["id"]}

        def assert_no_duplicate(state):
            current_demo_ids = {memory["id"] for memory in state["memories"] if "/demo/" in memory["id"]}
            assert current_demo_ids - initial_demo_ids == {memory_id}, "Verification created a second demo memory instead of extending its owner page"

        paused = []
        for memory in initial["memories"]:
            if memory["active"] and "/demo/" in memory["id"]:
                await demo.post(f"/api/memories/{memory['id']}/stop")
                paused.append(memory["id"])
        report["paused_demo_watches"] = paused

        await brain.put_page(memory_id, passive, request_id=str(uuid4()))
        await demo.post("/api/sync")
        saved, state = await demo.wait(find, "ordinary passive page registration")
        assert saved["body"] == body
        assert saved["recipe"] == ""
        assert saved["compiled"]["triggers"] == saved["compiled"]["reminders"] == []
        assert_metadata(saved["markdown"], metadata)
        assert_no_duplicate(state)
        report["passive_page"] = {"registered_same_id": True, "body_preserved": True, "no_recipe_required": True}
        print(json.dumps({"step": "passive_page", **report["passive_page"]}), flush=True)

        page = await brain.get_page(memory_id)
        await brain.put_page(memory_id, full, expected_revision=page["revision"], request_id=str(uuid4()))
        await demo.post("/api/sync")
        authored, state = await demo.wait(
            lambda state: (memory if (memory := find(state)) and memory["compiled"] == expected_recipe else None),
            "assistant-authored recipe registration",
        )
        assert authored["body"] == body
        assert_metadata(authored["markdown"], metadata)
        assert_no_duplicate(state)
        report["authored_recipe"] = {"same_id": True, "conditions": len(authored["compiled"]["triggers"])}
        print(json.dumps({"step": "authored_recipe", **report["authored_recipe"]}), flush=True)

        roadmap_id = f"demo:page:{run_id}:roadmap"
        roadmap_state = await demo.event(roadmap_id, ROADMAP)
        assert not any(n["event_id"] == roadmap_id and n["status"] != "canceled" for n in roadmap_state["notifications"]), "A roadmap is not a release"
        assert find(roadmap_state)["revision"] == authored["revision"], "Roadmap changed the owner memory"
        assert_no_duplicate(roadmap_state)
        report["roadmap"] = {"alerted": False, "owner_unchanged": True}
        print(json.dumps({"step": "roadmap", **report["roadmap"]}), flush=True)

        shipped_id = f"demo:page:{run_id}:shipped"
        began = time.monotonic()
        shipped_state = await demo.event(shipped_id, SHIPPED)
        rewritten = find(shipped_state)
        assert rewritten and shipped_id in rewritten["sources"], "Release must rewrite its original owner page"
        assert rewritten["revision"] != authored["revision"]
        assert [memory["id"] for memory in shipped_state["memories"] if shipped_id in memory["sources"]] == [memory_id], "Release created a duplicate memory"
        assert_no_duplicate(shipped_state)
        assert_metadata(rewritten["markdown"], metadata)
        actual = await brain.get_page(memory_id)
        assert_metadata(actual["content"], metadata)
        assert parse_memory(actual["content"])["body"] == rewritten["body"]
        report["rewrite"] = {
            "seconds": round(time.monotonic() - began, 2), "same_id": True,
            "metadata_preserved": True, "canonical_body_matches": True,
            "no_duplicate_memory": True,
        }
        report["semantic_audit"] = await audit_rewrite(settings, rewritten)

        def delivered(state):
            notifications = [n for n in state["notifications"] if n["event_id"] == shipped_id and n["status"] != "canceled"]
            assert len(notifications) == 1, "Release must cause exactly one notification"
            notification = notifications[0]
            assert notification["kind"] == "alert"
            return notification if notification["status"] == "delivered" else None

        notification, state = await demo.wait(delivered, "one Gmail acceptance receipt")
        assert "DEMO" in (notification.get("delivery_subject") or "")
        assert notification.get("delivery_id"), "Gmail acceptance must have a message ID"
        report["email"] = {"count": 1, "delivered": True, "demo_labeled": True, "notification_id": notification["id"]}
        report["event_ids"] = {"roadmap": roadmap_id, "shipped": shipped_id}
        report["success"] = True
        print(json.dumps({"step": "complete", "report": report}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-file", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8877")
    parser.add_argument("--report-file", type=Path)
    parser.add_argument("--env-file")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.base_url.isdigit():
        args.base_url = f"http://127.0.0.1:{args.base_url}"
    if urlsplit(args.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("base-url must address the local Memento server")
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    if args.dry_run:
        full, _, metadata, _ = load_fixture(args.page_file)
        print(json.dumps({
            "dry_run": True, "fixture_valid": True,
            "metadata_keys": sorted(metadata),
            "conditions": len(collect(parse_memory(full)["recipe"]).triggers),
            "live_run_sends_demo_emails": 1,
        }))
        return
    report: dict[str, Any] = {"success": False}
    try:
        asyncio.run(run(args, report))
    finally:
        if args.report_file:
            args.report_file.parent.mkdir(parents=True, exist_ok=True)
            args.report_file.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
