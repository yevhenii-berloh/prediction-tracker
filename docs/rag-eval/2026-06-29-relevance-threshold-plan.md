# Relevance threshold (A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Зробити refusal детермінованим: налаштувати поріг релевантності офлайн-sweep'ом (retrieval-only) і застосувати його в проді (`QueryOrchestrator`), а також переформулювати query-gold-питання на prediction-centric.

**Architecture:** Реалізація за [дизайном A](2026-06-29-relevance-threshold-design.md). Прод: `Settings.relevance_threshold` + фільтр `distance ≤ threshold` у `QueryOrchestrator.search`. Eval: `scripts/rag/threshold_eval.py` ганяє реальний retrieval по gold (без LLM) → `sweep_thresholds` рахує криву й обирає T (trust-first). Питання: рерайт `build_query_prompt` на ретроспективну перевірку прогнозу.

**Tech Stack:** Python 3.14, Pydantic v2, pytest (`asyncio_mode=auto`), ruff (line 100). Venv `.venv`. Сюїта: `.venv/bin/python -m pytest tests/ -q`.

**Гілка:** нова feature-гілка від `main` (напр. `relevance-threshold`), merge у `main` по завершенні. Коміти — conventional commits українською.

**Інваріант:** після кожного коміту `pytest tests/ -q` зелений. `threshold_eval.py` НЕ в сюїті (інтеграційний CLI).

**Дато-конвенція (CLAUDE.md):** регенеровані файли — дато-суфіксовані; консьюмери беруть явний шлях (CLI `--gold`/`--out`).

---

## File structure

| Файл | Роль | Зміна |
|------|------|-------|
| `src/prophet_checker/config.py` | Settings | + `relevance_threshold: float \| None = None` |
| `src/prophet_checker/query/orchestrator.py` | retrieval | + опц. `relevance_threshold`; фільтр matches |
| `src/prophet_checker/factory.py` | composition | передати поріг із Settings |
| `tests/test_query_orchestrator.py` | тест | threshold-фільтр + None=без фільтра |
| `scripts/retrieval/build_query_gold.py` | gold-build | рерайт `build_query_prompt` (prediction-centric) + дато-вихід |
| `tests/test_retrieval_query_gold.py` | тест | guard на prediction-centric промпт |
| `scripts/rag/__init__.py` | пакет | новий |
| `scripts/rag/threshold_sweep.py` | sweep-логіка | `ThresholdReport` + `sweep_thresholds` + `category_breakdown` (чисті) |
| `tests/test_threshold_sweep.py` | тест | sweep на іграшковому прикладі з дизайну |
| `scripts/rag/threshold_eval.py` | CLI | retrieval-прогін + sweep + звіт |

---

## Task 1: Прод-поріг релевантності

**Скоуп:** прод-механізм порога — `Settings.relevance_threshold` + фільтр `distance ≤ T` у `QueryOrchestrator.search` + проводка через factory. Дефолт `None` = поточна поведінка; робить refusal детермінованим, коли поріг виставлено.

**Files:**
- Modify: `src/prophet_checker/config.py`
- Modify: `src/prophet_checker/query/orchestrator.py`
- Modify: `src/prophet_checker/factory.py:83-92`
- Test: `tests/test_query_orchestrator.py`

- [ ] **Step 1: Додати тести threshold**

У `tests/test_query_orchestrator.py` додай (наприкінці файлу; `_embedder`, `Prediction`, `FakeVectorStore`, `FakePredictionRepo`, `date` уже імпортовані вгорі):

```python
async def _store_repo_p1_p2():
    store = FakeVectorStore()
    # FakeVectorStore: distance = порядок вставки (0-based) → p1=0.0, p2=1.0
    await store.store_embedding("p1", [0.1, 0.1, 0.1])
    await store.store_embedding("p2", [0.2, 0.2, 0.2])
    repo = FakePredictionRepo()
    for pid in ("p1", "p2"):
        await repo.save(
            Prediction(id=pid, document_id="d", person_id="x", claim_text=pid, prediction_date=date(2024, 1, 1))
        )
    return store, repo


async def test_search_applies_relevance_threshold():
    store, repo = await _store_repo_p1_p2()
    orch = QueryOrchestrator(_embedder(), store, repo, relevance_threshold=0.5)
    result = await orch.search("q", limit=10)
    assert [r.prediction.id for r in result.results] == ["p1"]  # p2@1.0 > 0.5 відсічено


async def test_search_threshold_none_keeps_all():
    store, repo = await _store_repo_p1_p2()
    orch = QueryOrchestrator(_embedder(), store, repo)  # дефолт None
    result = await orch.search("q", limit=10)
    assert [r.prediction.id for r in result.results] == ["p1", "p2"]  # без фільтра
```

