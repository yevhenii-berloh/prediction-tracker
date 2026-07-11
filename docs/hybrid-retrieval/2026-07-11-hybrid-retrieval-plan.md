# Hybrid Retrieval Частина B v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** NL-запити з автором/датами («що Арестович казав про Крим у 2022?») працюють end-to-end: self-querying LLM-планер витягує типізовані фільтри, векторний пошук застосовує їх як `WHERE`-предикати.

**Architecture:** за дизайном [`2026-07-11-hybrid-retrieval-design.md`](2026-07-11-hybrid-retrieval-design.md) (рішення Р1–Р7 там, план їх не пере-аргументує). Новий `QueryPlanner` (LLM → `QueryPlan`), фільтри протікають `QueryOrchestrator → VectorStore.search_similar` як опційний параметр; збій планера = `QueryPlanningError` → наявний 500-boundary в `app.py`; невідомий автор = явна відмова в `AnswerOrchestrator`.

**Tech Stack:** Python 3.14, Pydantic, SQLAlchemy async + pgvector (exact scan, без ANN), LiteLLM (Gemini Flash Lite, temp 0), pytest (`asyncio_mode=auto`, fakes без Docker).

**Гілка:** `feat/hybrid-retrieval`. Усі команди — з кореня репо `/Users/evgenijberlog/Brain/prediction-tracker`.

**Гейти на кожен коміт:** pre-commit хук жене complexipy ratchet автоматично; перед комітом руками — `.venv/bin/ruff check src tests` (зелений на змінених файлах).

**Пояснення після кожного таска:** останній крок кожного таска — скіл `explain-diff-html`
на свіжому коміті таска (HTML-розбір змін + квіз). Цей крок виконує **головна сесія, не
імплементаційний субагент** (квіз — інтерактив з користувачем). Наступний таск не
стартує, доки користувач не пройшов квіз попереднього.

---

### Task 1: Доменні моделі — `SearchFilters`, `QueryPlan`, `QueryResult.unknown_author`

**Скоуп:** додати дві нові Pydantic-моделі й одне опційне поле в `QueryResult` (design §5.1). Без тестів: чисті field-декларації — репо-правило «Don't unit-test pure Pydantic models»; поведінку перевіряють консумери в Tasks 2–7.

**Files:**
- Modify: `src/prophet_checker/models/domain.py` (після `class VectorMatch`, ~рядок 99)

- [ ] **Step 1: Додати моделі**

У `models/domain.py` одразу після `class VectorMatch` вставити:

```python
class SearchFilters(BaseModel):
    person_id: str | None = None
    unknown_author: str | None = None  # ім'я, як згадано в питанні; взаємовиключне з person_id
    prediction_date_from: date | None = None
    prediction_date_to: date | None = None
    target_date_from: date | None = None
    target_date_to: date | None = None


class QueryPlan(BaseModel):
    semantic_query: str
    filters: SearchFilters
```

У `class QueryResult` додати поле (після `results`):

```python
class QueryResult(BaseModel):
    query: str
    results: list[RetrievedPrediction]
    unknown_author: str | None = None  # сигнал для явної відмови в answer-шарі (design Р3)
```

- [ ] **Step 2: Регресія + лінт**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check src`
Expected: `312 passed`, ruff чистий.

- [ ] **Step 3: Commit**

```bash
git add src/prophet_checker/models/domain.py
git commit -m "feat(query): доменні моделі SearchFilters/QueryPlan + QueryResult.unknown_author"
```

- [ ] **Step 4: Explain-diff з квізом (головна сесія)**

Викликати скіл `explain-diff-html` на коміті таска (діф: `git show HEAD`).
Дочекатися проходження квізу користувачем перед Task 2.

---

### Task 2: Фільтри у `VectorStore` Protocol + `FakeVectorStore`

**Скоуп:** розширити Protocol-метод `search_similar` параметром `filters` і навчити фейк тим самим предикатам in-memory (design §5.4), включно з null-inclusive семантикою `target_date` (Р2). Фейк додатково записує `last_filters` — Task 6 асертить прокидання.

**Files:**
- Modify: `src/prophet_checker/storage/interfaces.py:62-68`
- Modify: `tests/fakes.py:133-149` (`FakeVectorStore`)
- Test: `tests/test_vector_store_filters.py` (новий)

- [ ] **Step 1: Написати падаючі тести**

Створити `tests/test_vector_store_filters.py`:

```python
from datetime import date

from fakes import FakeVectorStore

from prophet_checker.models.domain import SearchFilters


async def _store_with_three() -> FakeVectorStore:
    store = FakeVectorStore()
    await store.store_embedding(
        "p1", [0.1], person_id="a1",
        prediction_date=date(2022, 3, 1), target_date=date(2023, 6, 1),
    )
    await store.store_embedding(
        "p2", [0.2], person_id="a2",
        prediction_date=date(2023, 5, 1), target_date=None,
    )
    await store.store_embedding(
        "p3", [0.3], person_id="a1",
        prediction_date=date(2024, 1, 1), target_date=date(2024, 2, 1),
    )
    return store


async def test_no_filters_returns_all():
    store = await _store_with_three()
    matches = await store.search_similar([0.0], limit=10)
    assert [m.prediction_id for m in matches] == ["p1", "p2", "p3"]


async def test_person_filter():
    store = await _store_with_three()
    matches = await store.search_similar(
        [0.0], limit=10, filters=SearchFilters(person_id="a1")
    )
    assert [m.prediction_id for m in matches] == ["p1", "p3"]


