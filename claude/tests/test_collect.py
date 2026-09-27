import asyncio, os
from pathlib import Path
import pytest
from memento.collect import collect, lint, fmt
from memento.dsl import RecipeError
from memento.model import EventTrigger, TimeTrigger

FLIGHT = '''from memento import at, hours, remind, on, email, chat, noul
from memento import alert, surface, rewrite, expires, this

departure = at("2026-09-28 08:05")

remind(departure - hours(24), "Flight UA 123 to JFK tomorrow 8:05. Check in.")
remind(departure - hours(3), "Leave for SFO by 5:30.")
on(
    email,
    when=noul("Does `email` delay, change or cancel UA 123 on Sep 28?"),
    do=[alert("Your flight UA 123 changed"), rewrite(this)],
)
on(
    chat,
    when=noul("Is `message` about plans for Monday Sep 28 before noon?"),
    do=surface(this),
)
expires(departure + hours(6))
'''

def test_flight_recipe():
    r = collect(FLIGHT, memory="memories/flight")
    assert [t.kind for t in r.triggers] == ["time", "time", "event", "event"]
    assert r.triggers[0].id.startswith("memories/flight#0-")
    assert collect(FLIGHT, memory="memories/flight").triggers[0].id == r.triggers[0].id  # stable
    assert r.triggers[0].fire_at.isoformat() == "2026-09-27T08:05:00-07:00"
    assert r.expires.isoformat() == "2026-09-28T14:05:00-07:00"

@pytest.mark.parametrize("bad, msg", [
    ("import os\n", "only `from memento import"),
    ("from memento import at\nopen('/etc/passwd')\n", "`open` is not allowed"),
    ("from memento import on, email, noul, alert\non(email, when=noul('Is it late?'), do=alert('x'))\n", "must name the item as `email`"),
    ("from memento import remind, at\nremind(at('tomorrow'), 'x')\n", 'write it as "YYYY-MM-DD HH:MM"'),
    ("from memento import at\nx = at('2026-09-28 08:05')\n", "declares no triggers"),
])
def test_errors(bad, msg):
    with pytest.raises(RecipeError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        collect(bad, memory="m")

def test_fmt_keeps_lines_short():
    ugly = 'from memento import at, hours, remind, on, email, chat, noul, alert, surface, rewrite, expires, this\n'
    assert all(len(l) <= 78 for l in fmt(ugly).splitlines())


def test_fmt_splits_long_questions_into_adjacent_literals():
    long = (
        "from memento import on, email, noul, alert\n"
        "on(email, when=noul('Does `email` confirm smog check completion or registration "
        "renewal for the 2019 Honda Civic before October 15?'), do=alert('Registration done'))\n"
    )
    out = fmt(long)
    assert all(len(line) <= 78 for line in out.splitlines())
    recipe = collect(out, memory="m")
    assert recipe.triggers[0].when.question.endswith("before October 15?")
    assert "  " not in recipe.triggers[0].when.question


def test_fmt_packs_exploded_imports():
    exploded = "from memento import (\n" + "".join(
        f"    {n},\n" for n in ["at", "days", "remind", "on", "email", "noul", "alert", "rewrite", "expires", "this"]
    ) + ")\n\ndue = at('2026-10-15 23:59')\nremind(due - days(1), 'Renew registration')\n"
    out = fmt(exploded)
    imports = [line for line in out.splitlines() if line.startswith("from memento import")]
    assert len(imports) == 2 and all(len(line) <= 78 for line in imports)
    assert len(collect(out, memory="m").triggers) == 1