- [ ] **Step 2: Запусти — має впасти**

Run: `.venv/bin/python -m pytest tests/test_query_orchestrator.py -q`
Expected: FAIL — `QueryOrchestrator.__init__` ще не приймає `relevance_threshold` (`TypeError`).

- [ ] **Step 3: `Settings.relevance_threshold`**

У `src/prophet_checker/config.py` додай поле після `verification_confidence_threshold`:

```python
    verification_confidence_threshold: float = 0.6
    relevance_threshold: float | None = None  # None = top-k без порога; ставимо після sweep (задача A)
    log_level: str = "INFO"
```

- [ ] **Step 4: `QueryOrchestrator` — фільтр**

Заміни `src/prophet_checker/query/orchestrator.py` цілком на:

```python
from __future__ import annotations

from prophet_checker.llm import EmbeddingClient
from prophet_checker.models.domain import QueryResult, RetrievedPrediction
from prophet_checker.storage.interfaces import PredictionRepository, VectorStore


class QueryOrchestrator:
    def __init__(
        self,
        embedder: EmbeddingClient,
        vector_store: VectorStore,
        prediction_repo: PredictionRepository,
        relevance_threshold: float | None = None,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._prediction_repo = prediction_repo
        self._relevance_threshold = relevance_threshold

    async def search(self, question: str, limit: int = 10) -> QueryResult:
        embedding = await self._embedder.embed(question)
        matches = await self._vector_store.search_similar(embedding, limit=limit)
        if self._relevance_threshold is not None:
            matches = [m for m in matches if m.distance <= self._relevance_threshold]
        by_id = {
            p.id: p
            for p in await self._prediction_repo.get_by_ids([m.prediction_id for m in matches])
        }
        results = [
            RetrievedPrediction(prediction=by_id[m.prediction_id], distance=m.distance, rank=rank)
            for rank, m in enumerate(matches, start=1)
            if m.prediction_id in by_id
        ]
        return QueryResult(query=question, results=results)
```

- [ ] **Step 5: Фабрика — передати поріг**

У `src/prophet_checker/factory.py` останній рядок `build_query_orchestrator` (зараз `return QueryOrchestrator(embedder, vector_store, prediction_repo)`) заміни на:

```python
    return QueryOrchestrator(
        embedder, vector_store, prediction_repo, relevance_threshold=settings.relevance_threshold
    )
```

- [ ] **Step 6: Запусти — пройде + сюїта**

Run: `.venv/bin/python -m pytest tests/test_query_orchestrator.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Lint + commit**

```bash
.venv/bin/ruff check src/prophet_checker/config.py src/prophet_checker/query/orchestrator.py src/prophet_checker/factory.py tests/test_query_orchestrator.py
git add src/prophet_checker/config.py src/prophet_checker/query/orchestrator.py src/prophet_checker/factory.py tests/test_query_orchestrator.py
git commit -m "feat(query): поріг релевантності у QueryOrchestrator (Settings.relevance_threshold)"
```

---

## Task 2: Query-gold питання — prediction-centric + дато-вихід

**Скоуп:** переписати `build_query_prompt` на ретроспективну перевірку прогнозу (не форкастинг/факт) + дато-суфікс на вихід build'у; оновити наявний prompt-тест + додати guard.

**Files:**
- Modify: `scripts/retrieval/build_query_gold.py` (`build_query_prompt` + `main` default `--out`)
- Test: `tests/test_retrieval_query_gold.py`

- [ ] **Step 1: Оновити наявний тест + додати guard**

У `tests/test_retrieval_query_gold.py` (`build_query_prompt` уже імпортовано зверху файлу):

(a) **Онови наявний** `test_prompt_demands_anchors_and_paraphrase` — рядок `assert "найближчим часом" in p` (стара anti-pattern-фраза, у новому промпті її нема → інакше сюїта червона) заміни на `assert "форкастинг" in p`. Сусідній `assert "перефразуй" in p.lower()` лишається (новий промпт зберігає «перефразуй»). Тіло стає:

```python
def test_prompt_demands_anchors_and_paraphrase():
    p = build_query_prompt(_ROW, "claim_text")
    assert "перефразуй" in p.lower()  # не копіювати формулювання
    assert "форкастинг" in p  # форкастинг присутній лише як заборона (негативний приклад)
