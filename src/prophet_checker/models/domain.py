from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum

from pydantic import BaseModel


class SourceType(str, Enum):
    TELEGRAM = "telegram"
    NEWS = "news"


class PredictionStatus(str, Enum):
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    UNRESOLVED = "unresolved"
    PREMATURE = "premature"


class PredictionStrength(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PredictionValue(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Person(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: datetime | None = None

    def model_post_init(self, __context) -> None:
        if self.created_at is None:
            self.created_at = datetime.now(UTC)


class PersonSource(BaseModel):
    id: str
    person_id: str
    source_type: SourceType
    source_identifier: str
    enabled: bool = True
    last_collected_at: datetime | None = None

    def model_post_init(self, __context) -> None:
        if self.last_collected_at is None:
            self.last_collected_at = datetime.now(UTC)


class RawDocument(BaseModel):
    id: str
    person_id: str
    source_type: SourceType
    url: str
    published_at: datetime
    raw_text: str
    language: str = "uk"
    collected_at: datetime | None = None

    def model_post_init(self, __context) -> None:
        if self.collected_at is None:
            self.collected_at = datetime.now(UTC)


class Prediction(BaseModel):
    id: str
    document_id: str
    person_id: str
    claim_text: str
    situation: str | None = None
    prediction_date: date
    target_date: date | None = None
    topic: str = ""
    status: PredictionStatus = PredictionStatus.UNRESOLVED
    confidence: float = 0.0
    evidence_url: str | None = None
    evidence_text: str | None = None
    verified_at: datetime | None = None
    embedding: list[float] | None = None
    prediction_strength: PredictionStrength | None = None
    prediction_value: PredictionValue | None = None
    max_horizon: date | None = None
    next_check_at: date | None = None
    verify_attempts: int = 0
    last_verify_error: str | None = None
    last_verify_error_at: datetime | None = None


class VectorMatch(BaseModel):
    prediction_id: str
    distance: float  # cosine-distance: менше = ближче


class RetrievedPrediction(BaseModel):
    prediction: Prediction
    distance: float
    rank: int  # 1-based, порядок за схожістю


class QueryResult(BaseModel):
    query: str
    results: list[RetrievedPrediction]


class AnswerResult(BaseModel):
    query: str
    answer: str
    sources: list[RetrievedPrediction]
