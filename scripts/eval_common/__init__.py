from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel

from eval_common.clients import build_eval_llm
from eval_common.fakes import FakeJudge, fake_sut
from eval_common.judge import Judge, LLMJudge, fingerprint_prompt, shuffle_options
from eval_common.models import (
    EvalCase,
    EvalMetadata,
    EvalReport,
    EvalRun,
    ScoreCard,
    ScoredRun,
)
from eval_common.protocols import Scorer
from eval_common.report import write_report
from eval_common.runner import run_cases

__all__ = [
    "EvalCase",
    "EvalRun",
    "ScoreCard",
    "ScoredRun",
    "EvalMetadata",
    "EvalReport",
    "Scorer",
    "Judge",
    "LLMJudge",
    "FakeJudge",
    "fake_sut",
    "build_eval_llm",
    "fingerprint_prompt",
    "shuffle_options",
    "run_cases",
    "write_report",
    "run_eval",
]

logger = logging.getLogger(__name__)


async def run_eval(
    cases: list[EvalCase],
    run_one: Callable[[EvalCase], Awaitable[BaseModel]],
    scorers: list[Scorer],
    aggregate: Callable[[list[ScoredRun]], BaseModel],
    metadata: EvalMetadata,
    out_dir: Path,
    *,
    concurrency: int = 5,
) -> EvalReport:
    """Single-pass eval: run SUT → score each run → aggregate → write report."""
    runs = await run_cases(cases, run_one, concurrency=concurrency)
    total = len(runs)
    logger.info("scoring %d runs (%d scorers)", total, len(scorers))
    scored: list[ScoredRun] = []
    step = max(1, total // 10)  # прогрес ~кожні 10%
    for i, run in enumerate(runs, start=1):
        cards = await asyncio.gather(*(s.score(run) for s in scorers))
        scored.append(ScoredRun(run=run, cards=list(cards)))
        if i % step == 0 or i == total:
            logger.info("scored %d/%d", i, total)
    metrics = aggregate(scored)
    report = EvalReport(metadata=metadata, metrics=metrics, runs=scored)
    write_report(report, out_dir)
    logger.info("wrote report → %s", out_dir)
    return report
