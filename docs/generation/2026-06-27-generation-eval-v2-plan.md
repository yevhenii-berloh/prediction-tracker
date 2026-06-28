# Generation Eval v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перебудувати generation-eval на ізольовану генерацію поверх **заморожених** gold-прогнозів — faithfulness + completeness, без refusal, БД-free рантайм.

**Architecture:** Прод `AnswerOrchestrator` розділяємо на `answer_from_sources` (generate-only) + `answer` (search→делегує). Eval годує генератор повними `Prediction`-ами, вмороженими в gold під час build (build читає БД раз через `get_by_ids`); у рантаймі — нуль БД/retrieval, лише LLM-генератор + LLM-суддя. Реалізація — за [дизайном](2026-06-27-generation-eval-v2-design.md).

**Tech Stack:** Python 3.14, Pydantic v2, pytest (`asyncio_mode=auto`), ruff (line 100). Venv: `.venv`. Тести: `.venv/bin/python -m pytest tests/ -q`. Lint: `.venv/bin/ruff check <шлях>`.

**Коміти:** conventional commits українською (`type(scope): subject`), як решта історії. Гілка `main` (попередні generation-eval-коміти йшли в `main`).

**Інваріант:** після кожного коміту `pytest tests/ -q` зелений. Скрипт `generation_eval.py` НЕ входить у сюїту й НЕ запускається до Task 7 — між Task 2 і Task 7 він посилається на видалені символи (це нормально, фіксується в Task 7).

---

## File structure

| Файл                                               | Роль              | Зміна                                                               |
| -------------------------------------------------- | ----------------- | ------------------------------------------------------------------- |
| `src/prophet_checker/query/answer_orchestrator.py` | прод-orchestrator | split: `answer_from_sources` + опц. `query_orchestrator`            |
| `src/prophet_checker/factory.py`                   | composition root  | новий конструктор-порядок                                           |
| `scripts/generation/scorers.py`                    | скорери           | − `RefusalScorer`; `CompletenessScorer` судить `run.result.sources` |
| `scripts/generation/judge_prompts.py`              | промпти судді     | − refusal; `build_completeness_prompt(+situation)`                  |
| `scripts/generation/gen_models.py`                 | моделі евалу      | − `RefusalDetail`/refusal-метрики; `ExpectedSource{prediction}`     |
| `scripts/generation/metrics.py`                    | агрегація         | − refusal-гілки/поля                                                |
| `scripts/generation/gold.py`                       | завантажувач gold | без змін коду (верифікувати)                                        |
| `scripts/generation/build_generation_gold.py`      | побудова gold     | читає БД → вморожує повні прогнози                                  |
| `scripts/generation/generation_eval.py`            | CLI-раннер        | БД-free generate-only                                               |
| `scripts/data/generation_gold.json`                | датасет           | перегенерувати з вмороженими прогнозами                             |
| `tests/test_answer_orchestrator.py`                | тест              | новий порядок ctor + тести `answer_from_sources`                    |
| `tests/test_generation_scorers.py`                 | тест              | − refusal; completeness проти sources                               |
| `tests/test_generation_judge_prompts.py`           | тест              | − `test_parse_refusal_response`                                     |
| `tests/test_generation_metrics.py`                 | тест              | − refusal                                                           |
| `tests/test_generation_gold.py`                    | тест              | `expected_sources` з `prediction`                                   |
| `tests/test_build_generation_gold.py`              | тест              | `predictions_by_id` замість `claim_by_id`                           |

---

## Task 1: AnswerOrchestrator split (прод)

**Files:**
- Modify: `src/prophet_checker/query/answer_orchestrator.py`
- Modify: `src/prophet_checker/factory.py:95-105`
- Test: `tests/test_answer_orchestrator.py`

- [ ] **Step 1: Оновити тести — новий порядок ctor + тести `answer_from_sources`**

Заміни верхні імпорти й обидва виклики `AnswerOrchestrator(qo, llm)` на `AnswerOrchestrator(llm, qo)`, і додай нові тести. Повний новий вміст файлу:

