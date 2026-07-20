from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PersonDB(Base):
    __tablename__ = "persons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sources: Mapped[list[PersonSourceDB]] = relationship(back_populates="person")
    documents: Mapped[list[RawDocumentDB]] = relationship(back_populates="person")
    predictions: Mapped[list[PredictionDB]] = relationship(back_populates="person")


class PersonSourceDB(Base):
    __tablename__ = "person_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_identifier: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    person: Mapped[PersonDB] = relationship(back_populates="sources")


class RawDocumentDB(Base):
    __tablename__ = "raw_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False, unique=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="uk")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed: Mapped[bool] = mapped_column(Boolean, default=False)  # pipeline flag: predictions extracted?

    person: Mapped[PersonDB] = relationship(back_populates="documents")
    predictions: Mapped[list[PredictionDB]] = relationship(back_populates="document")


class PredictionDB(Base):
    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("raw_documents.id"), nullable=False)
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    situation: Mapped[str | None] = mapped_column(Text, nullable=True)
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    topic: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default="unresolved")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding = mapped_column(Vector(1536), nullable=True)
    prediction_strength: Mapped[str | None] = mapped_column(String(10), nullable=True)
    prediction_value: Mapped[str | None] = mapped_column(String(10), nullable=True)
    max_horizon: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_check_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    verify_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default=text("0")
    )
    last_verify_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verify_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[RawDocumentDB] = relationship(back_populates="predictions")
    person: Mapped[PersonDB] = relationship(back_populates="predictions")

    __table_args__ = (
        Index("idx_predictions_eligible", "verified_at", "next_check_at", "max_horizon"),
    )


class QueryLogDB(Base):
    """Слід публічного запиту до бота. Пишеться, читається лише через psql."""

    __tablename__ = "query_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # BigInteger, не Integer: Telegram user id виходить за межі int32
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)  # NULL = впало до відповіді
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_query_logs_created_at", "created_at"),)
