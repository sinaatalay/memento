# Jev and River: live integration experiments

Measured locally on September 27, 2026. Inputs below are synthetic fixtures, not
private mail. API keys live outside the repository. These are small experiments,
not a calibrated accuracy evaluation.

## Decisions that survived experiments

- Use Pydantic AI's `TypeSafeModel.decide(DecisionRequest(...), settings)` for Jev.
  Batch every active question for an event plus the worth-remembering gate in one
  request. Pin `jev-1.13.0`, since `jev-latest` can change threshold behavior.
- Jev cannot write arbitrary text. Use River to generate the memory and recipe.
- River currently has usable credits. The earlier attachment's insufficient-funds
  result did **not** reproduce. `health_check()` succeeded, and this key could
  access 13 models via `get_capabilities()`.
- River's native `chat_complete` accepts the OpenAI wire format over gRPC.
  `RiverConnection` bridges this inside the process with an HTTP transport so
  Pydantic AI's `OpenAIChatModel` owns message mapping, retries and validation.
  It makes no OpenRouter requests and requires no separate proxy server.
- Ordinary `output_type=MemoryDraft` uses tool calling. In a real trial River's
  Qwen3.6-35B route ignored required tool output and answered plain text. This
  exhausted Pydantic's output retries. `PromptedOutput(MemoryDraft)` succeeded
  for a small structured greeting; use prompted JSON plus schema validation and
  the recipe collector's exact-error repair loop instead.
- Disable Qwen thinking for this compiler. A raw 16-token sample spent the entire
  budget beginning reasoning rather than answering. Native chat with
  `chat_template_kwargs={"enable_thinking": False}` returned the answer.
- Coalesce all system/developer messages into one leading system message in the
  adapter. Pydantic's prompted JSON schema otherwise creates a second system
  message; Qwen3.5-9B rejected this with `System message must be at the beginning`.
  A full 35B attempt timed out before this fix; after the fix it compiled in 6.6s.

## Reproduced Jev measurements

Same synthetic UA123 schedule-change event; distractors reference other flight
numbers and dates. Two requests at each size, so no p95 claim is justified.

| Questions | Wall time (ms) | Input tokens | Target P(yes) | Largest distractor |
|---|---:|---:|---:|---:|
| 1 | 117.53–147.40 | 417 | .93–.95 | — |
| 10 | 103.83–121.62 | 957 | .93 | .01 |
| 200 | 150.97–190.38 | 12,357 | .92–.93 | .02 |

Noul criteria are included in every question, so this token count is larger than
an earlier benchmark with shorter questions. Measured through our actual adapter.

Worth-remembering gate, one request per case:

| Case | Expected | P(yes) | Wall time (ms) |
|---|---|---:|---:|
| Confirmed flight booking | remember | .98 | 81.41 |
| Flight advertisement | ignore | .04 | 122.27 |
| Generic AI newsletter | ignore | .03 | 161.94 |
| Promise with a deadline | remember | .92 | 89.41 |
| Hypothetical flight example | ignore | .03 | 79.85 |
| Newsletter with instruction injection | ignore | .04 | 98.59 |
| Explicit user request to remember | remember | .95 | 78.74 |
| Uncommitted daydreaming | ignore | .05 | 119.50 |

One adversarial fixture does not demonstrate injection resistance. Jev's own
published limitations warn that adversarial state can shift its classifications.
The runtime and collector enforce permissions independently of model answers.

Choice and Score were exercised in one request (90.23 ms): the event category
was `travel`, probability and confidence 1.0. Urgency score was 1.61 across four
levels (0–3), confidence .58, full distribution {.12, .17, .69, .02}. The adapter
normalizes score to .537 for threshold comparison. That number is an expected
rubric position, **not** P(yes). Choice triggers must name a `match` option; a
confident unrelated winner must not activate a travel trigger.

## River measurements

Native SDK 0.12.0 health check: healthy, 1,215 ms including connection setup.
Actual sample calls: Qwen3.6-35B 3,816 ms; Qwen3.5-9B 3,189 ms. Native chat with
thinking disabled and a tiny JSON answer: 2,956 ms. Longer recipe generation
was then measured separately:

| Compiler model | Result | Total time |
|---|---|---:|
| Qwen3.5-9B | Repeated overlong import after two repair attempts; rejected | ~7.5s |
| Qwen3.6-35B-A3B-FP8 | Valid flight recipe on first attempt, 348 output tokens | 6.60s |
| Qwen3.5-122B-A10B-FP8 | First draft had an already-past 24h reminder; validator rejected it; repair correctly removed it | 7.09s |

The successful recipe scheduled a 06:05 reminder for an 08:05 departure,
registered email alert+rewrite and chat surface triggers, and expired a day after
arrival. The 122B run is a live demonstration that the exact-error repair loop
works for a semantic time constraint, not only JSON/schema validation.

## Lifecycle experiments and changes driven by failures

All dates refer to September 28, 2026; simulated current time was September 27
14:40 in America/Los_Angeles. The stored fixtures are separate from private mail.

