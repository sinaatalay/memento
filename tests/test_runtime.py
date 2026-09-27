"""The runtime against a brain on disk, with Jev, River and delivery faked."""

import asyncio
import hashlib
from datetime import datetime, timedelta

import pytest

from memento import deliver, state
from memento.api import TZ, Says
from memento.gbrain import Brain
from memento.jev import Asked
from memento.river import Draft
from memento.runtime import Runtime

FLIGHT_RECIPE = """\
from memento import at, when, notify, update, hours

flight = at("2026-09-28 08:05")

@at(flight - hours(3))
def leave():
    notify("Leave for SFO.")

@when("flight UA123 is delayed", until=flight)
def delayed(news):
    notify(f"UA123 changed: {news.title}")
    update(news)
"""

DELAYED_RECIPE = FLIGHT_RECIPE.replace("08:05", "10:40")


class FakeBrain(Brain):
    """A content root on disk; GBrain's operations applied to the files."""

    def __init__(self, root):
        super().__init__(root=root, command=["gbrain"])
        self.calls = []

    def put(self, slug, text):
        path = self.root / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    async def call(self, operation, params):
        self.calls.append(operation)
        path = self.root / f"{params['slug']}.md"
        text = path.read_text()
        revision = hashlib.sha1(text.encode()).hexdigest()
        if operation == "get_page":
            return {"content": text, "revision": revision}
        if operation == "put_page":
            assert params["expected_revision"] == revision
            path.write_text(params["content"])
        elif operation == "add_timeline_entry":
            path.write_text(
                text + f"\n- **{params['date']}** | {params.get('source')} — {params['summary']}\n"
            )
        return {"status": "ok"}


class FakeJev:
    """Says yes to a claim when the page mentions all of the claim's marked words."""

    def __init__(self, matches):
        self.matches = matches  # claim substring -> word the page must contain
        self.loop = None

    def p(self, page, question):
        for claim, word in self.matches.items():
            if isinstance(question, Says) and claim in question.claim:
                return 0.97 if word.lower() in page.text.lower() else 0.02
        return 0.02

    async def ask(self, page, now, questions):
        return Asked({k: self.p(page, q) for k, q in questions.items()}, 100, 500)

    def answer(self, page, now, question):
        return self.p(page, question) >= 0.5


class FakeRiver:
    def __init__(self, recipes, revised=None):
        self.recipes, self.revised, self.wrote = recipes, revised, []

    async def awrite(self, page, now, current=None, others=()):
        self.wrote.append(page.slug)
        return Draft(self.recipes.get(page.slug, ""), None, 0.1, 1)

    async def arevise(self, memory, recipe, news, now):
        return Draft(self.revised, "UA123 now departs 10:40.", 0.1, 1)


@pytest.fixture
def world(tmp_path, monkeypatch):
    clock = {"now": datetime(2026, 9, 27, 15, 0, tzinfo=TZ)}
    monkeypatch.setattr(state, "HOME", tmp_path / "home")
    monkeypatch.setattr(state, "now", lambda: clock["now"])
    sent = []

    async def send(title, text):
        sent.append((title, text))

    monkeypatch.setattr(deliver, "send", send)
    brain = FakeBrain(tmp_path / "brain")
    brain.root.mkdir()
    return brain, clock, sent


async def settle(runtime):
    runtime.scan()
    while runtime.tasks:
        await asyncio.gather(*list(runtime.tasks))
        runtime.scan()


def page(title, body, recipe=None):
    head = f"---\ntype: note\ntitle: {title}\n"
    if recipe:
        head += "recipe: |\n" + "".join(
            f"  {line}\n" if line else "\n" for line in recipe.splitlines()
        )
    return head + f"---\n\n# {title}\n\n{body}\n"


def test_first_run_treats_the_brain_as_history(world):
    brain, _, _ = world
    brain.put("notes/old", page("Old plan", "Dinner with Ana on Friday"))
    river = FakeRiver({"notes/old": FLIGHT_RECIPE})
    runtime = Runtime(brain, FakeJev({}), river)

    async def go():
        runtime.scan(baseline=True)
        await settle(runtime)

    asyncio.run(go())
    assert river.wrote == []


def test_river_writes_a_recipe_for_a_new_memory_and_its_write_is_not_news(world):
    brain, _, _ = world
    river = FakeRiver({"trips/nyc": FLIGHT_RECIPE})
    runtime = Runtime(brain, FakeJev({"future moment": "UA123"}), river)
    runtime.scan(baseline=True)
    brain.put("trips/nyc", page("Flight to NYC", "UA123 SFO to JFK Monday 8:05am"))
    asyncio.run(settle(runtime))
    assert river.wrote == ["trips/nyc"]
    assert brain.calls == ["get_page", "put_page"]
    assert "recipe: |" in (brain.root / "trips/nyc.md").read_text()
    assert set(runtime.memories) == {"trips/nyc"}


