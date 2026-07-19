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
    CITATION_SYSTEM,
    COMPLETENESS_SYSTEM,
    FAITHFULNESS_SYSTEM,
    build_citation_prompt,
    build_completeness_prompt,
    build_faithfulness_prompt,
    parse_citation_response,
    parse_completeness_response,
    parse_faithfulness_response,
)
from generation.sentences import sentence_at
from prophet_checker.query.citations import drop_markers
from prophet_checker.models.domain import CitationRef, RetrievedPrediction


class FaithfulnessScorer:
    name = "faithfulness"

    def __init__(self, judge: Judge) -> None:
        self._judge = judge

    async def score(self, run: EvalRun) -> ScoreCard:
        labels = run.case.labels
        if run.result is None or not labels.answerable:
            return ScoreCard(scorer=self.name, score=None)
        # Суддя оцінює твердження, а не розмітку: маркери [1] прибираються, щоб вхід
        # лишався тотожним доцитатному й базлайн 0.947 не зсунувся
        prompt = build_faithfulness_prompt(
            drop_markers(run.result.answer, keep=set()), run.result.sources
        )
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
        if run.result is None or not run.result.sources:  # порожні sources = refusal → N/A
            return ScoreCard(scorer=self.name, score=None)
        coverage = []
        for s in run.result.sources:
            p = s.prediction
            raw = await self._judge.assess(
                build_completeness_prompt(run.result.answer, p.claim_text, p.situation),
                system=COMPLETENESS_SYSTEM,
            )
            covered, reason = parse_completeness_response(raw)
            coverage.append(SourceCoverage(prediction_id=p.id, covered=covered, reason=reason))
        score = sum(1 for c in coverage if c.covered) / len(coverage)
        return ScoreCard(scorer=self.name, score=score, detail=CompletenessDetail(coverage=coverage))


def _source_by_id(sources: list[RetrievedPrediction], prediction_id: str) -> RetrievedPrediction:
    for source in sources:
        if source.prediction.id == prediction_id:
            return source
    raise KeyError(f"джерело {prediction_id} відсутнє серед поданих")


def citation_coverage(refs: list[CitationRef], expected_ids: list[str]) -> float | None:
    """Скільки різних очікуваних прогнозів реально процитовано. Без судді."""
    expected = set(expected_ids)
    if not expected:
        return None
    cited = set()
    for ref in refs:
        cited.add(ref.prediction_id)
    hit = 0
    for pid in expected:
        if pid in cited:
            hit += 1
    return hit / len(expected)


class CitationPrecisionScorer:
    """Одиниця судження — входження маркера: модель сама окреслила, що чим підпирає."""

    name = "citation_precision"

    def __init__(self, judge: Judge) -> None:
        self._judge = judge

    async def score(self, run: EvalRun) -> ScoreCard:
        if run.result is None or not run.result.refs:
            return ScoreCard(scorer=self.name, score=None)
        supported = 0
        for ref in run.result.refs:
            sentence = sentence_at(run.result.answer, ref.offset)
            source = _source_by_id(run.result.sources, ref.prediction_id)
            raw = await self._judge.assess(
                build_citation_prompt(sentence, source), system=CITATION_SYSTEM
            )
            verdict, _ = parse_citation_response(raw)
            if verdict:
                supported += 1
        return ScoreCard(scorer=self.name, score=supported / len(run.result.refs))


class CitationCoverageScorer:
    """Судді не потребує навмисно: метрика детермінована, зайва залежність це б приховала."""

    name = "citation_coverage"

    async def score(self, run: EvalRun) -> ScoreCard:
        labels = run.case.labels
        if run.result is None or not labels.answerable:
            return ScoreCard(scorer=self.name, score=None)
        expected_ids = []
        for es in labels.expected_sources:
            expected_ids.append(es.prediction.id)
        return ScoreCard(scorer=self.name, score=citation_coverage(run.result.refs, expected_ids))
