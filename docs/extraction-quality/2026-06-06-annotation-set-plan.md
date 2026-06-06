# Annotation-set builder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development або superpowers:executing-plans. Кроки — checkbox (`- [ ]`).

**Goal:** Скрипт `scripts/extraction/build_annotation_set.py`, що будує 50/50 JSON-набір (позитиви з БД + негативи через extractor) з порожніми полями score/note для ручної оцінки.

**Architecture:** Один скрипт. Позитиви — async-запит у Postgres (ORM `RawDocumentDB`/`PredictionDB`). Негативи — прогін `PredictionExtractor` на пулі `all.json`, збір постів із 0 claims. `post_url` — чиста функція (тестується). Лінк-only, без вкладеного тексту.

**Tech Stack:** Python 3.12, SQLAlchemy async, LiteLLM (Gemini Flash Lite), pytest (`asyncio_mode=auto`).

**Обмеження:** NO inline comments (module-header docstring OK; `# noqa: E402` дозволено як у sibling-скриптах). `.venv/bin/python`. cwd `/Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker` (префікс `cd`). Українські коміти. Import у тестах: `from extraction.build_annotation_set import post_url` (pythonpath містить `scripts`).

---

### Task 1: Скелет скрипта + `post_url` + хелпери (TDD)

**Files:**
- Create: `scripts/extraction/build_annotation_set.py`
- Create: `tests/test_build_annotation_set.py`

- [ ] **Step 1: Написати падючий тест на `post_url`**

`tests/test_build_annotation_set.py`:
```python
from extraction.build_annotation_set import post_url


def test_post_url_from_db_id_strips_at():
    assert post_url("tg:@O_Arestovich_official:20") == "https://t.me/O_Arestovich_official/20"


def test_post_url_from_alljson_id():
    assert post_url("O_Arestovich_official_7780") == "https://t.me/O_Arestovich_official/7780"
```