```python
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakes import FakePredictionRepo, FakeVectorStore

from prophet_checker.models.domain import Prediction, RetrievedPrediction
from prophet_checker.query.answer_orchestrator import REFUSAL_NO_DATA, AnswerOrchestrator
from prophet_checker.query.orchestrator import QueryOrchestrator


def _embedder():
    e = MagicMock()
    e.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return e


def _llm(text="згенерована відповідь"):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=text)
    return llm


def _pred(pid="p1"):
    return Prediction(
        id=pid, document_id="d", person_id="x", claim_text="claim", prediction_date=date(2024, 1, 1)
    )


# --- answer() (end-to-end, search→делегування) ---


async def test_answer_refuses_without_calling_llm_when_no_sources():
    qo = QueryOrchestrator(_embedder(), FakeVectorStore(), FakePredictionRepo())
    llm = _llm()
    orch = AnswerOrchestrator(llm, qo)

    result = await orch.answer("q")

    assert result.answer == REFUSAL_NO_DATA
    assert result.sources == []
    llm.complete.assert_not_awaited()


async def test_answer_generates_with_sources():
    store = FakeVectorStore()
    repo = FakePredictionRepo()
    await store.store_embedding("p1", [0.1, 0.1, 0.1])
    await repo.save(_pred("p1"))
    qo = QueryOrchestrator(_embedder(), store, repo)
    llm = _llm("  відповідь  ")
    orch = AnswerOrchestrator(llm, qo)

    result = await orch.answer("питання", limit=5)

    assert result.query == "питання"
    assert result.answer == "відповідь"
    assert [s.prediction.id for s in result.sources] == ["p1"]
    llm.complete.assert_awaited_once()
    assert "p1" in llm.complete.call_args.args[0]


# --- answer_from_sources() (generate-only, без query_orchestrator) ---


async def test_answer_from_sources_refuses_on_empty():
    orch = AnswerOrchestrator(_llm())
    result = await orch.answer_from_sources("q", [])
    assert result.answer == REFUSAL_NO_DATA
    assert result.sources == []


async def test_answer_from_sources_generates():
    llm = _llm("  відповідь  ")
    orch = AnswerOrchestrator(llm)
    sources = [RetrievedPrediction(prediction=_pred("p1"), distance=0.0, rank=1)]

    result = await orch.answer_from_sources("питання", sources)

    assert result.answer == "відповідь"
    assert [s.prediction.id for s in result.sources] == ["p1"]
    llm.complete.assert_awaited_once()
    assert "p1" in llm.complete.call_args.args[0]


async def test_answer_raises_without_query_orchestrator():
    orch = AnswerOrchestrator(_llm())
    with pytest.raises(RuntimeError):
        await orch.answer("q")
```

- [ ] **Step 2: Запусти — має впасти**

Run: `.venv/bin/python -m pytest tests/test_answer_orchestrator.py -q`
Expected: FAIL (`TypeError` на новому порядку ctor / `AttributeError: answer_from_sources`).

- [ ] **Step 3: Реалізувати split**

Повний новий вміст `src/prophet_checker/query/answer_orchestrator.py`:

```python
from __future__ import annotations

import logging

from prophet_checker.llm import LLMClient
from prophet_checker.llm.prompts import RAG_SYSTEM, build_rag_prompt
from prophet_checker.models.domain import AnswerResult, RetrievedPrediction
from prophet_checker.query.orchestrator import QueryOrchestrator

logger = logging.getLogger(__name__)

REFUSAL_NO_DATA = (
    "За наявними даними я не знайшов релевантних прогнозів на цей запит. "
    "Аналіз автоматизований і може містити неточності."
)


class AnswerOrchestrator:
    def __init__(self, llm: LLMClient, query_orchestrator: QueryOrchestrator | None = None) -> None:
        self._llm = llm
        self._query_orchestrator = query_orchestrator

    async def answer_from_sources(
        self, question: str, sources: list[RetrievedPrediction]
    ) -> AnswerResult:
        if not sources:
            logger.info("answer_from_sources: no sources, refusing")
            return AnswerResult(query=question, answer=REFUSAL_NO_DATA, sources=[])
        prompt = build_rag_prompt(question, sources)
        text = await self._llm.complete(prompt, system=RAG_SYSTEM)
        logger.info("answer_from_sources: generated from %d sources", len(sources))
        return AnswerResult(query=question, answer=text.strip(), sources=sources)

    async def answer(self, question: str, limit: int = 10) -> AnswerResult:
        if self._query_orchestrator is None:
            raise RuntimeError("answer() requires a query_orchestrator (this instance is generate-only)")
        result = await self._query_orchestrator.search(question, limit=limit)
        return await self.answer_from_sources(question, result.results)
```

- [ ] **Step 4: Оновити фабрику**

У `src/prophet_checker/factory.py` останній рядок `build_answer_orchestrator` (зараз `return AnswerOrchestrator(query_orchestrator, llm)`) заміни на:

```python
    return AnswerOrchestrator(llm, query_orchestrator)
```

- [ ] **Step 5: Запусти — має пройти + вся сюїта**

