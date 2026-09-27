"""River writes recipes: it reads a memory and decides how it should come back.

Two jobs. `write` gives a page its recipe (or decides it needs none). `revise`
folds news into a memory: a one-line note for its timeline, and a new recipe.
Every recipe is loaded before it's accepted; errors go back to the model.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from .api import TZ, Page, RecipeError
from .recipe import Recipe, load

MODEL = os.environ.get("MEMENTO_RIVER_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash")
REPAIRS = 2

LANGUAGE = '''\
A recipe is a small Python module stored in a memory page's frontmatter. It
says when the memory should come back to the user on their own, without them
asking. Two decorators bind functions to the world:

    from memento import at, when, notify, update, this, now
    from memento import minutes, hours, days, weeks

    at("2026-09-28 08:05")        # a moment, in the user's timezone
    @at(moment)                   # run the function at that moment
    @at(moment, every=weeks(1))   # ...and repeat (optional until=moment)
    @when("claim", unless="near misses", until=moment)
                                  # run it when a NEW page in the user's
                                  # brain (email, note, chat memory...)
                                  # reports that the claim is true
    def handler(news): ...        # a @when function may take that page

Inside a function:
    notify("text")                # push a notification to the user now
    update(news)                  # revise this memory with the news: a
                                  # timeline note and a new recipe
    this                          # this memory (a page)
    now()                         # the current time
    page.title, page.text         # any page: this, or news
    page.says("claim", unless=...)       -> bool   (yes/no)
    page.which("question", a="...", b="...") -> "a" | "b"   (pick one)
    page.rate("question", ["low", "mid", "high"]) -> 0..2  (scale)

Claims and questions are answered by Jev, a fast decision model that reads
only the page in front of it, literally. So:
- Make a claim specific and self-contained: names, flight numbers, companies,
  dates as written ("Monday, September 28"). Never "my flight" or "it".
- One claim per watch. Never ask Jev to do date or time arithmetic.
- Use unless= for the near misses that must not fire (a plan vs. done, a
  booking confirmation vs. a change, another person with the same name).
- Time math is plain Python: at(...) plus or minus minutes/hours/days/weeks.

Only `from memento import ...` is allowed; no other imports, no while loops,
no classes, no names starting with an underscore. Keep lines under 80 chars.
'''

PRINCIPLES = '''\
Good proactive memory is rare and precise. The user should be glad of every
notification: it arrives at the right moment, is specific, and says what to do.

When to give a page a recipe: a dated plan (trip, appointment, meeting,
dinner), a deadline or bill, a promise the user made or is owed, something to
watch for, a recurring habit the user asked for, or a need or opportunity
that future news might meet (someone is hiring, looking for an intro, waiting
on something). Most pages need nothing: facts, preferences, reference notes,
things already done or past. Then answer NONE.

Timing, as a thoughtful assistant would:
- Flight: ~3h before departure (leave for the airport); also the evening
  before for a morning flight. Meeting or appointment: 30-60 min before, or
  the evening before if it's early. Deadline: the morning of the due day,
  plus a day ahead for big ones. Birthday: a few days ahead (gift) and that
  morning. Bill: 2 days before it's due.
- Only schedule moments after now. One or two timers, not five.
- If a reminder is pointless once something is done, check it:
  `if not this.says("the deck was already sent to Priya"): notify(...)`.

Watching:
- Changes to the plan (delay, reschedule, cancellation, new time or place):
  notify and update(news). Use until= the event itself.
- Completion (sent, paid, done, confirmed): update(news) so the memory knows.
- People chasing or asking about a promise: notify.
- Needs and opportunities: watch for the news that would meet them.

Notification text: short, concrete, actionable. Name the thing and the time.
'''

EXAMPLES = '''\
Example page "Flight to NYC", now Sunday, September 27, 2026, 3:00 PM:
  UA123 SFO to JFK, Monday Sep 28, departs 8:05am. Seat 14C. Conf K7Q2LM.

```python
from memento import at, when, notify, update, hours

flight = at("2026-09-28 08:05")

@at(flight - hours(12))
def early_flight_tomorrow():
    notify("UA123 leaves SFO at 8:05 tomorrow. Early night, bag by the door.")

@at(flight - hours(3))
def leave_for_the_airport():
    notify("Time to leave for SFO. UA123 to JFK departs at 8:05.")

@when(
    "flight UA123 on Monday, September 28 is delayed, rescheduled or cancelled",
    unless="it only confirms the booking, the seat or offers an upgrade",
    until=flight,
)
def flight_changed(news):
    if news.says("flight UA123 was cancelled"):
        notify("UA123 was cancelled. Rebook now.")
    else:
        notify(f"UA123 changed: {news.title}")
    update(news)
```

Example page "Investor deck for Priya", now Monday, September 28, 2026, 9 AM:
  Priya Raman (Northwind VC) asked for our investor deck. I promised it by
  Wednesday end of day so she can share it with her partners on Thursday.

```python
from memento import at, when, notify, update, this, hours

due = at("2026-09-30 17:00")

@at(due - hours(8))
def send_the_deck():
    if not this.says("the investor deck was already sent to Priya"):
        notify("Send Priya the investor deck today. Her partners see it Thu.")

@when("the investor deck was sent to Priya Raman", until=due)
def deck_sent(news):
    update(news)

@when("Priya Raman asks about or chases the investor deck", until=due)
def priya_asks(news):
    notify("Priya is asking about the deck. You promised it by Wednesday.")
```

Example page "Priya is hiring", now Sunday, September 27, 2026:
  Priya is starting a company in SF and needs a founding product designer.

```python
from memento import when, notify

@when(
    "an experienced product designer is looking for a new role",
    unless="it is a job post, or the designer is not available",
)
def possible_intro(news):
    notify(f"Intro for Priya? \\"{news.title}\\" fits her founding designer role.")
```

Example page "Aisle seats": I prefer aisle seats on long flights.

NONE
'''

SYSTEM = f"""You write memento recipes: small Python modules that make a \
memory proactive.

