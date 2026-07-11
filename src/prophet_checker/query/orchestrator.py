from __future__ import annotations

from prophet_checker.llm import EmbeddingClient
from prophet_checker.models.domain import (
    QueryPlan,
    QueryResult,
    RetrievedPrediction,
    SearchFilters,
)
from prophet_checker.query.planner import QueryPlanner
from prophet_checker.storage.interfaces import PredictionRepository, VectorStore


class QueryOrchestrator:
    def __init__(
        self,
        embedder: EmbeddingClient,
        vector_store: VectorStore,
        prediction_repo: PredictionRepository,
        relevance_threshold: float | None = None,
        planner: QueryPlanner | None = None,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._prediction_repo = prediction_repo
        self._relevance_threshold = relevance_threshold
        self._planner = planner

    async def search(self, question: str, limit: int = 10) -> QueryResult:
        plan = await self._resolve_plan(question)
        # short-circuit: план уже знає, що автор невідомий — embed/пошук зайві (design §5.5/Р3)
        if plan.filters.unknown_author is not None:
            return QueryResult(
                query=question, results=[], unknown_author=plan.filters.unknown_author
            )
        embedding = await self._embedder.embed(plan.semantic_query)
        matches = await self._vector_store.search_similar(
            embedding, limit=limit, filters=plan.filters
        )
        if self._relevance_threshold is not None:
            matches = [m for m in matches if m.distance <= self._relevance_threshold]
        by_id = {
            p.id: p
            for p in await self._prediction_repo.get_by_ids([m.prediction_id for m in matches])
        }
        results = [
            RetrievedPrediction(prediction=by_id[m.prediction_id], distance=m.distance, rank=rank)
            for rank, m in enumerate(matches, start=1)
            if m.prediction_id in by_id
        ]
        return QueryResult(query=question, results=results)

    async def _resolve_plan(self, question: str) -> QueryPlan:
        if self._planner is None:
            return QueryPlan(semantic_query=question, filters=SearchFilters())
        return await self._planner.plan(question)