Run: `.venv/bin/python -m pytest tests/test_answer_orchestrator.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: PASS (усе зелене).

- [ ] **Step 6: Lint + commit**

```bash
.venv/bin/ruff check src/prophet_checker/query/answer_orchestrator.py src/prophet_checker/factory.py tests/test_answer_orchestrator.py
git add src/prophet_checker/query/answer_orchestrator.py src/prophet_checker/factory.py tests/test_answer_orchestrator.py
git commit -m "refactor(query): винести answer_from_sources з AnswerOrchestrator.answer"
```

---

## Task 2: Прибрати RefusalScorer

**Files:**
- Modify: `scripts/generation/scorers.py`
- Test: `tests/test_generation_scorers.py`

- [ ] **Step 1: Прибрати refusal-тести зі scorers-тесту**

У `tests/test_generation_scorers.py`: у рядку імпорту прибери `RefusalScorer` (лишається `from generation.scorers import CompletenessScorer, FaithfulnessScorer`), і **видали весь блок `# --- refusal ---`** (5 тестів: `test_refusal_na_on_sut_error`, `test_refusal_hardrefusal_on_answerable_is_wrong`, `test_refusal_hardrefusal_on_offcorpus_is_correct`, `test_refusal_soft_refusal_via_judge`, `test_refusal_false_answer_on_offcorpus`). Решту (faithfulness + completeness) лишаємо без змін у цьому таску.

- [ ] **Step 2: Видалити RefusalScorer + його імпорти зі scorers.py**

У `scripts/generation/scorers.py`:
- видали клас `RefusalScorer` цілком (рядки `class RefusalScorer` … до `class CompletenessScorer`);
- прибери рядок `from prophet_checker.query.answer_orchestrator import REFUSAL_NO_DATA`;
- з імпорту `generation.gen_models` прибери `RefusalDetail`;
- з імпорту `generation.judge_prompts` прибери `REFUSAL_SYSTEM`, `build_refusal_prompt`, `parse_refusal_response`.

Блок імпортів має стати таким:

```python
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
```

(`FaithfulnessScorer` і `CompletenessScorer` лишаються без змін у цьому таску.)

- [ ] **Step 3: Запусти — сюїта зелена**

Run: `.venv/bin/python -m pytest tests/test_generation_scorers.py tests/ -q`
Expected: PASS (refusal-тестів більше нема; решта зелена).

- [ ] **Step 4: Lint + commit**

```bash
.venv/bin/ruff check scripts/generation/scorers.py tests/test_generation_scorers.py
git add scripts/generation/scorers.py tests/test_generation_scorers.py
git commit -m "refactor(generation-eval): прибрати RefusalScorer (v2 — поза скоупом генерації)"
```

---

## Task 3: Прибрати refusal-промпти з judge_prompts

**Files:**
- Modify: `scripts/generation/judge_prompts.py`
- Test: `tests/test_generation_judge_prompts.py`

- [ ] **Step 1: Прибрати тест парсера відмови**

У `tests/test_generation_judge_prompts.py`: з імпорту прибери `parse_refusal_response` і **видали** `def test_parse_refusal_response()` (рядки з двома асертами). Решту лишаємо.

- [ ] **Step 2: Видалити refusal-символи з judge_prompts.py**

У `scripts/generation/judge_prompts.py` видали:
- константу `REFUSAL_SYSTEM = ...`;
- функцію `build_refusal_prompt`;
- функцію `parse_refusal_response`.

(`FAITHFULNESS_SYSTEM`, `COMPLETENESS_SYSTEM`, `render_sources`, `build_faithfulness_prompt`, `build_completeness_prompt`, обидва інші парсери — лишаються.)

- [ ] **Step 3: Запусти — сюїта зелена**

Run: `.venv/bin/python -m pytest tests/test_generation_judge_prompts.py tests/ -q`
Expected: PASS.

- [ ] **Step 4: Lint + commit**

```bash
.venv/bin/ruff check scripts/generation/judge_prompts.py tests/test_generation_judge_prompts.py
git add scripts/generation/judge_prompts.py tests/test_generation_judge_prompts.py
git commit -m "refactor(generation-eval): прибрати refusal-промпти/парсер судді"
```

---

## Task 4: Прибрати refusal-метрики (gen_models + metrics) — атомарно

**Files:**
- Modify: `scripts/generation/gen_models.py`
- Modify: `scripts/generation/metrics.py`
- Test: `tests/test_generation_metrics.py`

- [ ] **Step 1: Переписати тест метрик без refusal**

Повний новий вміст `tests/test_generation_metrics.py`:

