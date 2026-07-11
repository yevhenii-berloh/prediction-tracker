from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from prophet_checker.models.domain import (
    Person,
    PersonSource,
    Prediction,
    PredictionStatus,
    RawDocument,
    SearchFilters,
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


@dataclass
class _VectorMeta:
    person_id: str | None = None
    prediction_date: date | None = None
    target_date: date | None = None


def _date_in_range(
    value: date | None, lo: date | None, hi: date | None, *, null_inclusive: bool
) -> bool:
    if lo is None and hi is None:
        return True
    if value is None:
        return null_inclusive
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


class FakeVectorStore(VectorStore):
    def __init__(self):
        self._entries: list[tuple[str, list[float]]] = []
        self._meta: dict[str, _VectorMeta] = {}
        self.last_filters: SearchFilters | None = None

    async def store_embedding(
        self,
        prediction_id: str,
        embedding: list[float],
        *,
        person_id: str | None = None,
        prediction_date: date | None = None,
        target_date: date | None = None,
    ) -> None:
        self._entries.append((prediction_id, embedding))
        self._meta[prediction_id] = _VectorMeta(person_id, prediction_date, target_date)

    async def is_embedding_present(self, prediction_id: str) -> bool:
        return any(pid == prediction_id for pid, _ in self._entries)

    async def search_similar(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[VectorMatch]:
        self.last_filters = filters
        matches: list[VectorMatch] = []
        for i, (pid, _) in enumerate(self._entries):
            if filters is not None and not self._passes(pid, filters):
                continue
            matches.append(VectorMatch(prediction_id=pid, distance=float(i)))
            if len(matches) == limit:
                break
        return matches

    def _passes(self, pid: str, f: SearchFilters) -> bool:
        meta = self._meta.get(pid, _VectorMeta())
        if f.person_id is not None and meta.person_id != f.person_id:
            return False
        if not _date_in_range(
            meta.prediction_date,
            f.prediction_date_from,
            f.prediction_date_to,
            null_inclusive=False,  # prediction_date NOT NULL у схемі; NULL валить предикат як у SQL
        ):
            return False
        return _date_in_range(
            meta.target_date,
            f.target_date_from,
            f.target_date_to,
            null_inclusive=True,  # Р2 дизайну
        )
