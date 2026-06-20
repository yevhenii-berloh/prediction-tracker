from retrieval.build_query_gold import sample_targets


def _row(rid, topic, dt):
    return {"id": rid, "topic": topic, "prediction_date": dt, "claim_text": "c", "situation": "s"}


def test_sample_is_deterministic_for_seed():
    corpus = [
        _row(str(i), "війна" if i % 2 else "економіка", f"2024-0{i % 9 + 1}-01") for i in range(20)
    ]
    a = [r["id"] for r in sample_targets(corpus, n=6, seed=42)]
    b = [r["id"] for r in sample_targets(corpus, n=6, seed=42)]
    assert a == b


def test_sample_size_capped_at_corpus():
    corpus = [_row("a", "війна", "2024-01-01"), _row("b", "війна", "2024-02-01")]
    assert len(sample_targets(corpus, n=10, seed=1)) == 2


def test_sample_spreads_across_topics():
    corpus = [_row(f"w{i}", "війна", "2024-01-01") for i in range(10)]
    corpus += [_row(f"e{i}", "економіка", "2024-01-01") for i in range(10)]
    ids = [r["id"] for r in sample_targets(corpus, n=4, seed=7)]
    assert any(i.startswith("w") for i in ids) and any(i.startswith("e") for i in ids)
