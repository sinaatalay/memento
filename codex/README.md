# Memento

Memories that know when to come back. A local, working research prototype for
the Own Your Intelligence hackathon.

Memento stores ordinary Markdown with a Python recipe in its YAML front matter.
The recipe registers reminders and semantic event conditions. Jev evaluates all
relevant conditions in one typed request; River writes and repairs recipes.
Both model paths run through PydanticAI. There is no OpenRouter dependency.

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
```

No Contacts scope is requested. The GBrain adapter serializes CLI operations
across processes because its PGLite database has one owner. Do not run
`gbrain serve` against the same installation.

Set `TELEGRAM_BOT_TOKEN` in the private env file and restart. The dashboard
shows a `/start <pair-code>` link for pairing your private chat. Until paired,
notifications appear in the dashboard and stay queued for phone delivery.

## Try the demo

1. Choose **Flight confirmation** and send the event. This is explicitly a
   synthetic event; Jev and River calls are real.
2. Inspect the resulting memory and executable recipe.
3. Choose **Unrelated email**. Inspect its low matching probabilities.
4. Choose **Breakfast conflict**. The relevant flight memory surfaces.
5. Choose **Schedule change**. The alert fires and River rewrites the memory.
6. Advance the demo clock until a reminder is due. Repeated ticks do not
   duplicate the notification.

Demo events and clock changes are isolated to `memories/demo/`. Real Google
events and their reminders use the real clock. **Stop watching** disables a
memory's recipe and cancels its pending notifications.

The composer accepts email, calendar, and chat events. It is a transparent
event-injection surface for experiments, not a fake email-sending interface.
Actual Gmail intake is read-only.

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
      when=noul("Does this confirm our SSO feature has shipped?"),
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
- Unmodified local GBrain with revision-safe writes, CLI locking, pagination,
  incremental polling, and Markdown recipes verified on disk.
- Telegram private-chat pairing and notifications, plus a local dashboard with
  memories, recipes, evidence, probabilities, latency, and clock controls.

The collector deliberately supports declarative Python expressions rather than
arbitrary Python I/O. It is not an OS security sandbox. Model-based matching is
probabilistic; the persisted scheduling and action lifecycle are deterministic.
Telegram has no general idempotent-send key, so a crash after Telegram accepts a
message but before its receipt is saved can still duplicate that delivery.

## Research and verification

- [Provider experiments](research/providers.md): measured Jev routing and the
  River compatibility issues discovered and fixed.
- [GBrain experiments](research/gbrain.md): actual Google ingestion, canonical
  Markdown behavior, and upstream intake limitations.

```sh
uv run pytest -q
```

Unit/integration tests use synthetic data and fake provider transports. The
research notes distinguish these from live service calls. Private credentials,
OAuth tokens, real emails/calendar entries, and raw local traces are not
committed.
