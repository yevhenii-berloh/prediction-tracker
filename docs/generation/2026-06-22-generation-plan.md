# RAG Answer Generation (v1.5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /answer` що на питання UA повертає зв'язну відповідь із посиланням на джерела-прогнози (`AnswerResult{query, answer, sources}`), retrieval-only поверх `QueryOrchestrator`, з short-circuit refusal на порожньому корпусі.

**Architecture:** Новий `AnswerOrchestrator(query_orchestrator, llm)` переюзає `QueryOrchestrator.search()`, на порожніх результатах віддає canned refusal (без LLM), інакше `build_rag_prompt(question, sources)` + `LLMClient.complete(..., RAG_SYSTEM)`. Endpoint у `app.py` через `lifespan`; `build_rag_prompt` гартується з magic-dict на типізований `list[RetrievedPrediction]` з id/датами.

**Tech Stack:** Python 3.14, FastAPI, LiteLLM (`LLMClient`, Gemini 3.1 Flash Lite), pytest (`asyncio_mode=auto`), httpx ASGITransport, ruff.

**Spec:** [`2026-06-22-generation-design.md`](2026-06-22-generation-design.md)

**Без міграцій:** схема БД не змінюється.

---

## File Structure

```
src/prophet_checker/
  models/domain.py             # + AnswerResult (без unit-тесту — pure Pydantic, CLAUDE.md)
  llm/prompts.py               # build_rag_prompt: list[dict] → list[RetrievedPrediction] + id/дати
  query/answer_orchestrator.py # NEW — AnswerOrchestrator + REFUSAL_NO_DATA
  factory.py                   # + build_answer_orchestrator
  app.py                       # + AnswerRequest, POST /answer, lifespan wiring
tests/
  test_llm_prompts.py          # update test_build_rag_prompt під нову сигнатуру
  test_answer_orchestrator.py  # NEW
  test_app_endpoints.py        # + /answer tests
```

Конвенції: типізовані Pydantic-межі (CLAUDE.md:80) + Protocol/деп-типи (82); edit-in-place (86);
`logger`, без `print()` у `src/` (92); коміти укр. conventional.

---

### Task 1: `AnswerResult` + гартування `build_rag_prompt`

**Files:**
- Modify: `src/prophet_checker/models/domain.py`
- Modify: `src/prophet_checker/llm/prompts.py:371-376`
- Test: `tests/test_llm_prompts.py:72-83`

`AnswerResult` — pure Pydantic (лише field-declarations), unit-тест не потрібен (CLAUDE.md).
Перший крок — додати модель, щоб тест промпта міг її імпортити.

- [ ] **Step 1: Додати `AnswerResult`** (в `models/domain.py`, після `QueryResult`)

```python
class AnswerResult(BaseModel):
    query: str
    answer: str
    sources: list[RetrievedPrediction]
```

- [ ] **Step 2: Переписати наявний тест** (замінити `test_build_rag_prompt`, рядки 72–83)

```python
def test_build_rag_prompt():
    from datetime import date

    from prophet_checker.models.domain import Prediction, RetrievedPrediction

    sources = [
        RetrievedPrediction(
            prediction=Prediction(
                id="pred-1",
                document_id="d",
                person_id="x",
                claim_text="Контрнаступ не досягне моря",
                prediction_date=date(2023, 6, 1),
                status=PredictionStatus.REFUTED,
                confidence=0.7,
            ),
            distance=0.2,
            rank=1,
        )
    ]
    prompt = build_rag_prompt(question="Що казав про контрнаступ?", sources=sources)
    assert "Що казав про контрнаступ?" in prompt
    assert "Контрнаступ не досягне моря" in prompt
    assert "pred-1" in prompt  # id для цитування
    assert "2023-06-01" in prompt  # дата
    assert "refuted" in prompt  # статус
```

Переконатись, що `PredictionStatus` імпортовано у `test_llm_prompts.py` (додати, якщо нема:
`from prophet_checker.models.domain import PredictionStatus`).

- [ ] **Step 3: Запустити — переконатись, що падає**

Run: `.venv/bin/python -m pytest tests/test_llm_prompts.py::test_build_rag_prompt -q`
Expected: FAIL — `TypeError` (несподіваний kwarg `sources`) або `AssertionError` на `pred-1`

