---
name: memento
description: Make a GBrain memory proactive by giving its page a `recipe:`, a small Python module that says when the memory should come back on its own. Use when the user shares a plan, deadline, promise, or something to watch for, or asks to be told when something happens.
triggers: ["remind me", "let me know when", "tell me if", "don't let me forget", "keep an eye out", "follow up"]
writes_pages: true
---

# Memento: memories that come back on their own

Keep writing ordinary GBrain pages. Memento runs next to the brain: every new
page without a recipe is read by River, which writes one if the memory should
come back. So you don't have to. Write a recipe yourself when the user is
explicit about *how* something should come back ("ping me the night before",
"tell me if Dan gets upset"). River leaves a recipe you write alone until the
page's facts change without it.

## Adding a recipe

1. `get_page` the page (or create it with `put_page` / `capture`).
2. Keep everything: body, title, tags, every frontmatter field.
3. Add `recipe: |` to the frontmatter with the module indented two spaces.
4. `put_page` it back with the `expected_revision` you read.

## The language

```python
from memento import at, when, notify, update, this, now
from memento import minutes, hours, days, weeks

flight = at("2026-09-28 08:05")          # a moment, user's timezone

@at(flight - hours(3))                   # run at a moment
def leave_for_the_airport():
    notify("Time to leave for SFO. UA123 departs at 8:05.")

@at("2026-10-04 18:00", every=weeks(1))  # repeat (until= optional)
def sunday_call():
    notify("Call Mom.")

@when("flight UA123 on Monday, September 28 is delayed or cancelled",
      unless="it only confirms the booking", until=flight)
def flight_changed(news):                # a NEW page reports the claim
    if news.says("flight UA123 was cancelled"):
        notify("UA123 was cancelled. Rebook now.")
    else:
        notify(f"UA123 changed: {news.title}")
    update(news)                         # revise this memory with the news
```

- `notify(text)` pushes a notification. `update(news)` has River fold the news
  into the page's timeline and rewrite the recipe.
- On any page (`this`, or `news`): `says(claim, unless=)` → bool,
  `which(question, a="…", b="…")` → label, `rate(question, [levels])` → index.
  Jev, a fast decision model, answers them from that page alone.
- `fetch("https://…")` reads a public page or JSON API as a page (literal URLs
  only). Poll it with `@at(..., every=hours(6))` for things the user waits on.
- Claims are specific and self-contained: names, flight numbers, dates as
  written. One claim per `@when`. No date arithmetic in claims; do time math
  in Python. `unless=` names the near misses that must not fire.
- Only `from memento import ...`. No other imports, no `while`, no classes,
  no names starting with `_`.

## Check before saving

If the `memento` CLI is available, run `memento check page.md`. It loads the
recipe and lists its triggers, or names the line that's wrong.
