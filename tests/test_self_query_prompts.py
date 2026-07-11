import json
from datetime import date

import pytest

from prophet_checker.llm.prompts import build_self_query_prompt, parse_query_plan
from prophet_checker.models.domain import Person

PERSONS = [Person(id="a1", name="Олексій Арестович")]
KNOWN_IDS = {"a1"}


def _raw(**overrides) -> str:
    plan = {
        "semantic_query": "прогнози про Крим",
        "person_id": None,
        "unknown_author": None,
        "prediction_date_from": None,
        "prediction_date_to": None,
        "target_date_from": None,
        "target_date_to": None,
    }
    plan.update(overrides)
    return json.dumps(plan)


def test_build_prompt_contains_persons_today_question():
    prompt = build_self_query_prompt("Що казав?", PERSONS, today=date(2026, 7, 11))
    assert "Олексій Арестович" in prompt
    assert "a1" in prompt
    assert "2026-07-11" in prompt
    assert "Що казав?" in prompt


def test_parse_valid_full_plan():
    raw = _raw(
        person_id="a1",
        prediction_date_from="2022-01-01",
        prediction_date_to="2022-12-31",
    )
    plan = parse_query_plan(raw, KNOWN_IDS, question="q")
    assert plan.semantic_query == "прогнози про Крим"
    assert plan.filters.person_id == "a1"
    assert plan.filters.prediction_date_from == date(2022, 1, 1)
    assert plan.filters.prediction_date_to == date(2022, 12, 31)


def test_parse_unknown_author_passes_through():
    plan = parse_query_plan(_raw(unknown_author="Портников"), KNOWN_IDS, question="q")
    assert plan.filters.unknown_author == "Портников"
    assert plan.filters.person_id is None


def test_parse_empty_unknown_author_normalized_to_none():
    plan = parse_query_plan(_raw(unknown_author="   "), KNOWN_IDS, question="q")
    assert plan.filters.unknown_author is None


def test_parse_broken_json_raises():
    with pytest.raises(ValueError):
        parse_query_plan("не json", KNOWN_IDS, question="q")


def test_parse_non_object_json_raises():
    with pytest.raises(ValueError, match="non-object"):
        parse_query_plan("[1, 2, 3]", KNOWN_IDS, question="q")


def test_parse_person_id_outside_list_raises():
    with pytest.raises(ValueError, match="unknown person_id"):
        parse_query_plan(_raw(person_id="ghost"), KNOWN_IDS, question="q")


def test_parse_person_and_unknown_author_together_raises():
    raw = _raw(person_id="a1", unknown_author="Хтось")
    with pytest.raises(ValueError, match="mutually exclusive"):
        parse_query_plan(raw, KNOWN_IDS, question="q")


def test_parse_inverted_range_raises():
    raw = _raw(target_date_from="2024-12-31", target_date_to="2024-01-01")
    with pytest.raises(ValueError, match="inverted"):
        parse_query_plan(raw, KNOWN_IDS, question="q")


def test_parse_empty_semantic_query_falls_back_to_question():
    plan = parse_query_plan(_raw(semantic_query="  "), KNOWN_IDS, question="оригінал")
    assert plan.semantic_query == "оригінал"


def test_parse_fenced_json_plan():
    raw = f"```json\n{_raw(person_id='a1')}\n```"
    plan = parse_query_plan(raw, KNOWN_IDS, question="q")
    assert plan.filters.person_id == "a1"


def test_parse_non_string_date_raises():
    with pytest.raises(ValueError, match="must be an ISO string"):
        parse_query_plan(_raw(prediction_date_from=20220101), KNOWN_IDS, question="q")
