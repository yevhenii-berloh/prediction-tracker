import json

from generation.gold import load_generation_gold


def test_load_generation_gold(tmp_path):
    gold = [
        {
            "id": "a000",
            "question": "q1",
            "answerable": True,
            "expected_sources": [{"prediction_id": "p1", "claim": "c1"}],
            "category": "single_source",
        },
        {
            "id": "o000",
            "question": "рецепт",
            "answerable": False,
            "expected_sources": [],
            "category": "off_domain",
        },
    ]
    path = tmp_path / "g.json"
    path.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")

    cases = load_generation_gold(path)
    assert len(cases) == 2
    assert cases[0].id == "a000"
    assert cases[0].input.question == "q1"
    assert cases[0].labels.answerable is True
    assert cases[0].labels.expected_sources[0].prediction_id == "p1"
    assert cases[1].labels.category == "off_domain"
