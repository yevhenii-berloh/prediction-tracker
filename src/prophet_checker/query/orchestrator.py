from __future__ import annotations

from prophet_checker.llm import EmbeddingClient
from prophet_checker.models.domain import QueryResult, RetrievedPrediction
from prophet_checker.storage.interfaces import PredictionRepository, VectorStore


class QueryOrchestrator:
    def __init__(
        self,
        embedder: EmbeddingClient,
        vector_store: VectorStore,
        prediction_repo: PredictionRepository,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._prediction_repo = prediction_repo

    async def search(self, question: str, limit: int = 10) -> QueryResult:
        embedding = await self._embedder.embed(question)
        matches = await self._vector_store.search_similar(embedding, limit=limit)
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
