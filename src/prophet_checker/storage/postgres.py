from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

from prophet_checker.models.db import (
    PersonDB,
    PersonSourceDB,
    PredictionDB,
    RawDocumentDB,
)
from prophet_checker.models.domain import (
    Person,
    PersonSource,
    Prediction,
    PredictionStatus,
    PredictionStrength,
    PredictionValue,
    RawDocument,
    SourceType,
    VectorMatch,
)


# --- Mappers: Domain <-> DB ---


def domain_to_person_db(person: Person) -> PersonDB:
    return PersonDB(
        id=person.id,
        name=person.name,
        description=person.description,
        created_at=person.created_at,
    )


def person_db_to_domain(db: PersonDB) -> Person:
    return Person(id=db.id, name=db.name, description=db.description, created_at=db.created_at)


def domain_to_person_source_db(ps: PersonSource) -> PersonSourceDB:
    return PersonSourceDB(
        id=ps.id,
        person_id=ps.person_id,
        source_type=ps.source_type.value,
        source_identifier=ps.source_identifier,
        enabled=ps.enabled,
        last_collected_at=ps.last_collected_at,
    )


def person_source_db_to_domain(db: PersonSourceDB) -> PersonSource:
    return PersonSource(
        id=db.id,
        person_id=db.person_id,
        source_type=SourceType(db.source_type),
        source_identifier=db.source_identifier,
        enabled=db.enabled,
        last_collected_at=db.last_collected_at,
    )


def domain_to_raw_document_db(doc: RawDocument) -> RawDocumentDB:
    return RawDocumentDB(
        id=doc.id,
        person_id=doc.person_id,
        source_type=doc.source_type.value,
        url=doc.url,
        published_at=doc.published_at,
        raw_text=doc.raw_text,
        language=doc.language,
        collected_at=doc.collected_at,
        processed=False,
    )


def raw_document_db_to_domain(db: RawDocumentDB) -> RawDocument:
    return RawDocument(
        id=db.id,
        person_id=db.person_id,
        source_type=SourceType(db.source_type),
        url=db.url,
        published_at=db.published_at,
        raw_text=db.raw_text,
        language=db.language,
        collected_at=db.collected_at,
    )


def domain_to_prediction_db(pred: Prediction) -> PredictionDB:
    return PredictionDB(
        id=pred.id,
        document_id=pred.document_id,
        person_id=pred.person_id,
        claim_text=pred.claim_text,
        situation=pred.situation,
        prediction_date=pred.prediction_date,
        target_date=pred.target_date,
        topic=pred.topic,
        status=pred.status.value,
        confidence=pred.confidence,
        evidence_url=pred.evidence_url,
        evidence_text=pred.evidence_text,
        verified_at=pred.verified_at,
        embedding=pred.embedding,
        prediction_strength=pred.prediction_strength.value if pred.prediction_strength else None,
        prediction_value=pred.prediction_value.value if pred.prediction_value else None,
        max_horizon=pred.max_horizon,
        next_check_at=pred.next_check_at,
        verify_attempts=pred.verify_attempts,
        last_verify_error=pred.last_verify_error,
        last_verify_error_at=pred.last_verify_error_at,
    )


def prediction_db_to_domain(db: PredictionDB) -> Prediction:
    return Prediction(
        id=db.id,
        document_id=db.document_id,
        person_id=db.person_id,
        claim_text=db.claim_text,
        situation=db.situation,
        prediction_date=db.prediction_date,
        target_date=db.target_date,
        topic=db.topic,
        status=PredictionStatus(db.status),
        confidence=db.confidence,
        evidence_url=db.evidence_url,
        evidence_text=db.evidence_text,
        verified_at=db.verified_at,
        prediction_strength=PredictionStrength(db.prediction_strength)
        if db.prediction_strength
        else None,
        prediction_value=PredictionValue(db.prediction_value) if db.prediction_value else None,
        max_horizon=db.max_horizon,
        next_check_at=db.next_check_at,
        verify_attempts=db.verify_attempts,
        last_verify_error=db.last_verify_error,
        last_verify_error_at=db.last_verify_error_at,
    )


# --- Repository implementations ---


class PostgresPersonRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def save(self, person: Person) -> Person:
        async with self._session_factory() as session:
            db_obj = domain_to_person_db(person)
            session.add(db_obj)
            await session.commit()
            await session.refresh(db_obj)
            return person_db_to_domain(db_obj)

    async def get_by_id(self, person_id: str) -> Person | None:
        async with self._session_factory() as session:
            result = await session.get(PersonDB, person_id)
            return person_db_to_domain(result) if result else None

    async def list_all(self) -> list[Person]:
        async with self._session_factory() as session:
            result = await session.execute(select(PersonDB))
            return [person_db_to_domain(row) for row in result.scalars().all()]


class PostgresSourceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def list_active_sources(self) -> list[PersonSource]:
        async with self._session_factory() as session:
            stmt = select(PersonSourceDB).where(PersonSourceDB.enabled == True)
            result = await session.execute(stmt)
            return [person_source_db_to_domain(row) for row in result.scalars().all()]

    async def save_person_source(self, ps: PersonSource) -> PersonSource:
        async with self._session_factory() as session:
            db_obj = domain_to_person_source_db(ps)
            session.add(db_obj)
            await session.commit()
            return ps

    async def get_person_sources(
        self, person_id: str, source_type: SourceType | None = None
    ) -> list[PersonSource]:
        async with self._session_factory() as session:
            stmt = select(PersonSourceDB).where(PersonSourceDB.person_id == person_id)
            if source_type is not None:
                stmt = stmt.where(PersonSourceDB.source_type == source_type.value)
            result = await session.execute(stmt)
            return [person_source_db_to_domain(row) for row in result.scalars().all()]

    async def save_document(
        self, doc: RawDocument, session: AsyncSession | None = None
    ) -> RawDocument:
        db_obj = domain_to_raw_document_db(doc)
        if session is not None:
            await session.merge(db_obj)
            return doc
        async with self._session_factory() as own_session:
            own_session.add(db_obj)
            await own_session.commit()
            return doc

    async def get_document_by_url(self, url: str) -> RawDocument | None:
        async with self._session_factory() as session:
            stmt = select(RawDocumentDB).where(RawDocumentDB.url == url)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return raw_document_db_to_domain(row) if row else None

    async def get_unprocessed_documents(self) -> list[RawDocument]:
        async with self._session_factory() as session:
            stmt = select(RawDocumentDB).where(RawDocumentDB.processed == False)
            result = await session.execute(stmt)
            return [raw_document_db_to_domain(row) for row in result.scalars().all()]

    async def get_last_collected_at(
        self, person_id: str, source_type: SourceType
    ) -> datetime | None:
        async with self._session_factory() as session:
            stmt = select(func.max(RawDocumentDB.collected_at)).where(
                RawDocumentDB.person_id == person_id,
                RawDocumentDB.source_type == source_type.value,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_source_cursor(
        self,
        person_source_id: str,
        cursor: datetime,
        session: AsyncSession | None = None,
    ) -> None:
        if session is not None:
            db_obj = await session.get(PersonSourceDB, person_source_id)
            if db_obj is None:
                logger.warning(
                    "update_source_cursor: PersonSource not found id=%s",
                    person_source_id,
                )
                return
            db_obj.last_collected_at = cursor
            return
        async with self._session_factory() as own_session:
            db_obj = await own_session.get(PersonSourceDB, person_source_id)
            if db_obj is None:
                logger.warning(
                    "update_source_cursor: PersonSource not found id=%s",
                    person_source_id,
                )
                return
            db_obj.last_collected_at = cursor
            await own_session.commit()


class PostgresPredictionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def save(
        self,
        prediction: Prediction,
        session: AsyncSession | None = None,
    ) -> Prediction:
        if session is not None:
            db_obj = domain_to_prediction_db(prediction)
            session.add(db_obj)
            return prediction
        async with self._session_factory() as own_session:
            db_obj = domain_to_prediction_db(prediction)
            own_session.add(db_obj)
            await own_session.commit()
            return prediction

    async def get_by_person(
        self, person_id: str, status: PredictionStatus | None = None
    ) -> list[Prediction]:
        async with self._session_factory() as session:
            stmt = select(PredictionDB).where(PredictionDB.person_id == person_id)
            if status is not None:
                stmt = stmt.where(PredictionDB.status == status.value)
            result = await session.execute(stmt)
            return [prediction_db_to_domain(row) for row in result.scalars().all()]

    async def get_unverified(self) -> list[Prediction]:
        async with self._session_factory() as session:
            stmt = select(PredictionDB).where(
                PredictionDB.status == PredictionStatus.UNRESOLVED.value,
                PredictionDB.verified_at.is_(None),
            )
            result = await session.execute(stmt)
            return [prediction_db_to_domain(row) for row in result.scalars().all()]

    async def get_by_ids(self, ids: list[str]) -> list[Prediction]:
        if not ids:
            return []
        async with self._session_factory() as session:
            stmt = select(PredictionDB).where(PredictionDB.id.in_(ids))
            result = await session.execute(stmt)
            by_id = {row.id: prediction_db_to_domain(row) for row in result.scalars().all()}
        return [by_id[i] for i in ids if i in by_id]

    async def update(self, prediction: Prediction) -> Prediction:
        async with self._session_factory() as session:
            db_obj = await session.get(PredictionDB, prediction.id)
            if db_obj:
                db_obj.status = prediction.status.value
                db_obj.confidence = prediction.confidence
                db_obj.evidence_url = prediction.evidence_url
                db_obj.evidence_text = prediction.evidence_text
                db_obj.prediction_strength = (
                    prediction.prediction_strength.value if prediction.prediction_strength else None
                )
                db_obj.prediction_value = (
                    prediction.prediction_value.value if prediction.prediction_value else None
                )
                db_obj.max_horizon = prediction.max_horizon
                db_obj.next_check_at = prediction.next_check_at
                db_obj.verify_attempts = prediction.verify_attempts
                db_obj.last_verify_error = prediction.last_verify_error
                db_obj.last_verify_error_at = prediction.last_verify_error_at
                db_obj.verified_at = prediction.verified_at
                await session.commit()
            return prediction


class PostgresVectorStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def store_embedding(self, prediction_id: str, embedding: list[float]) -> None:
        async with self._session_factory() as session:
            db_obj = await session.get(PredictionDB, prediction_id)
            if db_obj:
                db_obj.embedding = embedding
                await session.commit()

    async def is_embedding_present(self, prediction_id: str) -> bool:
        async with self._session_factory() as session:
            db_obj = await session.get(PredictionDB, prediction_id)
            return db_obj is not None and db_obj.embedding is not None

    async def search_similar(
        self, query_embedding: list[float], limit: int = 10
    ) -> list[VectorMatch]:
        async with self._session_factory() as session:
            dist = PredictionDB.embedding.cosine_distance(query_embedding)
            # лише ембеджені рядки: cosine_distance(NULL, q) = NULL → інакше distance None
            stmt = (
                select(PredictionDB.id, dist.label("distance"))
                .where(PredictionDB.embedding.is_not(None))
                .order_by(dist)
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [VectorMatch(prediction_id=r[0], distance=r[1]) for r in result.all()]
