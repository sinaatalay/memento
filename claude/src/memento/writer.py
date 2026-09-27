"""The writer: the only place an LLM runs.

It turns something new (an email, a calendar event, a chat message) into a
memory: a short note plus a recipe in the memento language. The recipe is run
before it's accepted; any error goes straight back to the model to fix.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from pydantic_ai import Agent, ModelRetry, RunContext

from . import river
from .collect import MAX_COLS, collect, fmt
from .dsl import RecipeError

API = f"""\
The memento language (a recipe may only use `from memento import ...`):

  at("YYYY-MM-DD HH:MM") -> datetime in the user's timezone (America/Los_Angeles)
  minutes(n), hours(n), days(n) -> timedelta   # all date math is plain Python

  remind(when, "text")          # notify the user at `when`
  on(source, when=QUESTION, do=ACTION or [ACTIONS], threshold=0.7)
      source: email | calendar | chat
  expires(when)                 # after this the memory goes quiet

  QUESTION, judged by a fast decision model that reads questions literally:
    noul("Does `email` ...?")                    # yes/no
    choice("Which ... `email` ...?", fire_on="a", a="...", b="...")
    score("How ... `message` ...?", ["level 0", "level 1", ...], at_least=2)
  The question MUST name the item in backticks: `email` for email,
  `event` for calendar, `message` for chat.

  ACTION:
    alert("text")      # notify the user now
    surface(this)      # bring this memory into the conversation
    rewrite(this)      # update this memory with the new information

Rules:
- Name exact identifiers in questions (flight number, date, person, company).
  Never ask the judge to compare or compute dates; put dates in the question
  as plain facts ("on Monday Sep 28") and do the math with at()/hours().
- Every line <= {MAX_COLS} characters, 4-space indents, no tabs.
- A good recipe usually has: remind(...) for each moment the user must act,
  on(email, ..., do=[alert(...), rewrite(this)]) for changes to the thing,
  on(chat, ..., do=surface(this)) when the user talks about related plans,
  and expires(...) once it no longer matters.
- Chat triggers catch ANY plans in the affected time window, not just the
  same topic: "Is `message` about plans on Monday Sep 28 before noon?",
  never "Is `message` about travel plans ...?".
- Skip reminders that would already be in the past (today is given below).
"""

EXAMPLE = '''\
# Dentist with Dr. Patel, Thu Oct 1, 3:30pm
- Cleaning at Mission Dental, 2100 Mission St
- Thursday Oct 1 at 3:30pm; rescheduling later than 24h before costs $50

```python
from memento import at, hours, days, remind, on, email, chat, noul
from memento import alert, surface, rewrite, expires, this

visit = at("2026-10-01 15:30")

remind(visit - days(1), "Dentist tomorrow 3:30pm, reschedule today if needed")
remind(visit - hours(1), "Leave for Mission Dental (Dr. Patel) now.")

on(
    email,
    when=noul("Does `email` move or cancel the Oct 1 dentist visit?"),
    do=[alert("Your dentist appointment changed"), rewrite(this)],
)
on(
    chat,
    when=noul("Is `message` about plans on Thursday Oct 1 afternoon?"),
    do=surface(this),
)
expires(visit + hours(2))
```'''

INSTRUCTIONS = f"""\
You maintain a user's proactive memory. Each memory is a short markdown note
plus a recipe: a Python module that declares WHEN the memory matters.

{API}
Output format, exactly:
# <title with the key facts>
<2-5 bullet lines of what to remember>

```python
<recipe>
```

Example:
{EXAMPLE}

If there is nothing the user will need to remember or act on later
(newsletters, receipts, promotions, notifications), output exactly: NOTHING
"""


@dataclass
class Draft:
    title: str
    body: str
    recipe: str


def parse(text: str) -> Draft | None:
    if text.strip().upper().startswith("NOTHING"):
        return None
    code = re.search(r"```python\n(.*?)```", text, re.S)
    title = re.search(r"^#\s+(.+)$", text, re.M)
    if not code or not title:
        raise ValueError("missing '# title' line or ```python recipe block")
    body = text[title.end() : code.start()].strip()
    return Draft(title=title.group(1).strip(), body=body, recipe=fmt(code.group(1)))


TIMEOUT = 25  # seconds before falling back to the faster River model
_agent: Agent[None, str] | None = None


def agent() -> Agent[None, str]:
    global _agent
    if _agent is None:
        _agent = Agent(river.model(river.MID), instructions=INSTRUCTIONS, output_type=str, retries=3)

        @_agent.output_validator
        async def check(ctx: RunContext[None], text: str) -> str:
            try:
                draft = parse(text)
                if draft is not None:
                    await asyncio.to_thread(collect, draft.recipe, memory="draft")
            except (ValueError, RecipeError) as e:
                raise ModelRetry(f"That does not work: {e}\nFix it and output the whole page again, same format.")
            return text

    return _agent


async def write(prompt: str) -> tuple[Draft | None, int]:
    """Returns the draft (None when nothing is worth remembering) and the number of model requests.

    River sometimes queues a request for a minute or more; past TIMEOUT the smaller model takes over.
    """
    try:
        result = await asyncio.wait_for(agent().run(prompt), TIMEOUT)
    except TimeoutError:
        result = await agent().run(prompt, model=river.model(river.FAST))
    return parse(result.output), result.usage.requests


def new_item_prompt(today: str, kind: str, item: str, existing: list[str]) -> str:
    known = "\n".join(f"- {t}" for t in existing[:40]) or "- (none)"
    return (
        f"Today is {today}.\n\nA new {kind} arrived:\n<{kind}>\n{item}\n</{kind}>\n\n"
        f"Memories that already exist (do not duplicate them):\n{known}\n\n"
        "Write the memory for it, or NOTHING."
    )


def rewrite_prompt(today: str, memory: str, kind: str, item: str) -> str:
    return (
        f"Today is {today}.\n\nThis memory:\n<memory>\n{memory}\n</memory>\n\n"
        f"Just received this {kind}, which changes it:\n<{kind}>\n{item}\n</{kind}>\n\n"
        "Rewrite the memory with the new facts: same format, update the note and every time "
        "in the recipe. Keep the same kinds of triggers."
    )
