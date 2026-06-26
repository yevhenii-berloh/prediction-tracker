from __future__ import annotations

import asyncio
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
    scored: list[ScoredRun] = []
    for run in runs:
        cards = await asyncio.gather(*(s.score(run) for s in scorers))
        scored.append(ScoredRun(run=run, cards=list(cards)))
    metrics = aggregate(scored)
    report = EvalReport(metadata=metadata, metrics=metrics, runs=scored)
    write_report(report, out_dir)
    return report
