from datetime import date
from unittest.mock import AsyncMock, MagicMock

from fakes import FakePredictionRepo, FakeVectorStore

from prophet_checker.models.domain import Prediction
from prophet_checker.query.orchestrator import QueryOrchestrator


def _embedder():
    e = MagicMock()
    e.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return e


async def test_search_ranks_and_orders_results():
    store = FakeVectorStore()
    await store.store_embedding("p1", [0.1, 0.1, 0.1])
    await store.store_embedding("p2", [0.2, 0.2, 0.2])
    repo = FakePredictionRepo()
    for pid in ("p1", "p2"):
        await repo.save(
            Prediction(
                id=pid,
                document_id="d",
                person_id="x",
                claim_text=pid,
                prediction_date=date(2024, 1, 1),
            )
        )
    orch = QueryOrchestrator(_embedder(), store, repo)

    result = await orch.search("питання", limit=10)

    assert result.query == "питання"
    assert [r.prediction.id for r in result.results] == ["p1", "p2"]
    assert [r.rank for r in result.results] == [1, 2]
    assert result.results[0].distance == 0.0  # FakeVectorStore: distance = індекс


async def test_search_drops_matches_without_prediction():
    store = FakeVectorStore()
    await store.store_embedding("ghost", [0.1, 0.1, 0.1])  # match є, прогнозу немає
    orch = QueryOrchestrator(_embedder(), store, FakePredictionRepo())
    result = await orch.search("q")
    assert result.results == []


async def test_search_empty_corpus_returns_empty():
    orch = QueryOrchestrator(_embedder(), FakeVectorStore(), FakePredictionRepo())
    result = await orch.search("q")
    assert result.results == []
