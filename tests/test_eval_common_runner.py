from pydantic import BaseModel

from eval_common.models import EvalCase
from eval_common.runner import run_cases


class _In(BaseModel):
    n: int


class _Out(BaseModel):
    n: int


async def test_run_cases_isolates_errors_and_captures_latency():
    cases = [EvalCase(id=str(i), input=_In(n=i)) for i in range(3)]

    async def run_one(case):
        if case.id == "1":
            raise RuntimeError("boom")
        return _Out(n=case.input.n)

    runs = await run_cases(cases, run_one, concurrency=2)
    by_id = {r.case.id: r for r in runs}

    assert len(runs) == 3
    assert by_id["1"].result is None
    assert by_id["1"].error == "RuntimeError"  # тип, не повідомлення
    assert by_id["0"].result is not None
    assert by_id["0"].error is None
    assert all(r.latency_s >= 0.0 for r in runs)


async def test_run_cases_empty_returns_empty():
    async def run_one(case):
        return _Out(n=0)

    assert await run_cases([], run_one) == []


async def test_run_cases_logs_progress(caplog):
    import logging

    cases = [EvalCase(id=str(i), input=_In(n=i)) for i in range(3)]

    async def run_one(case):
        return _Out(n=case.input.n)

    with caplog.at_level(logging.INFO, logger="eval_common.runner"):
        await run_cases(cases, run_one)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("3 cases" in m for m in msgs)  # стартовий рядок
    assert any("3/3" in m for m in msgs)  # фінальний прогрес
