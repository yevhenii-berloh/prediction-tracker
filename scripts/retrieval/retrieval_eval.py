from __future__ import annotations


def recall_at_k(ranked: list[str], target_id: str, k: int) -> float:
    return 1.0 if target_id in ranked[:k] else 0.0


def reciprocal_rank(ranked: list[str], target_id: str) -> float:
    if target_id in ranked:
        return 1.0 / (ranked.index(target_id) + 1)
    return 0.0


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def aggregate_metrics(results: list[dict], ks: list[int]) -> dict:
    """results: [{source_field, ranked, target_id}]. Повертає метрики overall + по source_field."""
    groups: dict[str, list[dict]] = {"overall": list(results)}
    for r in results:
        groups.setdefault(r["source_field"], []).append(r)
    out: dict[str, dict] = {}
    for name, rows in groups.items():
        metrics = {
            f"recall@{k}": _mean([recall_at_k(r["ranked"], r["target_id"], k) for r in rows])
            for k in ks
        }
        metrics["mrr"] = _mean([reciprocal_rank(r["ranked"], r["target_id"]) for r in rows])
        metrics["n"] = len(rows)
        out[name] = metrics
    return out
