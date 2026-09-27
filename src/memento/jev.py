"""Jev answers every semantic question memento asks.

A question is about one page (Jev's `state`). All questions about the same
page go out in one request: when news arrives, every memory's `@when` claim is
checked against it at once, typically in 100-200 ms.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime

from pydantic_ai.models.decision import (
    ChoiceQuestion,
    DecisionQuestion,
    DecisionRequest,
    NoulCriteria,
    NoulQuestion,
    ScoreQuestion,
)

from .api import Page, Question, Rate, Says, Which

MODEL = os.environ.get("MEMENTO_JEV_MODEL", "jev-1.13.0")
BATCH = 150  # questions per request; 200 short ones measured ~200 ms, well inside 64k tokens
FIRES = 0.8  # a @when claim fires at this P(yes); says() inside handlers is P(yes) >= 0.5

# Asked about every new page that has no recipe yet: should River look at it?
WORTH_A_RECIPE = Says(
    claim=(
        "it holds something that should come back to the user on its own later: "
        "a dated plan, trip or appointment, a deadline or bill, a promise made or "
        "owed, a recurring habit, or a need someone has that future news could meet"
    ),
    unless="it is only a fact, a preference, reference material, or already done",
)


def state(page: Page, now: datetime) -> dict:
    return {
        "now": now.strftime("%A, %B %-d, %Y, %-I:%M %p"),
        "page": {"title": page.title, "type": page.type or "note", "text": page.text.strip()[:8000]},
    }


def to_jev(question: Question) -> DecisionQuestion:
    if isinstance(question, Says):
        false = "The page does not report this. It is about something else, or mentions it only as a "
        false += "question, plan, possibility or hypothetical."
        if question.unless:
            false += f" Also no: {question.unless}."
        return NoulQuestion(
            instructions=f"Does this page report that {question.claim}?",
            criteria=NoulCriteria(true=f"The page itself reports that {question.claim}.", false=false),
        )
    if isinstance(question, Which):
        return ChoiceQuestion(instructions=question.question, criteria=dict(question.options))
    return ScoreQuestion(instructions=question.question, criteria=list(question.levels))


def value(question: Question, answer) -> float | str:
    """P(yes) for says; the label for which; the expected level for rate."""
    if isinstance(question, Says):
        return float(answer.noul)
    if isinstance(question, Which):
        return str(answer.choice)
    return float(answer.score)


@dataclass
class Asked:
    values: dict[str, float | str]
    ms: int
    tokens: int


class Jev:
    def __init__(self, api_key: str | None = None, model: str = MODEL) -> None:
        from pydantic_ai.models.typesafe import TypeSafeModel
        from pydantic_ai.providers.typesafe import TypeSafeProvider

        key = api_key or os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY")
        if not key:
            raise RuntimeError("TYPESAFE_API_KEY is not set")
        self.model = TypeSafeModel(model, provider=TypeSafeProvider(api_key=key))
        self.loop: asyncio.AbstractEventLoop | None = None

    async def ask(self, page: Page, now: datetime, questions: dict[str, Question]) -> Asked:
        """Answer all `questions` about `page`, in as few requests as possible."""
        if not questions:
            return Asked({}, 0, 0)
        started = time.perf_counter()
        keys = list(questions)
        chunks = [keys[i : i + BATCH] for i in range(0, len(keys), BATCH)]

        async def one(chunk: list[str]) -> tuple[dict[str, float | str], int]:
            names = {f"q{i}": key for i, key in enumerate(chunk)}  # short names keep requests small
            request = DecisionRequest(
                state=state(page, now), questions={n: to_jev(questions[k]) for n, k in names.items()}
            )
            response = await self.model.decide(request, {"timeout": 30.0})
            answers = {key: value(questions[key], response.answers[n]) for n, key in names.items()}
            return answers, response.usage.input_tokens or 0

        results = await asyncio.gather(*(one(c) for c in chunks))
        values: dict[str, float | str] = {}
        for answers, _ in results:
            values |= answers
        ms = round((time.perf_counter() - started) * 1000)
        return Asked(values, ms, sum(tokens for _, tokens in results))

    def answer(self, page: Page, now: datetime, question: Question) -> bool | str | int:
        """One question from a handler thread: says -> bool, which -> label, rate -> level."""
        assert self.loop is not None, "Jev.answer needs the runtime's event loop"
        future = asyncio.run_coroutine_threadsafe(self.ask(page, now, {"q": question}), self.loop)
        v = future.result(timeout=40).values["q"]
        if isinstance(question, Says):
            return float(v) >= 0.5
        if isinstance(question, Rate):
            return max(0, min(len(question.levels) - 1, round(float(v))))
        return v