```python
from datetime import date

from eval_common.models import EvalCase, EvalRun, ScoreCard, ScoredRun
from generation.gen_models import GenerationInput, GenerationLabels
from generation.metrics import aggregate
from prophet_checker.models.domain import AnswerResult, Prediction, RetrievedPrediction


def _pred():
    return Prediction(
        id="p", document_id="d", person_id="x", claim_text="c", prediction_date=date(2024, 1, 1)
    )


def _scored(category, answerable, *, faith=None, recall=None, error=False):
    labels = GenerationLabels(answerable=answerable, category=category)
    case = EvalCase(id="c", input=GenerationInput(question="q"), labels=labels)

    if error:
        run = EvalRun(case=case, result=None, latency_s=0.1, error="RuntimeError")
        cards = [ScoreCard(scorer=name, score=None) for name in ("faithfulness", "completeness")]
        return ScoredRun(run=run, cards=cards)

    result = AnswerResult(
        query="q",
        answer="a",
        sources=[RetrievedPrediction(prediction=_pred(), distance=0.1, rank=1)],
    )
    run = EvalRun(case=case, result=result, latency_s=0.1)
    cards = [
        ScoreCard(scorer="faithfulness", score=faith),
        ScoreCard(scorer="completeness", score=recall),
    ]
    return ScoredRun(run=run, cards=cards)


def test_aggregate_means_and_categories():
    # значення підібрані так, щоб середні були точні у float (без == на 0.7999…)
    scored = [
        _scored("single_source", True, faith=1.0, recall=1.0),
        _scored("single_source", True, faith=0.0, recall=0.0),
        _scored("synthesis", True, faith=0.5, recall=0.5),
        _scored("single_source", True, error=True),  # SUT error → обидва None
    ]
    m = aggregate(scored)
    assert m.n_total == 4
    assert m.n_errors == 1
    assert m.faithfulness_mean == 0.5  # (1.0 + 0.0 + 0.5) / 3
    assert m.hallucination_rate == 0.5
    assert m.recall_mean == 0.5  # (1.0 + 0.0 + 0.5) / 3
    assert m.by_category["single_source"].faithfulness_mean == 0.5  # (1.0 + 0.0) / 2
    assert m.by_category["synthesis"].recall_mean == 0.5
    assert not hasattr(m, "refusal_accuracy")  # поле прибране в v2 — це і дає RED проти старого коду


def test_aggregate_empty():
    m = aggregate([])
    assert m.n_total == 0
    assert m.faithfulness_mean is None
    assert m.by_category == {}
```

- [ ] **Step 2: Запусти — має впасти**

Run: `.venv/bin/python -m pytest tests/test_generation_metrics.py -q`
Expected: FAIL на `assert not hasattr(m, "refusal_accuracy")` — старий `GenerationMetrics` ще має це поле. (Решта асертів проходить і на старому коді: старий `aggregate` безпечно пропускає відсутню refusal-картку й заповнює refusal-поля 0.0 — тому саме sentinel робить крок червоним.)

- [ ] **Step 3: Прибрати refusal-поля з gen_models.py**

У `scripts/generation/gen_models.py`:
- видали клас `RefusalDetail` цілком;
- `CategoryMetrics` і `GenerationMetrics` заміни на:

```python
class CategoryMetrics(BaseModel):
    n: int
    faithfulness_mean: float | None
    recall_mean: float | None


class GenerationMetrics(BaseModel):
    n_total: int
    n_errors: int
    faithfulness_mean: float | None
    hallucination_rate: float | None
    recall_mean: float | None
    by_category: dict[str, CategoryMetrics]
```

(`GenerationInput`, `ExpectedSource`, `GenerationLabels`, `ClaimVerdict`, `FaithfulnessDetail`, `SourceCoverage`, `CompletenessDetail` — у цьому таску без змін.)

- [ ] **Step 4: Прибрати refusal-гілки з metrics.py**

Повний новий вміст `scripts/generation/metrics.py`:

```python
# scripts/generation/metrics.py
from __future__ import annotations

from eval_common.models import ScoredRun
from generation.gen_models import CategoryMetrics, GenerationMetrics


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _cards(run) -> dict:
    return {c.scorer: c for c in run.cards}


def aggregate(scored: list[ScoredRun]) -> GenerationMetrics:
    n_total = len(scored)
    n_errors = sum(1 for s in scored if s.run.result is None)

    faith: list[float] = []
    recall: list[float] = []
    by_cat: dict[str, dict[str, list]] = {}

    for s in scored:
        cat = s.run.case.labels.category
        bucket = by_cat.setdefault(cat, {"faith": [], "recall": [], "n": 0})
        bucket["n"] += 1
        cards = _cards(s)

        f = cards.get("faithfulness")
        if f is not None and f.score is not None:
            faith.append(f.score)
            bucket["faith"].append(f.score)

        c = cards.get("completeness")
        if c is not None and c.score is not None:
            recall.append(c.score)
            bucket["recall"].append(c.score)

    faithfulness_mean = _mean(faith)
    by_category = {
        cat: CategoryMetrics(
            n=b["n"],
            faithfulness_mean=_mean(b["faith"]),
            recall_mean=_mean(b["recall"]),
        )
        for cat, b in by_cat.items()
    }
    return GenerationMetrics(
        n_total=n_total,
        n_errors=n_errors,
        faithfulness_mean=faithfulness_mean,
        hallucination_rate=(1 - faithfulness_mean) if faithfulness_mean is not None else None,
        recall_mean=_mean(recall),
        by_category=by_category,
    )
```

- [ ] **Step 5: Запусти — має пройти + вся сюїта**