- [ ] **Step 4: Переписати `build_rag_prompt`** (`llm/prompts.py`, замінити рядки 371–376)

```python
def build_rag_prompt(question: str, sources: list["RetrievedPrediction"]) -> str:
    lines = []
    for s in sources:
        p = s.prediction
        target = f", target: {p.target_date.isoformat()}" if p.target_date else ""
        situation = f" | situation: {p.situation}" if p.situation else ""
        lines.append(
            f"[{p.id}] {p.claim_text}{situation} "
            f"(date: {p.prediction_date.isoformat()}{target}, "
            f"status: {p.status.value}, confidence: {p.confidence})"
        )
    context_str = "\n".join(lines)
    return RAG_TEMPLATE.format(question=question, predictions_context=context_str)
```

Додати імпорт типу під `TYPE_CHECKING` угорі `llm/prompts.py` (уникнути циклу imports):
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prophet_checker.models.domain import RetrievedPrediction
```
(Якщо `from __future__ import annotations` уже є у файлі — рядкова анотація `list["RetrievedPrediction"]` працює без рантайм-імпорту.)

- [ ] **Step 5: Запустити — переконатись, що проходить**

Run: `.venv/bin/python -m pytest tests/test_llm_prompts.py -q`
Expected: PASS

- [ ] **Step 6: Lint + commit**

```bash
.venv/bin/ruff check src/prophet_checker/models/domain.py src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py
.venv/bin/ruff format src/prophet_checker/models/domain.py src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py
git add src/prophet_checker/models/domain.py src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py
git commit -m "feat(generation): AnswerResult + build_rag_prompt типізований вхід + id/дати"
```

---

### Task 2: `AnswerOrchestrator`

**Files:**
- Create: `src/prophet_checker/query/answer_orchestrator.py`
- Test: `tests/test_answer_orchestrator.py`

- [ ] **Step 1: Написати падаючий тест**

```python
# tests/test_answer_orchestrator.py
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from fakes import FakePredictionRepo, FakeVectorStore

from prophet_checker.models.domain import Prediction
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


async def test_answer_refuses_without_calling_llm_when_no_sources():
    qo = QueryOrchestrator(_embedder(), FakeVectorStore(), FakePredictionRepo())
    llm = _llm()
    orch = AnswerOrchestrator(qo, llm)

    result = await orch.answer("q")

    assert result.answer == REFUSAL_NO_DATA
    assert result.sources == []
    llm.complete.assert_not_awaited()


async def test_answer_generates_with_sources():
    store = FakeVectorStore()
    repo = FakePredictionRepo()
    await store.store_embedding("p1", [0.1, 0.1, 0.1])
    await repo.save(
        Prediction(
            id="p1",
            document_id="d",
            person_id="x",
            claim_text="claim",
            prediction_date=date(2024, 1, 1),
        )
    )
    qo = QueryOrchestrator(_embedder(), store, repo)
    llm = _llm("  відповідь  ")
    orch = AnswerOrchestrator(qo, llm)

    result = await orch.answer("питання", limit=5)

    assert result.query == "питання"
    assert result.answer == "відповідь"  # обрізані пробіли
    assert [s.prediction.id for s in result.sources] == ["p1"]
    llm.complete.assert_awaited_once()
    prompt_arg = llm.complete.call_args.args[0]
    assert "p1" in prompt_arg  # промпт містить джерело
```

- [ ] **Step 2: Запустити — переконатись, що падає**

Run: `.venv/bin/python -m pytest tests/test_answer_orchestrator.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'prophet_checker.query.answer_orchestrator'`

- [ ] **Step 3: Реалізувати**

```python
# src/prophet_checker/query/answer_orchestrator.py
from __future__ import annotations

import logging

from prophet_checker.llm import LLMClient
from prophet_checker.llm.prompts import RAG_SYSTEM, build_rag_prompt
from prophet_checker.models.domain import AnswerResult
from prophet_checker.query.orchestrator import QueryOrchestrator

logger = logging.getLogger(__name__)

