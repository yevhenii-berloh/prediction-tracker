import json

import pytest

from extraction.build_eval_dataset import (
    build_payload,
    load_excluded_v1_ids,
    post_id_for,
    sample_channel,
)


class RecordingExtractor:
    """Записує, які пости бачив детектор, і кого визнав позитивним."""

    def __init__(self, positive_ids: set[str]) -> None:
        self._positive = positive_ids
        self.seen: list[str] = []

    async def extract(self, *, text, person_id, document_id, person_name, published_date):
        self.seen.append(document_id)
        return ["prediction"] if document_id in self._positive else []


def _candidates(n: int) -> list[dict]:
    posts = []
    for i in range(n):
        posts.append(
            {"id": f"p{i:03d}", "author": "A", "text": "t", "published_date": "2026-06-01"}
        )
    return posts


async def test_random_slice_is_never_shown_to_the_detector():
    """Випадкова strata має бути незаймана: інакше вона анти-фільтрована."""
    candidates = _candidates(40)
    extractor = RecordingExtractor({p["id"] for p in candidates})

    posts = await sample_channel(extractor, candidates, target=10, seed=1)

    random_ids = {p["id"] for p in posts if p["stratum"] == "random"}
    assert random_ids
    assert not (random_ids & set(extractor.seen))


async def test_strata_hit_the_seventy_thirty_split():
    candidates = _candidates(60)
    extractor = RecordingExtractor({p["id"] for p in candidates})

    posts = await sample_channel(extractor, candidates, target=10, seed=1)

    assert sum(1 for p in posts if p["stratum"] == "prefilter") == 7
    assert sum(1 for p in posts if p["stratum"] == "random") == 3


async def test_shortfall_leaves_the_channel_short_rather_than_mixing_strata():
    """Недобір префільтра не добивається випадковими — інакше strata знову змішані."""
    candidates = _candidates(40)
    extractor = RecordingExtractor({"p039"})  # лише один позитивний

    posts = await sample_channel(extractor, candidates, target=10, seed=1)

    assert sum(1 for p in posts if p["stratum"] == "prefilter") == 1
    assert sum(1 for p in posts if p["stratum"] == "random") == 3
    assert len(posts) == 4  # менше за target, і це навмисно


async def test_sampling_is_deterministic_for_a_seed():
    candidates = _candidates(40)
    positives = {p["id"] for p in candidates}

    first = await sample_channel(RecordingExtractor(positives), candidates, 10, seed=5)
    second = await sample_channel(RecordingExtractor(positives), candidates, 10, seed=5)

    assert [p["id"] for p in first] == [p["id"] for p in second]


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
