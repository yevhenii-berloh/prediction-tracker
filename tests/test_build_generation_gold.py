import pytest

from generation.build_generation_gold import build_gold


def _retrieval():
    return [
        {"query": "claim-фраза A", "target_id": "t1", "source_field": "claim_text"},
        {"query": "situation-фраза A", "target_id": "t1", "source_field": "situation"},
        {"query": "claim-фраза B", "target_id": "t2", "source_field": "claim_text"},
        {"query": "situation-фраза B", "target_id": "t2", "source_field": "situation"},
    ]


def _claims():
    return {"t1": "клейм-1", "t2": "клейм-2", "s1": "синтез-клейм"}


def test_build_gold_single_source_5050_and_enrichment():
    manual = [
        {"question": "синтез?", "category": "synthesis", "prediction_ids": ["t1", "s1"]},
        {"question": "рецепт борщу", "category": "off_domain", "prediction_ids": []},
    ]
    out = build_gold(_retrieval(), manual, _claims())

    single = [r for r in out if r["category"] == "single_source"]
    assert len(single) == 2
    # 50/50: t1 (idx0) → claim-фраза, t2 (idx1) → situation-фраза
    by_tid = {r["expected_sources"][0]["prediction_id"]: r for r in single}
    assert by_tid["t1"]["question"] == "claim-фраза A"
    assert by_tid["t2"]["question"] == "situation-фраза B"
    assert by_tid["t1"]["expected_sources"][0]["claim"] == "клейм-1"  # збагачено

    syn = next(r for r in out if r["category"] == "synthesis")
    assert syn["answerable"] is True
    assert {e["prediction_id"] for e in syn["expected_sources"]} == {"t1", "s1"}
    assert {e["claim"] for e in syn["expected_sources"]} == {"клейм-1", "синтез-клейм"}

    off = next(r for r in out if r["category"] == "off_domain")
    assert off["answerable"] is False
    assert off["expected_sources"] == []


def test_build_gold_failloud_on_unknown_prediction():
    manual = [{"question": "x", "category": "synthesis", "prediction_ids": ["NOPE"]}]
    with pytest.raises(KeyError):
        build_gold(_retrieval(), manual, _claims())
