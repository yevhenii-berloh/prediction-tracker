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
    total = len(cases)
    logger.info("run: %d cases, concurrency=%d", total, concurrency)
    sem = asyncio.Semaphore(concurrency)
    done = 0
    step = max(1, total // 10)  # прогрес ~кожні 10%

    async def _run(case: EvalCase) -> EvalRun:
        nonlocal done
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
        eval_run = EvalRun(case=case, result=result, latency_s=latency, error=error)
        done += 1
        if done % step == 0 or done == total:
            logger.info("run: %d/%d cases done", done, total)
        return eval_run

    return list(await asyncio.gather(*(_run(c) for c in cases)))
