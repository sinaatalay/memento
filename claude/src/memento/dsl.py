"""The memento language: what a memory's recipe imports.

A recipe is a Python module stored in a memory page's frontmatter. It only
*declares* when its memory matters; running it fills a registry the runtime
reads. Time math is plain Python datetime. Judgments are questions that Jev,
a decision model, answers about each incoming email, calendar event or chat
message.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .model import (
    STATE_KEY,
    Action,
    Alert,
    Choice,
    EventTrigger,
    Noul,
    Recipe,
    Rewrite,
    Score,
    Source,
    Surface,
    TimeTrigger,
)

TZ = ZoneInfo("America/Los_Angeles")

email = Source.email
calendar = Source.calendar
chat = Source.chat
this = "this"  # the memory the recipe belongs to

_recipe = Recipe()


class RecipeError(ValueError):
    """A recipe that can't run. Messages are written for the LLM that wrote it."""


def at(when: str) -> datetime:
    """A moment in the user's timezone (America/Los_Angeles): at("2026-09-28 08:05")."""
    try:
        moment = datetime.fromisoformat(when)
    except (TypeError, ValueError) as e:
        raise RecipeError(f'at({when!r}): write it as "YYYY-MM-DD HH:MM"') from e
    return moment if moment.tzinfo else moment.replace(tzinfo=TZ)


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)


def hours(n: float) -> timedelta:
    return timedelta(hours=n)


def days(n: float) -> timedelta:
    return timedelta(days=n)


def noul(question: str, *, yes: str | None = None, no: str | None = None) -> Noul:
    """A yes/no judgment. The trigger fires when P(yes) reaches its threshold."""
    return Noul(question=question, yes=yes, no=no)


def choice(question: str, *, fire_on: str, **options: str) -> Choice:
    """A pick-one judgment. The trigger fires when `fire_on` is likely enough."""
    if len(options) < 2:
        raise RecipeError("choice(): give at least two options as keyword arguments")
    if fire_on not in options:
        raise RecipeError(f"choice(): fire_on={fire_on!r} is not one of {sorted(options)}")
    return Choice(question=question, options=options, fire_on=fire_on)


def score(question: str, levels: list[str], *, at_least: int) -> Score:
    """A rubric judgment. The trigger fires when a level >= at_least is likely enough."""
    if not 2 <= len(levels) <= 10:
        raise RecipeError("score(): give between 2 and 10 levels")
    if not 0 < at_least < len(levels):
        raise RecipeError(f"score(): at_least must be between 1 and {len(levels) - 1}")
    return Score(question=question, levels=levels, at_least=at_least)


def alert(text: str) -> Alert:
    """Notify the user now."""
    return Alert(text=text)


def surface(target: str = this) -> Surface:
    """Bring this memory into the conversation."""
    return Surface()


def rewrite(target: str = this) -> Rewrite:
    """Update this memory with the item that fired the trigger."""
    return Rewrite()


def remind(when: datetime, text: str) -> None:
    """Notify the user with `text` at `when`."""
    if not isinstance(when, datetime) or when.tzinfo is None:
        raise RecipeError("remind(): `when` must come from at(...) plus or minus hours()/days()")
    _recipe.triggers.append(TimeTrigger(fire_at=when, action=Alert(text=text)))


def on(source: Source, *, when: Noul | Choice | Score, do: Action | list[Action], threshold: float = 0.7) -> None:
    """When an item from `source` arrives and `when` is judged true, run `do`."""
    if not isinstance(source, Source):
        raise RecipeError("on(): the source must be email, calendar or chat")
    if not isinstance(when, Noul | Choice | Score):
        raise RecipeError("on(): `when` must be noul(...), choice(...) or score(...)")
    key = STATE_KEY[source]
    if f"`{key}`" not in when.question:
        raise RecipeError(
            f'on({source.value}): the question must name the item as `{key}` in backticks, e.g. "Does `{key}` ...?"'
        )
    actions = do if isinstance(do, list) else [do]
    if not actions or not all(isinstance(a, Alert | Surface | Rewrite) for a in actions):
        raise RecipeError("on(): `do` must be alert(...), surface(this), rewrite(this) or a list of them")
    _recipe.triggers.append(EventTrigger(source=source, when=when, actions=actions, threshold=threshold))


def expires(when: datetime) -> None:
    """After `when` this memory goes quiet."""
    if not isinstance(when, datetime) or when.tzinfo is None:
        raise RecipeError("expires(): `when` must come from at(...) plus or minus hours()/days()")
    _recipe.expires = when