```

(b) **Додай** новий guard-тест:

```python
def test_query_prompt_is_prediction_centric():
    prompt = build_query_prompt(
        {
            "claim_text": "війна закінчиться у 2025",
            "situation": "на тлі переговорів",
            "prediction_date": "2024-01-01",
            "topic": "війна",
        },
        "claim_text",
    )
    # ретроспективна рамка перевірки прогнозу присутня
    assert "РЕТРОСПЕКТИВ" in prompt
    assert "прогнозував" in prompt
    assert "НЕ проси спрогнозувати майбутнє" in prompt
    # форкастинг згадується лише як НЕГАТИВНИЙ приклад
    assert "(форкастинг)" in prompt
```

- [ ] **Step 2: Запусти — має впасти**

Run: `.venv/bin/python -m pytest tests/test_retrieval_query_gold.py -q`
Expected: FAIL — старий промпт не має ретроспективної рамки (`test_query_prompt_is_prediction_centric`) і не містить «форкастинг» (оновлений `test_prompt_demands_anchors_and_paraphrase`). Решта тестів файлу лишаються зеленими (контекст/emphasis новий промпт зберігає).

- [ ] **Step 3: Переписати `build_query_prompt`**

У `scripts/retrieval/build_query_gold.py` заміни функцію `build_query_prompt` (рядки ~62-87) на (структура та сама, рамку й приклади змінено; `_EMPHASIS` лишається як є):

```python
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
```

- [ ] **Step 4: Дато-суфікс на вихід**

У `scripts/retrieval/build_query_gold.py`:

(a) додай у блок stdlib-імпортів зверху (файл наразі `datetime` НЕ імпортує):

```python
from datetime import date
```

(b) заміни дефолт `--out` у `main` (рядок `parser.add_argument("--out", type=Path, default=GOLD_PATH)`) на:

```python
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(f"scripts/data/retrieval/query_gold_{date.today().isoformat()}.json"),
    )
```

(Константу `GOLD_PATH` лишаємо — на неї посилається `build_generation_gold` як на дефолтний вхід; його оновлення на дато-шлях — у кроці регенерації, задача поза цим планом.)

- [ ] **Step 5: Запусти — пройде + сюїта**

Run: `.venv/bin/python -m pytest tests/test_retrieval_query_gold.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
.venv/bin/ruff check scripts/retrieval/build_query_gold.py tests/test_retrieval_query_gold.py
git add scripts/retrieval/build_query_gold.py tests/test_retrieval_query_gold.py
git commit -m "feat(retrieval): prediction-centric query-gold промпт + дато-суфікс на вихід"
```

---

## Task 3: `sweep_thresholds` + `ThresholdReport`

**Скоуп:** чиста sweep-логіка — `sweep_thresholds` (крива метрик по T + trust-first вибір) + Pydantic-моделі `ThresholdReport`; юніт на іграшковому прикладі. Без БД/LLM.

**Files:**
- Create: `scripts/rag/__init__.py`
- Create: `scripts/rag/threshold_sweep.py`
- Test: `tests/test_threshold_sweep.py`

- [ ] **Step 1: Тест sweep (іграшковий приклад із дизайну)**

Створи `tests/test_threshold_sweep.py`:

```python
from datetime import date

from eval_common.models import EvalCase, EvalRun
from generation.gen_models import ExpectedSource, GenerationInput, GenerationLabels
from prophet_checker.models.domain import Prediction, QueryResult, RetrievedPrediction
from rag.threshold_sweep import sweep_thresholds


def _pred(pid: str) -> Prediction:
    return Prediction(id=pid, document_id="d", person_id="x", claim_text=pid, prediction_date=date(2024, 1, 1))


