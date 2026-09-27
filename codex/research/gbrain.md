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
- A second preservation test used a 131-character import, a 212-character
  comment, and a 270-character reminder/string line. All three survived exactly
  in `get.frontmatter`, canonical `get.content`, and the physical Markdown file.
  A hard 78-character recipe limit is therefore unnecessary for this version;
  line wrapping can remain optional style guidance. The probe was soft-deleted.
- An attempted replacement without the read revision failed with a
  `revision_conflict`; the original content survived. Passing the current
  revision succeeded. The synthetic probe was subsequently soft-deleted.
- Six adapter protocol tests pass: pagination including equal timestamps and
  tombstones, stdin content privacy and revisions, separate-instance ownership,
  conflict diagnostics, environment isolation, and terminating timed-out
  children before unlocking.

These measurements distinguish a working connector from a mocked fixture. They
do not establish that every kind of Gmail message is retained (see below).

## Real end-to-end pipeline verification

Ran the actual Runtime, Jev, River, recipe compiler, and GBrain adapter over
one real upcoming Calendar page and one real automated email page. The runtime
used a separate private SQLite ledger, consumed only these two records, and did
not start a daemon or configure Telegram.

| Actual input | Jev remember probability | Jev latency | Outcome |
| --- | ---: | ---: | --- |
| Upcoming Calendar appointment | 0.89 | 150.66 ms | River wrote a valid memory with one future reminder and one event trigger; committed GBrain page read back successfully |
| Automated email | 0.03 | 143.82 ms | Rejected by the memory gate; no memory written |

Calendar processing including generation, validation, durable write, and
readback took **18.146 seconds**. The email completed in **0.145 seconds**.
Both runtime events reached `done`; Jev, River, and GBrain reported connected.
There were zero due notifications and zero outbound messages.

Because verification used a separate event identity, the single generated
verification memory was subsequently soft-deleted using its original stored
GBrain revision, and deactivated in the private ledger. This prevents the test
from duplicating a future reminder when normal ingestion processes that source.
No personal titles, content, identities, or source IDs are included in this
report; the verification ledger and sanitized counts stay under `.context/`.

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

**Live deletion finding:** upstream soft-delete sets `deleted_at` without
advancing `updated_at`; restore clears `deleted_at` without advancing it either.
An incremental timestamp query alone therefore misses these lifecycle changes,
and a seen marker containing only `updated_at` also skips them. The local
runtime now reconciles the full lightweight page metadata inventory on each
poll and compares `(updated_at, deleted_at)`. Bodies are fetched only when this
fingerprint changes. This favors correctness at demo scale over metadata-query
efficiency. A regression covers delete and restore with an unchanged content
timestamp, pending-notification cancellation, and no repeated body reads.
The live dashboard subsequently reconciled the previously missed verification
tombstone and deactivated that watch without a manual stop operation.

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
will be retained. Memento now supplies a supplemental Google read-only intake
path that preserves these messages through ordinary GBrain writes without
modifying upstream.

### Supplemental intake: implemented and tested live

`GoogleAutomatedIntake` imports the pinned upstream `GoogleTokenProvider`,
`GmailClient`, and sender predicate through a small Bun bridge. It reuses the
existing authorized credential vault. Tokens stay inside the bridge process;
Python receives normalized messages only. No extra scope or consent is needed.

A live read found **7 real automated messages** omitted by native ingestion.
The first supplemental poll committed all seven as
`default:events/gmail/<message_id>` pages in **12.242 seconds**. An immediate
second poll returned **zero new events** in **2.167 seconds**. Existing pages,
including tombstones, are durable deduplication records. These were ordinary
automated messages; this does not imply an actual flight booking was present.

Each poll reads at most **25 message IDs** from a fixed last-24-hour snapshot,
with three concurrent thread reads. Threads containing a human author are
left to native GBrain. A continuation token resumes the rest of the bounded
snapshot on subsequent polls, and advances only after every intercepted page
write commits. Stable write UUIDs preserve idempotency across uncertain results.
New snapshots overlap previous ones, and existing pages prevent repeated work.
This is intentionally a recent-mail prototype, not historical mailbox import.

No marketing keyword filter is used: airline offers and actual confirmations
can share a sender. The existing Jev gate decides whether each observation is
worth remembering. Individual message bodies are capped at 8,000 characters.
The runtime must allow default-source `events/gmail/` pages into its email path.

The bridge relies on source-module interfaces from the pinned checkout, so a
GBrain upgrade needs a compatibility check. It leaves that checkout unmodified.

