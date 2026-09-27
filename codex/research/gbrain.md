# GBrain integration: measured findings

Research date: 2026-09-27. Upstream version 0.59.0.0, pinned commit
`e78f1c38b947b053f3a46881340f74f316be855a`.
The upstream checkout is unmodified (`git status --short` is empty).

## What actually worked

- Initialized a separate PGLite brain with embeddings disabled and no server.
- Connected the supplied Desktop OAuth client using the browser consent flow.
  Granted scopes are Gmail readonly, Calendar readonly, and Google identity.
  Contacts were neither requested nor registered.
- Registered one Google source with a one-day history window. First sync took
  **48.84 seconds**, persisted **6 real Gmail threads and 5 calendar events**,
  created 23 text chunks, and performed **zero embeddings**.
- A second incremental sync returned `up_to_date` in **1.162 seconds**.
- Read real email and calendar pages with the Python adapter. Both supplied
  canonical full Markdown, typed frontmatter, and a page revision.
- Listed all 11 Google pages with a deliberately small page size of five;
  all three batches were returned. This took 2.090 seconds.
- Created a synthetic memory with a literal Python module in YAML frontmatter.
  `put` wrote the Markdown file and `get` preserved the recipe exactly.
- An attempted replacement without the read revision failed with a
  `revision_conflict`; the original content survived. Passing the current
  revision succeeded. The synthetic probe was subsequently soft-deleted.
- Six adapter protocol tests pass: pagination including equal timestamps and
  tombstones, stdin content privacy and revisions, separate-instance ownership,
  conflict diagnostics, environment isolation, and terminating timed-out
  children before unlocking.

These measurements distinguish a working connector from a mocked fixture. They
do not establish that every kind of Gmail message is retained (see below).

## Local installation and ownership

The private install lives outside the project checkout at
`~/.local/share/memento-gbrain-research/`:

| Path beneath that directory | Purpose |
| --- | --- |
| `checkout/` | Unmodified upstream Git checkout and Bun dependencies |
| `home/.gbrain/` | PGLite database, isolated configuration, OAuth credential vault |
| `memory/` | Source `default`: Memento's canonical Markdown |
| `google/` | Source `google`: connector-owned email/calendar Markdown |

`GBRAIN_HOME` is a **parent directory**: GBrain appends `.gbrain` itself.
It must point to `.../home`, not `.../home/.gbrain`.
The private `.context/memento/gbrain.json` records the exact command and home
for this machine, without credentials or email addresses.

Installation used `bun install --frozen-lockfile --ignore-scripts`; this avoids
the upstream postinstall hook finding and migrating an unrelated installation.
Initialization used `init --pglite --non-interactive --no-embedding --no-git
--content-root <owned-memory-directory>`. No existing brain was touched.

Only one CLI process may own this PGLite database at a time. `GBrain` serializes
its subprocesses with an asyncio lock and a shared file lock, including every
batch of a paginated read. It stops and reaps timed-out processes before
releasing ownership. Do not run `gbrain serve` on this database alongside it.

The adapter strips inherited `GBRAIN_*`, `DATABASE_URL`, and `*_API_KEY`
variables before setting its own isolated `GBRAIN_HOME`. Upstream gives an
exported `DATABASE_URL` precedence over even an explicitly configured PGLite
database, so home isolation alone is insufficient.

## The actual CLI contract

The architecture's logical operations map to these upstream commands:

```text
sync --source google --no-embed --no-extract --no-pull --json
call list_pages '{"source_id":"__all__","sort":"updated_asc",...}'
get <slug> --source-id <source> --include-content true --json
put <slug> --source-id <source> --request-id <uuid> --json
    [--expected-revision <read-revision>]    # Markdown supplied on stdin
```

**`list --json` is a trap in this pinned version: it still returns TSV.**
Use `call list_pages` for machine-readable JSON. `get --json` and `put --json`
work. JSON and Markdown are passed as subprocess arguments/stdin, never
interpolated into a shell command.

`list_pages` returns `slug`, `source_id`, `type`, `title`, `updated_at`, and
optionally `deleted_at`. It does not return a revision. `get_page` with
`include_content` returns canonical `content`, `frontmatter`, and `revision`.

