#!/usr/bin/env python3
"""Mock memento backend for the console UI. Stdlib only.

Serves claude/src/memento/static/index.html at / and fakes every endpoint in
claude/API.md with events shaped like the real engine (engine.py): every event
carries `at`, Jev results carry `title`, fired events come before the gate, and
a fired rewrite makes the gate skip. A scripted loop plays the demo story:

  UA 123 schedule change -> Jev (12 questions, one fires at 0.96) -> alert ->
  rewrite -> writer -> memory updated; "SOC 2 planned" does NOT fire but
  "SOC 2 issued" does; a calendar invite becomes a new memory; clock jumps
  fire time reminders; a newsletter is skipped by the gate; then it resets.

    uv run --no-project python claude/scripts/mock_ui_server.py              # :8766
    uv run --no-project python claude/scripts/mock_ui_server.py --no-script  # no auto story
"""

from __future__ import annotations

import argparse
import itertools
import json
import queue
import random
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "src" / "memento" / "static" / "index.html"
TZ = datetime.now().astimezone().tzinfo
UNITS = {"minutes": timedelta(minutes=1), "hours": timedelta(hours=1), "days": timedelta(days=1)}

OFFSET = timedelta()
PAUSE = True  # False while seeding the initial feed, so seeding is instant
FEED: deque[dict] = deque(maxlen=200)
SUBS: set[queue.Queue] = set()
LOCK = threading.Lock()
_ids = itertools.count(1)


def now() -> datetime:
    return datetime.now(TZ) + OFFSET


