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
            "context": "Контрнаступ почнеться влітку 2023 року",
        }
    ]
})

LLM_RESPONSE_NONE = json.dumps({"predictions": []})


async def test_extract_returns_predictions():
    llm = make_llm(LLM_RESPONSE_ONE)
    extractor = PredictionExtractor(llm)

    predictions = await extractor.extract(
        text="Контрнаступ почнеться влітку 2023 року",
        person_id="person-1",
        document_id="doc-10",
        person_name="Арестович",
        published_date="2023-01-15",
    )

    assert len(predictions) == 1
    p = predictions[0]
    assert isinstance(p, Prediction)
    assert p.claim_text == "Контрнаступ почнеться влітку 2023 року"
    assert p.status == PredictionStatus.UNRESOLVED
    assert p.confidence == 0.0
    assert p.person_id == "person-1"
    assert p.document_id == "doc-10"
    assert p.topic == "війна"
    assert p.context == "Контрнаступ почнеться влітку 2023 року"
    assert p.id is not None  # UUID generated
    assert p.embedding is None


async def test_extract_no_predictions():
    llm = make_llm(LLM_RESPONSE_NONE)
    extractor = PredictionExtractor(llm)

    predictions = await extractor.extract(
        text="Сьогодні гарна погода.",
        person_id="person-1",
        document_id="doc-10",
        person_name="Арестович",
        published_date="2023-01-15",
    )

    assert predictions == []


async def test_extract_llm_error_returns_empty():
    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=Exception("LLM unavailable"))
    extractor = PredictionExtractor(llm)

    predictions = await extractor.extract(
        text="Щось станеться завтра.",
        person_id="person-1",
        document_id="doc-10",
        person_name="Арестович",
        published_date="2023-01-15",
    )

    assert predictions == []


async def test_extract_drops_prediction_with_hallucinated_context():
    response = json.dumps({"predictions": [{
        "claim_text": "Війна закінчиться скоро",
        "prediction_date": "2023-01-15", "target_date": None, "topic": "війна",
        "context": "цього тексту немає в оригінальному пості взагалі",
    }]})
    llm = make_llm(response)
    extractor = PredictionExtractor(llm)
    predictions = await extractor.extract(
        text="Реальний пост: Війна закінчиться скоро, я впевнений.",
        person_id="p1", document_id="d1", person_name="Арестович",
        published_date="2023-01-15",
    )
    assert predictions == []


async def test_extract_drops_prediction_with_missing_context():
    response = json.dumps({"predictions": [{
        "claim_text": "Щось станеться",
        "prediction_date": "2023-01-15", "target_date": None, "topic": "війна",
    }]})
    llm = make_llm(response)
    extractor = PredictionExtractor(llm)
    predictions = await extractor.extract(
        text="Реальний пост без потрібного context.",
        person_id="p1", document_id="d1", person_name="Арестович",
        published_date="2023-01-15",
    )
    assert predictions == []