`updated_after` means strict `>` and full UTC timestamps are returned. Keep an
overlapping runtime cursor and deduplicate by source, slug, and update/revision.
Pagination uses one fixed filter and offsets while holding ownership; advancing
the timestamp at each page boundary would lose rows sharing that timestamp.
Read with `include_deleted` so cancellation can retire behavior.

Writes without `expected_revision` are create-only. Replacements must pass the
read revision. Persist a request UUID with the write intent and replay the
**identical arguments and UUID** after an uncertain transport result. Receipt
state `committed` is durable success; pending/queued receipts are not. The
adapter deliberately does not use `force`.

The canonical Markdown may gain GBrain's type and provenance fields. Preserve
the canonical returned content when modifying a page; do not rebuild it from
`compiled_truth` alone.

## Google configuration and limits

Read-only OAuth was requested with `google connect --scopes gmail,calendar`.
The source was separately registered with `--services gmail,calendar
--history-days 1`. Both settings matter: scope grants and source configuration
are distinct. Gmail history is restricted to the most recent day.

The native Calendar connector currently hardcodes a **60-day forward window**;
there is no CLI option for one week. The historical window shares
`--history-days`. Calendar pages retain stable `event_id` metadata, but a
reschedule can rename their slug and tombstone the old page. Match identities
by `event_id` when connecting old and new records.

GBrain's optional model work is disabled: `embedding_disabled` at init,
`facts.extraction_enabled=false`, `loops.extraction_enabled=false`, and sync
with both `--no-embed` and `--no-extract`. Memento owns Jev and River calls.
No OpenRouter or other GBrain model provider is configured.

Observed email frontmatter keys:
`account, cc, date, first_message_date, from, labels, message_count,
message_id, message_ids, participants, senders, thread_id, to`.

Observed calendar keys:
`account, all_day, attendees, end, event_id, organizer, start, url`.

## Important product mismatch: automated emails can disappear

The native renderer's `renderThreadPage` returns `null` if every message is
from an address containing `noreply`, `no-reply`, `notifications@`, and several
similar patterns. This is an ingestion filter, not merely an open-loop filter,
and the pinned version has no configuration override.

A direct experiment invoking the **unmodified** renderer with a synthetic
flight confirmation produced:

```json
{"automated_flight_retained": false, "human_forward_retained": true}
```

Consequently, the native connector alone does **not** fulfill the flight
confirmation use case for many real airlines. A self-email or human-forwarded
demo works, but should not be presented as proof that automated booking mail
will be retained. A supplemental Google read-only intake path can preserve
these messages through ordinary GBrain writes without modifying upstream.

Another detail: a Gmail page represents a **whole thread**, oldest message
first. Its `message_id` is the newest message and `message_ids` lists all of
them. The renderer emits `## <sender> · YYYY-MM-DD HH:MM` before each message;
the Gmail client already trims quoted replies inside each message. Evaluate
the new message, not a stale relevant sentence anywhere in the entire thread,
or later unrelated replies can re-trigger old conditions.

## Primary references

- [Google setup and scoping](https://github.com/garrytan/gbrain/blob/master/docs/guides/google-connect.md)
- [Data ingestion and write receipts](https://github.com/garrytan/gbrain/blob/master/docs/guides/data-ingestion.md)
- [Page operations, revisions, and list filters](https://github.com/garrytan/gbrain/blob/e78f1c38b947b053f3a46881340f74f316be855a/src/core/ops/pages.ts)
- [Calendar bounds and Google sync](https://github.com/garrytan/gbrain/blob/e78f1c38b947b053f3a46881340f74f316be855a/src/core/google/google-source.ts)
- [Noise filtering and thread rendering](https://github.com/garrytan/gbrain/blob/e78f1c38b947b053f3a46881340f74f316be855a/src/core/google/google-render.ts)
- [Environment precedence and GBRAIN_HOME](https://github.com/garrytan/gbrain/blob/e78f1c38b947b053f3a46881340f74f316be855a/src/core/config.ts)