def _run(qid, answerable, category, expected_ids, matches):
    labels = GenerationLabels(
        answerable=answerable,
        expected_sources=[ExpectedSource(prediction=_pred(p)) for p in expected_ids],
        category=category,
    )
    case = EvalCase(id=qid, input=GenerationInput(question="q"), labels=labels)
    results = [
        RetrievedPrediction(prediction=_pred(mid), distance=md, rank=i)
        for i, (mid, md) in enumerate(matches, start=1)
    ]
    return EvalRun(case=case, result=QueryResult(query="q", results=results), latency_s=0.1)


def _runs():
    return [
        _run("a001", True, "single_source", ["p1"], [("p1", 0.20), ("p9", 0.55)]),
        _run("a002", True, "single_source", ["p2"], [("p7", 0.30), ("p2", 0.45), ("p5", 0.60)]),
        _run("s001", True, "synthesis", ["p4", "p6"], [("p4", 0.25), ("p6", 0.48), ("p8", 0.70)]),
        _run("o001", False, "off_domain", [], [("p3", 0.85)]),
        _run("n001", False, "near_domain", [], [("p5", 0.52)]),
    ]


def _pt(report, t):
    return next(p for p in report.curve if p.threshold == t)


def test_sweep_curve_and_choice():
    report = sweep_thresholds(_runs(), recall_target=0.9)

    # @0.30 (плато <0.45): усі answerable відповідають, але recall 0.5; обидва off — відмова
    p30 = _pt(report, 0.30)
    assert p30.answer_rate == 1.0 and p30.recall == 0.5 and p30.refusal_rate == 1.0
    # @0.48 — солодка точка: recall 1.0 і off-refusal 1.0
    p48 = _pt(report, 0.48)
    assert p48.answer_rate == 1.0 and p48.recall == 1.0 and p48.refusal_rate == 1.0
    # @0.52 — near_domain протік: recall 1.0, refusal 0.5
    p52 = _pt(report, 0.52)
    assert p52.recall == 1.0 and p52.refusal_rate == 0.5

    assert report.chosen_threshold == 0.48
    assert report.recall_target == 0.9


def test_sweep_no_threshold_meets_recall():
    # очікуване джерело надто далеко → recall <0.9 за будь-якого T → chosen None
    runs = [_run("a", True, "single_source", ["pX"], [("pY", 0.10)])]  # очікуване pX взагалі не знайдено
    report = sweep_thresholds(runs, recall_target=0.9)
    assert report.chosen_threshold is None
```

- [ ] **Step 2: Запусти — має впасти**

Run: `.venv/bin/python -m pytest tests/test_threshold_sweep.py -q`
Expected: FAIL — `rag.threshold_sweep` ще не існує (`ModuleNotFoundError`).

- [ ] **Step 3: Створити пакет + sweep**

Створи `scripts/rag/__init__.py` (порожній):

```python
```

Створи `scripts/rag/threshold_sweep.py`:

```python
# scripts/rag/threshold_sweep.py
from __future__ import annotations

from pydantic import BaseModel


class ThresholdPoint(BaseModel):
    threshold: float
    answer_rate: float  # answerable: частка з ≥1 match ≤T
    recall: float  # answerable: середня частка очікуваних джерел, знайдених ≤T
    refusal_rate: float  # off-corpus: частка з 0 matches ≤T


class CategoryBreakdown(BaseModel):
    category: str
    n: int
    answer_rate: float | None = None
    recall: float | None = None
    refusal_rate: float | None = None


class ThresholdReport(BaseModel):
    curve: list[ThresholdPoint]
    chosen_threshold: float | None
    recall_target: float
    by_category_at_chosen: list[CategoryBreakdown]


def _kept_ids(run, t: float) -> set[str]:
    results = run.result.results if run.result is not None else []
    return {r.prediction.id for r in results if r.distance <= t}


def _point(runs, t: float) -> ThresholdPoint:
    ans_n = ans_answered = 0
    recall_sum = 0.0
    off_n = off_refused = 0
    for run in runs:
        labels = run.case.labels
        if labels is None:  # EvalCase.labels номінально nullable; gold завжди їх ставить
            continue
        kept = _kept_ids(run, t)
        if labels.answerable:
            ans_n += 1
            if kept:
                ans_answered += 1
            expected = [es.prediction.id for es in labels.expected_sources]
            if expected:
                recall_sum += sum(1 for e in expected if e in kept) / len(expected)
        else:
            off_n += 1
            if not kept:
                off_refused += 1
    return ThresholdPoint(
        threshold=t,
        answer_rate=(ans_answered / ans_n) if ans_n else 0.0,
        recall=(recall_sum / ans_n) if ans_n else 0.0,
        refusal_rate=(off_refused / off_n) if off_n else 0.0,
    )