Run: `.venv/bin/python -m pytest tests/test_generation_metrics.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
.venv/bin/ruff check scripts/generation/gen_models.py scripts/generation/metrics.py tests/test_generation_metrics.py
git add scripts/generation/gen_models.py scripts/generation/metrics.py tests/test_generation_metrics.py
git commit -m "refactor(generation-eval): прибрати refusal-метрики з GenerationMetrics/aggregate"
```

---

## Task 5: CompletenessScorer судить подані джерела + situation

**Files:**
- Modify: `scripts/generation/scorers.py`
- Modify: `scripts/generation/judge_prompts.py`
- Test: `tests/test_generation_scorers.py`

- [ ] **Step 1: Оновити completeness-тести**

У `tests/test_generation_scorers.py`:

(a) З імпорту `generation.gen_models` прибери `ExpectedSource` (стане непотрібним) — лишиться `from generation.gen_models import GenerationInput, GenerationLabels`.

(b) Заміни хелпер `_run` (додаємо параметр `source_ids`, sources будуються з нього):

```python
def _run(answer, *, answerable, category, source_ids=("p1",)):
    labels = GenerationLabels(answerable=answerable, expected_sources=[], category=category)
    case = EvalCase(id="c1", input=GenerationInput(question="q"), labels=labels)
    result = None
    if answer is not None:
        result = AnswerResult(
            query="q",
            answer=answer,
            sources=[
                RetrievedPrediction(prediction=_pred(pid), distance=0.1, rank=i)
                for i, pid in enumerate(source_ids, 1)
            ],
        )
    return EvalRun(case=case, result=result, latency_s=0.1)
```

(c) Заміни весь блок `# --- completeness ---` на:

```python
# --- completeness ---


async def test_completeness_na_on_sut_error():
    card = await CompletenessScorer(_SeqJudge()).score(
        _run(None, answerable=True, category="single_source")
    )
    assert card.score is None


async def test_completeness_na_when_no_sources():
    # порожні sources (refusal / DB-miss) → N/A, а не recall=0
    run = EvalRun(
        case=EvalCase(
            id="c1",
            input=GenerationInput(question="q"),
            labels=GenerationLabels(answerable=True, expected_sources=[], category="single_source"),
        ),
        result=AnswerResult(query="q", answer=REFUSAL_NO_DATA, sources=[]),
        latency_s=0.1,
    )
    card = await CompletenessScorer(_SeqJudge()).score(run)
    assert card.score is None


async def test_completeness_recall_half():
    judge = _SeqJudge('{"covered": true}', '{"covered": false}')
    card = await CompletenessScorer(judge).score(
        _run("відп", answerable=True, category="synthesis", source_ids=("p1", "p2"))
    )
    assert card.score == 0.5
    assert [c.covered for c in card.detail.coverage] == [True, False]
    assert [c.prediction_id for c in card.detail.coverage] == ["p1", "p2"]
```

(Faithfulness-тести й хелпери `_SeqJudge`/`_pred` лишаються; `_pred` уже приймає `pid`.)

- [ ] **Step 2: Запусти — має впасти**

Run: `.venv/bin/python -m pytest tests/test_generation_scorers.py -q`
Expected: FAIL (`CompletenessScorer` ще ітерує `labels.expected_sources`; на `_run` без `expected` дає N/A → `recall_half` падає; `build_completeness_prompt` ще без `situation`).

- [ ] **Step 3: Розширити build_completeness_prompt (situation як контекст)**

У `scripts/generation/judge_prompts.py` заміни `build_completeness_prompt` на:

```python
def build_completeness_prompt(answer: str, claim: str, situation: str | None = None) -> str:
    ctx = (
        "\n\nКОНТЕКСТ (ситуація прогнозу — лише щоб правильно зрозуміти ТВЕРДЖЕННЯ; "
        f"переказувати її не треба):\n{situation}"
        if situation
        else ""
    )
    return (
        "Чи ВІДПОВІДЬ відображає (згадує або передає суть) ТВЕРДЖЕННЯ? "
        'Формат: {"covered": true|false, "reason": "..."}\n\n'
        f"ТВЕРДЖЕННЯ:\n{claim}{ctx}\n\nВІДПОВІДЬ:\n{answer}"
    )
```

- [ ] **Step 4: Переписати CompletenessScorer**

У `scripts/generation/scorers.py` заміни клас `CompletenessScorer` на:

```python
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
```

> **Свідома зміна поведінки:** guard тепер лише `not run.result.sources` (без старого `not labels.answerable`). Completeness став **source-driven**, не label-driven — судимо подане (узгоджено з дизайном). off-corpus у v2 відсіюється фільтром `answerable` у `generation_eval.py`, а навіть якби потрапив — має порожні `expected_sources` → порожні sources → N/A через guard. Тому старий `test_completeness_na_on_offcorpus` не відновлюємо: його роль (N/A на нерелевантному) грає `test_completeness_na_when_no_sources`.

- [ ] **Step 5: Запусти — має пройти + вся сюїта**