async def test_prediction_date_range():
    store = await _store_with_three()
    filters = SearchFilters(
        prediction_date_from=date(2022, 1, 1), prediction_date_to=date(2022, 12, 31)
    )
    matches = await store.search_similar([0.0], limit=10, filters=filters)
    assert [m.prediction_id for m in matches] == ["p1"]


async def test_target_date_range_is_null_inclusive():
    # p2 має target_date=None → лишається (Р2); p3 поза діапазоном → відсікається
    store = await _store_with_three()
    filters = SearchFilters(
        target_date_from=date(2023, 1, 1), target_date_to=date(2023, 12, 31)
    )
    matches = await store.search_similar([0.0], limit=10, filters=filters)
    assert [m.prediction_id for m in matches] == ["p1", "p2"]


async def test_filters_are_anded():
    store = await _store_with_three()
    filters = SearchFilters(person_id="a1", prediction_date_from=date(2024, 1, 1))
    matches = await store.search_similar([0.0], limit=10, filters=filters)
    assert [m.prediction_id for m in matches] == ["p3"]


async def test_last_filters_recorded():
    store = await _store_with_three()
    filters = SearchFilters(person_id="a1")
    await store.search_similar([0.0], limit=10, filters=filters)
    assert store.last_filters == filters
```

- [ ] **Step 2: Переконатися, що падають**

Run: `.venv/bin/python -m pytest tests/test_vector_store_filters.py -q`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'person_id'` (store_embedding) або `'filters'`.

- [ ] **Step 3: Розширити Protocol**

У `storage/interfaces.py`: додати `SearchFilters` до наявного import-блоку з `models.domain` і змінити сигнатуру:

```python
class VectorStore(Protocol):
    async def store_embedding(self, prediction_id: str, embedding: list[float]) -> None: ...
    async def is_embedding_present(self, prediction_id: str) -> bool: ...
    async def search_similar(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[VectorMatch]: ...
```

- [ ] **Step 4: Реалізувати фейк**

Замінити `FakeVectorStore` у `tests/fakes.py` (додати імпорти `date` з `datetime`, `dataclass`/`field` з `dataclasses`, `SearchFilters` з `prophet_checker.models.domain`):

```python
@dataclass
class _VectorMeta:
    person_id: str | None = None
    prediction_date: date | None = None
    target_date: date | None = None


def _date_in_range(
    value: date | None, lo: date | None, hi: date | None, *, null_inclusive: bool
) -> bool:
    if lo is None and hi is None:
        return True
    if value is None:
        return null_inclusive
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


class FakeVectorStore(VectorStore):
    def __init__(self):
        self._entries: list[tuple[str, list[float]]] = []
        self._meta: dict[str, _VectorMeta] = {}
        self.last_filters: SearchFilters | None = None

    async def store_embedding(
        self,
        prediction_id: str,
        embedding: list[float],
        *,
        person_id: str | None = None,
        prediction_date: date | None = None,
        target_date: date | None = None,
    ) -> None:
        self._entries.append((prediction_id, embedding))
        self._meta[prediction_id] = _VectorMeta(person_id, prediction_date, target_date)

    async def is_embedding_present(self, prediction_id: str) -> bool:
        return any(pid == prediction_id for pid, _ in self._entries)

    async def search_similar(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[VectorMatch]:
        self.last_filters = filters
        matches: list[VectorMatch] = []
        for i, (pid, _) in enumerate(self._entries):
            if filters is not None and not self._passes(pid, filters):
                continue
            matches.append(VectorMatch(prediction_id=pid, distance=float(i)))
            if len(matches) == limit:
                break
        return matches

    def _passes(self, pid: str, f: SearchFilters) -> bool:
        meta = self._meta.get(pid, _VectorMeta())
        if f.person_id is not None and meta.person_id != f.person_id:
            return False
        if not _date_in_range(
            meta.prediction_date, f.prediction_date_from, f.prediction_date_to,
            null_inclusive=False,  # prediction_date NOT NULL у схемі; NULL валить предикат як у SQL
        ):
            return False
        return _date_in_range(
            meta.target_date, f.target_date_from, f.target_date_to,
            null_inclusive=True,  # Р2 дизайну
        )

```

Keyword-only meta-аргументи `store_embedding` — розширення лише фейка (структурно сумісне: прод-код кличе двома позиційними).

- [ ] **Step 5: Тести зелені, регресія**

Run: `.venv/bin/python -m pytest tests/test_vector_store_filters.py tests/ -q`
Expected: нові 6 PASS, уся сюїта зелена (318).

- [ ] **Step 6: Commit**

```bash
git add src/prophet_checker/storage/interfaces.py tests/fakes.py tests/test_vector_store_filters.py
git commit -m "feat(storage): фільтри у VectorStore Protocol + FakeVectorStore"
```

- [ ] **Step 7: Explain-diff з квізом (головна сесія)**

Викликати скіл `explain-diff-html` на коміті таска (діф: `git show HEAD`).
Дочекатися проходження квізу користувачем перед Task 3.

---

### Task 3: `WHERE`-предикати у `PostgresVectorStore.search_similar`

**Скоуп:** транслювати `SearchFilters` у SQLAlchemy-предикати поверх наявного exact-скану (design §5.4, Р7). Предикат-білдер — чиста функція, юніт-тестована через компільований SQL (реальна БД — у смоуку Task 9).

**Files:**
- Modify: `src/prophet_checker/storage/postgres.py:352-365` (`search_similar`) + нова модульна функція перед `class PostgresVectorStore`
- Test: `tests/test_vector_store_filters.py` (додати клас-блок тестів)