def at(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def iso(value: datetime) -> str:
    return value.isoformat()


def pause(seconds: float) -> None:
    if PAUSE:
        time.sleep(seconds)


def publish(event: dict) -> None:
    event = {k: v for k, v in event.items() if v is not None}
    event.setdefault("at", iso(now()))
    with LOCK:
        FEED.append(event)
        subscribers = list(SUBS)
    for q in subscribers:
        q.put(event)


# ---------------------------------------------------------------- memories


@dataclass
class Trig:
    source: str
    question: str
    actions: list[str]
    threshold: float = 0.8
    match: str = ""  # mock Jev says yes when this regex matches the item
    near: str = ""  # ...and "sort of" (a visible near miss) when this one does
    hit: float = 0.96


@dataclass
class Mem:
    slug: str
    title: str
    body: str
    recipe: str
    origin: str = "memento"
    times: list[tuple[datetime, str]] = field(default_factory=list)
    events: list[Trig] = field(default_factory=list)
    expires: datetime | None = None
    sources: list[str] = field(default_factory=list)
    fired: set[int] = field(default_factory=set)
    updated_at: datetime = field(default_factory=now)
    error: str | None = None
    last_status: str = "active"

    def tid(self, n: int) -> str:
        return f"{self.slug}#{n}"

    def status(self) -> str:
        if self.error:
            return "error"
        return "expired" if self.expires and now() >= self.expires else "active"

    def view(self) -> dict:
        triggers = [
            {"id": self.tid(i), "kind": "time", "fire_at": iso(t), "fired": i in self.fired,
             "actions": [f"alert: {text}"]}
            for i, (t, text) in enumerate(self.times)
        ]  # fmt: skip
        base = len(self.times)
        triggers += [
            {"id": self.tid(base + j), "kind": "event", "source": e.source, "question": e.question,
             "threshold": e.threshold, "actions": e.actions}
            for j, e in enumerate(self.events)
        ]  # fmt: skip
        return {
            "slug": self.slug, "path": self.slug, "origin": self.origin, "title": self.title,
            "body": self.body, "recipe": self.recipe, "sources": self.sources,
            "expires": iso(self.expires) if self.expires else None, "status": self.status(),
            "error": self.error, "triggers": triggers, "updated_at": iso(self.updated_at),
        }  # fmt: skip


def recipe_src(name: str, anchor: str | None, reminders, events: list[Trig], expires) -> str:
    """Render a plausible memento recipe for generated memories."""
    used: list[str] = []

    def use(*names: str) -> None:
        used.extend(n for n in names if n not in used)

    body: list[str] = []
    if anchor:
        use("at")
        body += [f'{name} = at("{anchor}")', ""]
    for amount, unit, text in reminders:
        use(unit, "remind")
        body.append(f'remind({name} - {unit}({amount}), "{text}")')
    if reminders:
        body.append("")
    for e in events:
        use("on", e.source, "noul")
        acts = []
        for a in e.actions:
            verb, _, arg = a.partition(":")
            use(verb)
            acts.append(f'alert("{arg.strip()}")' if verb == "alert" else f"{verb}(this)")
            if verb != "alert":
                use("this")
        do = acts[0] if len(acts) == 1 else "[" + ", ".join(acts) + "]"
        body += ["on(", f"    {e.source},", f'    when=noul("{e.question}"),', f"    do={do},"]
        if e.threshold != 0.8:
            body.append(f"    threshold={e.threshold},")
        body.append(")")
    if expires:
        use(expires[1], "expires")
        body.append(f"expires({name} + {expires[1]}({expires[0]}))")
    order = ["at", "minutes", "hours", "days", "remind", "on", "email", "calendar", "chat",
             "noul", "alert", "surface", "rewrite", "expires", "this"]  # fmt: skip
    lines, cur = [], "from memento import "
    for n in [n for n in order if n in used]:
        piece = n if cur.endswith("import ") else f", {n}"
        if len(cur + piece) > 78:
            lines.append(cur)
            cur = f"from memento import {n}"
        else:
            cur += piece
    return "\n".join([*lines, cur, "", *body]) + "\n"


def gen(slug, title, body, *, name="when", anchor=None, reminders=(), events=(), expires=None,
        origin="memento", sources=None, age_min=30) -> Mem:  # fmt: skip
    events = list(events)
    base = at(anchor) if anchor else None
    times = [(base - amount * UNITS[unit], text) for amount, unit, text in reminders] if base else []
    return Mem(
        slug=slug, title=title, body=body, origin=origin,
        recipe=recipe_src(name, anchor, reminders, events, expires),
        times=times, events=events,
        expires=base + expires[0] * UNITS[expires[1]] if base and expires else None,
        sources=sources if sources is not None else [f"emails/2026/09/{slug.split('/')[-1]}"],
        updated_at=now() - timedelta(minutes=age_min),
    )  # fmt: skip


FLIGHT = "memories/flight-ua-123"
ACME = "projects/acme-soc2"
DINNER = "memories/ana-birthday-dinner-nopa"
FLIGHT_RECIPE = '''from memento import at, hours, remind, on, email, chat, noul
from memento import alert, surface, rewrite, expires, this

departure = at("2026-09-28 {hhmm}")

remind(departure - hours(24), "Flight UA 123 to JFK tomorrow {hm}. Check in.")
remind(departure - hours(3), "Leave for SFO by {leave}.")

on(
    email,
    when=noul("Does `email` delay, change or cancel flight UA 123 on Sep 28?"),
    do=[alert("Your flight UA 123 changed"), rewrite(this)],
)
on(
    chat,
    when=noul("Is the user making plans for Monday Sep 28 before noon?"),
    do=surface(this),
)
expires(departure + hours(6))
'''
ACME_RECIPE = '''from memento import on, email, noul, alert, rewrite, this

issued = noul(
    "Does `email` say our SOC 2 report has been issued?",
    true="The SOC 2 report is final and issued to us.",
    false="A plan, an audit date, an estimate, or anything unfinished.",
)
on(
    email,
    when=issued,
    do=[alert("SOC 2 issued. Follow up with Maya at Acme."), rewrite(this)],
    threshold=0.85,
)
'''


def flight_memory(delayed: bool = False) -> Mem:
    hhmm, hm, leave = ("10:40", "10:40", "7:40") if delayed else ("08:05", "8:05", "5:30")
    departure = at(f"2026-09-28 {hhmm}")
    body = (
        "UA 123 SFO → JFK on Monday Sep 28, **now departs 10:40am** (was 8:05am, per United's "
        "schedule change). Seat 14C. Confirmation K7Q2LM."
        if delayed
        else "UA 123 SFO → JFK on Monday Sep 28, departs 8:05am. Seat 14C. Confirmation K7Q2LM."
    )
    sources = ["emails/2026/09/your-trip-confirmation-sfo-to-jfk-mon-sep-28"]
    if delayed:
        sources.append("emails/2026/09/schedule-change-ua-123-on-mon-sep-28")
    return Mem(
        slug=FLIGHT,
        title=f"Flight UA 123 SFO → JFK, Mon Sep 28, {hm}{'am (delayed)' if delayed else 'am'}",
        body=body,
        recipe=FLIGHT_RECIPE.format(hhmm=hhmm, hm=hm, leave=leave),
        times=[
            (departure - timedelta(hours=24), f"Flight UA 123 to JFK tomorrow {hm}. Check in."),
            (departure - timedelta(hours=3), f"Leave for SFO by {leave}."),
        ],
        events=[
            Trig("email", "Does `email` delay, change or cancel flight UA 123 on Sep 28?",
                 ["alert: Your flight UA 123 changed", "rewrite"],
                 match=r"(?=.*\bua ?123\b)(?=.*\b(delay\w*|chang\w*|cancel\w*|now departs)\b)",
                 near=r"\bua ?123\b|\bunited\b"),
            Trig("chat", "Is the user making plans for Monday Sep 28 before noon?", ["surface"],
                 match=r"\b(monday|mon|sep(tember)? 28|breakfast|tomorrow morning)\b"),
        ],  # fmt: skip
        expires=departure + timedelta(hours=6),
        sources=sources,
    )


def acme_memory(issued: bool = False) -> Mem:
    body = (
        "✅ Our **SOC 2 Type II report was issued**. Maya at Acme was waiting on it to sign the "
        "pilot.\n\n- Follow up with Maya today\n- Owner: Priya"
        if issued
        else "Maya at Acme will sign the pilot once our **SOC 2 Type II report is issued**. A planned "
        "audit or a date estimate does not count.\n\n- Owner: Priya\n"
        "- I promised to follow up with Maya the day it is issued"
    )
    return Mem(
        slug=ACME, origin="you", recipe=ACME_RECIPE, body=body,
        title="Acme: SOC 2 issued, follow up with Maya" if issued else "Acme is waiting on our SOC 2 report",
        events=[
            Trig("email", "Does `email` say our SOC 2 report has been issued?",
                 ["alert: SOC 2 issued. Follow up with Maya at Acme.", "rewrite"], threshold=0.85,
                 match=r"(?=.*\bsoc ?2\b)(?=.*\bissued\b)(?!.*\b(planned|should be|will be|expected)\b)",
                 near=r"\bsoc ?2\b", hit=0.94),
        ],
        sources=[], updated_at=now() - timedelta(hours=3),
    )  # fmt: skip


def dinner_memory(moved: bool = False) -> Mem:
    day, date = ("Fri", "2026-10-02 19:30") if moved else ("Sat", "2026-10-03 19:30")
    return gen(
        DINNER,
        f"Ana's birthday dinner, {day} Oct {date[9]}, 7:30pm at Nopa" + (" (moved, table for 4)" if moved else ""),
        (f"Dinner with Ana at **Nopa**, {day} Oct {date[9]} at 7:30pm."
         + ("\n\n- Moved from Sat Oct 3 per Ana's email\n- Table for 4; Ana books by Wednesday" if moved else
            "\n\n- Bring the ceramics class gift card")),
        name="dinner", anchor=date,
        reminders=[(3, "hours", "Ana's birthday dinner at Nopa tonight 7:30pm")],
        events=[Trig("email", "Does `email` change or cancel the dinner with Ana at Nopa?",
                     ["alert: Your dinner with Ana changed", "rewrite"], threshold=0.7,
                     match=r"\bnopa\b", hit=0.91)],
        expires=(4, "hours"), age_min=95,
    )  # fmt: skip


def base_world() -> dict[str, Mem]:
    mems = [
        acme_memory(),
        dinner_memory(),
        gen("memories/yc-w27-interview", "YC W27 interview, Fri Oct 2, 10:00am PT",
            "- Zoom interview for the Y Combinator W27 batch\n- Join 5 minutes early",
            name="interview", anchor="2026-10-02 10:00",
            reminders=[(1, "days", "YC interview tomorrow 10am PT. Prep and test Zoom."),
                       (1, "hours", "YC interview in 1 hour. Join Zoom 5 min early.")],
            events=[Trig("email", "Does `email` move or cancel the Oct 2 YC interview?",
                         ["alert: Your YC interview changed", "rewrite"], threshold=0.7,
                         match=r"(?=.*\b(yc|y combinator)\b)(?=.*\b(mov\w*|resched\w*|cancel\w*)\b)",
                         near=r"\binterview\b")],
            expires=(2, "hours"), age_min=50),
        gen("memories/dentist-dr-patel", "Dentist with Dr. Patel, Thu Oct 1, 3:30pm",
            "Cleaning with **Dr. Patel** (Valencia St). Thursday Oct 1 at 3:30pm.",
            name="appt", anchor="2026-10-01 15:30",
            reminders=[(2, "hours", "Dentist with Dr. Patel at 3:30pm")],
            events=[Trig("email", "Does `email` move or cancel the Oct 1 dentist visit?",
                         ["alert: Your dentist appointment changed", "rewrite"], threshold=0.7,
                         match=r"(?=.*\b(dentist|dr\.? patel)\b)(?=.*\b(mov\w*|resched\w*|cancel\w*)\b)",
                         hit=0.93)],
            expires=(2, "hours"), age_min=70),
        gen("memories/october-rent", "October rent $2,450 due Thu Oct 1, autopay off",
            "Rent is **$2,450**, due Thursday Oct 1. Autopay is off this month; pay by Zelle.",
            name="due", anchor="2026-10-01 09:00",
            reminders=[(1, "days", "Rent $2,450 due tomorrow. Autopay is off.")],
            events=[Trig("email", "Does `email` change the October rent amount or due date?",
                         ["alert: Your rent changed", "rewrite"], threshold=0.7, match=r"\b(rent|landlord)\b")],
            expires=(1, "days"), age_min=80),
        gen("memories/hotel-standard-high-line", "Hotel: The Standard High Line, Sep 28–30",
            "- 2 nights, check-in Mon Sep 28 after 3pm\n- Confirmation 88213",
            name="checkin", anchor="2026-09-28 15:00",
            events=[Trig("email", "Does `email` change or cancel the Sep 28-30 hotel reservation?",
                         ["alert: Your hotel booking changed", "rewrite"], threshold=0.7,
                         match=r"(?=.*\b(standard|hotel)\b)(?=.*\b(chang\w*|cancel\w*)\b)")],
            expires=(2, "days"), age_min=85),
        gen("memories/macbook-delivery", "MacBook Pro delivery Tue Sep 29, 2–6 PM",
            "UPS delivery window **2–6 PM**, signature required.",
            name="delivery", anchor="2026-09-29 14:00",
            reminders=[(1, "hours", "MacBook delivery window starts at 2pm. Signature required.")],
            events=[Trig("email", "Does `email` change the Sep 29 MacBook Pro delivery?",
                         ["alert: Your MacBook delivery changed", "rewrite"], threshold=0.7,
                         match=r"(?=.*\b(macbook|ups)\b)(?=.*\b(delay\w*|chang\w*|resched\w*)\b)")],
            expires=(8, "hours"), age_min=100),
        gen("memories/investor-deck-priya", "Investor deck for Priya Raman, due Wed Sep 30 EOD",
            "Send the updated deck (with Q3 numbers) to **Priya Raman** by Wednesday EOD.",
            name="due", anchor="2026-09-30 18:00",
            reminders=[(1, "days", "Investor deck for Priya due tomorrow EOD")],
            events=[Trig("email", "Does `email` change the Sep 30 investor deck deadline or content?",
                         ["alert: The investor deck ask changed", "rewrite"], threshold=0.7,
                         match=r"(?=.*\bdeck\b)(?=.*\b(deadline|friday|thursday|push\w*)\b)",
                         near=r"\bpriya\b")],
            expires=(1, "days"), age_min=110),
        gen("memories/dmv-registration", "CA DMV registration renewal, due Oct 15",
            "- Plate 8ABC123, registration expires Oct 15\n- Smog check required this year",
            name="due", anchor="2026-10-15 00:00",
            reminders=[(7, "days", "Renew the Honda registration by Oct 15; smog check needed"),
                       (1, "days", "Registration due tomorrow. Renew at dmv.ca.gov")],
            events=[Trig("email", "Does `email` change the Oct 15 registration renewal or smog check?",
                         ["alert: DMV registration details changed", "rewrite"], threshold=0.7,
                         match=r"\b(dmv|smog)\b")],
            expires=(1, "days"), age_min=120),
        gen("projects/founding-designer", "Priya is hiring a founding designer",
            "Priya is hiring an experienced founding product designer in SF. If someone suitable "
            "becomes available, suggest an intro and let me decide.",
            events=[Trig("email", "Is `email` from an experienced designer seeking a founding role?",
                         ["alert: A possible designer intro for Priya came up"], threshold=0.9,
                         match=r"(?=.*\bdesigner\b)(?=.*\b(founding|looking|available)\b)",
                         near=r"\bdesign\w*\b"),
                    Trig("chat", "Is the user talking about hiring or finding a designer?", ["surface"],
                         threshold=0.85, match=r"\bdesigner\b")],
            origin="you", sources=[], age_min=200),
    ]  # fmt: skip
    return {m.slug: m for m in mems}


MEMS: dict[str, Mem] = {}


def refresh_missed(m: Mem) -> None:
    """Time triggers already in the past count as fired (the engine calls them missed)."""
    t = now()
    m.fired = {i for i, (fire_at, _) in enumerate(m.times) if fire_at <= t}


# ---------------------------------------------------------------- the engine, faked

GATE_KINDS = [
    (r"newsletter|unsubscribe|this week in|digest|the download", (0.02, 0.06)),
    (r"flight|united|ua ?\d+|hotel|trip|dentist|appointment|dinner|nopa|soc ?2|acme|coffee|"
     r"meeting|interview|deck|rent|delivery|invite", (0.9, 0.98)),
]  # fmt: skip
DEMO = {
    "flight": ("United Airlines", "Your trip confirmation: SFO to JFK, Mon Sep 28",
               "Thanks for choosing United. Confirmation number: K7Q2LM\n"
               "Flight UA 123, San Francisco (SFO) to New York (JFK)\n"
               "Monday, September 28, 2026. Departs 8:05 AM, arrives 4:41 PM. Seat 14C."),
    "change": ("United Airlines", "Schedule change: UA 123 on Mon, Sep 28",
               "Your flight UA 123 from San Francisco (SFO) to New York (JFK) on Monday, September 28 "
               "now departs at 10:40 AM instead of 8:05 AM and arrives at 7:16 PM. Seat 14C is unchanged."),
    "soc2_planned": ("Priya Raman", "SOC 2: audit fieldwork planned for October",
                     "Heads up: SOC 2 Type II fieldwork is planned for October. The report should be "
                     "issued in November if all goes well."),
    "soc2_issued": ("Priya Raman", "Our SOC 2 Type II report has been issued",
                    "Great news: the auditors issued our SOC 2 Type II report today. PDF attached."),
    "nopa": ("Ana Lopez", "Dinner Friday at Nopa",
             "Confirming dinner with Ana at Nopa on Friday Oct 2 at 7:30pm, table for 4. "
             "She said she'd book it by Wednesday."),
}  # fmt: skip


def jev_p(t: Trig, blob: str) -> float:
    if t.match and re.search(t.match, blob, re.S):
        return t.hit
    if t.near and re.search(t.near, blob, re.S):
        return round(random.uniform(0.05, 0.22), 2)
    return random.choice([0.01, 0.02, 0.02, 0.02, 0.03])


def gate_p(blob: str) -> float:
    for pattern, (lo, hi) in GATE_KINDS:
        if re.search(pattern, blob):
            return round(random.uniform(lo, hi), 2)
    return round(random.uniform(0.1, 0.3), 2)


def handle(source: str, title: str, text: str) -> list[tuple[Mem, Trig, float]]:
    """One Jev call: the item against every active trigger on its source, plus the write gate."""
    event_id = f"{next(_ids):08x}"
    publish({"type": "event_in", "id": event_id, "source": source, "title": title})
    blob = f"{title}\n{text}".lower()
    pairs = [(m, len(m.times) + j, t) for m in list(MEMS.values()) if m.status() == "active"
             for j, t in enumerate(m.events) if t.source == source]  # fmt: skip
    ranked = sorted(((jev_p(t, blob), m, n, t) for m, n, t in pairs), key=lambda r: -r[0])
    p_write = gate_p(blob)
    n_questions = len(pairs) + 1
    pause(random.uniform(0.1, 0.2))
    publish({
        "type": "jev", "event_id": event_id, "source": source, "ms": random.randint(96, 188),
        "n_questions": n_questions, "input_tokens": 410 + 31 * n_questions + random.randint(0, 40),
        "results": [
            {"trigger_id": m.tid(n), "memory": m.slug, "title": m.title, "question": t.question,
             "p": p, "threshold": t.threshold, "fired": p >= t.threshold}
            for p, m, n, t in ranked[:8]
        ],
    })  # fmt: skip
    fired = [(m, t, p) for p, m, n, t in ranked if p >= t.threshold]
    rewrites = []
    for p, m, n, t in ranked:
        if p < t.threshold:
            continue
        for action in t.actions:
            verb, _, arg = action.partition(":")
            event = {"type": "fired", "trigger_id": m.tid(n), "memory": m.slug, "title": m.title,
                     "action": verb, "p": p}  # fmt: skip
            if verb == "alert":
                event["text"] = arg.strip()
            if verb == "rewrite":
                rewrites.append(m)
            pause(0.08)
            publish(event)
    decision = "write" if p_write >= 0.5 and not rewrites and source != "chat" else "skip"
    publish({"type": "gate", "event_id": event_id, "kind": "worth remembering", "p": p_write,
             "decision": decision})  # fmt: skip
    for m in rewrites:
        run(rewrite, m, blob)
    if decision == "write":
        run(write_new, source, title, text)
    return fired


def run(fn, *args) -> None:
    """Like asyncio.create_task in the engine: background when live, inline while seeding."""
    if PAUSE:
        threading.Thread(target=fn, args=args, daemon=True).start()
    else:
        fn(*args)


def rewritten(m: Mem, blob: str) -> Mem:
    if m.slug == FLIGHT:
        return flight_memory(delayed=bool(re.search(r"delay|10:40|now departs|chang", blob)))
    if m.slug == ACME:
        return acme_memory(issued=True)
    if m.slug == DINNER:
        return dinner_memory(moved=True)
    line = blob.strip().splitlines()[0][:90]
    return replace(m, body=f"{m.body}\n\n- **Update:** {line}", fired=set(m.fired))


def rewrite(m: Mem, blob: str) -> None:
    started = time.perf_counter()
    publish({"type": "writer", "status": "start", "memory": m.slug, "title": m.title})
    pause(random.uniform(1.8, 3.0))
    new = rewritten(m, blob)
    new.updated_at = now()
    refresh_missed(new)
    MEMS[new.slug] = new
    publish({"type": "writer", "status": "done", "memory": new.slug, "title": new.title,
             "ms": int((time.perf_counter() - started) * 1000) or 2380, "attempts": 1})  # fmt: skip
    publish({"type": "memory", "memory": new.view(), "change": "updated"})


def new_memory(title: str, text: str) -> Mem | None:
    t = f"{title}\n{text}".lower()
    if "ua 123" in t or "trip confirmation" in t:
        return flight_memory()
    if "nopa" in t:
        return dinner_memory(moved=True)
    if "coffee with lena" in t:
        return gen("memories/coffee-lena", "Coffee with Lena (designer), Tue Sep 29, 9:00am",
                   "Coffee with **Lena Park** at Sightglass. She is leaving Figma and looking for a founding "
                   "design role, which may matter for Priya.",
                   name="coffee", anchor="2026-09-29 09:00",
                   reminders=[(30, "minutes", "Coffee with Lena at Sightglass in 30 min")],
                   events=[Trig("email", "Does `email` move or cancel coffee with Lena on Sep 29?",
                                ["alert: Coffee with Lena changed", "rewrite"], threshold=0.7,
                                match=r"(?=.*\blena\b)(?=.*\b(mov\w*|cancel\w*|resched\w*)\b)")],
                   expires=(3, "hours"), sources=["calendar/coffee-with-lena"], age_min=0)  # fmt: skip
    if re.search(r"newsletter|unsubscribe", t):
        return None
    slug = "memories/" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    return gen(slug, title[:90], text.strip()[:400] or title,
               events=[Trig("email", f"Is `email` a follow-up about: {title[:48]}?", ["alert: Follow-up arrived"],
                            match=re.escape(title.lower()[:20]))],
               age_min=0)  # fmt: skip


def write_new(source: str, title: str, text: str) -> None:
    started = time.perf_counter()
    publish({"type": "writer", "status": "start"})
    pause(random.uniform(2.0, 3.4))
    m = new_memory(title, text)
    ms = int((time.perf_counter() - started) * 1000) or 3120
    if m is None:
        publish({"type": "writer", "status": "done", "ms": ms, "attempts": 1})
        return
    m.updated_at = now()
    refresh_missed(m)
    change = "updated" if m.slug in MEMS else "added"
    MEMS[m.slug] = m
    publish({"type": "writer", "status": "done", "memory": m.slug, "title": m.title, "ms": ms,
             "attempts": random.choice([1, 1, 2])})  # fmt: skip
    publish({"type": "memory", "memory": m.view(), "change": change})


def check_time() -> None:
    """Fire due time reminders (the engine's tick)."""
    t = now()
    for m in list(MEMS.values()):
        changed = False
        for i, (fire_at, text) in enumerate(m.times):
            if m.status() == "active" and i not in m.fired and fire_at <= t:
                m.fired.add(i)
                changed = True
                publish({"type": "fired", "trigger_id": m.tid(i), "memory": m.slug, "title": m.title,
                         "action": "alert", "text": text})  # fmt: skip
                pause(0.3)
        if m.status() != m.last_status:
            m.last_status = m.status()
            changed = True
        if changed:
            publish({"type": "memory", "memory": m.view(), "change": "updated"})


def set_clock(advance: int | None = None, reset: bool = False) -> dict:
    global OFFSET
    if reset:
        OFFSET = timedelta()
    elif advance:
        OFFSET += timedelta(minutes=int(advance))
    minutes = int(OFFSET.total_seconds() // 60)
    publish({"type": "clock", "now": iso(now()), "offset_minutes": minutes})
    if not reset:
        run(check_time)
    return {"now": iso(now()), "offset_minutes": minutes}


def do_sync(items: list[tuple[str, str, str]] = ()) -> None:
    publish({"type": "sync", "status": "start"})
    pause(random.uniform(0.8, 1.6))
    publish({"type": "sync", "status": "done", "new_events": len(items)})
    for source, title, text in items:
        handle(source, title, text)


def gmail_send(key: str) -> dict:
    sender, subject, text = DEMO[key]

    def arrive() -> None:
        pause(random.uniform(6, 9))  # Gmail -> GBrain sync
        do_sync([("email", subject, f"From: {sender}\n{text}")])

    run(arrive)
    return {"ok": True, "id": f"mock-{next(_ids):06x}", "subject": subject}


def chat(message: str) -> dict:
    publish({"type": "chat", "role": "user", "text": message})
    fired = handle("chat", message[:80], message)
    chips = [{"slug": m.slug, "title": m.title, "p": p} for m, t, p in fired if "surface" in t.actions]
    pause(0.6)
    if any(c["slug"] == FLIGHT for c in chips):
        f = MEMS[FLIGHT]
        reply = f"Careful: you fly UA 123 on Monday ({f.title.split(', ')[-1]}). Leave SFO plans before then."
    else:
        reply = "Noted. Nothing in your memories conflicts with that."
    publish({"type": "chat", "role": "assistant", "text": reply, "surfaced": chips})
    return {"reply": reply, "surfaced": chips}


def reset_world() -> None:
    """End of a scripted cycle: real time again and the story's memories back to the start."""
    set_clock(reset=True)

    def same(a: Mem, b: Mem) -> bool:
        return {**a.view(), "updated_at": ""} == {**b.view(), "updated_at": ""}

    fresh = base_world()
    fresh[FLIGHT] = flight_memory()
    for slug, old in list(MEMS.items()):
        m = fresh.get(slug) or replace(old, fired=set(old.fired))
        refresh_missed(m)
        m.last_status = m.status()
        if not same(old, m):
            m.updated_at = now()
            MEMS[slug] = m
            publish({"type": "memory", "memory": m.view(), "change": "updated"})


def seed() -> None:
    """Initial world + a short history, so the first GET /api/state has something to show."""
    global PAUSE
    PAUSE = False
    for m in base_world().values():
        refresh_missed(m)
        MEMS[m.slug] = m
    do_sync([("email", DEMO["flight"][1], DEMO["flight"][2])])
    publish({"type": "memory", "memory": MEMS[ACME].view(), "change": "added"})  # a person wrote this page
    do_sync([("email", "Dr. Patel's office: your Oct 1 visit was moved to 4:30pm",
              "Your dentist appointment with Dr. Patel on Oct 1 has been rescheduled to 4:30pm.")])
    do_sync()
    PAUSE = True


def story(interval: float) -> None:
    steps = [
        lambda: do_sync(),
        lambda: do_sync([("email", DEMO["change"][1], DEMO["change"][2])]),
        lambda: do_sync([("email", DEMO["soc2_planned"][1], DEMO["soc2_planned"][2])]),
        lambda: set_clock(advance=360),
        lambda: do_sync([("email", DEMO["soc2_issued"][1], DEMO["soc2_issued"][2])]),
        lambda: do_sync([("calendar", "Coffee with Lena (designer) · Tue Sep 29, 9:00am",
                          "Invite from Lena Park. Sightglass Coffee, 9:00-9:45am.")]),
        lambda: set_clock(advance=720),
        lambda: do_sync([("email", "The Download: AI agents are everywhere",
                          "This week in AI: agents that book travel. Unsubscribe anytime.")]),
        lambda: do_sync([("email", DEMO["nopa"][1], DEMO["nopa"][2])]),
        lambda: chat("Can we grab breakfast Monday at 7 before my trip?"),
        reset_world,
    ]  # fmt: skip
    time.sleep(3)
    while True:
        for step in steps:
            try:
                step()
            except Exception as e:  # keep the story going
                print(f"[mock] step failed: {e!r}")
            time.sleep(interval)


# ---------------------------------------------------------------- HTTP


def state() -> dict:
    mems = sorted(MEMS.values(), key=lambda m: m.updated_at, reverse=True)
    with LOCK:
        feed = list(FEED)
    return {"now": iso(now()), "offset_minutes": int(OFFSET.total_seconds() // 60),
            "memories": [m.view() for m in mems], "feed": feed}  # fmt: skip


class Handler(BaseHTTPRequestHandler):
    server_version = "memento-mock/0.2"

    def log_message(self, fmt: str, *args) -> None:
        if self.command == "POST":
            print(f"[mock] POST {self.path} -> {args[1] if len(args) > 1 else ''}")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except json.JSONDecodeError:
            data = {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._json({"detail": f"missing {INDEX}"}, 404)
        elif path == "/api/state":
            self._json(state())
        elif path == "/api/stream":
            self._stream()
        else:
            self._json({"detail": "Not Found"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        body = self._body()
        if path == "/api/chat":
            message = str(body.get("message") or "").strip()
            self._json(chat(message) if message else {"detail": "message required"}, 200 if message else 422)
        elif path == "/api/clock":
            self._json(set_clock(advance=body.get("advance_minutes"), reset=bool(body.get("reset"))))
        elif path == "/api/sync":
            run(do_sync)
            self._json({"ok": True})
        elif path == "/api/simulate/email":
            subject = str(body.get("subject") or "(no subject)")
            text = f"From: {body.get('from') or 'someone@example.com'}\n{body.get('body') or ''}"
            run(handle, "email", subject, text)
            self._json({"ok": True})
        elif path == "/api/gmail/send":
            key = str(body.get("demo") or "")
            if key in DEMO:
                self._json(gmail_send(key))
            else:
                self._json({"detail": f"unknown demo {key!r}; one of {sorted(DEMO)}"}, 422)
        else:
            self._json({"detail": "Not Found"}, 404)

    def _stream(self) -> None:
        q: queue.Queue = queue.Queue()
        with LOCK:
            SUBS.add(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n")
            while True:
                try:
                    event = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
        except OSError:  # client went away
            pass
        finally:
            with LOCK:
                SUBS.discard(q)


def main() -> None:
    ap = argparse.ArgumentParser(description="Mock memento backend for the console UI.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--interval", type=float, default=4.0, help="seconds between scripted story steps")
    ap.add_argument("--no-script", action="store_true", help="do not play the demo story automatically")
    args = ap.parse_args()
    seed()
    if not args.no_script:
        threading.Thread(target=story, args=(args.interval,), daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"memento mock UI on http://{args.host}:{args.port}  (serving {INDEX})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
