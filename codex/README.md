# Memento

Memories that know when to come back. A local, working research prototype for
the Own Your Intelligence hackathon.

Memento adds proactive behavior to ordinary GBrain Markdown pages. The existing
assistant, a person, or an ingestion pipeline can write a memory. An optional
Python recipe in its YAML front matter registers reminders and semantic event
conditions. Jev evaluates matching conditions; River handles optional automatic
capture and generative updates. Both model paths run through PydanticAI.
There is no OpenRouter dependency.

Keep using your existing assistant. The local web page is an inspector and demo
console, not a required chat destination. See the [product decisions](research/product.md)
and the portable [agent authoring skill](skills/memento/SKILL.md).

## Run locally

```sh
cd codex
uv sync --python 3.13
uv run memento doctor
uv run memento serve --port 8877
```

Open <http://127.0.0.1:8877>. The default private credentials file is
`../.context/memento/credentials.env`, and private runtime data lives in
`../.context/memento/runtime/`. Both are ignored by Git. See `.env.example`
for available settings; `memento --env-file /path/to/private.env serve` selects
another credentials file.

The local GBrain installation is isolated outside this repository. To use it:

```sh
uv run memento serve --gbrain --port 8877
# Also sync the already-authorized read-only Gmail and Calendar source:
uv run memento serve --gbrain --google --port 8877
# Background watches without the web UI:
uv run memento watch --gbrain --google
```

No Contacts scope is requested. PGLite has one owner. Standalone mode serializes
CLI operations and must not run alongside a separate `gbrain serve`. To share
one running GBrain HTTP server with existing agents, configure
`MEMENTO_GBRAIN_MCP_URL` and `MEMENTO_GBRAIN_MCP_CREDENTIALS` (a private renewable
client handoff). Memento then uses ordinary MCP page tools with revision checks.
The [integration research](research/gbrain.md) records the tested setup and
upstream compatibility findings.

Set `MEMENTO_EMAIL_TO` in the private env file to your connected Gmail account.
Authorize the additional `gmail.send` scope once; Memento uses the same local
Google token vault. Alerts and reminders arrive by email. Chat answers stay in
the dashboard. Historical experiment notifications are kept local when email
is first enabled, so connecting delivery does not flood your inbox.

## Try the demo

The headline demonstration should require **no chat request after remembering**:
an agent writes an ordinary project note about following up when SSO ships;
a roadmap announcement does nothing; an actual release causes an unsolicited
owner email and updates the same note. Its tags, page identity, and unrelated
metadata survive. That demonstrates the proactive extension directly.

With the shared MCP owner and Memento running, reproduce it with:

```sh
uv run python scripts/verify_page_memory.py --page-file examples/acme-page.md \
  --report-file ../.context/memento/page-memory-report.json
```

This writes through a separate GBrain MCP client, verifies both passive and
active forms of the same page, sends two synthetic source events, and checks
one clearly labeled demo email. It pauses older synthetic watches first.

The flight sequence is also available in the inspector. Breakfast recall is a
compatibility check; regular memory systems can do that too.

1. Choose **Flight confirmation** and send the event. This is explicitly a
   synthetic event; Jev and River calls are real.
2. Inspect the resulting memory and executable recipe.
3. Choose **Unrelated email**. Inspect its low matching probabilities.
4. Choose **Breakfast conflict**. The relevant flight memory surfaces.
5. Choose **Schedule change**. The alert fires and River rewrites the memory.
6. Advance the demo clock until a reminder is due. Repeated ticks do not
   duplicate the notification.

Demo events and clock changes are isolated to page paths containing `/demo/`. Real Google
events and their reminders use the real clock. **Stop watching** disables a
memory's recipe and cancels its pending notifications.

To rehearse the same flow through the actual running server:

```sh
uv run python scripts/verify_demo.py --report-file ../.context/memento/demo-report.json
```

This stops existing synthetic watches, creates a fresh synthetic flight through
Jev and River, checks an unrelated message and a breakfast question, verifies a
schedule rewrite, and advances the demo clock. With email connected, it sends
two clearly labeled demo emails. It also checks that source history is preserved
and that a clock retry does not duplicate delivery.

The composer accepts email, calendar, and chat events. It is a transparent
event-injection surface for experiments, not a fake email-sending interface.
Gmail intake is read-only. Sending uses the separate `gmail.send` scope and
is restricted to the configured connected account.

## A memory

```yaml
---
title: Follow up with Acme after SSO ships
sources:
  - meeting:acme-pilot
recipe: |
  from memento import on, email, noul, alert

  on(
      email,
      when=noul(
          "Does this confirm our SSO feature has shipped?",
          true="SSO is available to customers now.",
          false="A roadmap, proposal, or unfinished implementation.",
      ),
      do=alert("SSO shipped. Follow up with Maya at Acme."),
  )
---
Acme will start its pilot when SSO is available. I promised Maya an update.
```

`uv run memento collect examples/flight.md` validates and displays the typed
triggers without executing any actions. See [examples](examples/) for flights,
customer follow-ups, and introductions.

## What is actually implemented

- Declarative Python DSL: `at`, durations, `remind`, `on`, `expires`,
  `noul`, `choice`, `score`, `alert`, `surface`, and `rewrite`.
- Recipe collection in a child process with a timeout, a restricted AST,
  typed validation, timezone/DST validation, and source-size limits.
- Jev `TypeSafeModel.decide`, preserving full probability distributions and
  measured latency. Choice gates require the intended choice; scores use a
  normalized threshold.
- River generation through a native gRPC adapter and PydanticAI structured
  output. Its output validator compiles recipes and rejects invalid sources
  or past reminders before storing them.
- SQLite event and notification ledger, recovery, memory revision checks,
  independent clock processing, cancellation, and delivery serialization.
- Ordinary GBrain pages with revision-safe writes, shared HTTP MCP or serialized
  CLI access, paginated metadata reconciliation, and recipes verified on disk.
- Working-tree edits, optional recipes, canonical metadata preservation, and an
  authoring skill for the agent that already writes the user's memory.
- Gmail self-notifications, plus a local dashboard with
  memories, recipes, evidence, probabilities, latency, and clock controls.

The collector deliberately supports declarative Python expressions rather than
arbitrary Python I/O. It is not an OS security sandbox. Model-based matching is
probabilistic; the persisted scheduling and action lifecycle are deterministic.
Delivery uses stable notification identities and stores Gmail receipts.
Messages generated by Memento are excluded from intake to prevent notification
feedback loops.

## Research and verification

- [Product and behavior](research/product.md): who writes memories, when they
  act, the semantic boundary, and useful cases beyond flight reminders.
- [Provider experiments](research/providers.md): measured Jev routing and the
  River compatibility issues discovered and fixed.
- [GBrain experiments](research/gbrain.md): actual Google ingestion, canonical
  Markdown behavior, and upstream intake limitations.

```sh
uv run pytest -q
uv run python scripts/benchmark_jev.py --env-file ../.context/memento/credentials.env
```

Unit/integration tests use synthetic data and fake provider transports. The
research notes distinguish these from live service calls. Private credentials,
OAuth tokens, real emails/calendar entries, and raw local traces are not
committed.