def test_news_fires_a_memory_which_notifies_and_updates_itself(world):
    brain, _, sent = world
    brain.put("trips/nyc", page("Flight to NYC", "UA123 Monday 8:05am", FLIGHT_RECIPE))
    river = FakeRiver({}, revised=DELAYED_RECIPE)
    runtime = Runtime(brain, FakeJev({"delayed": "10:40"}), river)
    runtime.scan(baseline=True)

    brain.put("emails/united", page("Schedule change: UA123", "UA123 now departs 10:40"))
    asyncio.run(settle(runtime))

    assert sent == [("Flight to NYC", "UA123 changed: Schedule change: UA123")]
    text = (brain.root / "trips/nyc.md").read_text()
    assert "UA123 now departs 10:40." in text and 'at("2026-09-28 10:40")' in text
    assert river.wrote == []  # news for an existing memory doesn't become a memory of its own
    leave = next(t for t in runtime.memories["trips/nyc"].recipe.timers.values())
    assert leave.at.hour == 7 and leave.at.minute == 40


def test_unrelated_news_fires_nothing(world):
    brain, _, sent = world
    brain.put("trips/nyc", page("Flight to NYC", "UA123 Monday 8:05am", FLIGHT_RECIPE))
    runtime = Runtime(brain, FakeJev({"delayed": "10:40"}), FakeRiver({}))
    runtime.scan(baseline=True)
    brain.put("notes/lunch", page("Lunch", "Lunch with Sam on Tuesday"))
    asyncio.run(settle(runtime))
    assert sent == []


def test_timers_fire_once_when_due(world):
    brain, clock, sent = world
    brain.put("trips/nyc", page("Flight to NYC", "UA123 Monday 8:05am", FLIGHT_RECIPE))
    runtime = Runtime(brain, FakeJev({}), FakeRiver({}))
    runtime.scan(baseline=True)

    async def at(moment):
        clock["now"] = moment
        runtime.check_timers()
        await settle(runtime)

    asyncio.run(at(datetime(2026, 9, 28, 5, 0, tzinfo=TZ)))
    assert sent == []
    asyncio.run(at(datetime(2026, 9, 28, 5, 6, tzinfo=TZ)))
    asyncio.run(at(datetime(2026, 9, 28, 5, 30, tzinfo=TZ)))
    assert sent == [("Flight to NYC", "Leave for SFO.")]


def test_a_timer_already_past_when_written_never_fires(world):
    brain, clock, sent = world
    clock["now"] = datetime(2026, 9, 28, 6, 0, tzinfo=TZ)  # after the 5:05 reminder
    brain.put("trips/nyc", page("Flight to NYC", "UA123 Monday 8:05am", FLIGHT_RECIPE))
    runtime = Runtime(brain, FakeJev({}), FakeRiver({}))
    runtime.scan(baseline=True)
    runtime.check_timers()
    asyncio.run(settle(runtime))
    assert sent == []


def test_recurring_timers_keep_firing(world):
    brain, clock, sent = world
    recipe = (
        'from memento import at, notify, weeks\n\n@at("2026-09-27 18:00", every=weeks(1))\n'
        'def call_mom():\n    notify("Call mom.")\n'
    )
    brain.put("notes/mom", page("Call mom", "Every Sunday evening", recipe))
    runtime = Runtime(brain, FakeJev({}), FakeRiver({}))
    runtime.scan(baseline=True)

    async def on(day):
        clock["now"] = datetime(2026, 9, 27, 18, 1, tzinfo=TZ) + timedelta(days=day)
        runtime.check_timers()
        await settle(runtime)

    for day in (0, 3, 7, 7, 14):
        asyncio.run(on(day))
    assert len(sent) == 3


def test_a_broken_recipe_is_reported_not_run(world):
    brain, _, _ = world
    brain.put("notes/bad", page("Bad", "x", "import os\n"))
    runtime = Runtime(brain, FakeJev({}), FakeRiver({}))
    runtime.scan(baseline=True)
    assert "only `from memento import" in runtime.memories["notes/bad"].error


def test_a_reminder_long_overdue_is_skipped(world):
    brain, clock, sent = world
    brain.put("trips/nyc", page("Flight to NYC", "UA123 Monday 8:05am", FLIGHT_RECIPE))
    runtime = Runtime(brain, FakeJev({}), FakeRiver({}))
    runtime.scan(baseline=True)

    async def at(moment):
        clock["now"] = moment
        runtime.check_timers()
        await settle(runtime)

    asyncio.run(at(datetime(2026, 9, 28, 7, 30, tzinfo=TZ)))  # 5:05 was 2h25m ago
    assert sent == []
