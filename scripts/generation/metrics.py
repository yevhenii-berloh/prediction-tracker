# scripts/generation/metrics.py
from __future__ import annotations

from eval_common.models import ScoredRun
from generation.gen_models import CategoryMetrics, GenerationMetrics


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _cards(run) -> dict:
    return {c.scorer: c for c in run.cards}


def aggregate(scored: list[ScoredRun]) -> GenerationMetrics:
    n_total = len(scored)
    n_errors = sum(1 for s in scored if s.run.result is None)

    faith: list[float] = []
    recall: list[float] = []
    refusal_scores: list[float] = []
    n_answered = n_refused = 0
    over_num = over_den = false_num = false_den = 0
    by_cat: dict[str, dict[str, list]] = {}

    for s in scored:
        cat = s.run.case.labels.category
        bucket = by_cat.setdefault(cat, {"faith": [], "recall": [], "refusal": [], "n": 0})
        bucket["n"] += 1
        cards = _cards(s)

        f = cards.get("faithfulness")
        if f is not None and f.score is not None:
            faith.append(f.score)
            bucket["faith"].append(f.score)

        c = cards.get("completeness")
        if c is not None and c.score is not None:
            recall.append(c.score)
            bucket["recall"].append(c.score)

        r = cards.get("refusal")
        if r is not None and r.score is not None:
            refusal_scores.append(r.score)
            bucket["refusal"].append(r.score)
            d = r.detail  # RefusalDetail
            if d.refused:
                n_refused += 1
            else:
                n_answered += 1
            if d.answerable:
                over_den += 1
                over_num += 1 if d.refused else 0
            else:
                false_den += 1
                false_num += 1 if not d.refused else 0

    faithfulness_mean = _mean(faith)
    by_category = {
        cat: CategoryMetrics(
            n=b["n"],
            faithfulness_mean=_mean(b["faith"]),
            recall_mean=_mean(b["recall"]),
            refusal_accuracy=_mean(b["refusal"]) or 0.0,
        )
        for cat, b in by_cat.items()
    }
    return GenerationMetrics(
        n_total=n_total,
        n_answered=n_answered,
        n_refused=n_refused,
        n_errors=n_errors,
        faithfulness_mean=faithfulness_mean,
        hallucination_rate=(1 - faithfulness_mean) if faithfulness_mean is not None else None,
        recall_mean=_mean(recall),
        refusal_accuracy=_mean(refusal_scores) or 0.0,
        over_refusal_rate=(over_num / over_den) if over_den else 0.0,
        false_answer_rate=(false_num / false_den) if false_den else 0.0,
        by_category=by_category,
    )
