# scripts/extraction/eval_v2/eval_models.py
from __future__ import annotations

from pydantic import BaseModel

from prophet_checker.models.domain import Prediction


class PostInput(BaseModel):
    """EvalCase.input — один пост датасету."""

    post_id: str
    author: str
    channel: str
    text: str
    published_date: str
    stratum: str  # "prefilter" | "random"
    url: str = ""


class ExtractionResult(BaseModel):
    """EvalRun.result — те, що прод-екстрактор витяг із поста."""

    predictions: list[Prediction]
    # True = виклик упав. Мовчання моделі й збій API — різні речі: перше
    # чесно карається по coverage, друге не має карати нікого.
    failed: bool = False


class PooledClaim(BaseModel):
    """Унікальний claim поста після дедуплікації (крок 3)."""

    claim_id: str
    claim_text: str
    situation: str | None = None
    models: list[str]  # учасники, чий claim потрапив у цей кластер


class ClaimVerdict(BaseModel):
    """Три перевірні питання по одному унікальному claim (крок 4)."""

    claim_id: str
    claim_grounded: bool
    situation_grounded: bool
    passes_rubric: bool
    truncated: bool = False
    reason: str = ""
    # False = суддя не відповів. «Не суджено» ≠ «модель збрехала»: інакше збій
    # судді вибивав би кандидата по blocking-gate hallucination_rate.
    measured: bool = True

    @property
    def is_valid(self) -> bool:
        return (
            self.measured
            and self.claim_grounded
            and self.situation_grounded
            and self.passes_rubric
            and not self.truncated
        )

    @property
    def is_hallucinated(self) -> bool:
        if not self.measured:
            return False
        return not self.claim_grounded or not self.situation_grounded

    @property
    def is_over_extraction(self) -> bool:
        if not self.measured:
            return False
        return self.claim_grounded and self.situation_grounded and not self.passes_rubric


class MissedClaim(BaseModel):
    text: str
    reason: str = ""


class PoolResult(BaseModel):
    """Вихід пулінгу: унікальні claims плюс факт, чи кластеризація взагалі відпрацювала."""

    claims: list[PooledClaim]
    clustering_failed: bool = False


class PostJudgement(BaseModel):
    """Усе, що суддя сказав про один пост — іде в ScoreCard.detail."""

    post_id: str
    claims: list[PooledClaim]
    verdicts: list[ClaimVerdict]
    missed: list[MissedClaim]
    judge_errors: int = 0
    # Кластеризація впала → дублікати лишились окремими claims, reference set завищений
    clustering_failed: bool = False
    # missed-виклик впав → reference set занижений, а отже coverage завищений
    missed_measured: bool = True


class ModelPostScore(BaseModel):
    extracted: int
    valid: int
    hallucinated: int
    over_extracted: int
    covered: int
    unmeasured: int = 0  # claims цієї моделі, які суддя не оцінив — виходять зі знаменника
    extraction_failed: bool = False  # екстрактор упав на цьому пості → пост не міряємо


class PostScore(BaseModel):
    post_id: str
    author: str
    stratum: str
    reference_size: int
    per_model: dict[str, ModelPostScore]
    # False = reference set цього поста не заслуговує довіри, coverage по ньому не рахуємо
    coverage_measurable: bool = True


class GateSpec(BaseModel):
    threshold: float
    blocking: bool
    relative_to_baseline: bool = False


class MetricSpec(BaseModel):
    """Дві незалежні здатності метрики. Обидві None = diagnostic."""

    name: str
    gate: GateSpec | None = None
    criterion_priority: int | None = None


class SliceMetrics(BaseModel):
    n_posts: int
    coverage: float | None
    precision: float | None


class ModelMetrics(BaseModel):
    model_id: str
    n_posts: int
    hallucination_rate: float | None
    precision: float | None
    coverage: float | None
    over_extraction_rate: float | None
    determinism: float | None = None
    cost_per_post: float | None = None
    by_author: dict[str, SliceMetrics] = {}
    by_stratum: dict[str, SliceMetrics] = {}


class ExtractionMetrics(BaseModel):
    """Metrics-сабтайп консумера — те, що лягає в EvalReport.metrics."""

    baseline_model: str
    per_model: dict[str, ModelMetrics]
    winner: str | None = None
    decided_by: str | None = None
    flags: list[str] = []
