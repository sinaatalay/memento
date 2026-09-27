"""Exercise the actual local HTTP/runtime/model/GBrain/notification pipeline.

Run against a running local Memento instance. With email configured this sends
two explicitly labeled demo emails: an alert and a clock reminder. Existing
demo watches are stopped; real memories are never modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8877")
    parser.add_argument("--report-file", type=Path)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    def get():
        with urlopen(base + "/api/state", timeout=10) as response:
            return json.load(response)

    def post(path, payload=None):
        request = Request(base + path, data=json.dumps(payload or {}).encode(), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=60) as response:
            return json.load(response)

    def wait_for(predicate, label, timeout=100):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = get()
            result = predicate(state)
            if result:
                return result, state
            time.sleep(1)
        raise RuntimeError(f"Timed out waiting for {label}; inspect the local trace")

    def event(source, text):
        result = post("/api/events", {"source": source, "text": text, "demo": True})
        event_id = result["event_id"]
        wait_for(lambda s: next((e for e in s["events"] if e["id"] == event_id and e["status"] == "done"), None), event_id)
        return event_id

    for memory in get()["memories"]:
        if "/demo/" in memory["id"] and memory["active"]:
            post(f"/api/memories/{memory['id']}/stop")
    post("/api/clock", {"reset": True})
    today = datetime.now(ZoneInfo("America/Los_Angeles"))
    tomorrow = (today + timedelta(days=1)).date()
    day = tomorrow.strftime("%A %Y-%m-%d")
    began = time.monotonic()
    booking = event("email", f"Booking confirmed: my flight UA123 SFO to JFK on {day}. Departure 08:05 America/Los_Angeles, arrival 16:30 America/New_York. Confirmation DEMO42.")
    memory, _ = wait_for(lambda s: next((m for m in s["memories"] if booking in m["sources"]), None), "written flight memory")
    memory_id = memory["id"]
    report = {"booking": {"seconds": round(time.monotonic() - began, 2), "reminders": len(memory["compiled"]["reminders"]), "conditions": len(memory["compiled"]["triggers"])}}
    print(json.dumps({"step": "booking", **report["booking"]}), flush=True)

    noise = event("email", "Weekend newsletter: favorite coffee shops, book recommendations, and new movie releases.")
    noise_state = get()
    assert not any(n["event_id"] == noise and n["status"] != "canceled" for n in noise_state["notifications"])
    assert not any(noise in m["sources"] for m in noise_state["memories"])
    report["unrelated_email"] = "no notification"
    print(json.dumps({"step": "unrelated_email", "result": "quiet"}), flush=True)

    chat = event("chat", f"Can we grab breakfast {tomorrow.strftime('%A')} at 7 before my trip?")
    surface, _ = wait_for(lambda s: next((n for n in s["notifications"] if n["event_id"] == chat and n["kind"] == "surface" and n["status"] != "canceled"), None), "chat memory surface")
    assert surface["status"] == "local", "Chat responses should not be emailed"
    assert not any(chat in m["sources"] for m in get()["memories"]), "A breakfast question is not a confirmed new commitment"
    report["chat"] = "memory surfaced locally"
    print(json.dumps({"step": "chat", "result": report["chat"]}), flush=True)

    change = event("email", f"United itinerary update for DEMO42: flight UA123 on {day} now departs SFO at 06:30 America/Los_Angeles instead of 08:05. Booking remains confirmed; arrival is still 16:30 America/New_York.")
    updated, state = wait_for(lambda s: next((m for m in s["memories"] if m["id"] == memory_id and change in m["sources"]), None), "flight rewrite")
    assert booking in updated["sources"]
    assert "06:30" in updated["body"] or "6:30" in updated["body"]
    assert len([m for m in state["memories"] if m["active"] and "/demo/" in m["id"]]) == 1, "A schedule update should replace its original memory"
    alert, _ = wait_for(lambda s: next((n for n in s["notifications"] if n["event_id"] == change and n["kind"] == "alert" and n["status"] != "canceled"), None), "schedule alert")
    report["schedule_change"] = {"rewritten": True, "source_history_preserved": True, "alert_status": alert["status"]}
    print(json.dumps({"step": "schedule_change", **report["schedule_change"]}), flush=True)

    reminders = updated["compiled"]["reminders"]
    assert reminders, "Updated future flight should retain a reminder"
    next_due = min(datetime.fromisoformat(r["at"]) for r in reminders)
    departure = datetime.fromisoformat(f"{tomorrow}T06:30:00").replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    assert next_due < departure, "Reminder must precede the updated departure"
    current = datetime.fromisoformat(get()["now"])
    post("/api/clock", {"hours": max(0, (next_due - current).total_seconds() / 3600) + 0.01})
    reminder, _ = wait_for(lambda s: next((n for n in s["notifications"] if n["memory_id"] == memory_id and n["kind"] == "reminder" and n["status"] != "canceled"), None), "clock reminder")
    # Clock retries must not create a second delivery identity.
    post("/api/clock", {"hours": 0})
    assert len([n for n in get()["notifications"] if n["id"] == reminder["id"]]) == 1
    if (get().get("email") or {}).get("ready"):
        for notification_id in (alert["id"], reminder["id"]):
            wait_for(lambda s: any(n["id"] == notification_id and n["status"] == "delivered" for n in s["notifications"]), "email receipt")
        report["email"] = "alert and reminder accepted by Gmail"
    report["clock"] = "reminder fired once"
    post("/api/clock", {"reset": True})
    report["memory_id"] = memory_id
    report["notification_ids"] = [alert["id"], reminder["id"]]
    if args.report_file:
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"step": "complete", "report": report}, indent=2), flush=True)


if __name__ == "__main__":
    main()