REFUSAL_NO_DATA = (
    "За наявними даними я не знайшов релевантних прогнозів на цей запит. "
    "Аналіз автоматизований і може містити неточності."
)


class AnswerOrchestrator:
    def __init__(self, query_orchestrator: QueryOrchestrator, llm: LLMClient) -> None:
        self._query_orchestrator = query_orchestrator
        self._llm = llm

    async def answer(self, question: str, limit: int = 10) -> AnswerResult:
        result = await self._query_orchestrator.search(question, limit=limit)
        if not result.results:
            logger.info("answer: no relevant sources, refusing")
            return AnswerResult(query=question, answer=REFUSAL_NO_DATA, sources=[])
        prompt = build_rag_prompt(question, result.results)
        text = await self._llm.complete(prompt, system=RAG_SYSTEM)
        logger.info("answer: generated from %d sources", len(result.results))
        return AnswerResult(query=question, answer=text.strip(), sources=result.results)
```

Логування: per-module `logger`, INFO-milestone з лічильником (без payload — CLAUDE.md:106);
помилки не ловить (спливають до endpoint, що логує один раз — CLAUDE.md:105).

- [ ] **Step 4: Запустити — переконатись, що проходить**

Run: `.venv/bin/python -m pytest tests/test_answer_orchestrator.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check src/prophet_checker/query/answer_orchestrator.py tests/test_answer_orchestrator.py
.venv/bin/ruff format src/prophet_checker/query/answer_orchestrator.py tests/test_answer_orchestrator.py
git add src/prophet_checker/query/answer_orchestrator.py tests/test_answer_orchestrator.py
git commit -m "feat(generation): AnswerOrchestrator (refusal short-circuit + RAG-генерація)"
```

---

### Task 3: `factory.build_answer_orchestrator`

**Files:**
- Modify: `src/prophet_checker/factory.py`

(Без unit-тесту — будує реальний engine/LLM, як інші білдери; покривається endpoint-тестами Task 5.)

- [ ] **Step 1: Додати імпорт** (`factory.py`)

```python
from prophet_checker.query.answer_orchestrator import AnswerOrchestrator
```

- [ ] **Step 2: Додати білдер** (у кінець `factory.py`)

```python
async def build_answer_orchestrator(
    settings: Settings, stack: AsyncExitStack
) -> AnswerOrchestrator:
    query_orchestrator = await build_query_orchestrator(settings, stack)
    llm = LLMClient(
        provider="gemini",
        model="gemini-3.1-flash-lite-preview",
        api_key=settings.gemini_api_key,
        temperature=0,
    )
    return AnswerOrchestrator(query_orchestrator, llm)
```

- [ ] **Step 3: Перевірити імпорт + lint**

Run: `.venv/bin/python -c "import prophet_checker.factory"`
Expected: без помилок

```bash
.venv/bin/ruff check src/prophet_checker/factory.py
.venv/bin/ruff format src/prophet_checker/factory.py
```

- [ ] **Step 4: Commit**

```bash
git add src/prophet_checker/factory.py
git commit -m "feat(generation): build_answer_orchestrator"
```

---

### Task 4: `POST /answer` endpoint + lifespan wiring

**Files:**
- Modify: `src/prophet_checker/app.py`
- Test: `tests/test_app_endpoints.py`

- [ ] **Step 1: Написати падаючі тести** (додати в `tests/test_app_endpoints.py`)

```python
@pytest.fixture(autouse=True)
def _clear_answer_orchestrator_state():
    yield
    if hasattr(app.state, "answer_orchestrator"):
        delattr(app.state, "answer_orchestrator")


async def test_answer_returns_answer_and_sources():
    from prophet_checker.models.domain import (
        AnswerResult,
        Prediction,
        RetrievedPrediction,
    )

    ao = MagicMock()
    pred = Prediction(
        id="p1", document_id="d", person_id="x", claim_text="c", prediction_date=date(2024, 1, 1)
    )
    ao.answer = AsyncMock(
        return_value=AnswerResult(
            query="q",
            answer="відповідь",
            sources=[RetrievedPrediction(prediction=pred, distance=0.1, rank=1)],
        )
    )
    app.state.answer_orchestrator = ao

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/answer", json={"question": "q", "limit": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "відповідь"
    assert body["sources"][0]["prediction"]["id"] == "p1"


