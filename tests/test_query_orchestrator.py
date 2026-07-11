from datetime import date
from unittest.mock import AsyncMock, MagicMock

from fakes import FakePredictionRepo, FakeVectorStore

from prophet_checker.models.domain import Prediction, QueryPlan, SearchFilters
from prophet_checker.query.orchestrator import QueryOrchestrator


def _embedder():
    e = MagicMock()
    e.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return e


def _planner(plan: QueryPlan) -> MagicMock:
    p = MagicMock()
    p.plan = AsyncMock(return_value=plan)
    return p


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


async def _store_repo_p1_p2():
    store = FakeVectorStore()
    # FakeVectorStore: distance = порядок вставки (0-based) → p1=0.0, p2=1.0
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
    return store, repo


async def test_search_applies_relevance_threshold():
    store, repo = await _store_repo_p1_p2()
    orch = QueryOrchestrator(_embedder(), store, repo, relevance_threshold=0.5)
    result = await orch.search("q", limit=10)
    assert [r.prediction.id for r in result.results] == ["p1"]  # p2@1.0 > 0.5 відсічено


async def test_search_threshold_none_keeps_all():
    store, repo = await _store_repo_p1_p2()
    orch = QueryOrchestrator(_embedder(), store, repo)  # дефолт None
    result = await orch.search("q", limit=10)
    assert [r.prediction.id for r in result.results] == ["p1", "p2"]  # без фільтра


async def test_search_passes_filters_and_embeds_semantic_query():
    store, repo = await _store_repo_p1_p2()
    filters = SearchFilters(person_id="x", prediction_date_from=date(2022, 1, 1))
    embedder = _embedder()
    orch = QueryOrchestrator(
        embedder,
        store,
        repo,
        planner=_planner(QueryPlan(semantic_query="тема", filters=filters)),
    )

    await orch.search("Що X казав з 2022 про тему?", limit=10)

    embedder.embed.assert_awaited_once_with("тема")
    assert store.last_filters == filters


async def test_search_unknown_author_short_circuits():
    embedder = _embedder()
    plan = QueryPlan(semantic_query="тема", filters=SearchFilters(unknown_author="Портников"))
    orch = QueryOrchestrator(
        embedder, FakeVectorStore(), FakePredictionRepo(), planner=_planner(plan)
    )

    result = await orch.search("Що прогнозував Портников?")

    assert result.results == []
    assert result.unknown_author == "Портников"
    embedder.embed.assert_not_awaited()


async def test_search_without_planner_passes_empty_filters():
    store, repo = await _store_repo_p1_p2()
    orch = QueryOrchestrator(_embedder(), store, repo)  # planner=None
    result = await orch.search("q", limit=10)
    assert [r.prediction.id for r in result.results] == ["p1", "p2"]
    assert store.last_filters == SearchFilters()
