from retrieval.retrieval_eval import aggregate_metrics, recall_at_k, reciprocal_rank


def test_recall_at_k_hit_and_miss():
    assert recall_at_k(["x", "a", "y"], "a", 2) == 1.0  # на позиції 2 (індекс 1) → у топ-2
    assert recall_at_k(["x", "y", "a"], "a", 2) == 0.0  # на позиції 3 → не в топ-2


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b"], "a") == 1.0
    assert reciprocal_rank(["b", "a"], "a") == 0.5
    assert reciprocal_rank(["b", "c"], "a") == 0.0


def test_aggregate_splits_by_source_field():
    results = [
        {"source_field": "claim_text", "ranked": ["a"], "target_id": "a"},  # hit@1
        {"source_field": "situation", "ranked": ["x", "b"], "target_id": "b"},  # hit@5, miss@1
    ]
    agg = aggregate_metrics(results, ks=[1, 5])
    assert agg["overall"]["recall@1"] == 0.5
    assert agg["claim_text"]["recall@1"] == 1.0
    assert agg["situation"]["recall@1"] == 0.0
    assert agg["situation"]["recall@5"] == 1.0
