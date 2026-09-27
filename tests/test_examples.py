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
