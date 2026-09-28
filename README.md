# Memento

**Proactive memory for [GBrain](https://github.com/garrytan/gbrain).** Your AI
remembers everything you tell it. Memento makes those memories come back on
their own: at the right moment, when the world changes, when the right person
shows up.

GBrain is the memory layer of AI agents: every memory is a markdown page, and
an agent pulls the relevant ones into context while you talk to it. That's
recall, and it only happens while you're talking. If you have a flight on
Monday and don't open a chat until Monday, nothing reminds you.

Memento adds one optional field to any GBrain page: `recipe:`, a small Python
module that says when that memory should come back. You never write it.
**River** reads each new memory and writes its recipe. **Jev** checks every
new page in the brain against every recipe in one request of 100–250 ms.
GBrain itself is unmodified.

```markdown
---
type: event
title: Flight to NYC
recipe: |
  from memento import at, when, notify, update, hours

  flight = at("2026-09-28 08:05")

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
---

# Flight to NYC

Flying UA123 SFO to JFK on Monday, September 28, departing 8:05am. Seat 14C.
```

When United's email lands in the brain, Jev scores it 0.98 against the claim
and `flight_changed` runs. You get the notification, and `update(news)` has
River add a line to the page's GBrain timeline ("UA123 now departs 10:40, not
8:05") and rewrite the recipe. The airport reminder moves to 7:40.

## How it works

```
you ⇄ your AI (Claude, ChatGPT, …) ── MCP ──▶ GBrain: markdown pages
                                                    │ a page is new or changed
                                                    ▼
                                             memento run
   Jev    one request per new page: which memories' @when claims does it meet?
          no recipe yet? is it worth one?                           (~100 ms)
   River  writes a recipe for a memory that deserves one; revises a memory
          when news calls update()                                  (~3 s)
   clock  @at moments come due
                           │ notify()                   │ update()
                           ▼                            ▼
                 macOS banner / phone push     GBrain timeline + new recipe
```

Each part does what it's good at:

- **Time is Python.** Jev is unreliable at date arithmetic, so recipes compute
  moments in code (`flight - hours(3)`).
- **Meaning is Jev.** "Delayed, rescheduled or cancelled, unless it only
  confirms the booking" is a yes/no question a decision model answers from the
  page alone. All memories' questions about one page go out in one request.
- **Writing is River.** A language model writes code only when a memory is
  created or changes, never on the hot path.
- **The memory is the state.** A recipe lives in its page. You can read it,
  edit it, version it, and any agent on the brain can see it. When the world
  changes, the page and its recipe change together.

## The language

Everything a recipe can import from `memento`:

| | |
|---|---|
| `at("2026-09-28 08:05")` | a moment, in your timezone; plus or minus `seconds`/`minutes`/`hours`/`days`/`weeks` |
| `@at(moment)` | run the function at that moment |
| `@at(moment, every=weeks(1), until=…)` | …and repeat |
| `@when("claim", unless="near misses", until=moment)` | run it when a *new page* in the brain (an email, a note, a chat memory) reports the claim; the function may take that page |
| `notify(text)` | push a notification now |
| `update(news)` | revise this memory with the news: a timeline line and a new recipe |
| `this` | this memory, as a page |
| `page.says(claim, unless=…)` | yes/no, from Jev |
| `page.which(question, a="…", b="…")` | pick one, from Jev |
| `page.rate(question, [levels])` | a level on a scale, from Jev |
| `now()` | the runtime's clock |
| `fetch("https://…")` | read a public web page or JSON API now, as a page; the URL must be written literally, so a recipe can look but never send your data anywhere |

Handlers are ordinary Python, so semantic answers compose with control flow:

```python
@when("Dan Kim at Acme writes about the pilot")
def dan_writes(news):
    mood = news.rate("How frustrated is Dan?", ["calm", "impatient", "escalating"])
    if mood == 2:
        notify("Dan at Acme is escalating about the pilot. Call him today.")

@at(due - hours(8))
def send_the_deck():
    if not this.says("the investor deck was already sent to Priya"):
        notify("Send Priya the investor deck today.")
```

Recipes are written from untrusted input (an email can say anything), so they
run under an allow-list: only `from memento import …`, no private names or
attributes, no `while`, restricted builtins, bounded `range`. Handlers only
record effects; the runtime carries them out.

## What it's for

Reminders are the least of it. [examples/](examples/) has thirteen memories;
the interesting ones are memories that wait for the world:

| memory | shape | what comes back, and when |
|---|---|---|
| [Priya is hiring](examples/priya-hiring.md) | a need that news can meet | "Intro for Priya?", the day you write down coffee with a designer who's leaving Figma |
| [Stay on one Postgres](examples/decision-postgres.md) | a decision with tripwires | "Revisit the decision", when an incident review shows 7.2k writes/sec or a customer demands EU residency |
| [Competitor: Linear](examples/battlecard-linear.md) | a belief that can go stale | "Our agency wedge is gone", the day Linear ships time tracking; not for their Series C, a forum request, or Jira |
| [The March outage](examples/lesson-migrations.md) | a lesson waiting for its situation | "Move it to Tuesday", when someone plans a Friday migration before a launch |
| [MacBook Pro](examples/applecare.md) | coverage that matters when things break | "Don't pay for the repair", when you mention the coffee on the keyboard |
| [Northwind early exercise](examples/83b-election.md) | a deadline that starts when something happens | when Carta confirms the exercise, River turns "within 30 days" into dated reminders for Oct 29 that stop once the 83(b) is mailed |
| [Churn hypothesis](examples/churn-hypothesis.md) | a hypothesis collecting evidence | every churn interview lands on the page's timeline; `which()` flags the ones that blame price |
| [Maria Santos](examples/owed-intro.md) | a promise owed to you | a nudge on Friday only if her intro hasn't arrived |

River recognizes these kinds on its own: given only each memory's plain text,
it wrote recipes like these (the churn one is rewritten by hand to show
`which()`).

## Measured

Live Jev (`jev-1.13.0`) against the example claims: every match fired and
every near miss stayed quiet. A claim fires at P ≥ 0.6; across 33 checks the
near misses scored at most 0.04 and the matches 0.72–0.99. A few of them:

| memory | news | P(claim) |
|---|---|---:|
| Flight to NYC | "UA 123 on Monday, September 28 now departs at 10:40 AM instead of 8:05" | 0.98 |
| | "Your trip is confirmed: UA123 SFO to JFK, Monday Sep 28" | 0.02 |
| | "Flight DL400 to Boston on Monday is delayed" | 0.01 |
| Priya is hiring a designer | "Alex Chen: senior product designer at Figma, leaving, wants a founding role" | 0.97 |
| | "Figma is hiring a senior product designer" | 0.04 |
| Competitor: Linear | "Linear changelog: Introducing Time Tracking" | 0.93 |
| | "Forum thread: Linear users ask when time tracking is coming" | 0.02 |
| Northwind early exercise | "Your exercise of 40,000 Northwind options was completed" | 0.89 |
| | "Your exercise request … is pending board approval" | 0.02 |
| Stay on one Postgres | "procurement says they can only sign if all customer data stays in the EU" | 0.72 |
| | "they asked for our SOC 2 report and a security questionnaire" | 0.02 |
| MacBook Pro | "Spilled coffee on my MacBook keyboard" | 0.80 |
| | "Cracked my iPad screen on the train" | 0.01 |

Whether a new page deserves a recipe at all is three narrow Jev questions
(time, knowledge, coverage), cut at 0.5. Nine use cases like these scored
0.66–0.97; groceries, a book note, a preference, a finished appointment and a
standup scored at most 0.18. A plain vendor fact ("Stripe charges 2.9% + 30c")
came closest at 0.46; River gets a second chance to answer NONE.

Each single question took 70–130 ms. Every watch in the examples plus the
worth-a-recipe question, eight in all, took 80–90 ms together in one request.
River (`DeepSeek-V4.1-Flash`) usually writes a recipe in 2–4 s (the slowest
observed was 13 s). Every recipe is loaded and checked before it's saved, and
errors go back to River to fix.

## Run it

You need [GBrain](https://github.com/garrytan/gbrain) (`bun install -g github:garrytan/gbrain#latest-stable`),
[uv](https://docs.astral.sh/uv/), a TypeSafe key for Jev and a River key.
Memento reads `~/.memento/.env`:

```sh
TYPESAFE_API_KEY=…
RIVER_API_KEY=…
GBRAIN_HOME=/Users/you/.memento/brain   # the brain to watch; unset = ~/.gbrain
MEMENTO_GBRAIN=gbrain                   # how to run GBrain's CLI
MEMENTO_NTFY=https://ntfy.sh/your-topic # optional: phone pushes via ntfy
```

```sh
uv sync
uv run memento run        # watch the brain; leave it running
uv run memento ls         # proactive memories and what they wait for
uv run memento show SLUG  # a memory's recipe
uv run memento check examples/flight.md
uv run memento time "mon 7:45"   # move the clock, for demos (reset to undo)
uv run pytest             # with TYPESAFE_API_KEY set, also checks claims against live Jev
```

Connect your AI to the same brain as usual, e.g. `claude mcp add gbrain -- gbrain serve`.
Memento reads pages from GBrain's content root and writes through GBrain's own
`put_page` and `add_timeline_entry`, which reach a running `gbrain serve`, so the
two share the brain safely. On macOS, allow notifications for Script Editor
(System Settings → Notifications) to see the banners.

### The demo

```sh
uv run scripts/demo.py stage   # fresh brain + two memories from "weeks ago"
uv run memento run             # terminal 1: the live log
uv run scripts/demo.py chat    # terminal 2: Claude Code, with GBrain as its memory
```

Every beat happens with the chat closed:

1. Tell Claude: *"We can't ship the iOS release until sinaatalay/memento#1 is
   fixed. Ship the moment it's fixed."* River writes a recipe that `fetch`es
   the issue every few hours. Close the chat.
2. Close the GitHub issue as completed. With `MEMENTO_DEMO_POLL=10` in
   `~/.memento/.env`, repeating checks run every 10 seconds, so within ten
   seconds Memento reads the real issue: "memento#1 is fixed. Your iOS release
   is unblocked." (Don't ask Claude to check often: it will try to poll the
   issue itself, inside the chat.)
3. An email lands: `scripts/demo.py email "Jen Alvarez (Prescient Assurance)"
   "Your final SOC 2 Type I report" "…"`. The weeks-old promise fires: "SOC 2
   is in. Send it to Dan at Acme today: it unblocks the $18k pilot."
4. `memento time "fri 17:05"`. Nothing arrived: "Friday 5pm and no term sheet
   from Northwind. Call Maya."

Reopen the issue and run `stage` again to rehearse. `scripts/demo.py play`
walks the same story with a pause for closing the issue.

## Layout

```
src/memento/
  api.py       the language: at, when, notify, update, this, says/which/rate
  recipe.py    lint, sandboxed load into triggers, run a handler into effects
  jev.py       every semantic question, batched per page
  river.py     writes and revises recipes, checked before they're accepted
  gbrain.py    pages from disk, writes through GBrain's put_page and timeline
  runtime.py   the loop: pages, news, timers, effects
  deliver.py   macOS banner and ntfy push
  state.py     what's been seen and fired; the demo clock
  cli.py       memento run | ls | show | check | write | time
skills/memento/SKILL.md   for agents that write recipes themselves
examples/                 six proactive memories
scripts/demo.py           the demo driver
```

## Limits

- Memento watches pages. A GBrain fact saved with `remember` about a person
  who has no page yet lives only in the database, so Memento doesn't see it.
  GBrain's `capture` and `put_page` both write pages.
- It watches the brain's `default` source.
- The recipe sandbox is an allow-list over Python in-process. It isn't an
  operating-system sandbox.
- Delivery is a macOS banner plus an optional ntfy push.
