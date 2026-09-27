"""The small declarative Python API embedded in a memory's front matter.

Calling ``remind``, ``on``, and ``expires`` registers intent. It does not make
network requests, send notifications, or execute actions. Recipes are collected
separately, and the runtime is responsible for deciding when to act.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterator, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RecipeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Question(RecipeModel):
    kind: Literal["noul", "choice", "score"]
    question: str = Field(min_length=1, max_length=4000)
    options: dict[str, str] = Field(default_factory=dict)
    levels: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_criteria(self) -> Question:
        if not self.question.strip():
            raise ValueError("question must contain non-whitespace text")
        if self.kind == "noul":
            if set(self.options) != {"true", "false"} or self.levels:
                raise ValueError("noul requires true/false descriptions only")
        elif self.kind == "choice":
            if not 2 <= len(self.options) <= 255 or self.levels:
                raise ValueError("choice requires 2 to 255 named options")
        elif self.options or not 2 <= len(self.levels) <= 10:
            raise ValueError("score requires 2 to 10 ordered levels")
        if any(not key.strip() or not value.strip()
               for key, value in self.options.items()):
            raise ValueError("question options must have nonempty names and text")
        if any(not level.strip() for level in self.levels):
            raise ValueError("score level descriptions must not be empty")
        return self


class Action(RecipeModel):
    kind: Literal["alert", "surface", "rewrite"]
    text: str | None = Field(default=None, min_length=1, max_length=8000)
    target: Literal["this"] | None = None

    @model_validator(mode="after")
    def valid_payload(self) -> Action:
        if self.kind == "alert":
            if self.text is None or not self.text.strip() or self.target is not None:
                raise ValueError("alert requires text and no target")
        elif self.target != "this" or self.text is not None:
            raise ValueError("surface/rewrite require target=this and no text")
        return self


class Reminder(RecipeModel):
    at: datetime
    text: str = Field(min_length=1, max_length=8000)

    @field_validator("at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _aware(value)

    @field_validator("text")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reminder text must not be empty")
        return value


class EventTrigger(RecipeModel):
    source: Literal["email", "calendar", "chat"]
    question: Question
    actions: list[Action] = Field(min_length=1, max_length=8)
    threshold: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    match: str | None = None
    filters: dict[str, str | int | float | bool | list[str]] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def explicit_choice_target(self) -> EventTrigger:
        if self.question.kind == "choice":
            if self.match not in self.question.options:
                raise ValueError("choice triggers require match=<option name>")
        elif self.match is not None:
            raise ValueError("match is only supported for choice questions")
        return self

    @property
    def action(self) -> Action:
        """The first action, for clients that only need a single action."""
        return self.actions[0]


class Recipe(RecipeModel):
    reminders: list[Reminder] = Field(default_factory=list, max_length=128)
    triggers: list[EventTrigger] = Field(default_factory=list, max_length=128)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def aware_expiration(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None

    @model_validator(mode="after")
    def reminders_before_expiration(self) -> Recipe:
        if self.expires_at is not None:
            for reminder in self.reminders:
                if reminder.at >= self.expires_at:
                    raise ValueError("reminders must occur before expiration")
        return self


class _Source(str, Enum):
    EMAIL = "email"
    CALENDAR = "calendar"
    CHAT = "chat"


class _This(str, Enum):
    MEMORY = "this"


email = _Source.EMAIL
calendar = _Source.CALENDAR
chat = _Source.CHAT
this = _This.MEMORY


class _Registration:
    def __init__(self) -> None:
        self.reminders: list[Reminder] = []
        self.triggers: list[EventTrigger] = []
        self.expires_at: datetime | None = None

    def check_size(self) -> None:
        if len(self.reminders) + len(self.triggers) >= 128:
            raise ValueError("a recipe may register at most 128 triggers")


_current: ContextVar[_Registration | None] = ContextVar(
    "memento_recipe_registration", default=None
)


@contextmanager
def _capture() -> Iterator[_Registration]:
    registration = _Registration()
    token = _current.set(registration)
    try:
        yield registration
    finally:
        _current.reset(token)


def _registry() -> _Registration:
    registry = _current.get()
    if registry is None:
        raise RuntimeError("recipe registration requires memento.collect()")
    return registry


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("recipe times must include a timezone")
    return value


def at(value: str, *, tz: str | None = None) -> datetime:
    """Parse an ISO local time with an IANA zone, or an explicit UTC offset.

    Naive times require ``tz``. DST gaps and ambiguous wall times are rejected;
    use an explicit offset in ``value`` to specify an ambiguous instant.
    """
    if not isinstance(value, str) or not ("T" in value or " " in value):
        raise ValueError("at() needs a date and time, e.g. 2026-09-28 08:05")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid date/time: {value!r}") from exc
    if parsed.tzinfo is not None:
        if tz is not None:
            raise ValueError("use an explicit UTC offset or tz, not both")
        return parsed
    if tz is None:
        raise ValueError("a local time requires tz, e.g. America/Los_Angeles")
    try:
        zone = ZoneInfo(tz)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"unknown timezone: {tz!r}") from exc
    candidate = parsed.replace(tzinfo=zone, fold=0)
    round_trip = candidate.astimezone(timezone.utc).astimezone(zone)
    if round_trip.replace(tzinfo=None) != parsed:
        raise ValueError("this local time does not exist due to a DST change")
    if candidate.utcoffset() != parsed.replace(tzinfo=zone, fold=1).utcoffset():
        raise ValueError("this local time is ambiguous; use an explicit offset")
    return candidate


def _duration(value: float, unit: str) -> timedelta:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("durations require finite numbers")
    if not math.isfinite(value) or abs(value) > 365_000:
        raise ValueError("duration is outside the supported range")
    return timedelta(**{unit: value})


def hours(value: float) -> timedelta:
    return _duration(value, "hours")


def minutes(value: float) -> timedelta:
    return _duration(value, "minutes")


def days(value: float) -> timedelta:
    return _duration(value, "days")


def remind(when: datetime, text: str) -> None:
    registry = _registry()
    registry.check_size()
    registry.reminders.append(Reminder(at=when, text=text))


def on(
    source: _Source,
    *,
    when: Question,
    do: Action | list[Action] | tuple[Action, ...],
    threshold: float = 0.8,
    match: str | None = None,
    **filters: Any,
) -> None:
    registry = _registry()
    registry.check_size()
    registry.triggers.append(
        EventTrigger(
            source=source,
            question=when,
            actions=list(do) if isinstance(do, (list, tuple)) else [do],
            threshold=threshold,
            match=match,
            filters=filters,
        )
    )


def expires(when: datetime) -> None:
    registry = _registry()
    if registry.expires_at is not None:
        raise ValueError("a recipe can declare expiration only once")
    registry.expires_at = _aware(when)


def noul(
    question: str,
    *,
    true: str = "The condition is true.",
    false: str = "The condition is false.",
) -> Question:
    return Question(
        kind="noul", question=question, options={"true": true, "false": false}
    )


def choice(question: str, **options: str) -> Question:
    return Question(kind="choice", question=question, options=options)


def score(question: str, levels: list[str] | tuple[str, ...]) -> Question:
    return Question(kind="score", question=question, levels=list(levels))


def alert(text: str) -> Action:
    return Action(kind="alert", text=text)


def surface(target: _This) -> Action:
    if target is not this:
        raise ValueError("surface() requires this")
    return Action(kind="surface", target="this")


def rewrite(target: _This) -> Action:
    if target is not this:
        raise ValueError("rewrite() requires this")
    return Action(kind="rewrite", target="this")


RECIPE_EXPORTS = {
    name: globals()[name]
    for name in (
        "at", "hours", "minutes", "days", "remind", "on", "expires",
        "email", "calendar", "chat", "this", "noul", "choice", "score",
        "alert", "surface", "rewrite",
    )
}
