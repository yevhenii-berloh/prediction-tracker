# scripts/generation/metrics.py
from __future__ import annotations

from eval_common.models import ScoredRun
from generation.gen_models import CategoryMetrics, GenerationMetrics


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _cards(run) -> dict:
    return {c.scorer: c for c in run.cards}


def _score(cards: dict, name: str) -> float | None:
    """Оцінка scorer-а за іменем, або None якщо картки нема чи вона N/A."""
    card = cards.get(name)
    return card.score if card is not None else None


# Які метрики збираємо загалом і які з них ще й розрізаємо за категорією
_OVERALL = ("faithfulness", "completeness", "citation_precision", "citation_coverage")
_BUCKETED = {"faithfulness": "faith", "completeness": "recall"}


def _collect(cards: dict, totals: dict, bucket: dict) -> None:
    for name in _OVERALL:
        score = _score(cards, name)
        if score is None:
            continue
        totals[name].append(score)
        key = _BUCKETED.get(name)
        if key is not None:
            bucket[key].append(score)


def aggregate(scored: list[ScoredRun]) -> GenerationMetrics:
    n_total = len(scored)
    n_errors = sum(1 for s in scored if s.run.result is None)

    totals: dict[str, list[float]] = {name: [] for name in _OVERALL}
    by_cat: dict[str, dict[str, list]] = {}

    for s in scored:
        cat = s.run.case.labels.category
        bucket = by_cat.setdefault(cat, {"faith": [], "recall": [], "n": 0})
        bucket["n"] += 1
        _collect(_cards(s), totals, bucket)

    faith = totals["faithfulness"]
    recall = totals["completeness"]
    faithfulness_mean = _mean(faith)
    by_category = {
        cat: CategoryMetrics(
            n=b["n"],
            faithfulness_mean=_mean(b["faith"]),
            recall_mean=_mean(b["recall"]),
        )
        for cat, b in by_cat.items()
    }
    return GenerationMetrics(
        n_total=n_total,
        n_errors=n_errors,
        faithfulness_mean=faithfulness_mean,
        hallucination_rate=(1 - faithfulness_mean) if faithfulness_mean is not None else None,
        recall_mean=_mean(recall),
        citation_precision_mean=_mean(totals["citation_precision"]),
        citation_coverage_mean=_mean(totals["citation_coverage"]),
        by_category=by_category,
    )