- [ ] **Step 1: Написати падаючі тести**

Додати в кінець `tests/test_vector_store_filters.py`:

```python
from prophet_checker.storage.postgres import _filter_predicates


def _sql(filters: SearchFilters) -> str:
    return " ; ".join(str(p) for p in _filter_predicates(filters))


def test_predicates_empty_filters():
    assert _filter_predicates(SearchFilters()) == []


def test_predicates_person():
    sql = _sql(SearchFilters(person_id="a1"))
    assert "predictions.person_id =" in sql


def test_predicates_prediction_date_bounds():
    sql = _sql(SearchFilters(
        prediction_date_from=date(2022, 1, 1), prediction_date_to=date(2022, 12, 31)
    ))
    assert "predictions.prediction_date >=" in sql
    assert "predictions.prediction_date <=" in sql


def test_predicates_target_date_null_inclusive():
    sql = _sql(SearchFilters(target_date_from=date(2023, 1, 1)))
    assert "predictions.target_date >=" in sql
    assert "predictions.target_date IS NULL" in sql
    assert " OR " in sql
```

- [ ] **Step 2: Переконатися, що падають**

Run: `.venv/bin/python -m pytest tests/test_vector_store_filters.py -q`
Expected: FAIL — `ImportError: cannot import name '_filter_predicates'`.

- [ ] **Step 3: Реалізувати**

У `storage/postgres.py`: розширити sqlalchemy-імпорт (`and_`, `or_`, `ColumnElement`), імпорт `SearchFilters` з `models.domain`. Перед `class PostgresVectorStore` додати:

```python
def _filter_predicates(filters: SearchFilters) -> list[ColumnElement[bool]]:
    preds: list[ColumnElement[bool]] = []
    if filters.person_id is not None:
        preds.append(PredictionDB.person_id == filters.person_id)
    if filters.prediction_date_from is not None:
        preds.append(PredictionDB.prediction_date >= filters.prediction_date_from)
    if filters.prediction_date_to is not None:
        preds.append(PredictionDB.prediction_date <= filters.prediction_date_to)

    target_bounds: list[ColumnElement[bool]] = []
    if filters.target_date_from is not None:
        target_bounds.append(PredictionDB.target_date >= filters.target_date_from)
    if filters.target_date_to is not None:
        target_bounds.append(PredictionDB.target_date <= filters.target_date_to)
    if target_bounds:
        # null-inclusive (design Р2): невідомий target_date не відсікаємо
        preds.append(or_(and_(*target_bounds), PredictionDB.target_date.is_(None)))
    return preds
```

У `search_similar` — нова сигнатура і одна вставка перед виконанням:

```python
    async def search_similar(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[VectorMatch]:
        async with self._session_factory() as session:
            dist = PredictionDB.embedding.cosine_distance(query_embedding)
            # лише ембеджені рядки: cosine_distance(NULL, q) = NULL → інакше distance None
            stmt = (
                select(PredictionDB.id, dist.label("distance"))
                .where(PredictionDB.embedding.is_not(None))
                .order_by(dist)
                .limit(limit)
            )
            if filters is not None:
                stmt = stmt.where(*_filter_predicates(filters))
            result = await session.execute(stmt)
            return [VectorMatch(prediction_id=r[0], distance=r[1]) for r in result.all()]
```

- [ ] **Step 4: Тести зелені, регресія**

Run: `.venv/bin/python -m pytest tests/test_vector_store_filters.py tests/ -q && .venv/bin/ruff check src tests`
Expected: все зелене.

- [ ] **Step 5: Commit**

```bash
git add src/prophet_checker/storage/postgres.py tests/test_vector_store_filters.py
git commit -m "feat(storage): typed WHERE-предикати у PostgresVectorStore.search_similar"
```

- [ ] **Step 6: Explain-diff з квізом (головна сесія)**

Викликати скіл `explain-diff-html` на коміті таска (діф: `git show HEAD`).
Дочекатися проходження квізу користувачем перед Task 4.

---

### Task 4: Self-query промпт + `parse_query_plan`

**Скоуп:** промпт-контракт планера і typed-парсер його відповіді (design §5.3, §7): валідний план проходить, невалідний падає `ValueError` (планер у Task 5 загорне у `QueryPlanningError`), порожній `semantic_query` нормалізується.

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py` (константи після `RAG_TEMPLATE`; функції в кінці файлу)
- Test: `tests/test_self_query_prompts.py` (новий)

- [ ] **Step 1: Написати падаючі тести**

Створити `tests/test_self_query_prompts.py`:

```python
import json
from datetime import date

import pytest

from prophet_checker.llm.prompts import build_self_query_prompt, parse_query_plan
from prophet_checker.models.domain import Person

PERSONS = [Person(id="a1", name="Олексій Арестович")]
KNOWN_IDS = {"a1"}


def _raw(**overrides) -> str:
    plan = {
        "semantic_query": "прогнози про Крим",
        "person_id": None,
        "unknown_author": None,
        "prediction_date_from": None,
        "prediction_date_to": None,
        "target_date_from": None,
        "target_date_to": None,
    }
    plan.update(overrides)
    return json.dumps(plan)


def test_build_prompt_contains_persons_today_question():
    prompt = build_self_query_prompt("Що казав?", PERSONS, today=date(2026, 7, 11))
    assert "Олексій Арестович" in prompt
    assert "a1" in prompt
    assert "2026-07-11" in prompt
    assert "Що казав?" in prompt