Run: `.venv/bin/python -m pytest tests/test_generation_scorers.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
.venv/bin/ruff check scripts/generation/scorers.py scripts/generation/judge_prompts.py tests/test_generation_scorers.py
git add scripts/generation/scorers.py scripts/generation/judge_prompts.py tests/test_generation_scorers.py
git commit -m "feat(generation-eval): completeness судить подані джерела + situation-контекст"
```

---

## Task 6: ExpectedSource несе заморожений Prediction; build читає БД

**Files:**
- Modify: `scripts/generation/gen_models.py`
- Modify: `scripts/generation/build_generation_gold.py`
- Verify (без змін коду): `scripts/generation/gold.py`
- Test: `tests/test_generation_gold.py`
- Test: `tests/test_build_generation_gold.py`

- [ ] **Step 1: Оновити тест завантажувача gold**

Повний новий вміст `tests/test_generation_gold.py`:

```python
import json

from generation.gold import load_generation_gold


def test_load_generation_gold(tmp_path):
    gold = [
        {
            "id": "a000",
            "question": "q1",
            "answerable": True,
            "expected_sources": [
                {
                    "prediction": {
                        "id": "p1",
                        "document_id": "d",
                        "person_id": "x",
                        "claim_text": "c1",
                        "prediction_date": "2024-01-01",
                    }
                }
            ],
            "category": "single_source",
        },
        {
            "id": "o000",
            "question": "рецепт",
            "answerable": False,
            "expected_sources": [],
            "category": "off_domain",
        },
    ]
    path = tmp_path / "g.json"
    path.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")

    cases = load_generation_gold(path)
    assert len(cases) == 2
    assert cases[0].id == "a000"
    assert cases[0].input.question == "q1"
    assert cases[0].labels.answerable is True
    assert cases[0].labels.expected_sources[0].prediction.id == "p1"
    assert cases[0].labels.expected_sources[0].prediction.claim_text == "c1"
    assert cases[1].labels.category == "off_domain"
```

- [ ] **Step 2: Оновити тест build_gold**

Повний новий вміст `tests/test_build_generation_gold.py`:

```python
from datetime import date

import pytest

from generation.build_generation_gold import build_gold
from prophet_checker.models.domain import Prediction


def _retrieval():
    return [
        {"query": "claim-фраза A", "target_id": "t1", "source_field": "claim_text"},
        {"query": "situation-фраза A", "target_id": "t1", "source_field": "situation"},
        {"query": "claim-фраза B", "target_id": "t2", "source_field": "claim_text"},
        {"query": "situation-фраза B", "target_id": "t2", "source_field": "situation"},
    ]


def _preds():
    def p(pid, claim):
        return Prediction(
            id=pid, document_id="d", person_id="x", claim_text=claim, prediction_date=date(2024, 1, 1)
        )

    return {"t1": p("t1", "клейм-1"), "t2": p("t2", "клейм-2"), "s1": p("s1", "синтез-клейм")}


def test_build_gold_single_source_5050_and_enrichment():
    manual = [
        {"question": "синтез?", "category": "synthesis", "prediction_ids": ["t1", "s1"]},
        {"question": "рецепт борщу", "category": "off_domain", "prediction_ids": []},
    ]
    out = build_gold(_retrieval(), manual, _preds())

    single = [r for r in out if r["category"] == "single_source"]
    assert len(single) == 2
    # 50/50: t1 (idx0) → claim-фраза, t2 (idx1) → situation-фраза
    by_tid = {r["expected_sources"][0]["prediction"]["id"]: r for r in single}
    assert by_tid["t1"]["question"] == "claim-фраза A"
    assert by_tid["t2"]["question"] == "situation-фраза B"
    assert by_tid["t1"]["expected_sources"][0]["prediction"]["claim_text"] == "клейм-1"  # вморожено

    syn = next(r for r in out if r["category"] == "synthesis")
    assert syn["answerable"] is True
    assert {e["prediction"]["id"] for e in syn["expected_sources"]} == {"t1", "s1"}
    assert {e["prediction"]["claim_text"] for e in syn["expected_sources"]} == {"клейм-1", "синтез-клейм"}

    off = next(r for r in out if r["category"] == "off_domain")
    assert off["answerable"] is False
    assert off["expected_sources"] == []


def test_build_gold_failloud_on_unknown_prediction():
    manual = [{"question": "x", "category": "synthesis", "prediction_ids": ["NOPE"]}]
    with pytest.raises(KeyError):
        build_gold(_retrieval(), manual, _preds())
```

- [ ] **Step 3: Запусти — має впасти**

Run: `.venv/bin/python -m pytest tests/test_generation_gold.py tests/test_build_generation_gold.py -q`
Expected: FAIL — `test_generation_gold`: старий `ExpectedSource{prediction_id, claim}` дає ValidationError на новому gold-записі `{prediction}`; `test_build_generation_gold`: старий `build_gold` пише ключі `{prediction_id, claim}` → асерти на `["prediction"]["id"]` падають (KeyError).

