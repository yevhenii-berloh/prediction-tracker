# scripts/generation/scorers.py
from __future__ import annotations

from eval_common.judge import Judge
from eval_common.models import EvalRun, ScoreCard
from generation.gen_models import (
    CompletenessDetail,
    FaithfulnessDetail,
    SourceCoverage,
)
from generation.judge_prompts import (
    COMPLETENESS_SYSTEM,
    FAITHFULNESS_SYSTEM,
    build_completeness_prompt,
    build_faithfulness_prompt,
    parse_completeness_response,
    parse_faithfulness_response,
)


class FaithfulnessScorer:
    name = "faithfulness"

    def __init__(self, judge: Judge) -> None:
        self._judge = judge

    async def score(self, run: EvalRun) -> ScoreCard:
        labels = run.case.labels
        if run.result is None or not labels.answerable:
            return ScoreCard(scorer=self.name, score=None)
        prompt = build_faithfulness_prompt(run.result.answer, run.result.sources)
        raw = await self._judge.assess(prompt, system=FAITHFULNESS_SYSTEM)
        claims = parse_faithfulness_response(raw)
        if not claims:  # відмова / нефактична відповідь → N/A
            return ScoreCard(scorer=self.name, score=None)
        supported = sum(1 for c in claims if c.supported)
        return ScoreCard(
            scorer=self.name,
            score=supported / len(claims),
            detail=FaithfulnessDetail(claims=claims),
        )


class CompletenessScorer:
    name = "completeness"

    def __init__(self, judge: Judge) -> None:
        self._judge = judge

    async def score(self, run: EvalRun) -> ScoreCard:
        labels = run.case.labels
        if run.result is None or not labels.answerable or not labels.expected_sources:
            return ScoreCard(scorer=self.name, score=None)
        coverage = []
        for es in labels.expected_sources:
            raw = await self._judge.assess(
                build_completeness_prompt(run.result.answer, es.claim), system=COMPLETENESS_SYSTEM
            )
            covered, reason = parse_completeness_response(raw)
            coverage.append(
                SourceCoverage(prediction_id=es.prediction_id, covered=covered, reason=reason)
            )
        score = sum(1 for c in coverage if c.covered) / len(coverage)
        return ScoreCard(
            scorer=self.name, score=score, detail=CompletenessDetail(coverage=coverage)
        )
