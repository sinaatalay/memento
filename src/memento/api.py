"""The recipe language: everything a recipe can import from `memento`.

A recipe is a small Python module in a memory page's frontmatter. It says when
the memory should come back on its own. Two decorators bind a function to the
world, and the function decides what to do:

    from memento import at, when, notify, update, hours

    flight = at("2026-09-28 08:05")

    @at(flight - hours(3))
    def leave_for_the_airport():
        notify("UA123 to JFK leaves at 8:05. Head to SFO now.")

    @when("flight UA123 is delayed, rescheduled or cancelled", until=flight)
    def flight_changed(news):
        notify(f"UA123 changed: {news.title}")
        update(news)

Time is plain Python. Meaning is Jev: a `when` claim, and `says`, `which` and
`rate` on any page, are questions a decision model answers in ~100 ms. Handlers
only record effects (`notify`, `update`); the runtime carries them out.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class RecipeError(ValueError):
    """A recipe that can't run. Messages are written for whoever wrote the recipe."""


def local_timezone() -> ZoneInfo:
    if name := os.environ.get("MEMENTO_TZ"):
        return ZoneInfo(name)
    link = Path("/etc/localtime")
    if link.is_symlink() and "zoneinfo/" in (target := str(link.resolve())):
        return ZoneInfo(target.split("zoneinfo/", 1)[1])
    return ZoneInfo("UTC")


TZ = local_timezone()


# ---- questions Jev answers -------------------------------------------------


@dataclass(frozen=True)
class Says:
    """Does the page report that `claim` is true? (yes/no)"""

    claim: str
    unless: str | None = None


@dataclass(frozen=True)
class Which:
    """Which one of `options` fits the page? (pick one)"""

    question: str
    options: tuple[tuple[str, str], ...]  # (label, description)


@dataclass(frozen=True)
class Rate:
    """Where does the page sit on the scale `levels`? (0 .. len(levels)-1)"""

    question: str
    levels: tuple[str, ...]


Question = Says | Which | Rate


# ---- pages -----------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    """A GBrain page: this memory, or the news that just arrived."""

    slug: str
    title: str
    text: str
    type: str = ""
    source: str = "default"

    def says(self, claim: str, *, unless: str | None = None) -> bool:
        """Does this page report that `claim` is true?

        `unless` names near misses that must not count, e.g. plans vs. done.
        """
        return bool(_ask(self, Says(_text(claim, "claim"), _optional(unless, "unless"))))

    def which(self, question: str, *options: str, **described: str) -> str:
        """Which option fits this page? Options are labels, or label="description"."""
        pairs = [(o, o) for o in options] + list(described.items())
        if len(pairs) < 2:
            raise RecipeError("which(): give at least two options")
        labels = [label for label, _ in pairs]
        if len(set(labels)) != len(labels):
            raise RecipeError("which(): options must be different")
        return str(_ask(self, Which(_text(question, "question"), tuple(pairs))))

    def rate(self, question: str, levels: list[str] | tuple[str, ...]) -> int:
        """Rate this page on a scale; returns the level's index, 0 .. len(levels)-1."""
        if not 2 <= len(levels) <= 10:
            raise RecipeError("rate(): give between 2 and 10 levels, lowest first")
        return int(_ask(self, Rate(_text(question, "question"), tuple(map(str, levels)))))

    def __str__(self) -> str:
        return self.title


class _This:
    """`this`: the memory the recipe lives in."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(_context().this, name)

    def __repr__(self) -> str:
        return "this"

    def __str__(self) -> str:
        return str(_context().this)


this: Page = _This()  # type: ignore[assignment]


# ---- time ------------------------------------------------------------------


class Moment(datetime):
    """A point in time in the user's timezone. Decorate a function with it to run it then."""

    def __call__(self, fn: Callable) -> Callable:
        return _register(Trigger(kind="at", fn=fn, at=self))


def _moment(value: str | datetime, what: str) -> Moment:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError:
            raise RecipeError(
                f'{what}: write the time as "YYYY-MM-DD HH:MM", not {value!r}'
            ) from None
    if not isinstance(value, datetime):
        raise RecipeError(f'{what}: expected a time like at("2026-09-28 08:05"), got {value!r}')
    if value.tzinfo is None:
        value = value.replace(tzinfo=TZ)
    return Moment.fromtimestamp(value.timestamp(), value.tzinfo)


def at(
    when: str | datetime, *, every: timedelta | None = None, until: str | datetime | None = None
):
    """A moment: `at("2026-09-28 08:05")`, in the user's timezone.

    As a decorator it runs the function at that moment: `@at(flight - hours(3))`.
    With `every=` it repeats (`@at("2026-10-04 18:00", every=weeks(1))`),
    optionally `until=` a last moment.
    """
    moment = _moment(when, "at()")
    if every is None and until is None:
        return moment
    if every is None:
        raise RecipeError("at(..., until=...) only makes sense with every=, e.g. every=days(1)")
    if not isinstance(every, timedelta) or every < timedelta(minutes=1):
        raise RecipeError("at(..., every=...): repeat at most once a minute, e.g. every=days(1)")
    end = _moment(until, "at(..., until=...)") if until is not None else None

    def decorate(fn: Callable) -> Callable:
        return _register(Trigger(kind="at", fn=fn, at=moment, every=every, until=end))

    return decorate


