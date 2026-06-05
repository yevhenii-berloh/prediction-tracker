"""Запуск одного циклу верифікації (first-pass): бере unverified прогнози з БД,
проганяє через Verifier (Flash Lite), пише результати назад, друкує report."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import AsyncExitStack
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from prophet_checker.config import Settings
from prophet_checker.factory import build_verification_orchestrator


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    settings = Settings()
    async with AsyncExitStack() as stack:
        orchestrator = await build_verification_orchestrator(settings, stack)
        report = await orchestrator.run_cycle(limit=args.limit)

    print(f"verified={report.verified} failed={report.failed} skipped={report.skipped}")
    for e in report.entries:
        print(f"  {e.prediction_id}: {e.status or ('ERROR ' + (e.error or ''))}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    for _noisy in ("LiteLLM", "litellm", "telethon", "httpx", "httpcore"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    asyncio.run(main())
