"""Behavioral and escape-resistance checks for the executable memory format."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memento.collector import (
    RecipeError,
    collect,
    collect_markdown,
    extract_recipe,
    validate_source,
)
from memento.dsl import Recipe, at, remind


def test_reminder_time_math_and_roundtrip() -> None:
    recipe = collect('''\
from memento import at, hours, remind, expires
departure = at("2026-09-28 11:00", tz="America/Los_Angeles")
remind(departure - hours(3), "Leave for your flight.")
expires(departure + hours(5))
''')
    reminder = recipe.reminders[0]
    assert reminder.at.isoformat() == "2026-09-28T08:00:00-07:00"
    assert reminder.at.astimezone(timezone.utc).hour == 15
    assert recipe.expires_at == datetime.fromisoformat("2026-09-28T16:00-07:00")
    serialized = json.loads(recipe.model_dump_json())
    assert serialized["reminders"][0]["text"] == "Leave for your flight."
    assert Recipe.model_validate(serialized) == recipe


def test_multiaction_event_keeps_labels_and_filters() -> None:
    recipe = collect('''\
from memento import on, email, noul, alert, rewrite, this
on(
    email,
    when=noul(
        "Does this change flight UA123?",
        true="A new departure time or cancellation for UA123.",
        false="Unrelated mail or a duplicate confirmation.",
    ),
    do=[alert("Your flight changed."), rewrite(this)],
    threshold=0.9,
    sender_domain="united.com",
)
''')
    trigger = recipe.triggers[0]
    assert trigger.source == "email"
    assert [action.kind for action in trigger.actions] == ["alert", "rewrite"]
    assert trigger.action.text == "Your flight changed."
    assert trigger.question.options["true"].startswith("A new departure")
    assert trigger.threshold == 0.9
    assert trigger.filters == {"sender_domain": "united.com"}


def test_qualified_and_aliased_imports_are_valid_python() -> None:
    source = '''\
import memento as m
from memento import noul as question
m.on(m.chat, when=question("Is travel relevant?"), do=m.surface(m.this))
'''
    compile(source, "example.py", "exec")
    recipe = collect(source)
    assert recipe.triggers[0].actions[0].target == "this"


def test_choice_requires_target_and_score_preserves_order() -> None:
    recipe = collect('''\
from memento import on, email, choice, score, alert
on(
    email,
    when=choice("What changed?", flight="Flight", hotel="Hotel"),
    match="flight",
    do=alert("A flight update arrived."),
)
on(
    email,
    when=score("How urgent?", ["Routine", "Soon", "Immediate"]),
    do=alert("This needs attention."),
    threshold=0.9,
)
''')
    assert recipe.triggers[0].match == "flight"
    assert recipe.triggers[1].question.levels == ["Routine", "Soon", "Immediate"]
    with pytest.raises(RecipeError, match="require match"):
        collect('''\
from memento import on, email, choice, alert
on(email, when=choice("Which?", a="A", b="B"), do=alert("Update"))
''')


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.system('touch /tmp/memento-should-not-exist')",
        "from pathlib import Path\nPath('/tmp/memento-probe').write_text('x')",
        "from memento import __dict__",
        "from memento import *",
        "from memento.dsl import at",
        "from .memento import at",
        "import memento\nmemento.__dict__",
        "from memento import at\nat.__globals__",
        "from memento import at\nat.__class__.__mro__",
        "from memento import at\nf = at\nf('2026-09-28T08:00Z')",
        "__import__('os')",
        "open('/tmp/memento-probe', 'w')",
        "exec('import os')",
        "eval('1')",
        "while True:\n    pass",
        "for i in []:\n    pass",
        "def unsafe():\n    pass",
        "class Unsafe:\n    pass",
        "x = lambda: 1",
        "x = [i for i in []]",
        "x = (i for i in [])",
        "x = f'{1}'",
        "x = ().__class__",
        "x = [1][0]",
        "x = 2 ** 1000000",
        "x = 'a' * 1000000000",
        "x = 'a' + 'a'",
        "x = 1\nx = 2",
        "from memento import at\nat = 'oops'",
        "from memento import at\nat(**{})",
        "from memento import hours\nhours(*[1])",
    ],
)
def test_unsafe_python_is_rejected_before_subprocess(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_subprocess(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid recipe must never reach process execution")

    monkeypatch.setattr(subprocess, "run", forbidden_subprocess)
    with pytest.raises(RecipeError):
        collect(source)


def test_valid_prefix_cannot_execute_before_invalid_suffix(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    source = (
        "from memento import remind, at\n"
        "remind(at('2026-09-28T08:00Z'), 'Legitimate trigger')\n"
        f"open({str(marker)!r}, 'w')\n"
    )
    with pytest.raises(RecipeError):
        collect(source)
    assert not marker.exists()


@pytest.mark.parametrize(
    ("value", "tz", "error"),
    [
        ("2026-09-28 08:00", None, "requires tz"),
        ("2026-09-28 08:00", "Mars/Olympus", "unknown timezone"),
        ("2026-03-08 02:30", "America/Los_Angeles", "does not exist"),
        ("2026-11-01 01:30", "America/Los_Angeles", "ambiguous"),
        ("2026-09-28T08:00Z", "America/Los_Angeles", "not both"),
        ("2026-09-28", "UTC", "date and time"),
    ],
)
def test_invalid_and_ambiguous_times_fail(value: str, tz: str | None, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        at(value, tz=tz)


def test_explicit_offset_resolves_dst_ambiguity() -> None:
    first = at("2026-11-01T01:30:00-07:00")
    second = at("2026-11-01T01:30:00-08:00")
    assert second - first == timedelta(hours=1)


@pytest.mark.parametrize(
    ("source", "error"),
    [
        ("\t# no tabs", "tabs"),
        ("#" * 16385, "16384 bytes"),
        ("from memento import at\nat('nonsense')", "date and time"),
        ("from memento import hours\nhours(1e309)", "supported range"),
        (
            "from memento import remind, expires, at\n"
            "remind(at('2026-09-28T08:00Z'), 'Flight')\n"
            "expires(at('2026-09-28T08:00Z'))",
            "before expiration",
        ),
        (
            "from memento import on, email, noul, alert\n"
            "on(email, when=noul('Changed?'), do=alert('A'), threshold=1.1)",
            "less than or equal",
        ),
        (
            "from memento import expires, at\n"
            "expires(at('2026-09-28T08:00Z'))\n"
            "expires(at('2026-09-28T09:00Z'))",
            "only once",
        ),
    ],
)
def test_actionable_validation_errors(source: str, error: str) -> None:
    with pytest.raises(RecipeError, match=error):
        collect(source)


def test_timeout_kills_child_and_becomes_recipe_error() -> None:
    with pytest.raises(RecipeError, match="timed out"):
        collect("", timeout=0.000001)


def test_long_source_lines_preserve_recipe_meaning() -> None:
    # The pinned GBrain version round-trips long imports, comments, and string
    # literals intact. Line length is a writing preference, not a validity rule.
    text = "Your flight leaves tomorrow. " + "Check the updated itinerary. " * 8
    source = (
        "from memento import at, hours, minutes, days, remind, on, expires, "
        "email, calendar, chat, noul, choice, score, alert, surface, rewrite, this\n"
        + "# " + "A long explanatory recipe comment. " * 8 + "\n"
        + f"remind(at('2026-09-28T08:00Z'), {text!r})\n"
    )
    assert all(len(line) > 78 for line in source.splitlines())
    recipe = collect(source)
    assert recipe.reminders[0].text == text


def test_registration_does_not_leak_between_memories() -> None:
    first = collect('''\
from memento import remind, at
remind(at("2026-09-28T08:00Z"), "One memory")
''')
    assert len(first.reminders) == 1
    assert collect("").reminders == []
    with pytest.raises(RuntimeError, match="requires memento.collect"):
        remind(at("2026-09-28T08:00Z"), "Outside collector")


def test_markdown_parsing_preserves_python_and_passive_memories() -> None:
    markdown = '''\
---
title: Flight to New York
recipe: |
  from memento import at, remind
  remind(at("2026-09-28T08:00Z"), "Your flight is tomorrow.")
---
# Flight
Flight UA123 leaves tomorrow.
'''
    source = extract_recipe(markdown)
    assert source is not None
    assert source.startswith("from memento import")
    assert collect_markdown(markdown).reminders[0].text == "Your flight is tomorrow."
    assert collect_markdown("# Preference\nI prefer tea.") == Recipe()
    assert collect_markdown("---\ntitle: Preference\n---\nTea") == Recipe()


@pytest.mark.parametrize(
    "markdown",
    [
        "---\nrecipe: []\n---\nBody",
        "---\n- recipe\n---\nBody",
        "---\nrecipe: |\n  import memento",
        "---\nrecipe: !!python/object/apply:os.system ['echo unsafe']\n---",
        "---\nrecipe: [unclosed\n---",
    ],
)
def test_invalid_front_matter_is_rejected(markdown: str) -> None:
    with pytest.raises(RecipeError):
        collect_markdown(markdown)


def test_all_example_memories_collect() -> None:
    examples = Path(__file__).resolve().parents[1] / "examples"
    files = list(examples.glob("*.md"))
    assert len(files) >= 3
    for path in files:
        recipe = collect_markdown(path.read_text())
        assert recipe.reminders or recipe.triggers, path.name
        Recipe.model_validate_json(recipe.model_dump_json())
