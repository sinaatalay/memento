---
name: memento
description: Add, inspect, or update proactive reminders and event watches on ordinary GBrain Markdown memory pages. Use when remembering a plan, commitment, or condition that should notify the owner later.
---

# Memento active memories

Keep using the user's existing assistant and GBrain memory. An optional Python
`recipe` in a page's YAML front matter gives that ordinary memory background
behavior. The Memento daemon executes registered watches even when no chat is
open. The dashboard is an inspector; it is not required for remembering or
notification delivery.

## Author the same memory, not a duplicate

Use GBrain's normal page tools. Read an existing page with `get_page` including
canonical content and revision. Preserve its slug, source, body sections, tags,
type, and unrelated front matter. Write the edited canonical Markdown through
`put_page` with the observed `expected_revision` and a stable UUID `request_id`.
A brand-new page omits `expected_revision`. Re-read and reconcile a conflict;
do not overwrite a newer page with an old draft.

The current daemon watches ordinary pages in the configured default source.
There is no required `memories/` directory. Direct file edits work when its
working-tree sync is enabled. Standalone GBrain fact rows are not yet watched:
use an ordinary page for a memory that needs a recipe, preserving provenance.
If only recording a preference or fact with no reason to interrupt, leave the
recipe absent or empty. An explicitly authored page does not need the Jev
"worth remembering" gate; that gate belongs to automatic event capture.

The agent writing the memory may write the recipe. Memento's River writer is an
optional capture/update path, not the exclusive way to create memories. Do not
make up a commitment or missing date merely to add behavior.

## Supported recipe language

Only declarative imports/calls from `memento` are accepted. No arbitrary Python
I/O, functions, loops, imports, or network calls.

```python
from memento import (
    at, minutes, hours, days, remind, expires,
    on, email, calendar, chat, noul, choice, score,
    alert, surface, rewrite, this,
)
```

- `at("2026-09-28 08:05", tz="America/Los_Angeles")` creates an aware time.
  Use the user's actual date/timezone, not this illustrative date.
- `remind(when, text)` schedules an owner notification. Calculate times with
  `hours`, `minutes`, and `days`; schedule future reminders only.
- `on(email, when=noul(question, true=positive, false=negative),
  do=[alert(text), rewrite(this)], threshold=0.8)` registers an event watch.
  Sources also include `calendar` and `chat`.
- `surface(this)` returns context locally; it is ordinary conversational recall,
  not the distinctive proactive behavior. Existing assistants use GBrain recall
  normally; no universal per-message chat hook is installed by this skill.
- `rewrite(this)` updates the same page after relevant evidence arrives.
  It requires Memento's configured writer even when another agent originally
  authored the page. Without that writer, have the owning agent update the page.
- `expires(when)` stops behavior after that instant; retain the underlying facts.
- Removing/emptying `recipe` stops behavior while keeping the memory. Deleting
  the page also stops its watches.

Ask one narrow semantic question per condition. Give explicit positive and
negative criteria and identify the relevant entity/booking. Separate departure
changes from cancellation: a booking may stay confirmed while its time changes.
Dates expressed differently can refer to the same booking. Do not ask Jev to do
clock arithmetic or infer confirmed completion from a proposal/question.
Choose a source that actually carries the expected evidence. Arbitrary GBrain
page changes do not currently become cross-memory semantic events; email,
Calendar, and explicitly forwarded chat do. Page changes re-register their own
recipe automatically.

Example conditional memory (the rest of the page remains ordinary Markdown):

```yaml
---
title: Acme pilot follow-up
type: project
tags: [acme]
recipe: |
  from memento import on, email, noul, alert, rewrite, this
  on(
      email,
      when=noul(
          "Does this message confirm our SSO feature is now available?",
          true="An actual release announcement says SSO has shipped.",
          false="A plan, feature request, or discussion of a future release.",
      ),
      do=[alert("SSO shipped. Follow up with Maya at Acme."), rewrite(this)],
  )
---
Maya said Acme can start its pilot when SSO is available. I promised an update.
```

When rewriting after a fulfilled condition, remove that completed watch. Keep
other unfinished obligations and source history. A request to cancel a booking
is not evidence the booking was canceled. A notification to the owner is not a
message to the customer.

If the local Memento CLI is available, validate the page with
`memento collect /path/to/page.md` before saving and confirm the daemon registered
its recipe. Otherwise report validation as unverified. Sending is limited to
the configured owner address; the recipe cannot choose another recipient.

## Connection boundary

A local PGLite brain needs one database owner. When using a GBrain MCP server,
point Memento and all agent clients at the same HTTP server; do not launch a
second stdio server against the same directory. ChatGPT requires GBrain's
separately configured public HTTPS/OAuth path; this skill does not publish the
brain or install a ChatGPT connection. The local machine and daemon must stay
running for reminders to arrive.