| Experiment | Actual result | Time |
|---|---|---:|
| Create 08:05 UA123 flight memory | 06:05 reminder, email alert+rewrite, chat surface | 5.893s |
| Delay to 10:40 | Replaced reminder with 08:40; both source IDs retained | 4.403s |
| Move earlier to 06:30 | Replaced reminder with 04:30; both source IDs retained | 5.680s |
| Cancellation after delay | Empty recipe, no pending reminders/watches; all three source IDs retained | 6.293s |
| Create Acme SSO promise | Email release watch with alert+rewrite | 3.953s |
| SSO release matches generated watches | Release .95, unrelated cancellation .02 | 96ms |
| Rewrite after SSO release | Removed fulfilled release watch, retained unfinished Maya follow-up and original/update sources | 6.922s |

Cancellation initially failed. The generated recipe invented a rebooking watch
and arbitrary expiry, with an overlong comment. The exact validator error was
`line 12: exceeds 78 characters`. Its repair dropped an import, producing
`line 12: unknown name 'expires'`, and exhausted two retries. A clear final
cancellation example and instruction to return `recipe=""` when no obligation
remains fixed the observed case. We did not bypass validation or store failed
code. The writer now also requires original source IDs plus the current event ID
to survive a rewrite; unknown citations and past reminders produce repair errors.

The generated email question matched an earlier departure change with .88 in
134ms. The first generated chat question, “planning something on Sep 28 before
noon”, missed “breakfast Monday at 7 before my trip” (.20–.22). A date/weekday
index computed in Python raised that same question to .84 (111ms).

Adding an exact 08:05 departure time into the semantic question still required
unreliable time comparison and scored only .68. We changed the writer to ask
directly about a proposed activity *before the upcoming trip* on the computed
weekday/date. In the same annotated state this scored .95 for the breakfast
message, versus .27 for “lunch Monday at 1”; both classify correctly at .8.
Another direct breakfast/morning question scored .94 versus .02. Exact flight
times remain in the memory and deterministic reminder. This is scoped semantic
relevance, not a claim that Jev reliably performs interval arithmetic.

`annotate_calendar(state)` supplies today, tomorrow, and the next seven days with
weekday names. It uses the user's timezone and leaves original event text intact.
The runtime should log this enriched state to make decisions inspectable.

Chat corrections need separate narrow questions. A compound
“correct, cancel, or stop tracking UA123 on Monday...” field returned only .62
for an explicit canceled-and-stop-reminders message. Splitting it gave .99 for
confirmed cancellation and .99 for stop-reminders; a separate departure-time
correction question gave .98. Hypothetical cancellation stayed at .01–.03.
The writer now includes these chat rewrite watches alongside chat surface.

“Actually cancel my flight” cannot identify UA123 from an isolated event. Its
scoped question scored .27 without context, versus .96 when the state supplied
the actual upcoming-flight memory. The runtime must provide bounded relevant
memory context to resolve these references. A request to cancel is recorded as
intent, not as proof an external booking was canceled or refunded.

The 78-character limit was subsequently disproved as a GBrain requirement by a
separate round-trip experiment. It is no longer a writer instruction or hard
collector check. The earlier failure remains documented because it explains why
we removed an unnecessary source of model retries. No-tabs and restricted Python
semantics remain enforced.

Final generated recipe check (after all prompt changes): compiled in 7.817s,
with an actual `surface(this)` chat action, separate narrow rewrite watches,
and the correct 06:05 reminder. Jev evaluated those generated questions with the
generated memory's actual title/body as context:

| Chat | Intended watch probability | Other relevant watches | Time |
|---|---:|---:|---:|
| Breakfast Monday at 7 before my trip | surface .93 | rewrite .02–.03 | 148ms |
| Actually cancel my flight | cancellation request .93 | confirmed cancellation .29 | 109ms |
| UA123 is canceled; stop reminding me | confirmed cancellation .99, stop .99 | cancellation request .18 | 106ms |
| What happens if my flight gets canceled? | all .02–.04 | no watch activates | 91ms |

This final run used the application classes and generated recipe, not hard-coded
questions substituted by the test harness. Provider/writer checks: 9 tests pass,
including system-message normalization, error propagation, source validation,
past-reminder rejection, recipe repair and timezone-correct calendar context.

## Full-runtime counterexamples and the final question compiler

A subsequent end-to-end run exposed two failures that the earlier tiny fixture
set missed. A schedule update saying the booking remained confirmed, departure
changed to 06:30, and arrival stayed unchanged scored only .50 against the broad
“change or cancel” condition. The generic salience gate also treated the breakfast
proposal as a new memory (.85) when remembered flight context was present.

The writer now separates departure changes from cancellations and supplies
explicit `true=` / `false=` boundary cases for every Noul question. Its output
validator rejects generic default criteria and asks the model to repair them.
Stable booking IDs scope the question; varying ISO/human date formatting is not
the sole identity. A booking remaining confirmed does not negate a departure
change, and unchanged arrival does not negate changed departure.

