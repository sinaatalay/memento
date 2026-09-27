"""Every judgment the runtime makes goes through Jev, via Pydantic AI.

One incoming item is Jev's `state`; every active trigger's condition is a
question. All of them are answered in parallel in one request.
"""

from __future__ import annotations

import asyncio
import time

from pydantic_ai.models.decision import (
    ChoiceQuestion,
    DecisionQuestion,
    DecisionRequest,
    NoulCriteria,
    NoulQuestion,
    ScoreQuestion,
)
from pydantic_ai.models.typesafe import TypeSafeModel

from .model import Choice, Noul, Score

# Questions per request. Jev allows 64k tokens for state plus all questions;
# 200 short questions measured at ~5k tokens and ~225 ms.
BATCH = 150

_model: TypeSafeModel | None = None


def model() -> TypeSafeModel:
    global _model
    if _model is None:
        _model = TypeSafeModel("jev-latest")
    return _model


def to_question(q: Noul | Choice | Score) -> DecisionQuestion:
    if isinstance(q, Noul):
        criteria = NoulCriteria(true=q.yes, false=q.no) if (q.yes or q.no) else None
        return NoulQuestion(instructions=q.question, criteria=criteria)
    if isinstance(q, Choice):
        return ChoiceQuestion(instructions=q.question, criteria=dict(q.options))
    return ScoreQuestion(instructions=q.question, criteria=list(q.levels))


def probability(q: Noul | Choice | Score, answer) -> float:
    """P(the trigger's condition holds)."""
    if isinstance(q, Noul):
        return float(answer.noul)
    if isinstance(q, Choice):
        return float(answer.probabilities.get(q.fire_on, 0.0))
    return float(sum(p for level, p in answer.probabilities.items() if int(level) >= q.at_least))


async def ask(state: dict, questions: dict[str, Noul | Choice | Score]) -> tuple[dict[str, float], dict]:
    """Answer every question about `state`. Returns P per key, plus ms / token stats."""
    keys = list(questions)
    started = time.perf_counter()
    chunks = [keys[i : i + BATCH] for i in range(0, len(keys), BATCH)]

    async def one(chunk: list[str]):
        # Question names are ours to choose; short ones keep requests small.
        names = {f"q{i}": key for i, key in enumerate(chunk)}
        request = DecisionRequest(state=state, questions={n: to_question(questions[k]) for n, k in names.items()})
        response = await model().decide(request, {})
        probs = {key: probability(questions[key], response.answers[n]) for n, key in names.items()}
        return probs, response.usage.input_tokens or 0

    results = await asyncio.gather(*(one(c) for c in chunks))
    probs: dict[str, float] = {}
    tokens = 0
    for chunk_probs, chunk_tokens in results:
        probs.update(chunk_probs)
        tokens += chunk_tokens
    stats = {"ms": round((time.perf_counter() - started) * 1000), "n_questions": len(keys), "input_tokens": tokens}
    return probs, stats
