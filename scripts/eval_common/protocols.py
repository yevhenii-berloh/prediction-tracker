from __future__ import annotations

from typing import Protocol

from eval_common.models import EvalRun, ScoreCard


class Scorer(Protocol):
    name: str

    async def score(self, run: EvalRun) -> ScoreCard:
        """Score one run. If `run.result is None` (the SUT failed), return
        `ScoreCard(score=None)` — do not access `run.result` unguarded."""
        ...