def test_parse_valid_full_plan():
    raw = _raw(
        person_id="a1",
        prediction_date_from="2022-01-01",
        prediction_date_to="2022-12-31",
    )
    plan = parse_query_plan(raw, KNOWN_IDS, question="q")
    assert plan.semantic_query == "прогнози про Крим"
    assert plan.filters.person_id == "a1"
    assert plan.filters.prediction_date_from == date(2022, 1, 1)
    assert plan.filters.prediction_date_to == date(2022, 12, 31)


def test_parse_unknown_author_passes_through():
    plan = parse_query_plan(_raw(unknown_author="Портников"), KNOWN_IDS, question="q")
    assert plan.filters.unknown_author == "Портников"
    assert plan.filters.person_id is None


def test_parse_broken_json_raises():
    with pytest.raises(ValueError):
        parse_query_plan("не json", KNOWN_IDS, question="q")


def test_parse_person_id_outside_list_raises():
    with pytest.raises(ValueError, match="unknown person_id"):
        parse_query_plan(_raw(person_id="ghost"), KNOWN_IDS, question="q")


def test_parse_person_and_unknown_author_together_raises():
    raw = _raw(person_id="a1", unknown_author="Хтось")
    with pytest.raises(ValueError, match="mutually exclusive"):
        parse_query_plan(raw, KNOWN_IDS, question="q")


def test_parse_inverted_range_raises():
    raw = _raw(target_date_from="2024-12-31", target_date_to="2024-01-01")
    with pytest.raises(ValueError, match="inverted"):
        parse_query_plan(raw, KNOWN_IDS, question="q")


def test_parse_empty_semantic_query_falls_back_to_question():
    plan = parse_query_plan(_raw(semantic_query="  "), KNOWN_IDS, question="оригінал")
    assert plan.semantic_query == "оригінал"
```

- [ ] **Step 2: Переконатися, що падають**

Run: `.venv/bin/python -m pytest tests/test_self_query_prompts.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_self_query_prompt'`.

- [ ] **Step 3: Реалізувати промпт і парсер**

У `llm/prompts.py` додати імпорти: `from datetime import date`, а до import-блоку з `models.domain` — `Person`, `QueryPlan`, `SearchFilters`.

Після `RAG_TEMPLATE` вставити константи:

```python
SELF_QUERY_SYSTEM = """You are a query planner for a database of predictions made by Ukrainian public figures.

Convert the user question (Ukrainian/Russian/English) into a JSON retrieval plan with a
semantic query and structured filters. You do NOT answer the question.

Filterable fields:
- person_id (string): prediction author. Match author mentions against the provided list
  of known persons (name variants and transliterations count as a match).
- prediction_date (date): when the prediction was MADE ("що казав у 2022" → this field).
- target_date (date): the time the prediction is ABOUT ("прогнози на 2023" → this field).

Rules:
1. semantic_query: the question stripped of author names and date constraints — keep only
   the topic. If nothing remains, restate the topic of the question in a few words.
2. Author mentioned and found in the list → person_id = its id, unknown_author = null.
3. Author mentioned but NOT in the list → unknown_author = the name exactly as mentioned
   in the question, person_id = null.
4. No author mentioned → person_id = null and unknown_author = null.
5. "When it was said" constraints → prediction_date_from/to. "About what time" constraints
   → target_date_from/to. A bare year YYYY expands to YYYY-01-01 .. YYYY-12-31.
6. Relative expressions ("минулого року", "нещодавно", "last month") resolve against
   today's date from the prompt.
7. Dates are ISO YYYY-MM-DD or null. Never invent constraints absent from the question.

Examples (assume known person "Олексій Арестович" id=a1, today 2026-07-11):
Q: "Що Арестович казав про Крим у 2022?"
{"semantic_query": "прогнози про Крим", "person_id": "a1", "unknown_author": null,
 "prediction_date_from": "2022-01-01", "prediction_date_to": "2022-12-31",
 "target_date_from": null, "target_date_to": null}
Q: "Які були прогнози на 2024 рік щодо завершення війни?"
{"semantic_query": "завершення війни", "person_id": null, "unknown_author": null,
 "prediction_date_from": null, "prediction_date_to": null,
 "target_date_from": "2024-01-01", "target_date_to": "2024-12-31"}
Q: "Що прогнозував Портников про вибори?"
{"semantic_query": "прогнози про вибори", "person_id": null, "unknown_author": "Портников",
 "prediction_date_from": null, "prediction_date_to": null,
 "target_date_from": null, "target_date_to": null}

Respond with ONLY the JSON object — no markdown fence, no commentary."""

SELF_QUERY_TEMPLATE = """Today: {today}

Known persons:
{persons}

Question: {question}"""
```

У кінець файлу — функції:

```python
def build_self_query_prompt(question: str, persons: list[Person], today: date) -> str:
    lines = [f"- {p.name} (id: {p.id})" for p in persons]
    return SELF_QUERY_TEMPLATE.format(
        today=today.isoformat(), persons="\n".join(lines), question=question
    )


_QUERY_PLAN_DATE_FIELDS = (
    "prediction_date_from",
    "prediction_date_to",
    "target_date_from",
    "target_date_to",
)


def parse_query_plan(raw: str, known_person_ids: set[str], question: str) -> QueryPlan:
    data = json.loads(_strip_code_fence(raw))

    person_id = data.get("person_id")
    unknown_author = data.get("unknown_author")
    if person_id is not None and person_id not in known_person_ids:
        raise ValueError(f"unknown person_id from planner: {person_id!r}")
    if person_id is not None and unknown_author is not None:
        raise ValueError("person_id and unknown_author are mutually exclusive")

    dates: dict[str, date | None] = {}
    for field in _QUERY_PLAN_DATE_FIELDS:
        value = data.get(field)
        dates[field] = date.fromisoformat(value) if value is not None else None

    for prefix in ("prediction_date", "target_date"):
        lo, hi = dates[f"{prefix}_from"], dates[f"{prefix}_to"]
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"inverted {prefix} range: {lo} > {hi}")

    semantic_query = (data.get("semantic_query") or "").strip() or question
    filters = SearchFilters(person_id=person_id, unknown_author=unknown_author, **dates)
    return QueryPlan(semantic_query=semantic_query, filters=filters)
```

Все невалідне падає `ValueError`-родиною (`json.JSONDecodeError` і помилка `date.fromisoformat` — її сабкласи), тож у Task 5 планер ловить один тип.

- [ ] **Step 4: Тести зелені, регресія**

Run: `.venv/bin/python -m pytest tests/test_self_query_prompts.py tests/ -q && .venv/bin/ruff check src tests`
Expected: нові 8 PASS, сюїта зелена.

- [ ] **Step 5: Commit**

```bash
git add src/prophet_checker/llm/prompts.py tests/test_self_query_prompts.py
git commit -m "feat(llm): self-query промпт і parse_query_plan"
```

- [ ] **Step 6: Explain-diff з квізом (головна сесія)**

Викликати скіл `explain-diff-html` на коміті таска (діф: `git show HEAD`).
Дочекатися проходження квізу користувачем перед Task 5.

---

### Task 5: `QueryPlanner` + `QueryPlanningError`

**Скоуп:** компонент query-understanding (design §5.2): персони → промпт → LLM → парсер; будь-який збій → `QueryPlanningError` (Р4, fail fast). Планер не логує помилку сам (boundary в `app.py` вже робить `logger.exception`).

**Files:**
- Create: `src/prophet_checker/query/planner.py`
- Test: `tests/test_query_planner.py` (новий)

- [ ] **Step 1: Написати падаючі тести**

Створити `tests/test_query_planner.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakes import FakePersonRepo

from prophet_checker.models.domain import Person
from prophet_checker.query.planner import QueryPlanner, QueryPlanningError

VALID_PLAN = json.dumps({
    "semantic_query": "прогнози про Крим",
    "person_id": "a1",
    "unknown_author": None,
    "prediction_date_from": "2022-01-01",
    "prediction_date_to": "2022-12-31",
    "target_date_from": None,
    "target_date_to": None,
})


def _llm(response: str | Exception) -> MagicMock:
    llm = MagicMock()
    if isinstance(response, Exception):
        llm.complete = AsyncMock(side_effect=response)
    else:
        llm.complete = AsyncMock(return_value=response)
    return llm


async def _repo() -> FakePersonRepo:
    repo = FakePersonRepo()
    await repo.save(Person(id="a1", name="Олексій Арестович"))
    return repo


async def test_plan_happy_path():
    planner = QueryPlanner(_llm(VALID_PLAN), await _repo())
    plan = await planner.plan("Що Арестович казав про Крим у 2022?")
    assert plan.semantic_query == "прогнози про Крим"
    assert plan.filters.person_id == "a1"


async def test_plan_prompt_contains_persons():
    llm = _llm(VALID_PLAN)
    planner = QueryPlanner(llm, await _repo())
    await planner.plan("питання")
    prompt = llm.complete.call_args.args[0]
    assert "Олексій Арестович" in prompt
    assert "питання" in prompt


async def test_llm_exception_wrapped():
    planner = QueryPlanner(_llm(RuntimeError("api down")), await _repo())
    with pytest.raises(QueryPlanningError):
        await planner.plan("питання")


async def test_unparseable_response_wrapped():
    planner = QueryPlanner(_llm("це не json"), await _repo())
    with pytest.raises(QueryPlanningError):
        await planner.plan("питання")
```

- [ ] **Step 2: Переконатися, що падають**

Run: `.venv/bin/python -m pytest tests/test_query_planner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'prophet_checker.query.planner'`.

- [ ] **Step 3: Реалізувати**

Створити `src/prophet_checker/query/planner.py`:

```python
from __future__ import annotations

import logging
from datetime import date

from prophet_checker.llm import LLMClient
from prophet_checker.llm.prompts import (
    SELF_QUERY_SYSTEM,
    build_self_query_prompt,
    parse_query_plan,
)
from prophet_checker.models.domain import QueryPlan
from prophet_checker.storage.interfaces import PersonRepository

logger = logging.getLogger(__name__)


class QueryPlanningError(Exception):
    """Планер не побудував валідний план — запит падає (design Р4, fail fast)."""


class QueryPlanner:
    def __init__(self, llm: LLMClient, person_repo: PersonRepository) -> None:
        self._llm = llm
        self._person_repo = person_repo

    async def plan(self, question: str) -> QueryPlan:
        persons = await self._person_repo.list_all()
        prompt = build_self_query_prompt(question, persons, today=date.today())
        try:
            raw = await self._llm.complete(prompt, system=SELF_QUERY_SYSTEM)
            plan = parse_query_plan(raw, {p.id for p in persons}, question)
        except Exception as exc:
            # не логуємо тут — boundary (app.py) робить logger.exception один раз
            raise QueryPlanningError(f"query planning failed: {exc}") from exc
        logger.debug("query plan: filters=%s", plan.filters)
        return plan
```

