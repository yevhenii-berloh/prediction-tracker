import pytest

from extraction.eval_v2.judge_prompts import (
    build_check_prompt,
    build_cluster_prompt,
    build_missed_prompt,
    parse_check_response,
    parse_cluster_response,
    parse_missed_response,
)


def test_parse_cluster_groups_indices():
    assert parse_cluster_response('{"clusters": [[0, 2], [1]]}') == [[0, 2], [1]]


def test_parse_cluster_tolerates_fenced_json():
    assert parse_cluster_response('```json\n{"clusters": [[0]]}\n```') == [[0]]


def test_parse_cluster_tolerates_trailing_prose():
    """Суддя час від часу дописує пояснення після JSON попри інструкцію."""
    raw = '{"clusters": [[0], [1]]}\n\nЯ згрупував їх так, бо...'
    assert parse_cluster_response(raw) == [[0], [1]]


def test_parse_check_reads_the_four_answers():
    verdict = parse_check_response(
        '{"claim_grounded": true, "situation_grounded": false, '
        '"passes_rubric": true, "truncated": false, "reason": "парафраз не простежується"}',
        claim_id="p1:0",
    )
    assert verdict.claim_id == "p1:0"
    assert verdict.is_valid is False
    assert verdict.is_hallucinated is True


def test_parse_check_defaults_truncated_to_false():
    verdict = parse_check_response(
        '{"claim_grounded": true, "situation_grounded": true, "passes_rubric": true}',
        claim_id="p1:0",
    )
    assert verdict.truncated is False
    assert verdict.is_valid is True


def test_parse_check_raises_on_garbage():
    with pytest.raises(ValueError):
        parse_check_response("не json взагалі", claim_id="p1:0")


def test_parse_check_raises_when_an_answer_is_missing():
    with pytest.raises(KeyError):
        parse_check_response('{"claim_grounded": true}', claim_id="p1:0")


def test_parse_missed_returns_claims():
    missed = parse_missed_response('{"missed": [{"text": "Курс впаде", "reason": "прогноз"}]}')
    assert [m.text for m in missed] == ["Курс впаде"]


def test_parse_missed_empty_list_is_valid():
    assert parse_missed_response('{"missed": []}') == []


def test_cluster_prompt_numbers_claims():
    prompt = build_cluster_prompt("текст поста", ["Ціни зростуть", "Ціни не зростуть"])
    assert "0. Ціни зростуть" in prompt
    assert "1. Ціни не зростуть" in prompt
    assert "текст поста" in prompt


def test_check_prompt_carries_post_claim_and_rubric():
    prompt = build_check_prompt("текст поста", "Ціни зростуть", "ситуація")
    assert "текст поста" in prompt
    assert "Ціни зростуть" in prompt
    assert "ситуація" in prompt
    assert "РУБРИКА" in prompt  # чинний EXTRACTION_SYSTEM, не власна рубрика евалу


def test_missed_prompt_marks_an_empty_claim_list():
    prompt = build_missed_prompt("текст поста", [])
    assert "нічого не витягнуто" in prompt
