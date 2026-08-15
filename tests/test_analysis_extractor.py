from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from prophet_checker.analysis.extractor import PredictionExtractor
from prophet_checker.models.domain import Prediction, PredictionStatus


def make_llm(complete_return: str):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=complete_return)
    return llm


LLM_RESPONSE_ONE = json.dumps({
    "predictions": [
        {
            "claim_text": "Контрнаступ почнеться влітку 2023 року",
            "prediction_date": "2023-01-15",
            "target_date": "2023-06-01",
            "topic": "війна",
            "situation": "Обговорення планів ЗСУ на літню кампанію 2023",
        }
    ]
})

LLM_RESPONSE_NONE = json.dumps({"predictions": []})


async def test_extract_returns_predictions():
    llm = make_llm(LLM_RESPONSE_ONE)
    extractor = PredictionExtractor(llm)

    outcome = await extractor.extract(
        text="Контрнаступ почнеться влітку 2023 року",
        person_id="person-1",
        document_id="doc-10",
        person_name="Арестович",
        published_date="2023-01-15",
    )

    assert len(outcome.predictions) == 1
    p = outcome.predictions[0]
    assert isinstance(p, Prediction)
    assert p.claim_text == "Контрнаступ почнеться влітку 2023 року"
    assert p.status == PredictionStatus.UNRESOLVED
    assert p.confidence == 0.0
    assert p.person_id == "person-1"
    assert p.document_id == "doc-10"
    assert p.topic == "війна"
    assert p.situation == "Обговорення планів ЗСУ на літню кампанію 2023"
    assert p.id is not None  # UUID generated
    assert p.embedding is None


async def test_extract_no_predictions_is_not_a_failure():
    llm = make_llm(LLM_RESPONSE_NONE)
    extractor = PredictionExtractor(llm)

    outcome = await extractor.extract(
        text="Сьогодні гарна погода.",
        person_id="person-1",
        document_id="doc-10",
        person_name="Арестович",
        published_date="2023-01-15",
    )

    assert outcome.predictions == []


async def test_extract_llm_error_is_marked_failed():
    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=Exception("LLM unavailable"))
    extractor = PredictionExtractor(llm)

    outcome = await extractor.extract(
        text="Щось станеться завтра.",
        person_id="person-1",
        document_id="doc-10",
        person_name="Арестович",
        published_date="2023-01-15",
    )

    assert outcome.predictions == []
    assert outcome.failed is True  # збій виклику ≠ «передбачень немає»
    assert outcome.error == "Exception"


async def test_extract_drops_prediction_with_empty_situation():
    response = json.dumps({"predictions": [{
        "claim_text": "Війна закінчиться скоро",
        "prediction_date": "2023-01-15", "target_date": None, "topic": "війна",
        "situation": "",
    }]})
    llm = make_llm(response)
    extractor = PredictionExtractor(llm)
    outcome = await extractor.extract(
        text="Реальний пост: Війна закінчиться скоро, я впевнений.",
        person_id="p1", document_id="d1", person_name="Арестович",
        published_date="2023-01-15",
    )
    assert outcome.predictions == []
    assert outcome.failed is False  # відповідь прийшла, просто нічого не лишилось


async def test_extract_drops_prediction_with_missing_situation():
    response = json.dumps({"predictions": [{
        "claim_text": "Щось станеться",
        "prediction_date": "2023-01-15", "target_date": None, "topic": "війна",
    }]})
    llm = make_llm(response)
    extractor = PredictionExtractor(llm)
    outcome = await extractor.extract(
        text="Реальний пост без situation.",
        person_id="p1", document_id="d1", person_name="Арестович",
        published_date="2023-01-15",
    )
    assert outcome.predictions == []


async def test_extract_uses_production_system_prompt_by_default():
    from prophet_checker.llm.prompts import get_extraction_system

    llm = make_llm(LLM_RESPONSE_ONE)
    extractor = PredictionExtractor(llm)
    await extractor.extract(
        text="T", person_id="p", document_id="d",
        person_name="Арестович", published_date="2023-01-15",
    )
    assert llm.complete.call_args.kwargs["system"] == get_extraction_system()


async def test_extract_uses_system_prompt_override():
    llm = make_llm(LLM_RESPONSE_ONE)
    extractor = PredictionExtractor(llm, system_prompt="CUSTOM PROMPT")
    await extractor.extract(
        text="T", person_id="p", document_id="d",
        person_name="Арестович", published_date="2023-01-15",
    )
    assert llm.complete.call_args.kwargs["system"] == "CUSTOM PROMPT"


async def test_unparsable_response_is_a_failure_not_an_empty_result():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value="це не json")
    extractor = PredictionExtractor(llm)

    outcome = await extractor.extract(
        text="текст",
        person_id="p",
        document_id="d",
        person_name="Арестович",
        published_date="2026-06-01",
    )

    assert outcome.failed is True
    assert outcome.error == "UnparsableResponse"
    assert outcome.predictions == []


async def test_extract_never_raises_on_a_broken_payload():
    """Інжест покладається на тотальність extract(): виняток звідси став би
    «halted at step=processing» і сховав справжню причину."""
    llm = MagicMock()
    llm.complete = AsyncMock(return_value='{"claims": []}')
    extractor = PredictionExtractor(llm)

    outcome = await extractor.extract(
        text="текст",
        person_id="p",
        document_id="d",
        person_name="Арестович",
        published_date="2026-06-01",
    )

    assert outcome.failed is True