- [ ] **Step 4: Тести зелені, регресія**

Run: `.venv/bin/python -m pytest tests/test_query_planner.py tests/ -q && .venv/bin/ruff check src tests`
Expected: нові 4 PASS, сюїта зелена.

- [ ] **Step 5: Commit**

```bash
git add src/prophet_checker/query/planner.py tests/test_query_planner.py
git commit -m "feat(query): QueryPlanner з fail-fast QueryPlanningError"
```

- [ ] **Step 6: Explain-diff з квізом (головна сесія)**

Викликати скіл `explain-diff-html` на коміті таска (діф: `git show HEAD`).
Дочекатися проходження квізу користувачем перед Task 6.

---

### Task 6: Planner-гілка у `QueryOrchestrator`

**Скоуп:** опційний планер у `search` (design §5.5): план → short-circuit на `unknown_author` → embed `semantic_query` → фільтри в `search_similar`. `planner=None` → поведінка як сьогодні (порожні фільтри семантично тотожні відсутнім).

**Files:**
- Modify: `src/prophet_checker/query/orchestrator.py`
- Test: `tests/test_query_orchestrator.py` (додати тести)

- [ ] **Step 1: Написати падаючі тести**

Додати в `tests/test_query_orchestrator.py` (розширити наявні імпорти: `SearchFilters`, `QueryPlan` з `prophet_checker.models.domain`):

```python
def _planner(plan: QueryPlan) -> MagicMock:
    p = MagicMock()
    p.plan = AsyncMock(return_value=plan)
    return p


async def test_search_passes_filters_and_embeds_semantic_query():
    store, repo = await _store_repo_p1_p2()
    filters = SearchFilters(person_id="x", prediction_date_from=date(2022, 1, 1))
    embedder = _embedder()
    orch = QueryOrchestrator(
        embedder, store, repo,
        planner=_planner(QueryPlan(semantic_query="тема", filters=filters)),
    )

    await orch.search("Що X казав з 2022 про тему?", limit=10)

    embedder.embed.assert_awaited_once_with("тема")
    assert store.last_filters == filters


async def test_search_unknown_author_short_circuits():
    embedder = _embedder()
    plan = QueryPlan(
        semantic_query="тема", filters=SearchFilters(unknown_author="Портников")
    )
    orch = QueryOrchestrator(
        embedder, FakeVectorStore(), FakePredictionRepo(), planner=_planner(plan)
    )

    result = await orch.search("Що прогнозував Портников?")

    assert result.results == []
    assert result.unknown_author == "Портников"
    embedder.embed.assert_not_awaited()


async def test_search_without_planner_passes_empty_filters():
    store, repo = await _store_repo_p1_p2()
    orch = QueryOrchestrator(_embedder(), store, repo)  # planner=None
    result = await orch.search("q", limit=10)
    assert [r.prediction.id for r in result.results] == ["p1", "p2"]
    assert store.last_filters == SearchFilters()
```

- [ ] **Step 2: Переконатися, що падають**

Run: `.venv/bin/python -m pytest tests/test_query_orchestrator.py -q`
Expected: нові 3 FAIL — `TypeError: ... unexpected keyword argument 'planner'`.

- [ ] **Step 3: Реалізувати**

Замінити вміст `src/prophet_checker/query/orchestrator.py`:

```python
from __future__ import annotations

from prophet_checker.llm import EmbeddingClient
from prophet_checker.models.domain import (
    QueryPlan,
    QueryResult,
    RetrievedPrediction,
    SearchFilters,
)
from prophet_checker.query.planner import QueryPlanner
from prophet_checker.storage.interfaces import PredictionRepository, VectorStore


class QueryOrchestrator:
    def __init__(
        self,
        embedder: EmbeddingClient,
        vector_store: VectorStore,
        prediction_repo: PredictionRepository,
        relevance_threshold: float | None = None,
        planner: QueryPlanner | None = None,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._prediction_repo = prediction_repo
        self._relevance_threshold = relevance_threshold
        self._planner = planner

    async def search(self, question: str, limit: int = 10) -> QueryResult:
        plan = await self._resolve_plan(question)
        if plan.filters.unknown_author is not None:
            return QueryResult(
                query=question, results=[], unknown_author=plan.filters.unknown_author
            )
        embedding = await self._embedder.embed(plan.semantic_query)
        matches = await self._vector_store.search_similar(
            embedding, limit=limit, filters=plan.filters
        )
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

    async def _resolve_plan(self, question: str) -> QueryPlan:
        if self._planner is None:
            return QueryPlan(semantic_query=question, filters=SearchFilters())
        return await self._planner.plan(question)
```

- [ ] **Step 4: Тести зелені, регресія**

Run: `.venv/bin/python -m pytest tests/test_query_orchestrator.py tests/ -q && .venv/bin/ruff check src tests`
Expected: усе зелене (старі 5 тестів оркестратора — без правок).

- [ ] **Step 5: Commit**

```bash
git add src/prophet_checker/query/orchestrator.py tests/test_query_orchestrator.py
git commit -m "feat(query): planner-гілка в QueryOrchestrator"
```

- [ ] **Step 6: Explain-diff з квізом (головна сесія)**

Викликати скіл `explain-diff-html` на коміті таска (діф: `git show HEAD`).
Дочекатися проходження квізу користувачем перед Task 7.

---

### Task 7: Явна відмова `REFUSAL_UNKNOWN_AUTHOR` в `AnswerOrchestrator`