Another detail: a Gmail page represents a **whole thread**, oldest message
first. Its `message_id` is the newest message and `message_ids` lists all of
them. The renderer emits `## <sender> · YYYY-MM-DD HH:MM` before each message;
the Gmail client already trims quoted replies inside each message. Evaluate
the new message, not a stale relevant sentence anywhere in the entire thread,
or later unrelated replies can re-trigger old conditions.

`normalize_gbrain_event(page)` implements this: it selects the newest exact
renderer message header, strips its source/routing preamble and quoted replies,
flattens sender/date or Calendar start/end metadata, and returns
`{source, payload, event_id}`. A Gmail event ID uses account identity plus the
actual message ID, so label-only changes and native/supplemental overlap do not
re-trigger an observation. Calendar IDs include the page revision, so a changed
appointment remains a new observation.

Six supplemental tests cover newest-message extraction with quoted and ordinary
Markdown headers, shared native/supplemental IDs, Calendar revisions, replay
and tombstone deduplication, pending-write recovery, and bounded snapshot
continuation. Together with the six adapter tests: **12 passing tests**.

## Primary references

- [Google setup and scoping](https://github.com/garrytan/gbrain/blob/master/docs/guides/google-connect.md)
- [Data ingestion and write receipts](https://github.com/garrytan/gbrain/blob/master/docs/guides/data-ingestion.md)
- [Page operations, revisions, and list filters](https://github.com/garrytan/gbrain/blob/e78f1c38b947b053f3a46881340f74f316be855a/src/core/ops/pages.ts)
- [Calendar bounds and Google sync](https://github.com/garrytan/gbrain/blob/e78f1c38b947b053f3a46881340f74f316be855a/src/core/google/google-source.ts)
- [Noise filtering and thread rendering](https://github.com/garrytan/gbrain/blob/e78f1c38b947b053f3a46881340f74f316be855a/src/core/google/google-render.ts)
- [Environment precedence and GBRAIN_HOME](https://github.com/garrytan/gbrain/blob/e78f1c38b947b053f3a46881340f74f316be855a/src/core/config.ts)

## Email delivery replaces Telegram

After the user selected email notifications, the existing Desktop OAuth grant
was extended with `gmail.send`, while preserving Gmail readonly, Calendar
readonly, and identity. Contacts remain absent. The native GBrain ingestion
code is still unmodified; `EmailNotifier` uses its token provider and vault.

The sender verifies that Gmail's connected profile matches the configured
recipient and sends only to that same account. The runtime never supplies a
recipient from an incoming message or generated recipe. It builds an RFC MIME
message, base64url-encodes it, and posts to Gmail `users.messages.send`, following
[Google's sending guide](https://developers.google.com/workspace/gmail/api/guides/sending)
and [send API contract](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send).

One authorized setup email was sent and read back through Gmail. The actual
message had both **SENT and INBOX** labels, the expected owner recipient,
`X-Memento-Notification`, and `Auto-Submitted: auto-generated`. Its Gmail ID
and receipt are stored privately; no message body or account identity is in
this report.

Every notification also carries a deterministic RFC `Message-ID`. A private
send ledger records its intent before POST and the Gmail receipt afterward.
Retries search the sent folder by that RFC ID to reconcile a crash after Gmail
accepted the message. Ambiguous transport/timeout/server failures never cause
a blind second POST. Definite API 4xx rejections, except HTTP 408, remain
retryable after runtime backoff. Reusing a notification ID for different
content is rejected.

Feedback prevention was checked through **real native GBrain ingestion** of
the setup email. The normalized page is marked `memento_notification=true`,
so the runtime suppresses it before model evaluation. Supplemental Gmail
intake also excludes the reserved notification subject prefix.

This live check caught a detail absent from the first mocked tests: GBrain's
source-citation labels strip square brackets and truncate at 80 characters.
The normalizer now reconstructs the original subject only when its exact
sanitized form matches the newest citation. It retains a distinct `Re:`
subject for a human response, so replying to one's own notification with a
correction is still processed. Regression tests include this real renderer
shape, known delivery IDs, wrong senders, self replies, and missing citations.

Focused sender, feedback, and intake verification: **31 passing tests**.

The final complete synthetic flight run then delivered two clearly labeled
demo emails: a schedule-change alert and a due reminder. Both runtime delivery
receipts were independently checked against Gmail using read-only requests:
**SENT and INBOX were present for both**, their owner recipient matched, and
both notification headers were intact. This verification sent no additional
messages. The sanitized receipt checks are retained privately in
`.context/memento/demo-email-verification.json`.
