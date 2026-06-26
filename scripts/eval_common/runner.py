from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from eval_common.models import EvalCase, EvalRun

logger = logging.getLogger(__name__)


async def run_cases(
    cases: list[EvalCase],
    run_one: Callable[[EvalCase], Awaitable[BaseModel]],
    *,
    concurrency: int = 5,
    min_interval_s: float = 0.0,
) -> list[EvalRun]:
    """Run the SUT (run_one) over each case concurrently; isolate per-case failures."""
    sem = asyncio.Semaphore(concurrency)

    async def _run(case: EvalCase) -> EvalRun:
        async with sem:
            start = time.monotonic()
            result: BaseModel | None
            try:
                result = await run_one(case)
                error = None
            except Exception as exc:  # ізоляція: падіння одного case не валить прогін
                logger.exception("run_one failed: case=%s", case.id)
                result = None
                error = type(exc).__name__
            latency = time.monotonic() - start
            if min_interval_s:
                await asyncio.sleep(min_interval_s)
            return EvalRun(case=case, result=result, latency_s=latency, error=error)

    return list(await asyncio.gather(*(_run(c) for c in cases)))