async def test_answer_422_on_empty_question():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/answer", json={"question": "", "limit": 5})
    assert resp.status_code == 422


async def test_answer_503_when_not_initialized():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/answer", json={"question": "q"})
    assert resp.status_code == 503
```

(`date` уже імпортовано у файлі з Task query-serving; якщо ні — додати `from datetime import date`.)

- [ ] **Step 2: Запустити — переконатись, що падає**

Run: `.venv/bin/python -m pytest tests/test_app_endpoints.py::test_answer_503_when_not_initialized -q`
Expected: FAIL — 404 (endpoint ще не існує)

- [ ] **Step 3: Оновити `app.py`**

Додати до імпортів:
```python
from prophet_checker.factory import (
    build_answer_orchestrator,
    build_orchestrator,
    build_query_orchestrator,
)
from prophet_checker.models.domain import AnswerResult, QueryResult
```
(замінити наявний рядок `from prophet_checker.factory import build_orchestrator, build_query_orchestrator`
і додати `AnswerResult` до наявного імпорту `QueryResult`.)

У `lifespan`, після `app.state.query_orchestrator = ...`, додати:
```python
        app.state.answer_orchestrator = await build_answer_orchestrator(settings, stack)
```

Додати модель і endpoint (після `query`):
```python
class AnswerRequest(BaseModel):
    question: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


@app.post("/answer", response_model=AnswerResult)
async def answer(req: AnswerRequest, request: Request) -> AnswerResult:
    answer_orchestrator = getattr(request.app.state, "answer_orchestrator", None)
    if answer_orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="answer orchestrator not initialized — server is starting up or shutting down",
        )
    try:
        return await answer_orchestrator.answer(req.question, req.limit)
    except Exception as exc:
        logger.exception("answer failed")
        raise HTTPException(status_code=500, detail=f"answer failure: {type(exc).__name__}")
```

- [ ] **Step 4: Запустити — повна сюїта**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (усі — наявні + 3 нові /answer)

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check src/prophet_checker/app.py tests/test_app_endpoints.py
.venv/bin/ruff format src/prophet_checker/app.py tests/test_app_endpoints.py
git add src/prophet_checker/app.py tests/test_app_endpoints.py
git commit -m "feat(generation): POST /answer endpoint + lifespan wiring"
```

---

### Task 5: Ручна інтеграційна перевірка (real Postgres + LLM, опційно)

**Files:** немає (ручні кроки)

**Передумова:** `docker compose up -d`; `alembic upgrade head`; ембединги backfill'нуто; `GEMINI_API_KEY` + `OPENAI_API_KEY` у `.env`.

- [ ] **Step 1: Запустити app**

Run: `.venv/bin/python -m prophet_checker`
Expected: uvicorn на `127.0.0.1:8000`

- [ ] **Step 2: Запит до /answer**

Run:
```bash
curl -s -X POST localhost:8000/answer \
  -H 'Content-Type: application/json' \
  -d '{"question": "що казали про завершення війни", "limit": 5}' | python3 -m json.tool
```
Expected: JSON `{"query": ..., "answer": "<зв'язний текст українською з дисклеймером>", "sources": [{"prediction": {...}, "distance": .., "rank": 1}, ...]}`.

- [ ] **Step 3: Перевірити refusal** (питання поза корпусом)

Run:
```bash
curl -s -X POST localhost:8000/answer \
  -d '{"question": "рецепт борщу"}' -H 'Content-Type: application/json' | python3 -m json.tool
```
Expected: якщо sources порожні — `answer` = текст `REFUSAL_NO_DATA`, `sources: []`. (Якщо корпус усе ж матчить — побачиш топ-k; refusal спрацьовує лише на 0 результатів.)

---

## Follow-ups (поза цим планом)

- **Eval генерації:** faithfulness / citation precision / refusal correctness (Ragas/Trust-Score) — окремий трек.
- **Маркерні цитати** [n]→id (варіант C); **поріг релевантності** для refusal за слабким матчем (тюнінг по gold).
- **Telegram-бот** — фронтенд над `/answer`.
