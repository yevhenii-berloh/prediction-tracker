import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakes import FakePersonRepo

from prophet_checker.models.domain import Person
from prophet_checker.query.planner import QueryPlanner, QueryPlanningError

VALID_PLAN = json.dumps(
    {
        "semantic_query": "прогнози про Крим",
        "person_id": "a1",
        "unknown_author": None,
        "prediction_date_from": "2022-01-01",
        "prediction_date_to": "2022-12-31",
        "target_date_from": None,
        "target_date_to": None,
    }
)


def _llm(response: str | Exception) -> MagicMock:
    llm = MagicMock()
    if isinstance(response, Exception):
        llm.complete = AsyncMock(side_effect=response)
    else:
        llm.complete = AsyncMock(return_value=response)
    return llm


async def _repo() -> FakePersonRepo:
    repo = FakePersonRepo()
    await repo.save(Person(id="a1", name="Олексій Арестович"))
    return repo


async def test_plan_happy_path():
    planner = QueryPlanner(_llm(VALID_PLAN), await _repo())
    plan = await planner.plan("Що Арестович казав про Крим у 2022?")
    assert plan.semantic_query == "прогнози про Крим"
    assert plan.filters.person_id == "a1"


async def test_plan_prompt_contains_persons():
    llm = _llm(VALID_PLAN)
    planner = QueryPlanner(llm, await _repo())
    await planner.plan("питання")
    prompt = llm.complete.call_args.args[0]
    assert "Олексій Арестович" in prompt
    assert "питання" in prompt


async def test_llm_exception_wrapped():
    planner = QueryPlanner(_llm(RuntimeError("api down")), await _repo())
    with pytest.raises(QueryPlanningError):
        await planner.plan("питання")


async def test_unparseable_response_wrapped():
    planner = QueryPlanner(_llm("це не json"), await _repo())
    with pytest.raises(QueryPlanningError):
        await planner.plan("питання")
