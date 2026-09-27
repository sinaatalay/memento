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
