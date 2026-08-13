# scripts/extraction/eval_v2/metrics.py
from __future__ import annotations

from extraction.eval_v2.eval_models import (
    GateSpec,
    MetricSpec,
    ModelMetrics,
    ModelPostScore,
    PostScore,
    SliceMetrics,
)

# Пороги, які знімаються з першого прогону — до нього обидва нульові й нічого не ріжуть
PRECISION_GATE_MARGIN = 0.0  # from first run (P3): наскільки нижче базлайну ще прийнятно
NOISE_BAND = 0.0  # from first run (P2)
HALLUCINATION_GATE = 0.02

METRIC_SPECS = [
    MetricSpec(
        name="hallucination_rate",
        gate=GateSpec(threshold=HALLUCINATION_GATE, blocking=True),
    ),
    MetricSpec(
        name="precision",
        gate=GateSpec(threshold=PRECISION_GATE_MARGIN, blocking=True, relative_to_baseline=True),
        criterion_priority=2,
    ),
    MetricSpec(name="determinism", gate=GateSpec(threshold=1.0, blocking=False)),
    MetricSpec(name="coverage", criterion_priority=1),
    MetricSpec(name="cost_per_post", criterion_priority=3),
    MetricSpec(name="over_extraction_rate"),  # diagnostic: не відсікає й не впорядковує
]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


class _Bucket:
    """Частки по постах; macro-усереднення робиться в кінці, вже над списком."""

    def __init__(self) -> None:
        self.precision: list[float] = []
        self.coverage: list[float] = []
        self.hallucination: list[float] = []
        self.over_extraction: list[float] = []
        self.n_posts = 0

    def add(self, row: ModelPostScore, reference_size: int) -> None:
        self.n_posts += 1
        # Пост із нулем витягнутих виходить із precision-подібних метрик: ділити нема на що.
        # З coverage він НЕ виходить — мовчання це провал покриття, а не ідеальна точність.
        # Не суджені claims теж виходять зі знаменника: збій судді не має псувати модель.
        judged = row.extracted - row.unmeasured
        if judged > 0:
            self.precision.append(row.valid / judged)
            self.hallucination.append(row.hallucinated / judged)
            self.over_extraction.append(row.over_extracted / judged)
        if reference_size:
            self.coverage.append(row.covered / reference_size)

    def slice_metrics(self) -> SliceMetrics:
        return SliceMetrics(
            n_posts=self.n_posts,
            coverage=_mean(self.coverage),
            precision=_mean(self.precision),
        )


def _slices(buckets: dict[str, _Bucket]) -> dict[str, SliceMetrics]:
    return {key: bucket.slice_metrics() for key, bucket in buckets.items()}


def _collect(scores: list[PostScore], model_id: str) -> tuple[_Bucket, dict, dict]:
    overall = _Bucket()
    by_author: dict[str, _Bucket] = {}
    by_stratum: dict[str, _Bucket] = {}

    for score in scores:
        row = score.per_model.get(model_id)
        if row is None:
            continue
        overall.add(row, score.reference_size)
        by_author.setdefault(score.author, _Bucket()).add(row, score.reference_size)
        by_stratum.setdefault(score.stratum, _Bucket()).add(row, score.reference_size)

    return overall, by_author, by_stratum


def aggregate(scores: list[PostScore], model_ids: list[str]) -> dict[str, ModelMetrics]:
    """Macro по постах; кожна метрика ще й у розрізі авторів і strata."""
    result: dict[str, ModelMetrics] = {}

    for model_id in model_ids:
        overall, by_author, by_stratum = _collect(scores, model_id)
        result[model_id] = ModelMetrics(
            model_id=model_id,
            n_posts=overall.n_posts,
            hallucination_rate=_mean(overall.hallucination),
            precision=_mean(overall.precision),
            coverage=_mean(overall.coverage),
            over_extraction_rate=_mean(overall.over_extraction),
            by_author=_slices(by_author),
            by_stratum=_slices(by_stratum),
        )

    return result
