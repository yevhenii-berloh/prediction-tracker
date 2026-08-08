import json

import pytest

from extraction.eval_v2.dataset import load_eval_dataset

_POST = {
    "id": "O_Arestovich_official_9001",
    "author": "Арестович",
    "channel": "@O_Arestovich_official",
    "text": "Ціни зростуть.",
    "published_date": "2026-06-01",
    "stratum": "prefilter",
    "url": "https://t.me/O_Arestovich_official/9001",
}


def _write(tmp_path, posts):
    path = tmp_path / "ds.json"
    path.write_text(json.dumps({"metadata": {}, "posts": posts}), encoding="utf-8")
    return path


def test_loads_posts_into_cases(tmp_path):
    cases = load_eval_dataset(_write(tmp_path, [_POST]))
    assert len(cases) == 1
    assert cases[0].id == "O_Arestovich_official_9001"
    assert cases[0].input.stratum == "prefilter"
    assert cases[0].labels is None  # reference-free за побудовою


def test_missing_field_fails_loud(tmp_path):
    broken = {**_POST}
    del broken["published_date"]
    with pytest.raises(ValueError, match="O_Arestovich_official_9001"):
        load_eval_dataset(_write(tmp_path, [broken]))


def test_unknown_stratum_fails_loud(tmp_path):
    with pytest.raises(ValueError, match="stratum"):
        load_eval_dataset(_write(tmp_path, [{**_POST, "stratum": "handpicked"}]))


def test_record_without_id_still_names_itself_in_the_error(tmp_path):
    anonymous = {k: v for k, v in _POST.items() if k != "id"}
    with pytest.raises(ValueError, match="без id"):
        load_eval_dataset(_write(tmp_path, [anonymous]))


def test_url_is_optional(tmp_path):
    no_url = {k: v for k, v in _POST.items() if k != "url"}
    cases = load_eval_dataset(_write(tmp_path, [no_url]))
    assert cases[0].input.url == ""
