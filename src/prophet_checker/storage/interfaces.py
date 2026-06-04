from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from prophet_checker.models.domain import (
    Person,
    PersonSource,
    Prediction,
    PredictionStatus,
    RawDocument,
    SourceType,
)


class PersonRepository(Protocol):
    async def save(self, person: Person) -> Person: ...
    async def get_by_id(self, person_id: str) -> Person | None: ...
    async def list_all(self) -> list[Person]: ...


class SourceRepository(Protocol):
    async def save_person_source(self, ps: PersonSource) -> PersonSource: ...
    async def get_person_sources(
        self, person_id: str, source_type: SourceType | None = None
    ) -> list[PersonSource]: ...
    async def save_document(
        self, doc: RawDocument, session: "AsyncSession | None" = None
    ) -> RawDocument: ...
    async def get_document_by_url(self, url: str) -> RawDocument | None: ...
    async def get_unprocessed_documents(self) -> list[RawDocument]: ...
    async def get_last_collected_at(
        self, person_id: str, source_type: SourceType
    ) -> datetime | None: ...
    async def list_active_sources(self) -> list[PersonSource]: ...
    async def update_source_cursor(
        self,
        person_source_id: str,
        cursor: datetime,
        session: "AsyncSession | None" = None,
    ) -> None: ...


class PredictionRepository(Protocol):
    async def save(
        self,
        prediction: Prediction,
        session: "AsyncSession | None" = None,
    ) -> Prediction: ...
    async def get_by_person(
        self, person_id: str, status: PredictionStatus | None = None
    ) -> list[Prediction]: ...
    async def get_unverified(self) -> list[Prediction]: ...
    async def update(self, prediction: Prediction) -> Prediction: ...


class VectorStore(Protocol):
    async def store_embedding(self, prediction_id: str, embedding: list[float]) -> None: ...
    async def search_similar(
        self, query_embedding: list[float], limit: int = 10
    ) -> list[str]: ...