- [ ] **Step 2: Прогнати — має впасти (модуль не існує)**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_build_annotation_set.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'extraction.build_annotation_set'`.

- [ ] **Step 3: Створити скрипт зі скелетом + `post_url` + хелпери**

`scripts/extraction/build_annotation_set.py`:
```python
"""Будує набір для ручної оцінки екстракції: 50 постів з передбаченнями (з БД) +
50 без (точковий прогін extractor на all.json) → JSON з порожніми score/note."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from prophet_checker.analysis.extractor import PredictionExtractor  # noqa: E402
from prophet_checker.config import Settings  # noqa: E402
from prophet_checker.llm.client import LLMClient  # noqa: E402
from prophet_checker.models.db import PredictionDB, RawDocumentDB  # noqa: E402

ALL_POSTS = PROJECT_ROOT / "scripts" / "data" / "arestovich" / "all.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "scripts" / "outputs" / "annotation" / "annotation_set.json"
DEFAULT_MODEL = "gemini/gemini-3.1-flash-lite-preview"

PROVIDER_API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def post_url(post_id: str) -> str:
    if post_id.startswith("tg:"):
        channel, _, msg = post_id[3:].rpartition(":")
    else:
        channel, _, msg = post_id.rpartition("_")
    return f"https://t.me/{channel.lstrip('@')}/{msg}"


def _claim_entry(p) -> dict:
    return {
        "claim_text": p.claim_text,
        "situation": p.situation,
        "prediction_date": p.prediction_date.isoformat() if p.prediction_date else None,
        "target_date": p.target_date.isoformat() if p.target_date else None,
        "topic": p.topic,
        "claim_score": None,
        "claim_note": "",
    }


def _post_entry(post_id: str, published_at, has_predictions: bool, source: str, claims: list) -> dict:
    return {
        "post_id": post_id,
        "url": post_url(post_id),
        "published_at": published_at,
        "has_predictions": has_predictions,
        "source": source,
        "post_score": None,
        "post_note": "",
        "claims": claims,
    }
```

- [ ] **Step 4: Прогнати — має пройти**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_build_annotation_set.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Ruff + повний набір**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/ruff check scripts/extraction/build_annotation_set.py tests/test_build_annotation_set.py && .venv/bin/python -m pytest tests/ -q`
Expected: `All checks passed!` + `207 passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/extraction/build_annotation_set.py tests/test_build_annotation_set.py && git commit -m "$(printf 'feat(extraction): annotation-set скелет + post_url\n\npost_url будує t.me-лінк з обох форматів id (tg:@chan:20 / chan_7780),\nприбирає @. + хелпери _post_entry/_claim_entry. TDD на post_url.\n\nCo-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: `load_db_positives` (позитиви з БД)

**Files:**
- Modify: `scripts/extraction/build_annotation_set.py`

- [ ] **Step 1: Додати функцію після `_post_entry`**

```python
async def load_db_positives(session_factory, n: int, min_chars: int, seed: int) -> list[dict]:
    async with session_factory() as session:
        docs = (await session.execute(
            select(RawDocumentDB)
            .where(func.length(RawDocumentDB.raw_text) >= min_chars)
            .where(RawDocumentDB.id.in_(select(PredictionDB.document_id).distinct()))
        )).scalars().all()
        chosen = random.Random(seed).sample(list(docs), min(n, len(docs)))
        result = []
        for doc in chosen:
            preds = (await session.execute(
                select(PredictionDB).where(PredictionDB.document_id == doc.id)
            )).scalars().all()
            published = doc.published_at.date().isoformat() if doc.published_at else None
            result.append(_post_entry(doc.id, published, True, "db", [_claim_entry(p) for p in preds]))
        return result
```

- [ ] **Step 2: Ruff (синтаксис/імпорти)**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/ruff check scripts/extraction/build_annotation_set.py`
Expected: `All checks passed!`

- [ ] **Step 3: Швидка перевірка запиту на реальній БД** (потребує docker `prophet_postgres`)

Run:
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python - <<'PY' 2>&1 | grep -v -iE "LiteLLM"
import asyncio
from dotenv import load_dotenv; load_dotenv(".env", override=True)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from prophet_checker.config import Settings
import sys; sys.path.insert(0, "scripts")
from extraction.build_annotation_set import load_db_positives
async def go():
    eng = create_async_engine(Settings().database_url)
    sf = async_sessionmaker(eng, expire_on_commit=False)
    pos = await load_db_positives(sf, 3, 300, 42)
    await eng.dispose()
    print(f"got {len(pos)} positives")
    if pos: print("sample url:", pos[0]["url"], "| claims:", len(pos[0]["claims"]))
asyncio.run(go())
PY
```
Expected: `got 3 positives`, валідний `https://t.me/...` URL, `claims >= 1`.

- [ ] **Step 4: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/extraction/build_annotation_set.py && git commit -m "$(printf 'feat(extraction): load_db_positives — позитиви з БД + claims\n\nЗапит raw_documents з предікшенами (фільтр довжини), seed-вибірка n,\nмапінг у схему annotation-set.\n\nCo-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

---

### Task 3: `build_extractor` + `collect_extractor_negatives`

**Files:**
- Modify: `scripts/extraction/build_annotation_set.py`

- [ ] **Step 1: Додати `build_extractor` (патерн із run_extraction.py)**

```python
def build_extractor(model_id: str) -> PredictionExtractor:
    provider, model = model_id.split("/", 1)
    env_var = PROVIDER_API_KEY_ENV.get(provider)
    if not env_var:
        raise ValueError(f"Unknown provider {provider!r}")
    api_key = os.environ.get(env_var)
    if not api_key:
        raise RuntimeError(f"Missing API key for {provider!r}: set {env_var}")
    client = LLMClient(provider=provider, model=model, api_key=api_key, temperature=0.0)
    return PredictionExtractor(client)
```

- [ ] **Step 2: Додати `collect_extractor_negatives`**

```python
async def collect_extractor_negatives(
    posts, extractor, n: int, min_chars: int, seed: int, max_extractions: int, exclude_urls: set
) -> list[dict]:
    eligible = [
        p for p in posts
        if p.get("person_name") == "Арестович"
        and len(p.get("text", "")) >= min_chars
        and post_url(p["id"]) not in exclude_urls
    ]
    random.Random(seed).shuffle(eligible)
    negatives, tried = [], 0
    for p in eligible:
        if len(negatives) >= n or tried >= max_extractions:
            break
        tried += 1
        try:
            preds = await extractor.extract(
                text=p["text"], person_id=p["person_name"], document_id=p["id"],
                person_name=p["person_name"], published_date=p["published_at"],
            )
        except Exception as exc:
            print(f"  skip {p['id']}: {type(exc).__name__}: {exc}", flush=True)
            continue
        if not preds:
            published = str(p["published_at"])[:10]
            negatives.append(_post_entry(p["id"], published, False, "extractor_pool", []))
        print(f"  [neg {len(negatives)}/{n}] tried={tried} {p['id']}: {len(preds)} claims", flush=True)
    return negatives
```

- [ ] **Step 3: Ruff**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/ruff check scripts/extraction/build_annotation_set.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/extraction/build_annotation_set.py && git commit -m "$(printf 'feat(extraction): collect_extractor_negatives + build_extractor\n\nПрогін extractor на пулі all.json, збір постів з 0 claims до n або кепу;\nseed-перемішування, виключення позитивів, skip+log на помилках.\n\nCo-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

---

### Task 4: `main()` + CLI + запис JSON

**Files:**
- Modify: `scripts/extraction/build_annotation_set.py`

- [ ] **Step 1: Додати `main()` і `__main__`**

```python
async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--min-chars", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-extractions", type=int, default=300)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--created", default=date.today().isoformat())
    args = parser.parse_args()

    n_pos = args.n // 2
    n_neg = args.n - n_pos

    engine = create_async_engine(Settings().database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    positives = await load_db_positives(session_factory, n_pos, args.min_chars, args.seed)
    await engine.dispose()
    print(f"positives from DB: {len(positives)}/{n_pos}")
    if len(positives) < n_pos:
        print(f"  WARN: лише {len(positives)} позитивів у БД (треба {n_pos})")

    posts = json.load(open(ALL_POSTS))
    exclude = {p["url"] for p in positives}
    extractor = build_extractor(args.model)
    negatives = await collect_extractor_negatives(
        posts, extractor, n_neg, args.min_chars, args.seed, args.max_extractions, exclude)
    print(f"negatives via extractor: {len(negatives)}/{n_neg}")
    if len(negatives) < n_neg:
        print(f"  WARN: лише {len(negatives)} негативів (пул/кеп вичерпано)")

    all_posts = positives + negatives
    random.Random(args.seed).shuffle(all_posts)
    out = {
        "meta": {
            "created": args.created,
            "model": args.model,
            "n": len(all_posts),
            "with_predictions": len(positives),
            "without_predictions": len(negatives),
            "seed": args.seed,
            "min_chars": args.min_chars,
        },
        "posts": all_posts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"saved → {args.output}  ({len(all_posts)} posts: {len(positives)} pos / {len(negatives)} neg)")


if __name__ == "__main__":
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    asyncio.run(main())
```

- [ ] **Step 2: Ruff + `--help` смоук + повний набір**

Run:
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && \
.venv/bin/ruff check scripts/extraction/build_annotation_set.py && \
.venv/bin/python scripts/extraction/build_annotation_set.py --help >/dev/null && echo "help OK" && \
.venv/bin/python -m pytest tests/ -q | tail -1
```
Expected: `All checks passed!`, `help OK`, `207 passed`.

- [ ] **Step 3: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/extraction/build_annotation_set.py && git commit -m "$(printf 'feat(extraction): main() annotation-set — CLI + злиття + запис JSON\n\nАргументи --n/--min-chars/--seed/--model/--max-extractions/--output;\nмета-блок з фактичними лічильниками; приглушено LiteLLM-логи.\n\nCo-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

---

### Task 5: Реальний прогін + валідація

**Files:** жодних (генерує `scripts/outputs/annotation/annotation_set.json`, gitignored через `scripts/outputs/`).

- [ ] **Step 1: Малий прогін для перевірки (n=10, дешево)**

Run:
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && \
.venv/bin/python scripts/extraction/build_annotation_set.py --n 10 --output scripts/outputs/annotation/_smoke.json 2>&1 | grep -v -iE "LiteLLM" | tail -15
```
Expected: лог `positives from DB: 5/5`, `[neg k/5 ...]`, `saved → ... (10 posts: 5 pos / 5 neg)`.

- [ ] **Step 2: Перевірити структуру JSON**

Run:
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "
import json
d=json.load(open('scripts/outputs/annotation/_smoke.json'))
m=d['meta']; print('meta:', m['with_predictions'],'pos /',m['without_predictions'],'neg')
pos=[p for p in d['posts'] if p['has_predictions']]; neg=[p for p in d['posts'] if not p['has_predictions']]
assert all(p['url'].startswith('https://t.me/') and '@' not in p['url'] for p in d['posts']), 'bad url'
assert all(p['post_score'] is None and p['post_note']=='' for p in d['posts']), 'score not empty'
assert all(c['claim_score'] is None for p in pos for c in p['claims']), 'claim_score not empty'
assert all(p['claims']==[] for p in neg), 'neg has claims'
assert all(p['claims'] for p in pos), 'pos missing claims'
print('OK: urls clean, score/note empty, pos have claims, neg empty')
"
```
Expected: `OK: urls clean, score/note empty, pos have claims, neg empty`.

- [ ] **Step 3: Повний прогін (n=100)** — фінальний артефакт

Run:
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && \
.venv/bin/python scripts/extraction/build_annotation_set.py --n 100 2>&1 | grep -v -iE "LiteLLM" | tail -8
```
Expected: `saved → scripts/outputs/annotation/annotation_set.json (100 posts: 50 pos / 50 neg)` (або warn, якщо позитивів у БД <50 — тоді менше; це нормально). Файл готовий до ручного заповнення.

---

## Self-Review

**Spec coverage:** N постів/довжина → `--n`/`--min-chars` (T4); 50/50 → `n_pos/n_neg` (T4) + DB(T2)/extractor(T3); посилання → `post_url` (T1); JSON → T4; порожні score/note на пості й claim-і → `_post_entry`/`_claim_entry` (T1); edge-cases warn → T4; валідація → T5.

**Placeholders:** немає — увесь код повний, команди конкретні.

**Type consistency:** `post_url`, `_post_entry`, `_claim_entry` визначені в T1 і використовуються в T2/T3/T4 із тими самими сигнатурами; `load_db_positives`(T2)/`collect_extractor_negatives`(T3)/`build_extractor`(T3) викликаються в `main`(T4) з тими самими аргументами. `_claim_entry` приймає об'єкт із `.claim_text/.situation/.prediction_date/.target_date/.topic` — збігається з `PredictionDB`.

**Ризик:** якщо в БД <50 постів ≥`min_chars` з предікшенами — позитивів буде менше 50 (warn, не помилка). Користувач може знизити `--min-chars` або донаповнити БД.