- [ ] **Step 4: ExpectedSource → {prediction: Prediction}**

У `scripts/generation/gen_models.py` верхній блок імпортів має стати таким (окрема first-party група — інакше ruff isort лає):

```python
# scripts/generation/gen_models.py
from __future__ import annotations

from pydantic import BaseModel

from prophet_checker.models.domain import Prediction
```

і заміни `ExpectedSource` на:

```python
class ExpectedSource(BaseModel):
    prediction: Prediction
```

- [ ] **Step 5: build_generation_gold — вморожувати повні прогнози + читати БД**

Повний новий вміст `scripts/generation/build_generation_gold.py`:

```python
# scripts/generation/build_generation_gold.py
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from prophet_checker.models.domain import Prediction  # noqa: E402

DATA = PROJECT_ROOT / "scripts" / "data"


def _frozen(pred: Prediction) -> dict:
    # embedding не потрібен генератору/судді й роздуває gold — виключаємо
    return {"prediction": pred.model_dump(mode="json", exclude={"embedding"})}


def build_gold(
    retrieval_gold: list[dict], manual: list[dict], predictions_by_id: dict[str, Prediction]
) -> list[dict]:
    """Pure transform: retrieval-gold + manual + вморожені прогнози → gold records."""
    by_target: dict[str, dict[str, str]] = {}
    for e in retrieval_gold:
        by_target.setdefault(e["target_id"], {})[e["source_field"]] = e["query"]

    out: list[dict] = []
    for i, tid in enumerate(sorted(by_target)):
        phr = by_target[tid]
        prefer = "claim_text" if i % 2 == 0 else "situation"
        other = "situation" if prefer == "claim_text" else "claim_text"
        out.append(
            {
                "id": f"a{i:03d}",
                "question": phr.get(prefer) or phr[other],
                "answerable": True,
                "expected_sources": [_frozen(predictions_by_id[tid])],
                "category": "single_source",
            }
        )

    s = o = 0
    for m in manual:
        answerable = m["category"] == "synthesis"
        if answerable:
            cid, s = f"s{s:03d}", s + 1
            expected = [_frozen(predictions_by_id[p]) for p in m["prediction_ids"]]
        else:
            cid, o = f"o{o:03d}", o + 1
            expected = []
        out.append(
            {
                "id": cid,
                "question": m["question"],
                "answerable": answerable,
                "expected_sources": expected,
                "category": m["category"],
            }
        )
    return out


async def _main() -> None:
    # DB-залежності — локально в entry-point: чиста build_gold лишається легкою для імпорту в тестах
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from prophet_checker.config import Settings
    from prophet_checker.storage.postgres import PostgresPredictionRepository

    retrieval_gold = json.loads((DATA / "retrieval_query_gold.json").read_text(encoding="utf-8"))
    manual = json.loads((DATA / "generation_manual_questions.json").read_text(encoding="utf-8"))

    ids = {e["target_id"] for e in retrieval_gold}
    for m in manual:
        ids.update(m.get("prediction_ids", []))

    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        repo = PostgresPredictionRepository(session_factory)
        predictions_by_id = {p.id: p for p in await repo.get_by_ids(sorted(ids))}
    finally:
        await engine.dispose()

    gold = build_gold(retrieval_gold, manual, predictions_by_id)
    out_path = DATA / "generation_gold.json"
    out_path.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(gold)} cases → {out_path}")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Перевірити gold.py (без змін коду)**

`scripts/generation/gold.py` лишається як є: `ExpectedSource(**es)` з `es = {"prediction": {...}}` Pydantic сам коерсить вкладений dict у `Prediction`. Нічого не міняй — просто переконайся, що тест із Step 1 проходить.

- [ ] **Step 7: Запусти — має пройти + вся сюїта**

Run: `.venv/bin/python -m pytest tests/test_generation_gold.py tests/test_build_generation_gold.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 8: Lint + commit**

```bash
.venv/bin/ruff check scripts/generation/gen_models.py scripts/generation/build_generation_gold.py tests/test_generation_gold.py tests/test_build_generation_gold.py
git add scripts/generation/gen_models.py scripts/generation/build_generation_gold.py tests/test_generation_gold.py tests/test_build_generation_gold.py
git commit -m "feat(generation-eval): вморожувати повні Prediction у gold (build читає БД)"
```

---

## Task 7: generation_eval.py — БД-free generate-only

**Files:**
- Modify: `scripts/generation/generation_eval.py`

Юніт-тесту нема (інтеграційний CLI). Перевірка — lint + dry-import.

- [ ] **Step 1: Переписати generation_eval.py**

Повний новий вміст `scripts/generation/generation_eval.py`:

