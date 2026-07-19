# scripts/generation/gen_models.py
from __future__ import annotations

from pydantic import BaseModel

from prophet_checker.models.domain import Prediction


class GenerationInput(BaseModel):
    question: str
    limit: int = 10


class ExpectedSource(BaseModel):
    prediction: Prediction


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


class CitationVerdict(BaseModel):
    """Вердикт судді по ОДНОМУ входженню маркера — одиниця citation-precision."""

    marker: int
    prediction_id: str
    sentence: str
    supported: bool
    reason: str = ""


class CitationDetail(BaseModel):
    citations: list[CitationVerdict]


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


class GenerationMetrics(BaseModel):
    n_total: int
    n_errors: int
    faithfulness_mean: float | None
    hallucination_rate: float | None
    recall_mean: float | None
    citation_precision_mean: float | None = None
    citation_coverage_mean: float | None = None
    by_category: dict[str, CategoryMetrics]
