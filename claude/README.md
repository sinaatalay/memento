# memento

**Proactive memory for GBrain: memories that know when they matter.**

GBrain has been adding proactivity one hard-coded feature at a time: who's waiting
on you (open loops), names mentioned in chat (push context), bias nudges on takes.
Memento turns it into a language. Any ordinary GBrain page can carry a `recipe:`,
a small Python module that declares when that memory matters. A runtime next to
GBrain checks every recipe against everything that happens, in one Jev call per
event (~150 ms for all memories). It fires reminders on time, and it rewrites the
page when the world changes it. The LLM only writes; firing never needs one.

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

Measured on real Gmail: "SOC 2 audit: fieldwork scheduled for Oct 12" scored 0.01 and
stayed quiet. "Your SOC 2 Type I report is ready" scored 0.98 in a 142 ms call: the
alert fired, and Memento appended a dated update to the page. It kept every word,
tag and field, and moved the recipe on to watching for Dan's approval.

## What it is

- **A language** (`src/memento/dsl.py`): `at`, `hours`, `remind`, `on`, `expires`;
  questions `noul` / `choice` / `score` answered by Jev; actions `alert`, `surface`,
  `rewrite`. Time math is Python; judgment is Jev; writing is the LLM.
- **A runtime next to GBrain** (`engine.py`): watches every page in the brain
  (the `mem` source and GBrain's default source) for a `recipe:`. It checks each
  new email, calendar event or chat message against all active triggers in one Jev
  request, and fires due reminders. Alerts are macOS notifications.
- **A skill** (`skills/memento/SKILL.md`) that teaches any agent writing GBrain
  pages (Claude Code, Codex, ChatGPT over GBrain's MCP) to add recipes.
- **Automatic capture from email**: GBrain's Gmail/Calendar connector turns mail
  into pages. One extra Jev question per email asks "worth remembering?" (the
  Substack newsletter scored 0.02, a flight confirmation 0.97), and only then
  does the River writer draft a memory and recipe.
- **A console** (`static/index.html`): an x-ray of the extension for the demo
  (every Jev call with each memory's probability, what fired, the recipes). It's
  not the product; Memento works without it.

| Module | Role |
|---|---|
| `collect.py` | Lints and runs a recipe in a subprocess (only `memento` importable, restricted builtins, lines <= 78 so GBrain keeps the recipe a literal YAML block) and returns typed triggers |
| `jev.py` | All judgments, via Pydantic AI `TypeSafeModel.decide`, batched per event |
| `writer.py` | Pydantic AI agent on River; its output validator runs the recipe and returns errors via `ModelRetry`; falls back to the smaller River model after 25 s |
| `river.py` | River base models behind a local OpenAI-compatible endpoint for Pydantic AI |
| `gbrain.py` | Vanilla GBrain: `gbrain sync`, `gbrain put`, pages read from its markdown files |
| `gmail.py` | Sends demo emails from the connected account to itself (separate `gmail.send` token) |

Rewrites respect ownership. A page Memento wrote from an email is rewritten whole.
A page you or your agent wrote keeps every word and field, and gets a dated
"Update (memento)" section plus a new recipe.

## Run

```bash
uv sync
scripts/reset_demo.sh      # fresh run: server, the Acme page, 9 seed emails via real Gmail
open http://127.0.0.1:8765
uv run pytest -q
```

Keys are read from `../.context/.env` (`TYPESAFE_API_KEY`, `RIVER_API_KEY`,
`GOOGLE_CLIENT_JSON`). GBrain lives in `/Users/sina/memento-gbrain` (isolated `HOME`,
its own git repos), with Gmail and Calendar connected through `gbrain google setup`.

## Demo (3 minutes)

1. **Any page can be proactive.** Show the Acme page: ordinary GBrain page, plus `recipe:`.
2. **Precision.** Click *SOC 2 planned* (real Gmail). One Jev call over every memory:
   Acme stays at ~0.01. Click *SOC 2 issued*. Acme jumps to ~0.98, the Mac notification
   pops ("Reached you"), and the page appends the update and moves its recipe on.
3. **Email becomes memory.** Click *flight*. The gate says "worth remembering", and
   River writes a GBrain page with a recipe (reminders, a change watch, a chat surface).
4. **The world changes.** Click *change*. Only the flight's trigger fires. It alerts,
   and the memory rewrites itself to 10:40 with its reminders re-timed.
5. **Time.** Click *+1 day*. The reminders reach you.
6. **Close.** Every judgment was one Jev call over all memories, ~150 ms, fractions of a
   cent. The LLM only wrote. Any agent on GBrain can write these pages (the skill).

Don't claim: ChatGPT live (it needs GBrain exposed over public MCP), phone alerts
(macOS notifications only), or catching all airline mail (GBrain's Gmail sync skips
threads whose senders are all automated; demo emails are sent from the account itself).
