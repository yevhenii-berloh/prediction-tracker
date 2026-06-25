from datetime import date
from unittest.mock import AsyncMock, MagicMock

from fakes import FakePredictionRepo, FakeVectorStore

from prophet_checker.models.domain import Prediction
from prophet_checker.query.answer_orchestrator import REFUSAL_NO_DATA, AnswerOrchestrator
from prophet_checker.query.orchestrator import QueryOrchestrator


def _embedder():
    e = MagicMock()
    e.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return e


def _llm(text="згенерована відповідь"):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=text)
    return llm


async def test_answer_refuses_without_calling_llm_when_no_sources():
    qo = QueryOrchestrator(_embedder(), FakeVectorStore(), FakePredictionRepo())
    llm = _llm()
    orch = AnswerOrchestrator(qo, llm)

    result = await orch.answer("q")

    assert result.answer == REFUSAL_NO_DATA
    assert result.sources == []
    llm.complete.assert_not_awaited()


async def test_answer_generates_with_sources():
    store = FakeVectorStore()
    repo = FakePredictionRepo()
    await store.store_embedding("p1", [0.1, 0.1, 0.1])
    await repo.save(
        Prediction(
            id="p1",
            document_id="d",
            person_id="x",
            claim_text="claim",
            prediction_date=date(2024, 1, 1),
        )
    )
    qo = QueryOrchestrator(_embedder(), store, repo)
    llm = _llm("  відповідь  ")
    orch = AnswerOrchestrator(qo, llm)

    result = await orch.answer("питання", limit=5)

    assert result.query == "питання"
    assert result.answer == "відповідь"  # обрізані пробіли
    assert [s.prediction.id for s in result.sources] == ["p1"]
    llm.complete.assert_awaited_once()
    prompt_arg = llm.complete.call_args.args[0]
    assert "p1" in prompt_arg
