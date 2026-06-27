# scripts/generation/gen_models.py
from __future__ import annotations

from pydantic import BaseModel


class GenerationInput(BaseModel):
    question: str
    limit: int = 10


class ExpectedSource(BaseModel):
    prediction_id: str
    claim: str


class GenerationLabels(BaseModel):
    answerable: bool
    expected_sources: list[ExpectedSource] = []
    category: str  # single_source | synthesis | off_domain | near_domain


class ClaimVerdict(BaseModel):
    claim: str
    supported: bool
    reason: str = ""


class FaithfulnessDetail(BaseModel):
    claims: list[ClaimVerdict]


class RefusalDetail(BaseModel):
    refused: bool
    answerable: bool
    category: str


class SourceCoverage(BaseModel):
    prediction_id: str
    covered: bool
    reason: str = ""


class CompletenessDetail(BaseModel):
    coverage: list[SourceCoverage]


class CategoryMetrics(BaseModel):
    n: int
    faithfulness_mean: float | None
    recall_mean: float | None
    refusal_accuracy: float


class GenerationMetrics(BaseModel):
    n_total: int
    n_answered: int
    n_refused: int
    n_errors: int
    faithfulness_mean: float | None
    hallucination_rate: float | None
    recall_mean: float | None
    refusal_accuracy: float
    over_refusal_rate: float
    false_answer_rate: float
    by_category: dict[str, CategoryMetrics]