**Скоуп:** одна нова гілка в `answer()` (design §5.6): `unknown_author` → відмова з ім'ям автора, без виклику LLM. `answer_from_sources` не чіпаємо.

**Files:**
- Modify: `src/prophet_checker/query/answer_orchestrator.py`
- Test: `tests/test_answer_orchestrator.py` (додати тест)

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_answer_orchestrator.py` (за зразком наявних тестів файлу; search мокається як у них):

```python
async def test_answer_unknown_author_refuses_without_llm():
    llm = _llm("не має викликатись")
    qo = MagicMock()
    qo.search = AsyncMock(
        return_value=QueryResult(query="q", results=[], unknown_author="Портников")
    )
    orch = AnswerOrchestrator(llm, qo)

    result = await orch.answer("Що прогнозував Портников?")

    assert "Портников" in result.answer
    assert result.sources == []
    llm.complete.assert_not_awaited()
```

(Якщо у файлі хелпер LLM-мока називається інакше — використати наявний; `QueryResult` додати до імпортів.)

- [ ] **Step 2: Переконатися, що падає**

Run: `.venv/bin/python -m pytest tests/test_answer_orchestrator.py -q`
Expected: новий тест FAIL — у `result.answer` текст `REFUSAL_NO_DATA`, без «Портников».

- [ ] **Step 3: Реалізувати**

У `query/answer_orchestrator.py` після `REFUSAL_NO_DATA` додати:

```python
REFUSAL_UNKNOWN_AUTHOR = (
    "У базі немає прогнозів автора «{author}». "
    "Аналіз автоматизований і може містити неточності."
)
```

`answer()` — вставити гілку між search і делегуванням:

```python
    async def answer(self, question: str, limit: int = 10) -> AnswerResult:
        if self._query_orchestrator is None:
            raise RuntimeError("answer() requires a query_orchestrator (this instance is generate-only)")
        result = await self._query_orchestrator.search(question, limit=limit)
        if result.unknown_author is not None:
            logger.info("answer: unknown author, refusing")
            return AnswerResult(
                query=question,
                answer=REFUSAL_UNKNOWN_AUTHOR.format(author=result.unknown_author),
                sources=[],
            )
        return await self.answer_from_sources(question, result.results)
```

- [ ] **Step 4: Тести зелені, регресія**

Run: `.venv/bin/python -m pytest tests/test_answer_orchestrator.py tests/ -q && .venv/bin/ruff check src tests`
Expected: усе зелене (наявний refusal-тест на порожні sources — без правок).

- [ ] **Step 5: Commit**

```bash
git add src/prophet_checker/query/answer_orchestrator.py tests/test_answer_orchestrator.py
git commit -m "feat(query): явна відмова REFUSAL_UNKNOWN_AUTHOR в AnswerOrchestrator"
```

- [ ] **Step 6: Explain-diff з квізом (головна сесія)**

Викликати скіл `explain-diff-html` на коміті таска (діф: `git show HEAD`).
Дочекатися проходження квізу користувачем перед Task 8.

---

### Task 8: Wiring — `query_planner_enabled` + factory

**Скоуп:** конфіг-перемикач і збирання планера в composition root (design §5.8). Той самий Gemini Flash Lite / temp 0, що вже стоїть у `build_answer_orchestrator`. Тестів немає: `Settings`-поле — чиста декларація, factory в репо юніт-тестами не покривається (перевірка — смоук Task 9).

**Files:**
- Modify: `src/prophet_checker/config.py:19` (після `relevance_threshold`)
- Modify: `src/prophet_checker/factory.py:84-95` (`build_query_orchestrator` + імпорти)

- [ ] **Step 1: Конфіг**

У `config.py` після `relevance_threshold`:

```python
    query_planner_enabled: bool = True  # False = аварійний обхід: пошук без фільтрів (design Р4)
```

- [ ] **Step 2: Factory**

У `factory.py`: до імпортів з `storage.postgres` додати `PostgresPersonRepository`; додати `from prophet_checker.query.planner import QueryPlanner`. Замінити `build_query_orchestrator`:

```python
async def build_query_orchestrator(settings: Settings, stack: AsyncExitStack) -> QueryOrchestrator:
    engine = make_engine(settings.database_url, settings.db_ssl_mode)
    stack.push_async_callback(engine.dispose)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    prediction_repo = PostgresPredictionRepository(session_factory)
    vector_store = PostgresVectorStore(session_factory)
    embedder = EmbeddingClient(model=settings.embedding_model, api_key=settings.openai_api_key)

    planner = None
    if settings.query_planner_enabled:
        planner_llm = LLMClient(
            provider="gemini",
            model="gemini-3.1-flash-lite-preview",
            api_key=settings.gemini_api_key,
            temperature=0,
        )
        planner = QueryPlanner(planner_llm, PostgresPersonRepository(session_factory))

    return QueryOrchestrator(
        embedder,
        vector_store,
        prediction_repo,
        relevance_threshold=settings.relevance_threshold,
        planner=planner,
    )
```

- [ ] **Step 3: Регресія + лінт**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check src`
Expected: сюїта зелена.

- [ ] **Step 4: Commit**

```bash
git add src/prophet_checker/config.py src/prophet_checker/factory.py
git commit -m "feat(config): query_planner_enabled + wiring QueryPlanner у factory"
```

- [ ] **Step 5: Explain-diff з квізом (головна сесія)**

Викликати скіл `explain-diff-html` на коміті таска (діф: `git show HEAD`).
Дочекатися проходження квізу користувачем перед Task 9.