{LANGUAGE}
{PRINCIPLES}
{EXAMPLES}
Answer with exactly one ```python block holding the whole recipe, or the single
word NONE when the page needs nothing. No other text."""

REVISE = """News just arrived for a memory. Write a one-line timeline note of \
what changed, then the memory's updated recipe: keep what still matters, re-time \
what moved, drop what's done or cancelled. Answer exactly:

NOTE: <one line, e.g. "UA123 now departs 10:40, not 8:05 (United email).">
```python
<the whole updated recipe>
```

or, if nothing proactive remains, NOTE: ... followed by NONE."""


def calendar(now: datetime) -> str:
    """The date context models get: now, and the next two weeks by weekday."""
    days_ = [(now + timedelta(days=i)).strftime("%A %B %-d") for i in range(1, 15)]
    return (
        f"Now: {now.strftime('%A, %B %-d, %Y, %-I:%M %p')} ({TZ.key}).\n"
        f"Coming days: {'; '.join(days_)}."
    )


def describe(page: Page) -> str:
    kind = f" ({page.type})" if page.type else ""
    return f'Page "{page.title}"{kind}, slug {page.slug}:\n{page.text.strip()[:6000]}'


_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


def parse(reply: str) -> tuple[str | None, str]:
    """(note, recipe) from a reply; the recipe is "" for NONE."""
    note = None
    if m := re.search(r"^\s*NOTE:\s*(.+)$", reply, re.M):
        note = m.group(1).strip()
    if m := _BLOCK.search(reply):
        return note, m.group(1).strip() + "\n"
    if re.search(r"\bNONE\b", reply):
        return note, ""
    raise RecipeError("answer with one ```python block, or NONE")


