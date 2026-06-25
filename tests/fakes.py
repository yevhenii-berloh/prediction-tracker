from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from prophet_checker.models.domain import (
    Person,
    PersonSource,
    Prediction,
    PredictionStatus,
    RawDocument,
    SourceType,
    VectorMatch,
)
from prophet_checker.storage.interfaces import (
    PersonRepository,
    PredictionRepository,
    SourceRepository,
    VectorStore,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakePersonRepo(PersonRepository):
    def __init__(self):
        self._persons: dict[str, Person] = {}

    async def save(self, person: Person) -> Person:
        self._persons[person.id] = person
        return person

    async def get_by_id(self, person_id: str) -> Person | None:
        return self._persons.get(person_id)

    async def list_all(self) -> list[Person]:
        return list(self._persons.values())


class FakeSourceRepo(SourceRepository):
    def __init__(self):
        self._sources: list[PersonSource] = []
        self._documents: list[RawDocument] = []

    async def save_person_source(self, ps: PersonSource) -> PersonSource:
        self._sources.append(ps)
        return ps

    async def get_person_sources(
        self, person_id: str, source_type: SourceType | None = None
    ) -> list[PersonSource]:
        return [
            s
            for s in self._sources
            if s.person_id == person_id and (source_type is None or s.source_type == source_type)
        ]

    async def save_document(
        self, doc: RawDocument, session: "AsyncSession | None" = None
    ) -> RawDocument:
        self._documents.append(doc)
        return doc

    async def get_document_by_url(self, url: str) -> RawDocument | None:
        return next((d for d in self._documents if d.url == url), None)

    async def get_unprocessed_documents(self) -> list[RawDocument]:
        return self._documents

    async def get_last_collected_at(
        self, person_id: str, source_type: SourceType
    ) -> datetime | None:
        docs = [
            d for d in self._documents if d.person_id == person_id and d.source_type == source_type
        ]
        if not docs:
            return None
        return max(d.collected_at for d in docs)

    async def list_active_sources(self) -> list[PersonSource]:
        return [s for s in self._sources if s.enabled]

    async def update_source_cursor(
        self,
        person_source_id: str,
        cursor: datetime,
        session: "AsyncSession | None" = None,
    ) -> None:
        for i, s in enumerate(self._sources):
            if s.id == person_source_id:
                self._sources[i] = s.model_copy(update={"last_collected_at": cursor})
                return


class FakePredictionRepo(PredictionRepository):
    def __init__(self):
        self._predictions: list[Prediction] = []

    async def save(
        self,
        prediction: Prediction,
        session: "AsyncSession | None" = None,
    ) -> Prediction:
        self._predictions.append(prediction)
        return prediction

    async def get_by_person(
        self, person_id: str, status: PredictionStatus | None = None
    ) -> list[Prediction]:
        return [
            p
            for p in self._predictions
            if p.person_id == person_id and (status is None or p.status == status)
        ]

    async def get_unverified(self) -> list[Prediction]:
        return [
            p
            for p in self._predictions
            if p.status == PredictionStatus.UNRESOLVED and p.verified_at is None
        ]

    async def get_by_ids(self, ids: list[str]) -> list[Prediction]:
        by_id = {p.id: p for p in self._predictions}
        return [by_id[i] for i in ids if i in by_id]

    async def update(self, prediction: Prediction) -> Prediction:
        self._predictions = [p if p.id != prediction.id else prediction for p in self._predictions]
        return prediction


class FakeVectorStore(VectorStore):
    def __init__(self):
        self._entries: list[tuple[str, list[float]]] = []

    async def store_embedding(self, prediction_id: str, embedding: list[float]) -> None:
        self._entries.append((prediction_id, embedding))

    async def is_embedding_present(self, prediction_id: str) -> bool:
        return any(pid == prediction_id for pid, _ in self._entries)

    async def search_similar(
        self, query_embedding: list[float], limit: int = 10
    ) -> list[VectorMatch]:
        return [
            VectorMatch(prediction_id=pid, distance=float(i))
            for i, (pid, _) in enumerate(self._entries[:limit])
        ]