---

### Task 9: Смоук на реальній інфрі + документація

**Скоуп:** поведінкове підтвердження end-to-end на реальних Postgres+LLM (design §8) і закриття треку в доках. Потребує заповненої БД з ембедингами (`.env` з ключами; якщо корпус не backfill-нутий — спершу `scripts/` backfill, див. progress.md RAG-нотатки).

**Files:**
- Modify: `progress.md` (нотатка в Notes + рядок Активного фокусу)
- Modify: `docs/README.md` (рядок індексу)

- [ ] **Step 1: Підняти інфру**

```bash
docker compose up -d && .venv/bin/alembic upgrade head
.venv/bin/python -m prophet_checker &
curl -s localhost:8000/health
```

Expected: `{"status":"ok"}`.

- [ ] **Step 2: Чотири смоук-запити через `/answer`**

```bash
# 1. Автор + рік сказання → відповідь лише з прогнозів Арестовича 2022 року
curl -s -X POST localhost:8000/answer -H 'Content-Type: application/json' \
  -d '{"question": "Що Арестович казав про Крим у 2022 році?"}'

# 2. Target-рік → target_date-фільтр (null-inclusive: null-рядки в кандидатах)
curl -s -X POST localhost:8000/answer -H 'Content-Type: application/json' \
  -d '{"question": "Які прогнози на 2024 рік щодо завершення війни?"}'

# 3. Без фільтрів → звичайний семантичний пошук
curl -s -X POST localhost:8000/answer -H 'Content-Type: application/json' \
  -d '{"question": "Що казали про мобілізацію?"}'

# 4. Невідомий автор → явна відмова з ім'ям
curl -s -X POST localhost:8000/answer -H 'Content-Type: application/json' \
  -d '{"question": "Що прогнозував Портников про вибори?"}'
```

Expected: 1–3 — змістовні відповіді (перевірити дати джерел у `sources`); 4 — `answer` містить `У базі немає прогнозів автора «Портников»`, `sources: []`.

Додатково перевірити фільтри напряму (без генерації): ті самі питання у `POST /query` — `results[].prediction.prediction_date` в межах 2022 для запиту 1.

- [ ] **Step 3: Зафіксувати результати смоуку**

Якщо запит 1 або 2 повертає порожньо через незаповнений корпус — зазначити в progress.md чесно (фільтр працює, дані відсутні), не «зелений смоук».

- [ ] **Step 4: Оновити progress.md**

У `## Notes` додати нотатку (дата = день виконання):

```markdown
- **Hybrid retrieval Частина B v1 (2026-07-XX):** self-querying + typed фільтри готові end-to-end.
  `QueryPlanner` (Flash Lite temp 0) парсить NL-запит у `QueryPlan(semantic_query, SearchFilters)`;
  `search_similar` застосовує person/date-предикати `WHERE` на exact-скані (без ANN — overfiltering
  не застосовний); `target_date` null-inclusive; невідомий автор → `REFUSAL_UNKNOWN_AUTHOR` з ім'ям;
  збій планера → `QueryPlanningError` → 500 (fail fast, аварійний обхід `query_planner_enabled=False`).
  Смоук: [результати]. Design+plan: `docs/hybrid-retrieval/`. Park: BM25/RRF, hybrid-eval
  (чип task_a358c756), наповнення target_date.
```

Оновити рядок «Активний фокус» і таблицю snapshot відповідно.

- [ ] **Step 5: Додати трек у `docs/README.md`**

Нова секція за зразком сусідніх треків:

```markdown
## 🔎 [`hybrid-retrieval/`](hybrid-retrieval/) — hybrid structured+vector retrieval (Частина B)

| Документ | Призначення |
|----------|-------------|
| [`2026-07-11-hybrid-retrieval-design.md`](hybrid-retrieval/2026-07-11-hybrid-retrieval-design.md) | Design: self-querying LLM-планер + typed фільтри поверх exact-скану pgvector |
| [`2026-07-11-hybrid-retrieval-plan.md`](hybrid-retrieval/2026-07-11-hybrid-retrieval-plan.md) | Implementation plan (9 tasks, TDD) |
```

- [ ] **Step 6: Commit**

```bash
git add progress.md docs/README.md
git commit -m "docs: hybrid retrieval Частина B v1 — смоук і закриття треку"
```

- [ ] **Step 7: Explain-diff з квізом (головна сесія) — фінальний огляд**

Викликати скіл `explain-diff-html` на всьому діфі гілки
(`git diff main...feat/hybrid-retrieval`) — підсумковий розбір фічі цілком + квіз.
Це закриває трек.

---

## Верифікація плану проти дизайну

| Вимога дизайну | Task |
|----------------|------|
| §5.1 моделі + `QueryResult.unknown_author` | 1 |
| §5.4 Protocol + fake (null-inclusive, Р2) | 2 |
| §5.4 Postgres `WHERE` на exact-скані (Р7) | 3 |
| §5.3 промпт-контракт + парсер (Р1, Р5; §7 валідації) | 4 |
| §5.2 планер + `QueryPlanningError` (Р4, Р6) | 5 |
| §5.5 planner-гілка, short-circuit | 6 |
| §5.6 `REFUSAL_UNKNOWN_AUTHOR` (Р3) | 7 |
| §5.8 config + factory | 8 |
| §8 смоук; docs/progress | 9 |
| §7 boundary-логування | без змін — `app.py` вже ловить `Exception` → `logger.exception` → 500 |
