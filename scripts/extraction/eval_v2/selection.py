# scripts/extraction/eval_v2/selection.py
from __future__ import annotations

from extraction.eval_v2.eval_models import ExtractionMetrics, ModelMetrics
from extraction.eval_v2.metrics import HALLUCINATION_GATE, PRECISION_GATE_MARGIN

# (метрика, чи більше — краще) у порядку пріоритету criterion
_CRITERIA = (("coverage", True), ("precision", True), ("cost_per_post", False))


def _passes_gates(candidate: ModelMetrics, baseline: ModelMetrics) -> bool:
    """Невідоме значення не проходить: None — це «не виміряли», а не «добре»."""
    if candidate.hallucination_rate is None or candidate.hallucination_rate > HALLUCINATION_GATE:
        return False
    if candidate.precision is None or baseline.precision is None:
        return False
    return candidate.precision >= baseline.precision - PRECISION_GATE_MARGIN


def _value(metrics: ModelMetrics, name: str, higher_is_better: bool) -> float:
    raw = getattr(metrics, name)
    if raw is None:
        return float("-inf")
    return raw if higher_is_better else -raw


def _best_by(
    survivors: list[ModelMetrics], name: str, higher_is_better: bool, band: float
) -> tuple[ModelMetrics, bool]:
    """Лідер за метрикою і чи відірвався він від другого далі за смугу шуму."""
    ranked = sorted(survivors, key=lambda m: _value(m, name, higher_is_better), reverse=True)
    leader = ranked[0]
    if len(ranked) == 1:
        return leader, True
    gap = _value(leader, name, higher_is_better) - _value(ranked[1], name, higher_is_better)
    return leader, gap > band


def _determinism_flags(survivors: list[ModelMetrics], baseline: ModelMetrics) -> list[str]:
    """non-blocking gate: прапорець вимагає явного людського рішення, але не відсіває."""
    flags: list[str] = []
    for candidate in survivors:
        if candidate.determinism is None or baseline.determinism is None:
            continue
        if candidate.determinism >= baseline.determinism:
            continue
        flags.append(
            f"determinism {candidate.model_id}={candidate.determinism:.2f} "
            f"нижче базлайну {baseline.determinism:.2f} — потрібне явне людське рішення"
        )
    return flags


def _rank(survivors: list[ModelMetrics], noise_band: float) -> tuple[ModelMetrics, str]:
    """Кожен наступний criterion консультується лише якщо попередній не розрізнив."""
    winner = survivors[0]
    decided_by = _CRITERIA[-1][0]
    for name, higher_is_better in _CRITERIA:
        winner, separated = _best_by(survivors, name, higher_is_better, noise_band)
        decided_by = name
        if separated:
            break
    return winner, decided_by


def select(
    per_model: dict[str, ModelMetrics], baseline_id: str, noise_band: float
) -> ExtractionMetrics:
    """Gates відсікають; criterion-и впорядковують решту (крок 6)."""
    baseline = per_model[baseline_id]
    survivors = []
    for metrics in per_model.values():
        if _passes_gates(metrics, baseline):
            survivors.append(metrics)

    if not survivors:
        return ExtractionMetrics(
            baseline_model=baseline_id,
            per_model=per_model,
            winner=None,
            decided_by=None,
            flags=["жоден кандидат не пройшов gates — включно з базлайном"],
        )

    winner, decided_by = _rank(survivors, noise_band)
    return ExtractionMetrics(
        baseline_model=baseline_id,
        per_model=per_model,
        winner=winner.model_id,
        decided_by=decided_by,
        flags=_determinism_flags(survivors, baseline),
    )