```python
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
from generation.scorers import CompletenessScorer, FaithfulnessScorer  # noqa: E402
from prophet_checker.config import Settings  # noqa: E402
from prophet_checker.llm import LLMClient  # noqa: E402
from prophet_checker.models.domain import RetrievedPrediction  # noqa: E402
from prophet_checker.query.answer_orchestrator import AnswerOrchestrator  # noqa: E402

logger = logging.getLogger(__name__)

GOLD_PATH = PROJECT_ROOT / "scripts" / "data" / "generation_gold.json"
OUT_DIR = PROJECT_ROOT / "scripts" / "outputs" / "generation_eval"


async def _main(judge_model: str, limit: int, concurrency: int) -> None:
    settings = Settings()
    cases = load_generation_gold(GOLD_PATH)
    cases = [c for c in cases if c.labels.answerable]  # v2: лише answerable — gold ізолює генерацію
    if limit:  # 0 = усі; інакше — перші N
        cases = cases[:limit]
    judge = LLMJudge(build_eval_llm(judge_model, temperature=0), judge_id=judge_model)
    scorers = [FaithfulnessScorer(judge), CompletenessScorer(judge)]
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
    orchestrator = AnswerOrchestrator(llm)  # generate-only: query_orchestrator=None

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
```

- [ ] **Step 2: Lint + dry-import (без БД/ключів)**

Run: `.venv/bin/ruff check scripts/generation/generation_eval.py && .venv/bin/python -c "import sys; sys.path[:0]=['src','scripts']; import generation.generation_eval"`
Expected: ruff чисто; import без помилок (резолвить усі символи; не запускає `_main`).

- [ ] **Step 3: Повна сюїта (не зачеплено)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/generation/generation_eval.py
git commit -m "feat(generation-eval): БД-free generate-only раннер (answer_from_sources на gold)"
```

---

## Task 8: Перегенерувати gold + smoke (потребує БД)

**Files:**
- Modify: `scripts/data/generation_gold.json`

**Передумова:** Postgres піднятий (`docker compose up -d`), застосовані міграції, і **корпусні прогнози присутні** в БД за тими ж id, що в `retrieval_query_gold.json`/`generation_manual_questions.json` (того ж сетапу, що його вимагав retrieval-eval). У `.env` — `DATABASE_URL`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`.

- [ ] **Step 1: Перегенерувати датасет із вмороженими прогнозами**

Run: `.venv/bin/python scripts/generation/build_generation_gold.py`
Expected: `wrote N cases → .../generation_gold.json`. Якщо `KeyError` — якогось expected-id нема в БД (fail-loud): досип корпус у БД, тоді повтори.

- [ ] **Step 2: Перевірити форму gold**

Run: `.venv/bin/python -c "import json; g=json.load(open('scripts/data/generation_gold.json')); a=[r for r in g if r['answerable']][0]; print(a['id'], list(a['expected_sources'][0]['prediction'].keys())[:6])"`
Expected: друкує id + ключі прогнозу (`id, document_id, person_id, claim_text, situation, prediction_date`) — тобто вморожено повний прогноз.

- [ ] **Step 3: Завантажувач приймає новий gold**

Run: `.venv/bin/python -c "import sys; sys.path[:0]=['src','scripts']; from generation.gold import load_generation_gold; from pathlib import Path; c=load_generation_gold(Path('scripts/data/generation_gold.json')); ans=[x for x in c if x.labels.answerable]; print(len(c),'cases', len(ans),'answerable'); print(ans[0].labels.expected_sources[0].prediction.claim_text[:40])"`
Expected: друкує лічильники + claim_text першого — без помилок валідації.

- [ ] **Step 4: Smoke — eval на 3 кейсах**

Run: `.venv/bin/python scripts/generation/generation_eval.py --limit 3 --concurrency 1`
Expected: лог `generation eval: 3 cases ...`, прогрес run/scoring, фінальний `faithfulness=… recall=…`, `report → .../report.md`. Жодних БД-звернень у рантаймі (лише виклики Gemini-генератора + Claude-судді).

- [ ] **Step 5: Повна сюїта + commit gold**

```bash
.venv/bin/python -m pytest tests/ -q
git add scripts/data/generation_gold.json
git commit -m "data(generation-eval): перегенерувати gold з вмороженими прогнозами (v2)"
```

---

## Готово, коли

- Уся сюїта зелена; refusal-код і поля прибрані всюди.
- `generation_eval.py` ганяє faithfulness+completeness на замороженому gold **без БД** у рантаймі.
- `generation_gold.json` містить повні вморожені прогнози в `expected_sources[].prediction`.
- `report.md`/`report.json` згенеровані на smoke-прогоні.

**Поза скоупом (парк-трек, чип `task_a358c756`):** refusal, off-corpus, поріг релевантності, end-to-end RAG. Off_domain/near_domain-питання лишаються в gold, але відсіюються фільтром `answerable` — знадобляться парк-треку.
