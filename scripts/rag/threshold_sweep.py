from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from eval_common.models import EvalRun


class ThresholdPoint(BaseModel):
    threshold: float
    answer_rate: float  # answerable: частка з ≥1 match ≤T
    recall: float  # answerable: середня частка очікуваних джерел, знайдених ≤T
    refusal_rate: float  # off-corpus: частка з 0 matches ≤T


class CategoryBreakdown(BaseModel):
    category: str
    n: int
    answer_rate: float | None = None
    recall: float | None = None
    refusal_rate: float | None = None


class ThresholdReport(BaseModel):
    curve: list[ThresholdPoint]
    chosen_threshold: float | None
    recall_target: float
    by_category_at_chosen: list[CategoryBreakdown]


def _kept_ids(run: EvalRun, t: float) -> set[str]:
    """Id прогнозів, знайдених у межах порога t."""
    results = run.result.results if run.result is not None else []
    return {r.prediction.id for r in results if r.distance <= t}


def _run_recall(run: EvalRun, kept: set[str]) -> float:
    """Частка очікуваних джерел кейса серед знайдених (kept), 0..1."""
    expected = [es.prediction.id for es in run.case.labels.expected_sources]
    if not expected:
        return 0.0
    return sum(1 for e in expected if e in kept) / len(expected)


def _group_by_category(runs: list[EvalRun]) -> dict[str, list[EvalRun]]:
    groups: dict[str, list[EvalRun]] = {}
    for run in runs:
        if run.case.labels is None:  # EvalCase.labels номінально nullable; gold завжди їх ставить
            continue
        groups.setdefault(run.case.labels.category, []).append(run)
    return groups


def _metrics(
    runs: list[EvalRun], t: float
) -> tuple[float | None, float | None, float | None]:
    """(answer_rate, recall, refusal_rate) за порога t; кожна над своєю підмножиною
    (answerable / off-corpus), None — якщо підмножина порожня."""
    answerable = [r for r in runs if r.case.labels is not None and r.case.labels.answerable]
    offcorpus = [r for r in runs if r.case.labels is not None and not r.case.labels.answerable]
    answer_rate = recall = refusal_rate = None
    if answerable:
        kept = [_kept_ids(r, t) for r in answerable]
        answer_rate = sum(1 for k in kept if k) / len(answerable)
        recall = sum(
            _run_recall(r, k) for r, k in zip(answerable, kept, strict=True)
        ) / len(answerable)
    if offcorpus:
        refusal_rate = sum(1 for r in offcorpus if not _kept_ids(r, t)) / len(offcorpus)
    return answer_rate, recall, refusal_rate


def _point(runs: list[EvalRun], t: float) -> ThresholdPoint:
    answer_rate, recall, refusal_rate = _metrics(runs, t)
    return ThresholdPoint(
        threshold=t,
        answer_rate=answer_rate or 0.0,
        recall=recall or 0.0,
        refusal_rate=refusal_rate or 0.0,
    )


def category_breakdown(runs: list[EvalRun], t: float) -> list[CategoryBreakdown]:
    out: list[CategoryBreakdown] = []
    for cat, crs in sorted(_group_by_category(runs).items()):
        answer_rate, recall, refusal_rate = _metrics(crs, t)
        out.append(
            CategoryBreakdown(
                category=cat,
                n=len(crs),
                answer_rate=answer_rate,
                recall=recall,
                refusal_rate=refusal_rate,
            )
        )
    return out


def _observed_distances(runs: list[EvalRun]) -> list[float]:
    """Відсортовані унікальні distance з усіх прогонів — точки переходу метрик."""
    distances: set[float] = set()
    for run in runs:
        if run.result is None:
            continue
        distances.update(r.distance for r in run.result.results)
    return sorted(distances)


def _choose_threshold(curve: list[ThresholdPoint], recall_target: float) -> float | None:
    """Trust-first: серед T із recall ≥ target бере max off-refusal (найменший T при нічиї);
    None — якщо жоден T не дотягує до target."""
    eligible = [p for p in curve if p.recall >= recall_target]
    if not eligible:
        return None
    best_refusal = max(p.refusal_rate for p in eligible)
    return min(p.threshold for p in eligible if p.refusal_rate == best_refusal)


def sweep_thresholds(runs: list[EvalRun], recall_target: float = 0.9) -> ThresholdReport:
    """Retrieval-only sweep: будує криву метрик по спостережених distance й обирає поріг
    trust-first. Кроки винесено в хелпери, щоб тіло лишалось плоским (низька cognitive
    complexity — нема вкладених циклів/розгалужень)."""
    grid = _observed_distances(runs)
    curve = [_point(runs, t) for t in grid]
    chosen = _choose_threshold(curve, recall_target)
    breakdown = category_breakdown(runs, chosen) if chosen is not None else []
    return ThresholdReport(
        curve=curve,
        chosen_threshold=chosen,
        recall_target=recall_target,
        by_category_at_chosen=breakdown,
    )
