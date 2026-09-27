from types import SimpleNamespace
import json
from datetime import datetime

import pytest
from pydantic_ai.models.decision import (
    ChoiceAnswer, DecisionResponse, NoulAnswer, ScoreAnswer,
)
from pydantic_ai.usage import RequestUsage

from memento.providers import JevEvaluator, to_jev_question


def test_question_mapping_preserves_criteria():
    q = to_jev_question({
        "kind": "noul", "question": "Does this change UA123?",
        "options": {"true": "This flight changed", "false": "Unrelated"},
    })
    assert q.criteria.true == "This flight changed"
    assert q.criteria.false == "Unrelated"
    assert to_jev_question({
        "kind": "score", "question": "Urgency?", "levels": ["low", "high"]
    }).criteria == ["low", "high"]


@pytest.mark.asyncio
async def test_batch_keeps_positive_probability_and_choice_distribution():
    class Model:
        model_name = "fixture"

        async def decide(self, request, settings):
            assert set(request.questions) == {"negative", "kind", "urgency"}
            return DecisionResponse(
                model_name="fixture",
                answers={
                    "negative": NoulAnswer(noul=0.02),
                    "kind": ChoiceAnswer(
                        choice="unrelated", confidence=0.96,
                        probabilities={"travel": 0.01, "unrelated": 0.99},
                    ),
                    "urgency": ScoreAnswer(
                        score=1.6, confidence=0.8,
                        probabilities={0: 0.1, 1: 0.2, 2: 0.7},
                    ),
                },
                usage=RequestUsage(input_tokens=50, output_tokens=5),
            )

    batch = await JevEvaluator(model=Model()).decide({"event": "example"}, {
        "negative": {"kind": "noul", "question": "Flight changed?"},
        "kind": {"kind": "choice", "question": "Kind?", "options": {
            "travel": "Travel", "unrelated": "Unrelated",
        }},
        "urgency": {"kind": "score", "question": "Urgency?", "levels": [
            "Routine", "Soon", "Now",
        ]},
    })
    assert batch.answers["negative"].probability == 0.02
    assert batch.answers["negative"].value is False
    assert batch.answers["kind"].probabilities["travel"] == 0.01
    assert batch.answers["urgency"].probability == 0.8
    assert batch.input_tokens == 50


@pytest.mark.asyncio
async def test_river_transport_uses_native_sdk_and_preserves_error():
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelHTTPError
    from memento.providers import RiverConnection

    class NativeClient:
        calls = []

        def chat_complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return SimpleNamespace(
                status_code=429,
                response_json='{"error":{"message":"insufficient_funds"}}',
            )

        def close(self):
            pass

    native = NativeClient()
    connection = RiverConnection(client=native)
    try:
        with pytest.raises(ModelHTTPError, match="429"):
            await Agent(connection.model).run("hello")
        assert len(native.calls) == 1
        assert native.calls[0][1]["base_model"] == "Qwen/Qwen3.6-35B-A3B-FP8"
        assert "model" not in native.calls[0][1]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_writer_retries_invalid_recipe_before_returning():
    from pydantic_ai.messages import ModelResponse, TextPart, RetryPromptPart
    from pydantic_ai.models.function import FunctionModel
    from memento.writer import MemoryWriter

    calls = []

    async def reply(messages, info):
        calls.append(messages)
        draft = {
            "title": "Seat preference", "body": "Prefers aisle seats.",
            "recipe": "import os" if len(calls) == 1 else "",
            "sources": ["chat/1"],
        }
        return ModelResponse(parts=[TextPart(json.dumps(draft))])

    writer = MemoryWriter(model=FunctionModel(reply))
    draft = await writer.write({"id": "chat/1", "text": "I prefer aisle seats"})
    assert draft.recipe == ""
    assert len(calls) == 2
    assert any(
        isinstance(part, RetryPromptPart)
        for message in calls[-1] for part in message.parts
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_draft", [
    {"recipe": "", "sources": ["invented/source"]},
    {"recipe": (
        "from memento import at, remind\n"
        "remind(at('2026-09-26T08:00:00-07:00'), 'Too late')\n"
    ), "sources": ["chat/1"]},
])
async def test_writer_rejects_invented_citations_and_past_reminders(bad_draft):
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    from memento.writer import MemoryWriter

    async def reply(messages, info):
        draft = {"title": "Bad draft", "body": "Example", **bad_draft}
        return ModelResponse(parts=[TextPart(json.dumps(draft))])

    writer = MemoryWriter(model=FunctionModel(reply))
    with pytest.raises(UnexpectedModelBehavior, match="output retries"):
        await writer.write(
            {"id": "chat/1", "text": "Example"},
            now=datetime.fromisoformat("2026-09-27T14:00:00-07:00"),
        )
