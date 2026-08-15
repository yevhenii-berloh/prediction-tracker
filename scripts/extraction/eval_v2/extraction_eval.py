# scripts/extraction/eval_v2/extraction_eval.py
"""Extraction eval v2 — прогін учасників, суддівство на пост, вибір моделі.

`run_eval()` каркаса не використовується навмисно: прогін багатостадійний,
пулінг і суддівство є окремими стадіями між Runner і Scorer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402

from eval_common.clients import build_eval_llm  # noqa: E402
from eval_common.judge import Judge, LLMJudge, fingerprint_prompt  # noqa: E402
from eval_common.models import (  # noqa: E402
    EvalCase,
    EvalMetadata,
    EvalReport,
    EvalRun,
    ScoreCard,
    ScoredRun,
)
from eval_common.report import write_report  # noqa: E402
from eval_common.runner import run_cases  # noqa: E402
from prophet_checker.analysis.extractor import PredictionExtractor  # noqa: E402

from extraction.eval_v2.dataset import DATASET_PATH, load_eval_dataset  # noqa: E402
from extraction.eval_v2.determinism import (  # noqa: E402
    determinism_score,
    determinism_subsample,
)
from extraction.eval_v2.eval_models import (  # noqa: E402
    ExtractionResult,
    PostJudgement,
    PostScore,
)
from extraction.eval_v2.health import health_flags, run_health  # noqa: E402
from extraction.eval_v2.judge_prompts import (  # noqa: E402
    CHECK_SYSTEM,
    CLUSTER_SYSTEM,
    MISSED_SYSTEM,
)
from extraction.eval_v2.judging import judge_post  # noqa: E402
from extraction.eval_v2.metrics import NOISE_BAND, aggregate  # noqa: E402
from extraction.eval_v2.pool import pool_claims  # noqa: E402
from extraction.eval_v2.scorers import score_post  # noqa: E402
from extraction.eval_v2.selection import select  # noqa: E402

logger = logging.getLogger(__name__)

OUT_DIR = PROJECT_ROOT / "scripts" / "outputs" / "extraction_eval_v2"

# Прод ганяє id `-preview`, але 2026-08-12 перевірено: це той самий модель-сервінг
# (30/30 ідентичних витягів, ті самі токени й ціна). Базлайном береться GA-id.
BASELINE_MODEL = "gemini/gemini-3.1-flash-lite"
PARTICIPANTS = [
    BASELINE_MODEL,
    "deepseek/deepseek-v4-flash",
    "openai/gpt-5.4-nano",
    "gemini/gemini-3.5-flash-lite",
    "openai/gpt-5.4-mini",
]
JUDGE_MODEL = "anthropic/claude-opus-5"

# $/1M токенів, звірено з прайс-сторінками провайдерів 2026-08-08
PRICES = {
    "gemini/gemini-3.1-flash-lite": (0.25, 1.50),
    "deepseek/deepseek-v4-flash": (0.14, 0.28),
    "openai/gpt-5.4-nano": (0.20, 1.25),
    "gemini/gemini-3.5-flash-lite": (0.30, 2.50),
    "openai/gpt-5.4-mini": (0.75, 4.50),
}


def _cost_per_post(llm, model_id: str, n_posts: int) -> float | None:
    price = PRICES.get(model_id)
    if price is None or not n_posts:
        return None
    prompt_price, completion_price = price
    total = (llm.prompt_tokens * prompt_price + llm.completion_tokens * completion_price) / 1e6
    return total / n_posts


async def run_participants(
    cases: list[EvalCase], model_id: str, concurrency: int
) -> tuple[dict[str, ExtractionResult], float | None]:
    """Один прохід прод-екстрактора по всьому датасету (крок 2)."""
    llm = build_eval_llm(model_id, temperature=0)
    extractor = PredictionExtractor(llm)

    async def run_one(case: EvalCase) -> ExtractionResult:
        post = case.input
        outcome = await extractor.extract(
            text=post.text,
            person_id=post.author,
            document_id=post.post_id,
            person_name=post.author,
            published_date=post.published_date,
        )
        return ExtractionResult(predictions=outcome.predictions, failed=outcome.failed)

    runs = await run_cases(cases, run_one, concurrency=concurrency)
    results: dict[str, ExtractionResult] = {}
    for run in runs:
        if run.result is not None:
            results[run.case.id] = run.result
    return results, _cost_per_post(llm, model_id, len(cases))


def _claims_of(by_model: dict[str, dict[str, ExtractionResult]], model_id: str, post_id: str):
    result = by_model[model_id].get(post_id)
    return result.predictions if result is not None else []


async def judge_all_posts(
    cases: list[EvalCase],
    by_model: dict[str, dict[str, ExtractionResult]],
    judge: Judge,
    concurrency: int = 4,
) -> tuple[list[PostScore], list[PostJudgement]]:
    """Кроки 3–5: пулінг, суддівство, reference set — по одному разу на пост."""
    model_ids = list(by_model)

    async def run_one(case: EvalCase) -> PostJudgement:
        post = case.input
        claims_by_model = {}
        for model_id in model_ids:
            claims_by_model[model_id] = _claims_of(by_model, model_id, case.id)
        pooled = await pool_claims(case.id, post.text, claims_by_model, judge)
        return await judge_post(case.id, post.text, pooled, judge)

    runs = await run_cases(cases, run_one, concurrency=concurrency)

    scores: list[PostScore] = []
    judgements: list[PostJudgement] = []
    for run in runs:
        judgement = run.result or PostJudgement(
            post_id=run.case.id, claims=[], verdicts=[], missed=[], judge_errors=1
        )
        counts = {}
        failed_models: set[str] = set()
        for model_id in model_ids:
            counts[model_id] = len(_claims_of(by_model, model_id, run.case.id))
            result = by_model[model_id].get(run.case.id)
            # Немає запису або failed=True — модель цього поста не обробила
            if result is None or result.failed:
                failed_models.add(model_id)
        judgements.append(judgement)
        scores.append(score_post(judgement, run.case.input, model_ids, counts, failed_models))
    return scores, judgements


def build_report(
    cases: list[EvalCase],
    scores: list[PostScore],
    judgements: list[PostJudgement],
    model_ids: list[str],
    baseline_id: str,
    judge_id: str,
    determinism: dict[str, float | None] | None = None,
    costs: dict[str, float | None] | None = None,
) -> EvalReport:
    """Агрегація + правило вибору + per-item деталі (кроки 6–7)."""
    per_model = aggregate(scores, model_ids)
    for model_id, metrics in per_model.items():
        metrics.determinism = (determinism or {}).get(model_id)
        metrics.cost_per_post = (costs or {}).get(model_id)

    metrics = select(per_model, baseline_id, NOISE_BAND)
    metrics.health = run_health(scores, judgements)
    metrics.flags.extend(health_flags(metrics.health))

    scored_runs = []
    for case, judgement, score in zip(cases, judgements, scores, strict=True):
        # result=None навмисно: одиниця звіту — пост, а не вихід однієї моделі;
        # усе, що сказав суддя, лежить у detail карток
        run = EvalRun(case=case, result=None, latency_s=0.0)
        scored_runs.append(
            ScoredRun(
                run=run,
                cards=[
                    ScoreCard(scorer="post_judgement", score=None, detail=judgement),
                    ScoreCard(
                        scorer="reference_size",
                        score=float(score.reference_size),
                        detail=score,
                    ),
                ],
            )
        )

    metadata = EvalMetadata(
        eval_name="extraction_v2",
        created_at=datetime.now(UTC).isoformat(),
        n_cases=len(cases),
        sut_models={model_id: model_id for model_id in model_ids},
        judge_id=judge_id,
        prompt_fingerprints={
            "cluster": fingerprint_prompt(CLUSTER_SYSTEM),
            "check": fingerprint_prompt(CHECK_SYSTEM),
            "missed": fingerprint_prompt(MISSED_SYSTEM),
        },
        dataset_path=str(DATASET_PATH),
    )
    return EvalReport(metadata=metadata, metrics=metrics, runs=scored_runs)


def _dump_stage(name: str, payload: dict) -> None:
    """Проміжний артефакт: підкрутив суддю — не платиш за екстракцію вдруге."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _serialise_extractions(by_model: dict[str, dict[str, ExtractionResult]]) -> dict:
    payload: dict[str, dict] = {}
    for model_id, results in by_model.items():
        rows = {}
        for post_id, result in results.items():
            rows[post_id] = result.model_dump(mode="json")
        payload[model_id] = rows
    return payload