def category_breakdown(runs, t: float) -> list[CategoryBreakdown]:
    cats: dict[str, list] = {}
    for run in runs:
        if run.case.labels is None:
            continue
        cats.setdefault(run.case.labels.category, []).append(run)
    out: list[CategoryBreakdown] = []
    for cat, crs in sorted(cats.items()):
        answerable = crs[0].case.labels.answerable
        if answerable:
            answered = sum(1 for r in crs if _kept_ids(r, t))
            rec = 0.0
            for r in crs:
                expected = [es.prediction.id for es in r.case.labels.expected_sources]
                kept = _kept_ids(r, t)
                if expected:
                    rec += sum(1 for e in expected if e in kept) / len(expected)
            out.append(
                CategoryBreakdown(
                    category=cat, n=len(crs), answer_rate=answered / len(crs), recall=rec / len(crs)
                )
            )
        else:
            refused = sum(1 for r in crs if not _kept_ids(r, t))
            out.append(CategoryBreakdown(category=cat, n=len(crs), refusal_rate=refused / len(crs)))
    return out


def sweep_thresholds(runs, recall_target: float = 0.9) -> ThresholdReport:
    """Retrieval-only sweep: для кожного спостереженого distance рахує (answer-rate, recall,
    off-refusal); обирає T = max off-refusal за умови recall ≥ target (trust-first)."""
    grid = sorted({r.distance for run in runs if run.result is not None for r in run.result.results})
    curve = [_point(runs, t) for t in grid]

    eligible = [p for p in curve if p.recall >= recall_target]
    chosen = None
    if eligible:
        best_refusal = max(p.refusal_rate for p in eligible)
        chosen = min(p.threshold for p in eligible if p.refusal_rate == best_refusal)

    breakdown = category_breakdown(runs, chosen) if chosen is not None else []
    return ThresholdReport(
        curve=curve, chosen_threshold=chosen, recall_target=recall_target, by_category_at_chosen=breakdown
    )
```

- [ ] **Step 4: Запусти — пройде + сюїта**

Run: `.venv/bin/python -m pytest tests/test_threshold_sweep.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check scripts/rag/__init__.py scripts/rag/threshold_sweep.py tests/test_threshold_sweep.py
git add scripts/rag/__init__.py scripts/rag/threshold_sweep.py tests/test_threshold_sweep.py
git commit -m "feat(rag): sweep_thresholds + ThresholdReport (trust-first вибір порога)"
```

---

## Task 4: `threshold_eval.py` CLI (retrieval-only)

**Скоуп:** CLI-раннер — retrieval-only прогін по gold (без генерації/судді) через `run_cases` → `sweep_thresholds` → JSON-звіт. Інтеграційний, без юніту.

**Files:**
- Create: `scripts/rag/threshold_eval.py`

Юніт-тесту нема (інтеграційний CLI, як інші eval-раннери). Перевірка — lint + dry-import.

- [ ] **Step 1: Створити CLI**

Створи `scripts/rag/threshold_eval.py`:

```python
# scripts/rag/threshold_eval.py
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
from rag.threshold_sweep import sweep_thresholds  # noqa: E402

logger = logging.getLogger(__name__)

# pre-regeneration stub — після Task 5 передавай дато-суфіксований gold через --gold
DEFAULT_GOLD = PROJECT_ROOT / "scripts" / "data" / "generation" / "gold.json"
OUT_DIR = PROJECT_ROOT / "scripts" / "outputs" / "threshold_eval"
TOP_N = 20  # стелю беремо з запасом, щоб sweep мав де відсікати


