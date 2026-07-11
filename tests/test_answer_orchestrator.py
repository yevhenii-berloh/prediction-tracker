from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakes import FakePredictionRepo, FakeVectorStore

from prophet_checker.models.domain import Prediction, QueryResult, RetrievedPrediction
from prophet_checker.query.answer_orchestrator import (
    REFUSAL_NO_DATA,
    REFUSAL_UNKNOWN_AUTHOR,
    AnswerOrchestrator,
)
from prophet_checker.query.orchestrator import QueryOrchestrator


def _embedder():
    e = MagicMock()
    e.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return e


def _llm(text="згенерована відповідь"):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=text)
    return llm


def _pred(pid="p1"):
    return Prediction(
        id=pid, document_id="d", person_id="x", claim_text="claim", prediction_date=date(2024, 1, 1)
    )


# --- answer() (end-to-end, search→делегування) ---


async def test_answer_refuses_without_calling_llm_when_no_sources():
    qo = QueryOrchestrator(_embedder(), FakeVectorStore(), FakePredictionRepo())
    llm = _llm()
    orch = AnswerOrchestrator(llm, qo)

    result = await orch.answer("q")

    assert result.answer == REFUSAL_NO_DATA
    assert result.sources == []
    llm.complete.assert_not_awaited()


async def test_answer_unknown_author_refuses_without_llm():
    llm = _llm("не має викликатись")
    qo = MagicMock()
    qo.search = AsyncMock(
        return_value=QueryResult(query="q", results=[], unknown_author="Портников")
    )
    orch = AnswerOrchestrator(llm, qo)

    result = await orch.answer("Що прогнозував Портников?")

    assert result.answer == REFUSAL_UNKNOWN_AUTHOR.format(author="Портников")
    assert result.sources == []
    llm.complete.assert_not_awaited()


async def test_answer_generates_with_sources():
    store = FakeVectorStore()
    repo = FakePredictionRepo()
    await store.store_embedding("p1", [0.1, 0.1, 0.1])
    await repo.save(_pred("p1"))
    qo = QueryOrchestrator(_embedder(), store, repo)
    llm = _llm("  відповідь  ")
    orch = AnswerOrchestrator(llm, qo)

    result = await orch.answer("питання", limit=5)

    assert result.query == "питання"
    assert result.answer == "відповідь"
    assert [s.prediction.id for s in result.sources] == ["p1"]
    llm.complete.assert_awaited_once()
    assert "p1" in llm.complete.call_args.args[0]


# --- answer_from_sources() (generate-only, без query_orchestrator) ---


async def test_answer_from_sources_refuses_on_empty():
    orch = AnswerOrchestrator(_llm())
    result = await orch.answer_from_sources("q", [])
    assert result.answer == REFUSAL_NO_DATA
    assert result.sources == []


async def test_answer_from_sources_generates():
    llm = _llm("  відповідь  ")
    orch = AnswerOrchestrator(llm)
    sources = [RetrievedPrediction(prediction=_pred("p1"), distance=0.0, rank=1)]

    result = await orch.answer_from_sources("питання", sources)

    assert result.answer == "відповідь"
    assert [s.prediction.id for s in result.sources] == ["p1"]
    llm.complete.assert_awaited_once()
    assert "p1" in llm.complete.call_args.args[0]


async def test_answer_raises_without_query_orchestrator():
    orch = AnswerOrchestrator(_llm())
    with pytest.raises(RuntimeError):
        await orch.answer("q")