def now() -> Moment:
    """The current time (the runtime's clock, so demos can travel in time)."""
    return _moment(_context().now, "now()")


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)


def hours(n: float) -> timedelta:
    return timedelta(hours=n)


def days(n: float) -> timedelta:
    return timedelta(days=n)


def weeks(n: float) -> timedelta:
    return timedelta(weeks=n)


# ---- meaning ---------------------------------------------------------------


def when(claim: str, *, unless: str | None = None, until: str | datetime | None = None):
    """Run the function when a new page in the brain reports that `claim` is true.

    The function may take the page that arrived: `def changed(news): ...`.
    `unless` names near misses that must not fire; `until` stops watching.
    """
    claim = _text(claim, "when()")
    unless = _optional(unless, "when(..., unless=...)")
    end = _moment(until, "when(..., until=...)") if until is not None else None

    def decorate(fn: Callable) -> Callable:
        return _register(Trigger(kind="when", fn=fn, claim=claim, unless=unless, until=end))

    return decorate


# ---- effects ---------------------------------------------------------------


@dataclass(frozen=True)
class Effect:
    kind: str  # "notify" | "update"
    text: str = ""
    news: Page | None = None


def notify(text: str) -> None:
    """Send the user a notification now."""
    _effects().append(Effect("notify", text=_text(str(text), "notify()")))


def fetch(url: str) -> Page:
    """Read a web page or JSON API now, as a page: `fetch("https://...")`.

    The URL must be written literally in the recipe, so a recipe can only look
    at the places it names, never send your memories anywhere.
    """
    ctx = _context()
    if ctx.fetch is None:
        raise RecipeError("fetch() only works inside an @at or @when function")
    return ctx.fetch(_text(url, "fetch()"))


def update(news: Page | None = None) -> None:
    """Revise this memory (its text and its recipe) in light of `news`."""
    if news is not None and not isinstance(news, Page):
        raise RecipeError("update(): pass the page the handler received, or nothing")
    _effects().append(Effect("update", news=news))


# ---- runtime plumbing (not part of the language) ---------------------------


@dataclass
class Trigger:
    kind: str  # "at" | "when"
    fn: Callable
    at: datetime | None = None
    every: timedelta | None = None
    until: datetime | None = None
    claim: str | None = None
    unless: str | None = None

    @property
    def name(self) -> str:
        return getattr(self.fn, "__name__", "handler")

    @property
    def question(self) -> Says:
        assert self.claim is not None
        return Says(self.claim, self.unless)

    def signature(self) -> str:
        """What the trigger is, independent of its position in the recipe."""
        if self.kind == "at":
            spec = self.at.isoformat() if self.at else ""
            if self.every:
                spec += f" every {self.every}"
        else:
            spec = f"{self.claim} | unless {self.unless}"
        if self.until:
            spec += f" until {self.until.isoformat()}"
        return f"{self.kind}:{self.name}:{spec}"


@dataclass
class Context:
    this: Page
    now: datetime
    triggers: list[Trigger] | None = None  # collecting: the recipe is being loaded
    effects: list[Effect] | None = None  # running: a handler is being called
    ask: Callable[[Page, Question], Any] | None = None
    fetch: Callable[[str], Page] | None = None
    asked: list[tuple[Page, Question, Any]] = field(default_factory=list)


_current: ContextVar[Context | None] = ContextVar("memento_context", default=None)


def _context() -> Context:
    ctx = _current.get()
    if ctx is None:
        raise RecipeError("this only works inside a recipe run by the memento runtime")
    return ctx


def _register(trigger: Trigger) -> Callable:
    ctx = _context()
    if ctx.triggers is None:
        raise RecipeError(
            "@at and @when belong at the top level of the recipe, not inside a handler"
        )
    if not callable(trigger.fn):
        raise RecipeError(f"@{trigger.kind} decorates a function (def ...:)")
    ctx.triggers.append(trigger)
    return trigger.fn


def _effects() -> list[Effect]:
    ctx = _context()
    if ctx.effects is None:
        raise RecipeError("notify() and update() only work inside an @at or @when function")
    return ctx.effects


def _ask(page: Page, question: Question) -> Any:
    ctx = _context()
    if ctx.ask is None:
        raise RecipeError("says(), which() and rate() only work inside an @at or @when function")
    answer = ctx.ask(page, question)
    ctx.asked.append((page, question, answer))
    return answer


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecipeError(f"{what}: expected non-empty text")
    return value.strip()


def _optional(value: object, what: str) -> str | None:
    return None if value is None else _text(value, what)


__all__ = [
    "Page",
    "RecipeError",
    "at",
    "days",
    "fetch",
    "hours",
    "minutes",
    "notify",
    "now",
    "this",
    "update",
    "weeks",
    "when",
]