async def _main(gold_path: Path, limit: int, concurrency: int, recall_target: float) -> None:
    settings = Settings()
    cases = load_generation_gold(gold_path)  # 112: answerable + off-corpus refusal-кейси
    if limit:
        cases = cases[:limit]
    logger.info("threshold eval: %d cases, top_n=%d, concurrency=%d", len(cases), TOP_N, concurrency)

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
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "threshold_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "chosen relevance_threshold = %s (recall_target=%.2f)", report.chosen_threshold, recall_target
    )
    if report.chosen_threshold is None:
        logger.warning("жоден поріг не дає recall ≥ %.2f → retrieval слабкий (див. криву у звіті)", recall_target)
    print(f"report → {OUT_DIR}/threshold_report.json")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(description="Relevance-threshold sweep (retrieval-only)")
    p.add_argument("--gold", type=Path, default=DEFAULT_GOLD, help="дато-суфіксований generation gold")
    p.add_argument("--limit", type=int, default=0, help="перші N кейсів (0 = усі)")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--recall-target", type=float, default=0.9)
    args = p.parse_args()
    asyncio.run(_main(args.gold, args.limit, args.concurrency, args.recall_target))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Lint + dry-import**

Run: `.venv/bin/ruff check scripts/rag/threshold_eval.py && .venv/bin/python -c "import sys; sys.path[:0]=['src','scripts']; import rag.threshold_eval; print('import ok')"`
Expected: ruff чисто; `import ok` (резолвить символи; не запускає `_main`).

- [ ] **Step 3: Сюїта (не зачеплено)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/rag/threshold_eval.py
git commit -m "feat(rag): threshold_eval CLI — retrieval-only прогін + sweep-звіт"
```

---

## Task 5: Регенерація + прогін + виставлення порога (потребує БД + LLM)

**Скоуп (ручне, БД+LLM):** регенерувати дато-gold (query → generation), прогнати sweep, виставити `relevance_threshold` у конфіг, smoke `/answer`.

Ручні кроки (твоя інфра), без юніт-тестів. Передумова: Postgres піднятий, embeddings backfill'нуті (✅), `.env` з `OPENAI_API_KEY`/`GEMINI_API_KEY`.

- [ ] **Step 1: Регенерувати prediction-centric query-gold**

Run: `.venv/bin/python scripts/retrieval/build_query_gold.py`
Output: `scripts/data/retrieval/query_gold_YYYY-MM-DD.json` (дато-суфікс). Перевір кілька питань — мають бути ретроспективні («що прогнозував…», «які прогнози були про…»).

- [ ] **Step 2: Каскадом регенерувати generation gold**

Онови вхід у `scripts/generation/build_generation_gold.py` (константа дефолтного `retrieval_query_gold` → новий дато-файл) АБО передай шлях, тоді:
Run: `.venv/bin/python scripts/generation/build_generation_gold.py`
Output: `scripts/data/generation/gold_YYYY-MM-DD.json` (дато-суфікс, single_source-питання тепер prediction-centric).

- [ ] **Step 3: Прогнати threshold-sweep**

Run: `.venv/bin/python scripts/rag/threshold_eval.py --gold scripts/data/generation/gold_YYYY-MM-DD.json`
Output: `scripts/outputs/threshold_eval/threshold_report.json` + у лог `chosen relevance_threshold = …`. Якщо `chosen=None` → retrieval слабкий (дивись криву; лагодити retrieval/репрезентацію — задача поза A).

- [ ] **Step 4: Виставити поріг у конфіг**

Додай у `.env` (або дефолт у `config.py`, якщо хочеш у коді): `RELEVANCE_THRESHOLD=<chosen>`. Прод `/answer` тепер детерміновано відмовляє на off-topic.

- [ ] **Step 5: Smoke `/answer`**

Підніми API (`.venv/bin/python -m prophet_checker`), перевір: on-topic питання → відповідь; явно off-topic («рецепт борщу») → `REFUSAL_NO_DATA` (детерміновано, не self-refusal).

---

## Готово, коли

- Сюїта зелена; threshold-фільтр у `QueryOrchestrator`, prediction-centric промпт, `sweep_thresholds` із тестом, `threshold_eval` CLI.
- (Ручне, після merge) регенеровано дато-суфіксований gold; sweep дав `chosen_threshold`; виставлено в конфіг; smoke `/answer` підтверджує детермінований refusal.

**Поза скоупом:** end-to-end якість (задача B); hybrid-search; автоматичне виставлення порога; розширення off-corpus gold.