def check(source: str, memory: Page, now: datetime) -> Recipe:
    """Load a new recipe and hold it to the principles that can be checked."""
    recipe = load(source, memory, now)
    if not recipe.triggers:
        raise RecipeError("the recipe declares nothing; add @at or @when, or answer NONE")
    for trigger in recipe.timers.values():
        if trigger.every is None and trigger.at <= now:
            when = trigger.at.strftime("%a %b %-d %-I:%M %p")
            raise RecipeError(f"@at for {trigger.name}() is {when}, already past; only schedule the future")
    return recipe


@dataclass
class Draft:
    recipe: str  # "" means no recipe
    note: str | None
    seconds: float
    attempts: int


class River:
    def __init__(self, api_key: str | None = None, model: str = MODEL) -> None:
        os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")  # river_client imports it
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        import river_client

        key = api_key or os.environ.get("RIVER_API_KEY")
        if not key:
            raise RuntimeError("RIVER_API_KEY is not set")
        self.model = model
        self.client = river_client.Client(api_key=key, timeout=120, enable_retries=True)

    def _complete(self, messages: list[dict]) -> str:
        result = self.client.chat_complete(
            messages,
            base_model=self.model,
            temperature=0.2,
            max_tokens=2000,
            chat_template_kwargs={"enable_thinking": False},
        )
        if result.status_code != 200:
            raise RuntimeError(f"River {result.status_code}: {result.response_json[:300]}")
        import json

        return json.loads(result.response_json)["choices"][0]["message"]["content"] or ""

    def _draft(self, prompt: str, memory: Page, now: datetime, revising: bool) -> Draft:
        started = time.perf_counter()
        system = SYSTEM + ("\n\n" + REVISE if revising else "")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        for attempt in range(1, REPAIRS + 2):
            reply = self._complete(messages)
            try:
                note, source = parse(reply)
                if revising and not note:
                    raise RecipeError("start with NOTE: and one line saying what changed")
                if source:
                    check(source, memory, now)
                return Draft(source, note, time.perf_counter() - started, attempt)
            except RecipeError as e:
                if attempt > REPAIRS:
                    raise
                messages += [
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": f"That recipe doesn't run: {e}\nFix it and answer again."},
                ]
        raise AssertionError("unreachable")

    def write(
        self, page: Page, now: datetime, current: str | None = None, others: list[Page] = ()
    ) -> Draft:
        """A recipe for `page`, or "" if it needs none. `current` is its old recipe, if any.

        `others` are the brain's other proactive memories: news that one of them
        already covers (an email about a known flight) needs no recipe of its own.
        """
        prompt = f"{calendar(now)}\n\n{describe(page)}"
        if others:
            listed = "\n".join(f"- {o.title} ({o.slug})" for o in others[:60])
            prompt += (
                f"\n\nThe brain already has these proactive memories. If one of them "
                f"already covers this page, answer NONE:\n{listed}"
            )
        if current:
            prompt += (
                f"\n\nThe page changed. Its current recipe is below; answer with the "
                f"recipe it should have now.\n```python\n{current.strip()}\n```"
            )
        return self._draft(prompt, page, now, revising=False)

    def revise(self, memory: Page, recipe: str, news: Page | None, now: datetime) -> Draft:
        """Fold `news` into `memory`: a timeline note plus its updated recipe."""
        what = describe(news) if news else "(no page: time passed, and the recipe asked to update)"
        prompt = (
            f"{calendar(now)}\n\nThe memory:\n{describe(memory)}\n\n"
            f"Its recipe:\n```python\n{recipe.strip()}\n```\n\nThe news:\n{what}"
        )
        return self._draft(prompt, memory, now, revising=True)

    async def awrite(
        self, page: Page, now: datetime, current: str | None = None, others: list[Page] = ()
    ) -> Draft:
        return await asyncio.to_thread(self.write, page, now, current, others)

    async def arevise(self, memory: Page, recipe: str, news: Page | None, now: datetime) -> Draft:
        return await asyncio.to_thread(self.revise, memory, recipe, news, now)
