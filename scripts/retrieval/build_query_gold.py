from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import defaultdict
from datetime import date
from itertools import cycle
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from prophet_checker.config import Settings  # noqa: E402
from prophet_checker.llm import LLMClient  # noqa: E402


def _cell(row: dict) -> tuple[str, str]:
    """Стратифікаційна клітинка: (topic, рік)."""
    year = str(row["prediction_date"])[:4]
    return (row.get("topic", ""), year)


def sample_targets(corpus: list[dict], n: int, seed: int) -> list[dict]:
    """Round-robin по клітинках (topic, рік) для рівномірного покриття; детерміновано по seed."""
    rng = random.Random(seed)
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in corpus:
        cells[_cell(row)].append(row)
    for key in cells:
        cells[key].sort(key=lambda r: r["id"])
        rng.shuffle(cells[key])
    keys = sorted(cells.keys())
    picked: list[dict] = []
    seen: set[str] = set()
    for key in cycle(keys):
        if len(picked) >= n or len(seen) >= len(corpus):
            break
        bucket = cells[key]
        if bucket:
            row = bucket.pop()
            picked.append(row)
            seen.add(row["id"])
    return picked[:n]


_EMPHASIS = {
    "claim_text": "на ЗМІСТ — що саме має статися за прогнозом",
    "situation": "на ОБСТАВИНИ — період і подію, за яких зроблено прогноз",
}


def build_query_prompt(row: dict, source_field: str) -> str:
    """Питання для ТРЕКЕРА ПРОГНОЗІВ: ретроспективна перевірка, що автор прогнозував (і чи
    справдилось) — НЕ форкастинг («чи станеться X») і НЕ фактичне питання («що відбулося»)."""
    return (
        "Ти формуєш питання, яке користувач написав би, щоб ПЕРЕВІРИТИ, що автор "
        "прогнозував про цю тему (і чи справдилось). Це ТРЕКЕР ПРОГНОЗІВ — користувач питає "
        "РЕТРОСПЕКТИВНО про вже зроблений прогноз. НЕ проси спрогнозувати майбутнє. "
        "НЕ питай просто факти.\n\n"
        "Прогноз (повний контекст):\n"
        f"- зміст: «{row['claim_text']}»\n"
        f"- обставини: «{row.get('situation') or '—'}»\n"
        f"- дата прогнозу: {row.get('prediction_date') or 'невідома'}\n"
        f"- тема: {row.get('topic') or '—'}\n\n"
        f"Зроби запит з акцентом {_EMPHASIS[source_field]}.\n\n"
        "Правила:\n"
        "- одне коротке природне питання українською;\n"
        "- ОБОВʼЯЗКОВО збережи конкретні якорі: субʼєкт/подію, назви та АБСОЛЮТНИЙ період "
        "(виведи його з дати прогнозу, напр. «наприкінці 2021»);\n"
        "- НЕ копіюй формулювання дослівно — перефразуй;\n"
        "- формулюй як ПЕРЕВІРКУ прогнозу, а не як форкастинг чи фактичне питання.\n\n"
        "Приклади:\n"
        "✗ «чи звільнить Україна території до 2022?» (форкастинг) → "
        "✓ «що прогнозували про звільнення територій до кінця 2022?»\n"
        "✗ «які навчання планували на вересень 2020?» (факт) → "
        "✓ «які прогнози були про навчання тероборони у вересні 2020?»\n"
        "акцент змісту → «що автор прогнозував про [зміст] [період]?»; "
        "акцент обставин → «які прогнози робив на тлі [обставини] [період]?»\n\n"
        "Поверни ЛИШЕ текст запиту, без лапок і пояснень."
    )


async def generate_queries(row: dict, llm) -> list[dict]:
    fields = ["claim_text"]
    if (row.get("situation") or "").strip():
        fields.append("situation")
    records = []
    for field in fields:
        query = (await llm.complete(build_query_prompt(row, field))).strip()
        records.append({"query": query, "target_id": row["id"], "source_field": field})
    return records


CORPUS_PATH = Path("scripts/data/retrieval/corpus.json")
GOLD_PATH = Path("scripts/data/retrieval/query_gold.json")
MANUAL_PATH = Path("scripts/data/retrieval/query_gold_manual.json")


def ensure_manual_stub(path: Path) -> None:
    if not path.exists():
        path.write_text("[]")


async def run_gold(corpus_path: Path, out_path: Path, n: int, seed: int, llm) -> int:
    corpus = json.loads(corpus_path.read_text())
    targets = sample_targets(corpus, n=n, seed=seed)
    records: list[dict] = []
    for row in targets:
        records.extend(await generate_queries(row, llm))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=80)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(f"scripts/data/retrieval/query_gold_{date.today().isoformat()}.json"),
    )
    args = parser.parse_args()
    settings = Settings()
    llm = LLMClient(
        provider="gemini",
        model="gemini-3.1-flash-lite-preview",
        api_key=settings.gemini_api_key,
        temperature=0,
    )
    count = asyncio.run(run_gold(args.corpus, args.out, args.n, args.seed, llm))
    ensure_manual_stub(MANUAL_PATH)
    print(f"gold: {count} queries → {args.out}; ручний зріз: {MANUAL_PATH}")


if __name__ == "__main__":
    main()