Chat has a separate memory gate: judge only the incoming message and save
affirmed facts/preferences, definite commitments, and explicit requests to
remember. Questions, invitations, hypothetical plans and tentative proposals do
not become new commitments. Existing memory/context is not new input evidence.

The actual regenerated recipe compiled in 9.304s and was then tested through the
real Jev adapter with its generated conditions and memory context:

| Regression input | Actual result | Time |
|---|---|---:|
| DEMO42/UA123 changes departure 08:05→06:30, booking still confirmed, arrival still16:30 | departure change .99; cancellation .02 | 129ms |
| “Can we grab breakfast Monday at 7 before my trip?” | surface .97; remember .04; rewrite conditions ≤.03 | 93ms |
| “I prefer aisle seats on long flights.” | remember .84 | 132ms |

Separate gate checks accepted confirmed breakfast (.96), explicit promise to
remember (.94), and a polite remember request (.96); rejected a flight question
(.02) and tentative proposal (.04). No activation threshold was lowered.
Provider/writer tests now total 10, including rejection and repair of unspecified
question criteria. The reusable benchmark uses the final narrow departure
question and explicit criteria (`memento-synthetic-flight-departure-batch-v2`);
older measurements above intentionally retain their original methodology.

The final v2 benchmark was run at 2026-09-27 21:56:53 UTC using the checked-in
script, two repetitions per size (six live requests total):

| Questions | Wall time (ms) | Input tokens | Target P(yes) | Largest distractor |
|---|---:|---:|---:|---:|
| 1 | 102.67–132.24 | 632 | .99 | — |
| 10 | 108.41–117.38 | 1,307 | .99 | .01 |
| 200 | 189.43–261.23 | 15,557 | .99 | .01–.02 |

All six runs separated the intended match at the unchanged .8 threshold.
Explicit criteria and calendar context add tokens compared with the original
experiment. These remain two observations per size, not a latency percentile or
an independent general accuracy estimate. Raw JSON is in the local ignored
`.context/memento/benchmark-final.json`.

## Interfaces

`JevEvaluator(api_key=...).decide(state, questions)` returns `DecisionBatch`,
including model version, elapsed_ms, token usage, and all answer distributions.
The adapter never writes event content into tracked logs.

`MemoryWriter(api_key=..., model_name=...).write(event, existing_memory=None,
now=..., timezone_name=...)` returns `MemoryDraft(title, body, recipe, sources)`.
`close()` releases its native and async HTTP clients. `collect()` runs as a
Pydantic AI output validator; invalid code becomes `ModelRetry` (maximum two
repairs), never a stored unchecked recipe.

## Primary references

- [Pydantic AI TypeSafe integration](https://pydantic.dev/docs/ai/models/typesafe/)
- [Decision model API and answer types](https://pydantic.dev/docs/ai/api/models/decision/)
- [River quickstart](https://docs.river.ai/quickstart/)
- [River model access](https://docs.river.ai/guides/models/)
- Installed `river-client==0.12.0` source: `Client.chat_complete`,
  `ChatCompleteFromBaseRequest`, `ChatCompleteResult`.

## Reproducing the batch benchmark

From `codex/`, preview the bounded experiment without making a provider request:

```sh
uv run python scripts/benchmark_jev.py --dry-run
```

Run it using an explicitly supplied local credential file:

```sh
uv run python scripts/benchmark_jev.py \
  --env-file ../.context/memento/credentials.env \
  --sizes 1 10 200 --repeats 2 \
  --json-out ../.context/memento/jev-benchmark.json
```

The script only reads synthetic fixtures. It loads `JEV_API_KEY` or
`TYPESAFE_API_KEY`, never prints credentials, and emits JSON containing model
version, latency, token counts, target probability and the largest distractor.
Allowed sizes are 1, 10 and 200; repeats are capped at five, so an invocation
makes at most 15 calls. It does not access Gmail/GBrain or send notifications.
The script's dry-run and help were exercised before its six-request final live
benchmark; the final measured results are recorded above.

## Email notification feedback

Self-email delivery creates a new Gmail event. Suppress that output before Jev,
the memory gate, or recovered writer work; otherwise a flight reminder can be
remembered again and create duplicate watches. An exact sent Gmail message ID
in the persistent delivery ledger is the strongest available local signal.

Subject fallback needs care: GBrain's thread title is the **first** message's
subject, but `from`, `message_id`, and the observed body come from the **latest**
message. Filtering `[Memento]` plus self sender using that inherited title would
also suppress a real human reply to a notification. Normalize the latest subject
from GBrain's generated source citation; when it cannot be recovered, do not use
the inherited subject for suppression. An anchored latest `[Memento]` subject
plus matching self sender and recipient catches our outgoing notification;
`Re:`/`Fwd:` and other senders remain ordinary observations.

The regression suite covers persisted sent IDs across restart, suppression
before any model or pending writer task, human self-replies, absent latest-subject
metadata, misleading display names, and restricting suppression to email.
All 16 feedback tests pass. They use synthetic thread pages and local SQLite;
this verification sends no email and makes no provider requests.
