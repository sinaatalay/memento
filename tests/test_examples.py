"""Every example page's recipe loads, and the live claims hold up against real Jev."""

import os
from datetime import datetime
from pathlib import Path

import pytest

from memento.api import TZ, Page
from memento.gbrain import parse
from memento.recipe import load

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.md"))
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=TZ)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_loads(path):
    stored = parse(path.stem, path.read_text())
    assert load(stored.recipe, stored.page, NOW).triggers


# (example, claim prefix, news that must fire, news that must not)
CASES = [
    (
        "flight",
        "flight UA123",
        [
            "Schedule change: UA 123 on Monday, September 28 now departs at 10:40 AM instead of 8:05 AM.",
            "We're sorry: your flight UA123 SFO to JFK on Sep 28 has been cancelled due to weather.",
        ],
        [
            "Your trip is confirmed: UA123 SFO to JFK, Monday Sep 28, departs 8:05 AM. Seat 14C.",
            "Upgrade offer: move to Economy Plus on UA123 for $59.",
            "Flight DL400 to Boston on Monday is delayed by 2 hours.",
        ],
    ),
    (
        "priya-hiring",
        "an experienced product designer",
        [
            "Coffee with Alex Chen: senior product designer at Figma for 6 years, leaving next month, "
            "wants a founding designer role at an early startup.",
        ],
        [
            "Figma is hiring a senior product designer. Apply by Friday.",
            "Lunch with Sam: he's a backend engineer happy at Stripe.",
        ],
    ),
    (
        "acme-pilot",
        "our SOC 2",
        [
            "Vanta Audits: Good news, your SOC 2 Type I report has been issued and is ready to share.",
        ],
        [
            "Vanta Audits: your SOC 2 audit fieldwork is scheduled for October 12-14.",
            "Reminder: SOC 2 readiness checklist is 80% complete.",
        ],
    ),
    (
        "investor-deck",
        "the investor deck was sent",
        [
            "Sent Priya Raman the seed investor deck this morning; she said thanks.",
        ],
        [
            "Still polishing the investor deck for Priya, should be done tomorrow.",
        ],
    ),
    (
        "decision-postgres",
        "our Postgres write volume",
        ["Incident review: writes held at 7.2k/sec for 40 minutes during the Acme bulk import."],
        ["Weekly metrics: writes averaged 1.8k/sec, p99 latency flat."],
    ),
    (
        "decision-postgres",
        "a customer requires",
        [
            "Siemens call notes: procurement says they can only sign if all customer data "
            "stays in the EU."
        ],
        ["Siemens call notes: they asked for our SOC 2 report and a security questionnaire."],
    ),
    (
        "battlecard-linear",
        "Linear launched",
        ["Linear changelog: Introducing Time Tracking. Log hours on any issue, on all plans."],
        [
            "Linear raises an $80M Series C.",
            "Forum thread: Linear users ask when time tracking is coming.",
            "Jira ships improvements to its time tracking reports.",
        ],
    ),
    (
        "83b-election",
        "the early exercise",
        [
            "Carta: Your exercise of 40,000 Northwind options was completed on Tuesday, "
            "September 29."
        ],
        ["Carta: Your exercise request for 40,000 Northwind options is pending board approval."],
    ),
    (
        "lesson-migrations",
        "a database migration",
        ["Deploy plan: run the billing schema migration Friday afternoon, before Monday's launch."],
        [
            "Ran the billing migration Tuesday morning, all green.",
            "Launch checklist for Monday: update the pricing page, send the announcement.",
        ],
    ),
    (
        "applecare",
        "my 14-inch MacBook Pro",
        ["Spilled coffee on my MacBook keyboard this morning, half the keys are dead."],
        [
            "Thinking about a MacBook Air for my sister's birthday.",
            "Cracked my iPad screen on the train.",
        ],
    ),
    (
        "churn-hypothesis",
        "a customer explains",
        [
            "Interview with Bolt Labs (churned in Aug): 'Honestly it was the price. Our bill doubled.'"
        ],
        ["Interview with Tandem (active customer): they love the new dashboard."],
    ),
    (
        "owed-intro",
        "Maria Santos introduced",
        [
            "Email from Maria Santos: Intro: you <> Devon (Head of Infra, Stripe). Devon, meet Sina..."
        ],
        ["Email from Maria Santos: Great seeing you yesterday! Let's grab dinner soon."],
    ),
]


@pytest.mark.skipif(
    not os.environ.get("TYPESAFE_API_KEY"), reason="live Jev check needs TYPESAFE_API_KEY"
)
@pytest.mark.parametrize("example, claim, fire, quiet", CASES, ids=[c[0] for c in CASES])
def test_claims_with_real_jev(example, claim, fire, quiet):
    import asyncio

    from memento.jev import FIRES, Jev

    path = next(p for p in EXAMPLES if p.stem == example)
    stored = parse(path.stem, path.read_text())
    trigger = next(
        t
        for t in load(stored.recipe, stored.page, NOW).watches.values()
        if t.claim.startswith(claim)
    )
    jev = Jev()

    async def p(text):
        news = Page(slug="news", title=text.split(":")[0][:60], text=text)
        return float((await jev.ask(news, NOW, {"q": trigger.question})).values["q"])

    async def all_():
        return await asyncio.gather(*(p(t) for t in fire)), await asyncio.gather(
            *(p(t) for t in quiet)
        )

    fired, stayed = asyncio.run(all_())
    assert all(v >= FIRES for v in fired), list(zip(fire, fired, strict=True))
    assert all(v < FIRES for v in stayed), list(zip(quiet, stayed, strict=True))


@pytest.mark.skipif(
    not os.environ.get("TYPESAFE_API_KEY"), reason="live Jev check needs TYPESAFE_API_KEY"
)
def test_which_reads_the_churn_reason_with_real_jev():
    import asyncio

    from memento.api import Which
    from memento.jev import Jev

    reasons = Which(
        "What does the customer give as the main reason?",
        (
            ("onboarding", "setup, onboarding or getting started was too hard"),
            ("price", "price, cost or the bill"),
            ("other", "missing features, switching tools, or anything else"),
        ),
    )
    interviews = {
        "price": "Bolt Labs churned: 'Honestly it was the price. When our seats doubled, so did the bill.'",
        "onboarding": "Quill churned: 'We never got it set up. Importing our projects took weeks.'",
        "other": "Harbor churned: 'We moved everything to Notion when we consolidated tools.'",
    }

    async def ask(text):
        page = Page("n", "Churn interview", text)
        return (await Jev().ask(page, NOW, {"q": reasons})).values["q"]

    got = {want: asyncio.run(ask(text)) for want, text in interviews.items()}
    assert got == {"price": "price", "onboarding": "onboarding", "other": "other"}