async def _measure_determinism(
    cases: list[EvalCase],
    by_model: dict[str, dict[str, ExtractionResult]],
    concurrency: int,
) -> dict[str, float | None]:
    """Другий прогін по фіксованій підвибірці; перший — уже оплачений основний."""
    subsample = determinism_subsample(cases)
    scores: dict[str, float | None] = {}
    for model_id in PARTICIPANTS:
        second, _ = await run_participants(subsample, model_id, concurrency)
        first = {}
        for case in subsample:
            result = by_model[model_id].get(case.id)
            if result is not None:
                first[case.id] = result
        scores[model_id] = determinism_score(first, second)
    return scores


async def _main(limit: int, concurrency: int) -> None:
    # build_eval_llm читає ключі з os.environ, а не з Settings — без цього прогін
    # падає на «Missing API key», хоча ключі лежать у .env
    load_dotenv(PROJECT_ROOT / ".env")

    cases = load_eval_dataset(DATASET_PATH)
    if limit:
        cases = cases[:limit]
    logger.info("extraction eval v2: %d постів, %d учасників", len(cases), len(PARTICIPANTS))

    by_model: dict[str, dict[str, ExtractionResult]] = {}
    costs: dict[str, float | None] = {}
    for model_id in PARTICIPANTS:
        by_model[model_id], costs[model_id] = await run_participants(cases, model_id, concurrency)
    _dump_stage("extractions.json", _serialise_extractions(by_model))

    determinism = await _measure_determinism(cases, by_model, concurrency)

    judge = LLMJudge(build_eval_llm(JUDGE_MODEL, temperature=None), judge_id=JUDGE_MODEL)
    scores, judgements = await judge_all_posts(cases, by_model, judge, concurrency=concurrency)
    _dump_stage("pooling.json", {j.post_id: j.model_dump(mode="json") for j in judgements})

    report = build_report(
        cases, scores, judgements, PARTICIPANTS, BASELINE_MODEL, JUDGE_MODEL, determinism, costs
    )
    write_report(report, OUT_DIR)
    health = report.metrics.health
    print(f"winner={report.metrics.winner} decided_by={report.metrics.decided_by}")
    print(
        f"health: {health.n_posts} постів, судді збоїв {health.judge_errors} "
        f"на {health.posts_with_judge_errors} постах, "
        f"coverage недостовірний на {health.posts_coverage_unmeasurable}"
    )
    for flag in report.metrics.flags:
        print(f"  flag: {flag}")
    print(f"report → {OUT_DIR}/report.md")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # сторонні бібліотеки логують INFO на кожен запит — топить наш прогрес
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Extraction eval v2")
    parser.add_argument("--limit", type=int, default=0, help="лише перші N постів (0 = усі)")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(_main(args.limit, args.concurrency))


if __name__ == "__main__":
    main()
