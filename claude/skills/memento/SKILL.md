---
name: memento
description: Make a GBrain memory proactive. Use when the user asks you to remember something that should come back on its own later, like a date, a deadline, a promise, or "tell me when X happens".
triggers: ["remind me", "let me know when", "tell me when", "don't let me forget", "follow up when", "remember that"]
tools: [get_page, put_page]
mutating: true
---

# Memento: memories that know when they matter

Keep writing ordinary GBrain pages. To make one proactive, add a `recipe:` field
to its frontmatter: a small Python module in the memento language that declares
when the memory matters. The Memento runtime next to GBrain picks the page up
within a second. It checks every new email, calendar event and chat message
against every recipe in one Jev call, and it fires reminders on time. You don't
need to keep a chat open.

## How to add a recipe

1. `get_page` the page you are updating, or pick a slug for a new one.
2. Keep the page as it is: title, body, tags and every other frontmatter field.
3. Add `recipe: |` as the last frontmatter field, with the module indented by
   2 spaces. Keep every line at 78 characters or fewer, so GBrain stores it as
   a literal block and the file still reads as Python.
4. `put_page` the whole page back.

Only write a recipe when the user wants something to happen later. A plain fact
or preference stays a plain page.

## The language

```python
from memento import at, minutes, hours, days
from memento import remind, on, expires, email, calendar, chat
from memento import noul, choice, score, alert, surface, rewrite, this
```

- `at("YYYY-MM-DD HH:MM")` is a moment in the user's timezone. `minutes(n)`,
  `hours(n)` and `days(n)` are durations. All date math is plain Python.
- `remind(when, "text")` notifies the user at `when`.
- `on(source, when=QUESTION, do=ACTION or [ACTIONS], threshold=0.7)` watches
  `email`, `calendar` or `chat`, and runs the actions when the question holds.
- `expires(when)` makes the memory go quiet afterwards.
- A QUESTION is judged by Jev, a fast decision model that reads literally:
  - `noul("Does `email` ...?", yes="...", no="...")` is yes/no.
  - `choice("...", fire_on="a", a="...", b="...")` picks one option.
  - `score("...", ["level 0", "level 1", ...], at_least=2)` rates on a rubric.

  The question must name the item in backticks: `email`, `event` for calendar,
  or `message` for chat. Name exact identifiers (flight number, person,
  company, date). Never ask Jev to do date arithmetic.
- ACTIONS:
  - `alert("text")` notifies the user now.
  - `surface(this)` brings the memory into the conversation.
  - `rewrite(this)` updates the page with what changed. On a page you wrote,
    Memento keeps every word and appends a dated update.

## Example

A project page an agent already keeps, made proactive:

```markdown
---
type: project
title: Acme pilot
tags: [acme, security, follow-up]
recipe: |
  from memento import on, email, noul, alert, rewrite, this

  on(
      email,
      when=noul(
          "Does `email` say our SOC 2 report has been issued?",
          yes="The report is finished and can be shared now.",
          no="A plan, a schedule, fieldwork, or anything not done.",
      ),
      do=[alert("SOC 2 is out. Send it to Dan at Acme today."), rewrite(this)],
  )
---
# Acme pilot

Dan Kim (Acme security) needs our SOC 2 report before he can approve the pilot.
I promised to send it the day it's issued.
```

An email saying the audit is *scheduled* stays quiet. The email saying the
report was *issued* sends the alert and appends the news to this page.
