# scripts/generation/generation_eval.py
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from eval_common import EvalMetadata, run_eval  # noqa: E402
from eval_common.clients import build_eval_llm  # noqa: E402
from eval_common.judge import LLMJudge, fingerprint_prompt  # noqa: E402
from generation.gold import load_generation_gold  # noqa: E402
from generation.judge_prompts import COMPLETENESS_SYSTEM, FAITHFULNESS_SYSTEM  # noqa: E402
from generation.metrics import aggregate  # noqa: E402
from generation.scorers import (  # noqa: E402
    CitationCoverageScorer,
    CitationPrecisionScorer,
    CompletenessScorer,
    FaithfulnessScorer,
)
from prophet_checker.config import Settings  # noqa: E402
from prophet_checker.llm import LLMClient  # noqa: E402
from prophet_checker.models.domain import RetrievedPrediction  # noqa: E402
from prophet_checker.query.answer_orchestrator import AnswerOrchestrator  # noqa: E402

logger = logging.getLogger(__name__)

GOLD_PATH = PROJECT_ROOT / "scripts" / "data" / "generation" / "gold.json"
OUT_DIR = PROJECT_ROOT / "scripts" / "outputs" / "generation_eval"


async def _main(judge_model: str, limit: int, concurrency: int) -> None:
    settings = Settings()
    cases = load_generation_gold(GOLD_PATH)
    cases = [c for c in cases if c.labels.answerable]  # v2: лише answerable — gold ізолює генерацію
    if limit:  # 0 = усі; інакше — перші N
        cases = cases[:limit]
    judge = LLMJudge(build_eval_llm(judge_model, temperature=0), judge_id=judge_model)
    scorers = [
        FaithfulnessScorer(judge),
        CompletenessScorer(judge),
        CitationPrecisionScorer(judge),
        CitationCoverageScorer(),
    ]
    logger.info(
        "generation eval: %d cases, judge=%s, concurrency=%d", len(cases), judge_model, concurrency
    )

    metadata = EvalMetadata(
        eval_name="generation",
        created_at=datetime.now(UTC).isoformat(),
        n_cases=len(cases),
        sut_models={"generator": "gemini/gemini-3.1-flash-lite-preview"},
        judge_id=judge_model,
        prompt_fingerprints={
            "faithfulness": fingerprint_prompt(FAITHFULNESS_SYSTEM),
            "completeness": fingerprint_prompt(COMPLETENESS_SYSTEM),
        },
        dataset_path=str(GOLD_PATH),
    )

    llm = LLMClient(
        provider="gemini",
        model="gemini-3.1-flash-lite-preview",
        api_key=settings.gemini_api_key,
        temperature=0,
    )
    # citations_enabled вмикає сам скрипт, а не навколишній .env: у проді прапорець
    # спершу False, і eval мовчки міряв би відповіді без цитат з coverage 0
    orchestrator = AnswerOrchestrator(llm, citations_enabled=True)  # generate-only

    async def run_one(case):
        sources = [
            RetrievedPrediction(prediction=es.prediction, distance=0.0, rank=i)
            for i, es in enumerate(case.labels.expected_sources, 1)
        ]
        return await orchestrator.answer_from_sources(case.input.question, sources)

    report = await run_eval(
        cases, run_one, scorers, aggregate, metadata, OUT_DIR, concurrency=concurrency
    )

    m = report.metrics
    logger.info(
        "generation eval: n=%d faithfulness=%.3f recall=%.3f",
        m.n_total,
        m.faithfulness_mean or 0.0,
        m.recall_mean or 0.0,
    )
    print(f"report → {OUT_DIR}/report.md  (judge-based, ще не human-calibrated)")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # сторонні бібліотеки логують INFO на кожен запит — топить наш прогрес
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(
        description="Generation eval v2 (faithfulness + completeness, isolated on frozen gold)"
    )
    p.add_argument("--judge", default="anthropic/claude-opus-4-8")
    p.add_argument("--limit", type=int, default=0, help="run only first N cases (0 = all)")
    p.add_argument("--concurrency", type=int, default=4)
    args = p.parse_args()
    asyncio.run(_main(args.judge, args.limit, args.concurrency))


if __name__ == "__main__":
    main()
