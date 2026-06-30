from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import AsyncExitStack
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from eval_common.runner import run_cases  # noqa: E402
from generation.gold import load_generation_gold  # noqa: E402
from prophet_checker.config import Settings  # noqa: E402
from prophet_checker.llm import EmbeddingClient  # noqa: E402
from prophet_checker.query.orchestrator import QueryOrchestrator  # noqa: E402
from prophet_checker.storage.postgres import (  # noqa: E402
    PostgresPredictionRepository,
    PostgresVectorStore,
)
from rag.threshold_sweep import ThresholdReport, sweep_thresholds  # noqa: E402

logger = logging.getLogger(__name__)

# pre-regeneration stub — після Task 5 передавай дато-суфіксований gold через --gold
DEFAULT_GOLD = PROJECT_ROOT / "scripts" / "data" / "generation" / "gold.json"
OUT_DIR = PROJECT_ROOT / "scripts" / "outputs" / "threshold_eval"
TOP_N = 20  # стелю беремо з запасом, щоб sweep мав де відсікати


def _write_report(report: ThresholdReport, recall_target: float) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "threshold_report.json"
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "chosen relevance_threshold = %s (recall_target=%.2f)",
        report.chosen_threshold,
        recall_target,
    )
    if report.chosen_threshold is None:
        logger.warning(
            "жоден поріг не дає recall ≥ %.2f → retrieval слабкий (див. криву у звіті)",
            recall_target,
        )
    print(f"report → {out}")


async def _main(gold_path: Path, limit: int, concurrency: int, recall_target: float) -> None:
    settings = Settings()
    cases = load_generation_gold(gold_path)  # 112: answerable + off-corpus refusal-кейси
    if limit:
        cases = cases[:limit]
    logger.info(
        "threshold eval: %d cases, top_n=%d, concurrency=%d", len(cases), TOP_N, concurrency
    )

    async with AsyncExitStack() as stack:
        engine = create_async_engine(settings.database_url, echo=False)
        stack.push_async_callback(engine.dispose)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        orchestrator = QueryOrchestrator(
            EmbeddingClient(model=settings.embedding_model, api_key=settings.openai_api_key),
            PostgresVectorStore(session_factory),
            PostgresPredictionRepository(session_factory),
            relevance_threshold=None,  # сирий top-k — поріг застосовує sweep
        )

        async def run_one(case):
            return await orchestrator.search(case.input.question, limit=TOP_N)

        runs = await run_cases(cases, run_one, concurrency=concurrency, min_interval_s=0.05)

    report = sweep_thresholds(runs, recall_target=recall_target)
    _write_report(report, recall_target)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(description="Relevance-threshold sweep (retrieval-only)")
    p.add_argument(
        "--gold", type=Path, default=DEFAULT_GOLD, help="дато-суфіксований generation gold"
    )
    p.add_argument("--limit", type=int, default=0, help="перші N кейсів (0 = усі)")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--recall-target", type=float, default=0.9)
    args = p.parse_args()
    asyncio.run(_main(args.gold, args.limit, args.concurrency, args.recall_target))


if __name__ == "__main__":
    main()
