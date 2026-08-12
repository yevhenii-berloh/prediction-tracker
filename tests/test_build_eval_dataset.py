import json

import pytest

from extraction.build_eval_dataset import (
    build_payload,
    load_excluded_v1_ids,
    post_id_for,
    split_strata,
)


def test_excluded_ids_come_from_the_v1_artifact(tmp_path):
    path = tmp_path / "v1.json"
    path.write_text(
        json.dumps({"extractions": {"model-a": {"p1": [], "p2": []}, "model-b": {"p2": []}}}),
        encoding="utf-8",
    )

    assert load_excluded_v1_ids(path) == {"p1", "p2"}


def test_empty_exclusion_list_fails_loud(tmp_path):
    """Порожній список виключень — це зламаний артефакт, а не «нема що виключати»."""
    path = tmp_path / "v1.json"
    path.write_text(json.dumps({"extractions": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="виключень"):
        load_excluded_v1_ids(path)


def test_post_id_matches_the_corpus_format():
    assert post_id_for("@O_Arestovich_official", 7683) == "O_Arestovich_official_7683"


def test_split_strata_respects_the_ratio():
    prefilter, random_part = split_strata(list(range(100)), prefilter_share=0.7, seed=1)

    assert len(prefilter) == 70
    assert len(random_part) == 30
    assert not set(prefilter) & set(random_part)


def test_split_strata_is_deterministic_for_a_seed():
    first, _ = split_strata(list(range(50)), seed=7)
    second, _ = split_strata(list(range(50)), seed=7)

    assert first == second


def test_payload_counts_authors_and_strata():
    posts = [
        {"id": "a1", "author": "Арестович", "stratum": "prefilter"},
        {"id": "a2", "author": "Арестович", "stratum": "random"},
        {"id": "k1", "author": "Кущ", "stratum": "prefilter"},
    ]

    payload = build_payload(posts, ["@a"], "m", excluded_ids={"old1", "old2"})

    assert payload["metadata"]["distribution"]["by_author"] == {"Арестович": 2, "Кущ": 1}
    assert payload["metadata"]["distribution"]["by_stratum"] == {"prefilter": 2, "random": 1}
    assert payload["metadata"]["selection_method"] == "prefilter+random"
    assert payload["metadata"]["excluded_v1_ids"] == ["old1", "old2"]
    assert json.dumps(payload)  # серіалізується


def test_payload_refuses_an_empty_exclusion_list():
    """Артефакт без списку виключень тихо повертає контамінацію через півроку."""
    with pytest.raises(ValueError, match="виключень"):
        build_payload([], ["@a"], "m", excluded_ids=set())
