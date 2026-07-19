# RAG-цитати: посилання на пости у відповіді бота — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Кожне твердження у відповіді бота отримує клікабельне посилання на пост, з якого воно взяте; фіча за прапорцем і вмикається лише після citation-евалу.

**Architecture:** Модель цитує ідентифікатором прогнозу. Чиста `resolve` міняє ідентифікатори на номери й віддає посилання на прогнози; `materialize` тягне документи й групує цитати за постом. Перша живе в `answer_from_sources`, друга в `answer` — тому eval бази не торкається.

**Tech Stack:** Python 3.14, Pydantic, SQLAlchemy async, aiogram, pytest (`asyncio_mode=auto`), LiteLLM, ruff, complexipy.

**Spec:** [`2026-07-18-rag-citations-design.md`](2026-07-18-rag-citations-design.md)

---

## Файлова структура

| Файл | Відповідальність |
|------|------------------|
| `src/prophet_checker/models/domain.py` | `CitationRef`, `Citation`, `ResolvedAnswer`; нові поля `AnswerResult` |
| `src/prophet_checker/query/citations.py` | **новий** — `resolve`, `materialize`, `drop_markers` |
| `src/prophet_checker/llm/prompts.py` | правило про дужки в `RAG_SYSTEM` / `RAG_TEMPLATE` |
| `src/prophet_checker/storage/interfaces.py` | `get_documents_by_ids` у `SourceRepository` |
| `src/prophet_checker/storage/postgres.py` | реалізація |
| `src/prophet_checker/query/answer_orchestrator.py` | проводка обох стадій |
| `src/prophet_checker/config.py` | `citations_enabled` |
| `src/prophet_checker/factory.py` | передача репо й прапорця |
| `src/prophet_checker/bot/texts.py` | рендер блоку джерел, складання повідомлення |
| `src/prophet_checker/bot/handlers.py` | виклик складання |
| `scripts/generation/judge_prompts.py` | citation-суддя |
| `scripts/generation/sentences.py` | **новий** — вирізання речення за offset |
| `scripts/generation/scorers.py`, `metrics.py`, `gen_models.py` | метрики |
| `scripts/generation/generation_eval.py` | підключення scorer-ів |
| `tests/test_citations.py` | **новий** — `resolve`, `materialize`, `drop_markers` |
| `tests/fakes.py` | фейк нового репо-методу |

---

## Task 1: доменні моделі

**Скоуп:** Додати три нові Pydantic-моделі й два поля в `AnswerResult`. Юніт-тестів нема свідомо — це чисті декларації полів, а за конвенцією репо їх тестує сам Pydantic; перевіряються вони тестами консумерів у Task 2 і далі.

**Files:**
- Modify: `src/prophet_checker/models/domain.py`

- [ ] **Step 1: Додати моделі**

Після класу `RetrievedPrediction` у `models/domain.py`:

```python
class CitationRef(BaseModel):
    """Посилання з тексту відповіді на прогноз. Вихід resolve, вхід materialize."""

    marker: int
    prediction_id: str
    document_id: str
    offset: int  # позиція маркера в ResolvedAnswer.text


class ResolvedAnswer(BaseModel):
    text: str  # ідентифікатори замінено на [1], [2] — у бот і citation-судді
    text_unmarked: str  # без маркерів — faithfulness-судді
    refs: list[CitationRef] = []


class Citation(BaseModel):
    """Один пост, на який веде одне або кілька посилань."""

    markers: list[int]
    url: str
    published_at: date
    prediction_ids: list[str]
```

- [ ] **Step 2: Додати поля в `AnswerResult`**

```python
class AnswerResult(BaseModel):
    query: str
    answer: str
    sources: list[RetrievedPrediction]
    refs: list[CitationRef] = []
    citations: list[Citation] = []
```

Дефолти `[]` обовʼязкові: наявні виклики конструктора не передають цих полів.

- [ ] **Step 3: Перевірити, що нічого не зламалось**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS, та сама кількість тестів, що й до зміни.

- [ ] **Step 4: Коміт**

```bash
git add src/prophet_checker/models/domain.py
git commit -m "feat(citations): доменні моделі CitationRef/Citation/ResolvedAnswer"
```

---

## Task 2: `resolve` — маркери на номери

**Скоуп:** Чиста функція: знайти в тексті ідентифікатори відомих прогнозів, замінити на порядкові номери за першою появою, віддати версію без маркерів і список посилань. Усе, що схоже на ідентифікатор, але не резолвиться, вирізається.

**Files:**
- Create: `src/prophet_checker/query/citations.py`
- Test: `tests/test_citations.py`

- [ ] **Step 1: Написати падючі тести**

Створити `tests/test_citations.py`:

```python
from datetime import date

from prophet_checker.models.domain import Prediction, RetrievedPrediction
from prophet_checker.query.citations import resolve

ID_A = "7c9f4e21-3a8b-4d15-9e02-6b1f8a4c7d33"
ID_B = "b4e18d70-52ac-4f39-8c61-9d3e7a0f2b5e"
UNKNOWN = "00000000-0000-4000-8000-000000000000"


def _prediction(pid: str, doc: str) -> Prediction:
    return Prediction(
        id=pid,
        document_id=doc,
        person_id="p1",
        claim_text="твердження",
        prediction_date=date(2020, 8, 12),
    )


def _sources(*pairs: tuple[str, str]) -> list[RetrievedPrediction]:
    out = []
    for rank, (pid, doc) in enumerate(pairs, start=1):
        out.append(RetrievedPrediction(prediction=_prediction(pid, doc), distance=0.1, rank=rank))
    return out


def test_markers_numbered_by_first_appearance():
    sources = _sources((ID_A, "d1"), (ID_B, "d2"))
    # у тексті B згадано ПЕРШИМ, хоча його rank другий
    answer = f"друге [{ID_B}] і перше [{ID_A}]"

    result = resolve(answer, sources)

    assert result.text == "друге [1] і перше [2]"
    assert [(r.marker, r.prediction_id) for r in result.refs] == [(1, ID_B), (2, ID_A)]


def test_repeated_id_keeps_one_number_but_two_refs():
    sources = _sources((ID_A, "d1"))
    answer = f"раз [{ID_A}] і ще раз [{ID_A}]"

    result = resolve(answer, sources)

    assert result.text == "раз [1] і ще раз [1]"
    assert len(result.refs) == 2
    assert {r.marker for r in result.refs} == {1}


def test_unknown_id_is_cut_out():
    sources = _sources((ID_A, "d1"))
    answer = f"відоме [{ID_A}] і вигадане [{UNKNOWN}]"

    result = resolve(answer, sources)

    assert UNKNOWN not in result.text
    assert result.text == "відоме [1] і вигадане "
    assert len(result.refs) == 1


def test_bare_identifier_is_cut_out():
    sources = _sources((ID_A, "d1"))
    answer = f"витік {ID_A} у прозі"

    result = resolve(answer, sources)

    assert ID_A not in result.text
    assert result.refs == []


def test_text_unmarked_has_no_markers():
    sources = _sources((ID_A, "d1"))
    answer = f"твердження [{ID_A}] далі"

    result = resolve(answer, sources)

    assert result.text_unmarked == "твердження  далі"
    assert "[1]" not in result.text_unmarked


def test_offset_points_at_marker_in_text():
    sources = _sources((ID_A, "d1"))
    answer = f"твердження [{ID_A}] далі"

    result = resolve(answer, sources)

    offset = result.refs[0].offset
    assert result.text[offset : offset + 3] == "[1]"


def test_ref_carries_document_id():
    sources = _sources((ID_A, "doc-42"))
    result = resolve(f"текст [{ID_A}]", sources)

    assert result.refs[0].document_id == "doc-42"
```

- [ ] **Step 2: Запустити — має впасти**

Run: `.venv/bin/python -m pytest tests/test_citations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prophet_checker.query.citations'`

- [ ] **Step 3: Реалізувати `resolve`**

Створити `src/prophet_checker/query/citations.py`:

```python
from __future__ import annotations

import logging
import re

from prophet_checker.models.domain import CitationRef, ResolvedAnswer, RetrievedPrediction

logger = logging.getLogger(__name__)

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# Один прохід ловить і маркер у дужках, і голий ідентифікатор у прозі: інакше
# другий прохід зіпсував би вже підставлені номери.
_TOKEN_RE = re.compile(rf"\[\s*(?P<bracketed>{_UUID})\s*\]|(?P<bare>{_UUID})")


def resolve(answer: str, sources: list[RetrievedPrediction]) -> ResolvedAnswer:
    """Замінити ідентифікатори прогнозів на порядкові номери за першою появою.

    Усе, що схоже на ідентифікатор і не належить поданим джерелам, вирізається.
    """
    by_id = {s.prediction.id: s.prediction for s in sources}
    marked: list[str] = []
    plain: list[str] = []
    refs: list[CitationRef] = []
    numbers: dict[str, int] = {}
    length = 0
    cursor = 0
    dropped = 0

    for match in _TOKEN_RE.finditer(answer):
        chunk = answer[cursor : match.start()]
        marked.append(chunk)
        plain.append(chunk)
        length += len(chunk)
        cursor = match.end()

        uid = match.group("bracketed")
        if uid is None or uid not in by_id:
            dropped += 1
            continue

        if uid not in numbers:
            numbers[uid] = len(numbers) + 1
        token = f"[{numbers[uid]}]"
        refs.append(
            CitationRef(
                marker=numbers[uid],
                prediction_id=uid,
                document_id=by_id[uid].document_id,
                offset=length,
            )
        )
        marked.append(token)
        length += len(token)

    tail = answer[cursor:]
    marked.append(tail)
    plain.append(tail)

    if dropped:
        logger.warning("citations: вирізано %d маркер(ів), що не резолвляться", dropped)

    return ResolvedAnswer(text="".join(marked), text_unmarked="".join(plain), refs=refs)
```

- [ ] **Step 4: Запустити — має пройти**

Run: `.venv/bin/python -m pytest tests/test_citations.py -v`
Expected: PASS, 7 тестів.

- [ ] **Step 5: Перевірити складність і лінт**

Run: `.venv/bin/complexipy src/prophet_checker/query/citations.py && .venv/bin/ruff check src/prophet_checker/query/citations.py`
Expected: `resolve` ≤ 12, ruff чистий.

- [ ] **Step 6: Коміт**

```bash
git add src/prophet_checker/query/citations.py tests/test_citations.py
git commit -m "feat(citations): resolve — ідентифікатори на номери за появою"
```

---

## Task 3: `get_documents_by_ids` у сховищі

**Скоуп:** Дістати документи пачкою по id-ах — операції нема, а `materialize` без неї не збудуєш. Дзеркало наявного `PredictionRepository.get_by_ids`.

**Files:**
- Modify: `src/prophet_checker/storage/interfaces.py`
- Modify: `src/prophet_checker/storage/postgres.py`
- Modify: `tests/fakes.py`
- Test: `tests/test_citations.py`

- [ ] **Step 1: Написати падючий тест на фейк**

Додати в `tests/test_citations.py`:

```python
from prophet_checker.models.domain import RawDocument
from tests.fakes import FakeSourceRepo


async def test_fake_repo_returns_documents_by_ids():
    doc = RawDocument(
        id="d1",
        person_id="p1",
        source_type=SourceType.TELEGRAM,
        url="https://t.me/@ch/1",
        published_at=datetime(2020, 8, 12, tzinfo=UTC),
        raw_text="текст",
    )
    repo = FakeSourceRepo(documents=[doc])

    found = await repo.get_documents_by_ids(["d1", "missing"])

    assert [d.id for d in found] == ["d1"]
```

Додати імпорти на початок файлу:

```python
from datetime import UTC, datetime

from prophet_checker.models.domain import RawDocument, SourceType
from tests.fakes import FakeSourceRepo
```

- [ ] **Step 2: Запустити — має впасти**

Run: `.venv/bin/python -m pytest tests/test_citations.py::test_fake_repo_returns_documents_by_ids -v`
Expected: FAIL — `AttributeError: 'FakeSourceRepo' object has no attribute 'get_documents_by_ids'`

- [ ] **Step 3: Додати метод у Protocol**

У `src/prophet_checker/storage/interfaces.py`, у клас `SourceRepository`, поруч із `get_document_by_url`:

```python
    async def get_documents_by_ids(self, ids: list[str]) -> list[RawDocument]: ...
```

- [ ] **Step 4: Реалізувати у фейку**

У `tests/fakes.py`, у `FakeSourceRepo`:

```python
    async def get_documents_by_ids(self, ids: list[str]) -> list[RawDocument]:
        wanted = set(ids)
        found = []
        for doc in self._documents:
            if doc.id in wanted:
                found.append(doc)
        return found
```

`FakeSourceRepo.__init__` наразі не приймає аргументів — замінити його на:

```python
    def __init__(self, documents: list[RawDocument] | None = None):
        self._sources: list[PersonSource] = []
        self._documents: list[RawDocument] = documents or []
```

Дефолт `None` обовʼязковий: наявні тести конструюють `FakeSourceRepo()` без аргументів.

- [ ] **Step 5: Реалізувати в Postgres**

У `src/prophet_checker/storage/postgres.py`, у клас, що реалізує `SourceRepository`:

```python
    async def get_documents_by_ids(self, ids: list[str]) -> list[RawDocument]:
        if not ids:
            return []
        async with self._session_factory() as session:
            stmt = select(RawDocumentDB).where(RawDocumentDB.id.in_(ids))
            rows = (await session.execute(stmt)).scalars().all()
        documents = []
        for row in rows:
            documents.append(raw_document_db_to_domain(row))
        return documents
```

`RawDocumentDB` і `raw_document_db_to_domain` уже імпортовані/визначені в цьому файлі
(`postgres.py:15` і `postgres.py:83`) — використати їх, нових не заводити. Патерн `async with
self._session_factory()` узятий із сусіднього `get_document_by_url` (`postgres.py:214`).

- [ ] **Step 6: Запустити**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Коміт**

```bash
git add src/prophet_checker/storage/interfaces.py src/prophet_checker/storage/postgres.py tests/fakes.py tests/test_citations.py
git commit -m "feat(storage): get_documents_by_ids — документи пачкою по id"
```

---

## Task 4: `materialize` і `drop_markers`

**Скоуп:** Перетворити посилання на прогнози в цитати, згруповані за постом, і прибрати з тексту маркери, для яких документа не знайшлося. Друге потрібне, бо правило виживання маркера діє й на цій стадії: без нього в тексті лишився б `[2]` без рядка в блоці джерел.

**Files:**
- Modify: `src/prophet_checker/query/citations.py`
- Test: `tests/test_citations.py`

- [ ] **Step 1: Написати падючі тести**

```python
async def test_two_predictions_from_one_post_share_one_citation():
    doc = RawDocument(
        id="d1", person_id="p1", source_type=SourceType.TELEGRAM,
        url="https://t.me/@ch/1", published_at=datetime(2020, 8, 12, tzinfo=UTC),
        raw_text="текст",
    )
    repo = FakeSourceRepo(documents=[doc])
    refs = [
        CitationRef(marker=1, prediction_id=ID_A, document_id="d1", offset=0),
        CitationRef(marker=3, prediction_id=ID_B, document_id="d1", offset=10),
    ]

    citations = await materialize(refs, repo)

    assert len(citations) == 1
    assert citations[0].markers == [1, 3]
    assert citations[0].prediction_ids == [ID_A, ID_B]
    assert citations[0].published_at == date(2020, 8, 12)


async def test_missing_document_yields_no_citation():
    repo = FakeSourceRepo(documents=[])
    refs = [CitationRef(marker=1, prediction_id=ID_A, document_id="gone", offset=0)]

    assert await materialize(refs, repo) == []


async def test_empty_url_yields_no_citation():
    doc = RawDocument(
        id="d1", person_id="p1", source_type=SourceType.TELEGRAM, url="",
        published_at=datetime(2020, 8, 12, tzinfo=UTC), raw_text="текст",
    )
    repo = FakeSourceRepo(documents=[doc])
    refs = [CitationRef(marker=1, prediction_id=ID_A, document_id="d1", offset=0)]

    assert await materialize(refs, repo) == []


def test_drop_markers_removes_only_unlisted():
    assert drop_markers("а [1] б [2] в", keep={1}) == "а [1] б  в"
```

Розширити імпорт: `from prophet_checker.query.citations import drop_markers, materialize, resolve`
і `from prophet_checker.models.domain import CitationRef`.

- [ ] **Step 2: Запустити — має впасти**

Run: `.venv/bin/python -m pytest tests/test_citations.py -k "materialize or drop_markers" -v`
Expected: FAIL — `ImportError: cannot import name 'materialize'`

- [ ] **Step 3: Реалізувати**

Додати в `src/prophet_checker/query/citations.py`:

```python
from prophet_checker.models.domain import Citation
from prophet_checker.storage.interfaces import SourceRepository

_MARKER_RE = re.compile(r"\[(\d+)\]")


def _unique_document_ids(refs: list[CitationRef]) -> list[str]:
    seen: list[str] = []
    for ref in refs:
        if ref.document_id not in seen:
            seen.append(ref.document_id)
    return seen


def _append_ref(citation: Citation, ref: CitationRef) -> None:
    if ref.marker not in citation.markers:
        citation.markers.append(ref.marker)
    if ref.prediction_id not in citation.prediction_ids:
        citation.prediction_ids.append(ref.prediction_id)


async def materialize(refs: list[CitationRef], source_repo: SourceRepository) -> list[Citation]:
    """Зібрати цитати: один пост — одна цитата, скільки б прогнозів з нього не було."""
    if not refs:
        return []

    documents = await source_repo.get_documents_by_ids(_unique_document_ids(refs))
    by_doc = {doc.id: doc for doc in documents}

    grouped: dict[str, Citation] = {}
    order: list[str] = []
    missing = 0

    for ref in refs:
        doc = by_doc.get(ref.document_id)
        if doc is None or not doc.url:
            missing += 1
            continue
        citation = grouped.get(ref.document_id)
        if citation is None:
            citation = Citation(
                markers=[], url=doc.url, published_at=doc.published_at.date(), prediction_ids=[]
            )
            grouped[ref.document_id] = citation
            order.append(ref.document_id)
        _append_ref(citation, ref)

    if missing:
        logger.warning("citations: %d посилан(ня) без придатного URL документа", missing)

    citations = []
    for doc_id in order:
        citations.append(grouped[doc_id])
    return citations


def drop_markers(text: str, keep: set[int]) -> str:
    """Прибрати з тексту маркери, яких нема серед keep."""

    def replace(match: re.Match[str]) -> str:
        return match.group(0) if int(match.group(1)) in keep else ""

    return _MARKER_RE.sub(replace, text)
```

- [ ] **Step 4: Запустити**

Run: `.venv/bin/python -m pytest tests/test_citations.py -v`
Expected: PASS, 11 тестів.

- [ ] **Step 5: Перевірити складність**

Run: `.venv/bin/complexipy src/prophet_checker/query/citations.py`
Expected: кожна функція ≤ 12.

- [ ] **Step 6: Коміт**

```bash
git add src/prophet_checker/query/citations.py tests/test_citations.py
git commit -m "feat(citations): materialize — цитата на пост, dedup і drop_markers"
```

---

## Task 5: контракт промпту

**Скоуп:** Дозволити ідентифікатор виключно у квадратних дужках після твердження, лишивши решту заборон. Розширити guard-тест, бо чинний про ідентифікатори мовчить.

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py`
- Test: `tests/test_llm_prompts.py:100-113`

- [ ] **Step 1: Розширити guard-тест**

Замінити тіло `test_rag_prompt_contract_drops_leak_directives` у `tests/test_llm_prompts.py`:

```python
def test_rag_prompt_contract_drops_leak_directives():
    from prophet_checker.llm.prompts import RAG_SYSTEM, RAG_TEMPLATE

    combined = RAG_SYSTEM + RAG_TEMPLATE
    # старі директиви, що провокували лік службових полів, прибрані
    assert "confidence scores" not in combined
    assert "accuracy statistics" not in combined
    # контракт присутній: переклад 4 статусів у людський вердикт
    assert "прогноз справдився" in RAG_SYSTEM
    assert "прогноз не справдився" in RAG_SYSTEM
    assert "оцінити не вдалося" in RAG_SYSTEM
    assert "ще зарано" in RAG_SYSTEM


def test_rag_prompt_allows_identifier_only_inside_brackets():
    from prophet_checker.llm.prompts import RAG_SYSTEM, RAG_TEMPLATE

    for text in (RAG_SYSTEM, RAG_TEMPLATE):
        # цитування дозволене
        assert "square brackets" in text
        # але лише в дужках — заборона на голий ідентифікатор у прозі лишається
        assert "never in running prose" in text
```

- [ ] **Step 2: Запустити — має впасти**

Run: `.venv/bin/python -m pytest tests/test_llm_prompts.py -k identifier -v`
Expected: FAIL — `assert "square brackets" in text`

- [ ] **Step 3: Правити `RAG_SYSTEM`**

У `src/prophet_checker/llm/prompts.py` замінити абзац заборон (рядки ~215-218):

```python
Cite your sources: put the prediction's identifier in square brackets immediately after the
statement it supports, e.g. "…розпадеться до 2024 року [7c9f4e21-3a8b-4d15-9e02-6b1f8a4c7d33]".
Use ONLY identifiers that appear in the source block. Every prediction you discuss gets its
identifier. An identifier may appear ONLY inside square brackets, never in running prose.

Do NOT put in the answer: the confidence number, the raw English status label
(confirmed/refuted/unresolved/premature), invented statistics (e.g. "0% успішності"), or
meta-statements about the database. Use the provided dates and status only to inform the wording —
never recite them as labelled fields.
```

- [ ] **Step 4: Правити `RAG_TEMPLATE`**

Замінити останнє речення шаблону:

```python
predictions into one coherent answer. Keep it short. Cite each prediction you discuss by putting
its identifier in square brackets right after the statement — inside brackets only, never in
running prose. No confidence numbers, no raw status labels, no invented statistics. End with the
single disclaimer line."""
```

- [ ] **Step 5: Запустити**

Run: `.venv/bin/python -m pytest tests/test_llm_prompts.py -v`
Expected: PASS.

- [ ] **Step 6: Коміт**

```bash
git add src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py
git commit -m "feat(prompts): цитування ідентифікатором у дужках, заборона в прозі лишається"
```

---

## Task 6: прапорець `citations_enabled`

**Скоуп:** Один прапорець конфігу, дефолт `False` — фіча не вмикається користувачам, поки eval не дасть числа.

**Files:**
- Modify: `src/prophet_checker/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Додати поле**

У `src/prophet_checker/config.py`, поруч із `query_planner_enabled`:

```python
    citations_enabled: bool = False  # вмикає посилання на пости у відповіді (design 2026-07-18)
```

- [ ] **Step 2: Задокументувати в `.env.example`**

```
# Посилання на пости у відповіді бота. Вмикати лише після citation-евалу
# (precision >= 0.95, coverage >= 0.90) — див. docs/citations/.
CITATIONS_ENABLED=false
```

- [ ] **Step 3: Перевірити**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 4: Коміт**

```bash
git add src/prophet_checker/config.py .env.example
git commit -m "feat(config): прапорець citations_enabled, дефолт вимкнено"
```

---

## Task 7: проводка в `AnswerOrchestrator`

**Скоуп:** Підключити `resolve` до generate-only гілки, а `materialize` — до повної. Саме це розміщення й тримає eval без бази.

**Files:**
- Modify: `src/prophet_checker/query/answer_orchestrator.py`
- Modify: `src/prophet_checker/factory.py`
- Test: `tests/test_answer_orchestrator.py`

- [ ] **Step 1: Написати падючі тести**

Додати в `tests/test_answer_orchestrator.py`:

```python
async def test_answer_from_sources_resolves_markers_when_enabled():
    llm = FakeLLM(response=f"твердження [{ID_A}]")
    orchestrator = AnswerOrchestrator(llm, citations_enabled=True)

    result = await orchestrator.answer_from_sources("питання", _sources((ID_A, "d1")))

    assert result.answer == "твердження [1]"
    assert [r.marker for r in result.refs] == [1]
    assert result.citations == []  # generate-only гілка в базу не ходить


async def test_answer_from_sources_leaves_text_alone_when_disabled():
    llm = FakeLLM(response="твердження без маркерів")
    orchestrator = AnswerOrchestrator(llm, citations_enabled=False)

    result = await orchestrator.answer_from_sources("питання", _sources((ID_A, "d1")))

    assert result.answer == "твердження без маркерів"
    assert result.refs == []
```

`ID_A` і `_sources` продублювати з `tests/test_citations.py` або винести у `tests/fakes.py`, якщо вони там доречні; не імпортувати тест із тесту.

- [ ] **Step 2: Запустити — має впасти**

Run: `.venv/bin/python -m pytest tests/test_answer_orchestrator.py -k citations -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'citations_enabled'`

- [ ] **Step 3: Розширити конструктор і обидві гілки**

У `src/prophet_checker/query/answer_orchestrator.py`:

```python
from prophet_checker.models.domain import AnswerResult, RetrievedPrediction
from prophet_checker.query.citations import drop_markers, materialize, resolve
from prophet_checker.storage.interfaces import SourceRepository


class AnswerOrchestrator:
    def __init__(
        self,
        llm: LLMClient,
        query_orchestrator: QueryOrchestrator | None = None,
        source_repo: SourceRepository | None = None,
        citations_enabled: bool = False,
    ) -> None:
        self._llm = llm
        self._query_orchestrator = query_orchestrator
        self._source_repo = source_repo
        self._citations_enabled = citations_enabled
```

`answer_from_sources` — після виклику LLM:

```python
        text = await self._llm.complete(prompt, system=RAG_SYSTEM)
        logger.info("answer_from_sources: generated from %d sources", len(sources))
        if not self._citations_enabled:
            return AnswerResult(query=question, answer=text.strip(), sources=sources)
        resolved = resolve(text.strip(), sources)
        return AnswerResult(
            query=question, answer=resolved.text, sources=sources, refs=resolved.refs
        )
```

`answer` — замінити останній рядок:

```python
        result = await self.answer_from_sources(question, result.results)
        return await self._attach_citations(result)

    async def _attach_citations(self, result: AnswerResult) -> AnswerResult:
        if not result.refs or self._source_repo is None:
            return result
        citations = await materialize(result.refs, self._source_repo)
        kept: set[int] = set()
        for citation in citations:
            kept.update(citation.markers)
        text = result.answer if len(kept) == len({r.marker for r in result.refs}) else drop_markers(
            result.answer, kept
        )
        return result.model_copy(update={"answer": text, "citations": citations})
```

- [ ] **Step 4: Запустити**

Run: `.venv/bin/python -m pytest tests/test_answer_orchestrator.py -v`
Expected: PASS.

- [ ] **Step 5: Проводка у factory**

У `src/prophet_checker/factory.py`, там, де будується `AnswerOrchestrator`, передати репо джерел і прапорець:

```python
    answer_orchestrator = AnswerOrchestrator(
        llm,
        query_orchestrator=query_orchestrator,
        source_repo=source_repo,
        citations_enabled=settings.citations_enabled,
    )
```

Змінну `source_repo` взяти ту, що вже створена в цій функції для інжесту; нової не заводити.

- [ ] **Step 6: Запустити всю сюїту**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/complexipy --diff HEAD --ratchet src`
Expected: PASS, ratchet зелений.

- [ ] **Step 7: Коміт**

```bash
git add src/prophet_checker/query/answer_orchestrator.py src/prophet_checker/factory.py tests/test_answer_orchestrator.py
git commit -m "feat(citations): проводка resolve у generate-гілку, materialize у повну"
```

---

## Task 8: рендер у боті

**Скоуп:** Скласти повідомлення з тексту й блоку джерел так, щоб воно влізло в ліміт Telegram і не лишило рядка джерела без маркера в тексті.

**Files:**
- Modify: `src/prophet_checker/bot/texts.py`
- Modify: `src/prophet_checker/bot/handlers.py`
- Test: `tests/test_bot_texts.py`

- [ ] **Step 1: Написати падючі тести**

Створити або доповнити `tests/test_bot_texts.py`:

```python
from datetime import date

from prophet_checker.bot.texts import compose_answer_message
from prophet_checker.models.domain import Citation


def _citation(markers: list[int], url: str, day: int) -> Citation:
    return Citation(
        markers=markers, url=url, published_at=date(2020, 8, day), prediction_ids=["x"]
    )


def test_sources_block_groups_markers_of_one_post():
    citations = [_citation([1, 3], "https://t.me/@ch/1", 12)]

    message = compose_answer_message("текст [1] і [3]", citations)

    assert "Джерела:" in message
    assert '<a href="https://t.me/@ch/1">[1][3] Пост від 12.08.2020</a>' in message


def test_no_citations_means_no_block():
    assert compose_answer_message("просто текст", []) == "просто текст"


def test_citation_dropped_when_its_marker_does_not_survive_truncation():
    long_text = "а" * 4000 + " [2]"
    citations = [_citation([2], "https://t.me/@ch/2", 13)]

    message = compose_answer_message(long_text, citations)

    assert "[2] Пост від" not in message
    assert len(message) <= 4096
```

- [ ] **Step 2: Запустити — має впасти**

Run: `.venv/bin/python -m pytest tests/test_bot_texts.py -v`
Expected: FAIL — `ImportError: cannot import name 'compose_answer_message'`

- [ ] **Step 3: Реалізувати**

У `src/prophet_checker/bot/texts.py`:

```python
import re

from prophet_checker.models.domain import Citation

SOURCES_HEADER = "Джерела:"
_MARKER_RE = re.compile(r"\[(\d+)\]")


def _citation_line(citation: Citation) -> str:
    markers = ""
    for marker in citation.markers:
        markers += f"[{marker}]"
    day = citation.published_at.strftime("%d.%m.%Y")
    return f'<a href="{citation.url}">{markers} Пост від {day}</a>'


def _render_block(citations: list[Citation]) -> str:
    lines = [SOURCES_HEADER]
    for citation in citations:
        lines.append(_citation_line(citation))
    return "\n".join(lines)


def compose_answer_message(text: str, citations: list[Citation]) -> str:
    """Скласти повідомлення: спершу обрізати тіло, потім лишити тільки ті цитати,
    чиї маркери пережили обрізання. Інакше в блоці лишиться рядок без посилання в тексті."""
    if not citations:
        return truncate_for_telegram(text)

    budget = TELEGRAM_MESSAGE_LIMIT - len(_render_block(citations)) - 2
    body = truncate_for_telegram(text, limit=max(budget, 1))

    survived = set()
    for match in _MARKER_RE.finditer(body):
        survived.add(int(match.group(1)))

    kept = []
    for citation in citations:
        if survived.intersection(citation.markers):
            kept.append(citation)
    if not kept:
        return body
    return f"{body}\n\n{_render_block(kept)}"
```

- [ ] **Step 4: Запустити**

Run: `.venv/bin/python -m pytest tests/test_bot_texts.py -v`
Expected: PASS.

- [ ] **Step 5: Підключити в handler**

У `src/prophet_checker/bot/handlers.py` замінити рядок 50:

```python
    await message.answer(
        compose_answer_message(result.answer, result.citations), parse_mode="HTML"
    )
```

і додати імпорт `compose_answer_message` поруч із `truncate_for_telegram`.

- [ ] **Step 6: Запустити всю сюїту**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Коміт**

```bash
git add src/prophet_checker/bot/texts.py src/prophet_checker/bot/handlers.py tests/test_bot_texts.py
git commit -m "feat(bot): блок джерел з посиланнями, обрізання перед складанням"
```

---

## Task 9: вирізання речення за offset

**Скоуп:** Дати судді речення, у якому стоїть маркер. Живе в `scripts/`, не в `src/`: продакшн речень не потребує, а український поділ крихкий через дати.

**Files:**
- Create: `scripts/generation/sentences.py`
- Test: `tests/test_generation_sentences.py`

- [ ] **Step 1: Написати падючі тести**

```python
from generation.sentences import sentence_at


def test_returns_sentence_containing_offset():
    text = "Перше речення. Друге речення [1] тут. Третє."
    offset = text.index("[1]")

    assert sentence_at(text, offset) == "Друге речення [1] тут."


def test_date_does_not_split_sentence():
    text = "Прогноз від 12.08.2020 справдився [1]."
    offset = text.index("[1]")

    assert sentence_at(text, offset) == text


def test_abbreviation_does_not_split_sentence():
    text = "У 2020 р. він казав про це [1]."
    offset = text.index("[1]")

    assert sentence_at(text, offset) == text
```

- [ ] **Step 2: Запустити — має впасти**

Run: `.venv/bin/python -m pytest tests/test_generation_sentences.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'generation.sentences'`

- [ ] **Step 3: Реалізувати**

`scripts/generation/sentences.py`:

```python
from __future__ import annotations

import re

# Кінець речення — крапка/!/? + пробіл + велика літера. Дата (12.08.2020) і скорочення
# ("2020 р. він") цьому не відповідають: після крапки в даті йде цифра, а після "р." —
# мала літера. Цього досить для наших відповідей і не тягне NLP-залежність.
_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЇІЄҐA-Z])")


def sentence_at(text: str, offset: int) -> str:
    """Речення, всередині якого стоїть символ на позиції offset."""
    start = 0
    for match in _BOUNDARY_RE.finditer(text):
        if match.end() > offset:
            break
        start = match.end()
    end = len(text)
    for match in _BOUNDARY_RE.finditer(text):
        if match.start() > offset:
            end = match.start()
            break
    return text[start:end].strip()
```

- [ ] **Step 4: Запустити**

Run: `.venv/bin/python -m pytest tests/test_generation_sentences.py -v`
Expected: PASS, 3 тести.

- [ ] **Step 5: Коміт**

```bash
git add scripts/generation/sentences.py tests/test_generation_sentences.py
git commit -m "feat(eval): вирізання речення за offset, стійке до дат і скорочень"
```

---

## Task 10: citation-суддя

**Скоуп:** Промпт і парсер для питання «чи підтверджує це джерело це речення». Окремий від faithfulness, щоб той лишався незмінним.

**Files:**
- Modify: `scripts/generation/judge_prompts.py`
- Modify: `scripts/generation/gen_models.py`
- Test: `tests/test_generation_judge_prompts.py`

- [ ] **Step 1: Написати падючий тест**

```python
from datetime import date

from generation.judge_prompts import build_citation_prompt, parse_citation_response
from prophet_checker.models.domain import Prediction, RetrievedPrediction


def _source() -> RetrievedPrediction:
    prediction = Prediction(
        id="7c9f4e21-3a8b-4d15-9e02-6b1f8a4c7d33",
        document_id="d1",
        person_id="p1",
        claim_text="Росія розпадеться до 2024 року",
        prediction_date=date(2020, 8, 12),
    )
    return RetrievedPrediction(prediction=prediction, distance=0.1, rank=1)


def test_parse_citation_response_reads_verdict():
    supported, reason = parse_citation_response('{"supported": false, "reason": "інша тема"}')

    assert supported is False
    assert reason == "інша тема"


def test_citation_prompt_contains_sentence_and_source():
    prompt = build_citation_prompt("Речення [1].", _source())

    assert "Речення [1]." in prompt
    assert "Росія розпадеться до 2024 року" in prompt
```

- [ ] **Step 2: Запустити — має впасти**

Run: `.venv/bin/python -m pytest tests/test_generation_judge_prompts.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_citation_prompt'`

- [ ] **Step 3: Реалізувати**

У `scripts/generation/judge_prompts.py`:

```python
from prophet_checker.llm.prompts import render_predictions

CITATION_SYSTEM = (
    "Ти — прискіпливий рецензент. Тобі дають одне речення з відповіді та ОДНЕ джерело, "
    "на яке це речення посилається. Скажи, чи підтверджує саме це джерело саме це речення. "
    "Статус прогнозу в джерелі є авторитетним щодо того, справдився він чи ні. "
    "Відповідай лише JSON."
)


def build_citation_prompt(sentence: str, source: RetrievedPrediction) -> str:
    return (
        "Чи підтверджує подане джерело твердження в реченні? "
        'Формат: {"supported": true/false, "reason": "коротко"}\n\n'
        f"РЕЧЕННЯ:\n{sentence}\n\nДЖЕРЕЛО:\n{render_predictions([source])}"
    )


def parse_citation_response(text: str) -> tuple[bool, str]:
    data = _extract_json(text)
    return bool(data.get("supported", False)), data.get("reason", "")
```

- [ ] **Step 4: Запустити**

Run: `.venv/bin/python -m pytest tests/test_generation_judge_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Коміт**

```bash
git add scripts/generation/judge_prompts.py tests/test_generation_judge_prompts.py
git commit -m "feat(eval): citation-суддя — промпт і парсер"
```

---

## Task 11: метрики precision і coverage

**Скоуп:** Порахувати дві метрики й додати їх у наявний `GenerationMetrics`. Coverage детермінований, precision через суддю.

**Files:**
- Modify: `scripts/generation/scorers.py`
- Modify: `scripts/generation/metrics.py`
- Modify: `scripts/generation/gen_models.py`
- Test: `tests/test_generation_metrics.py`

- [ ] **Step 1: Написати падючий тест на coverage**

```python
from generation.scorers import citation_coverage


def test_coverage_is_cited_over_expected():
    refs = [CitationRef(marker=1, prediction_id=ID_A, document_id="d1", offset=0)]

    assert citation_coverage(refs, expected_ids=[ID_A, ID_B]) == 0.5


def test_coverage_counts_distinct_predictions_only():
    refs = [
        CitationRef(marker=1, prediction_id=ID_A, document_id="d1", offset=0),
        CitationRef(marker=1, prediction_id=ID_A, document_id="d1", offset=9),
    ]

    assert citation_coverage(refs, expected_ids=[ID_A, ID_B]) == 0.5
```

- [ ] **Step 2: Запустити — має впасти**

Run: `.venv/bin/python -m pytest tests/test_generation_metrics.py -k coverage -v`
Expected: FAIL — `ImportError: cannot import name 'citation_coverage'`

- [ ] **Step 3: Реалізувати coverage і додати поля метрик**

У `scripts/generation/scorers.py`:

```python
def citation_coverage(refs: list[CitationRef], expected_ids: list[str]) -> float | None:
    if not expected_ids:
        return None
    cited = set()
    for ref in refs:
        cited.add(ref.prediction_id)
    hit = 0
    for pid in set(expected_ids):
        if pid in cited:
            hit += 1
    return hit / len(set(expected_ids))
```

У `scripts/generation/gen_models.py` додати в `GenerationMetrics`:

```python
    citation_precision_mean: float | None = None
    citation_coverage_mean: float | None = None
```

У `scripts/generation/metrics.py::aggregate` — поруч зі збором `faith` і `recall`:

```python
    precision: list[float] = []
    coverage: list[float] = []
```

усередині циклу `for s in scored:`, після наявних двох блоків:

```python
        p = cards.get("citation_precision")
        if p is not None and p.score is not None:
            precision.append(p.score)

        cov = cards.get("citation_coverage")
        if cov is not None and cov.score is not None:
            coverage.append(cov.score)
```

і в конструкторі `GenerationMetrics(...)`:

```python
        citation_precision_mean=_mean(precision),
        citation_coverage_mean=_mean(coverage),
```

`_mean` уже є у файлі й віддає `None` на порожньому списку — окремої обробки не треба.

- [ ] **Step 4: Додати два scorer-и**

Обидва реалізують `Scorer` Protocol із `eval_common/protocols.py`: атрибут `name`, метод
`async def score(run: EvalRun) -> ScoreCard`, і гард на `run.result is None` — контракт вимагає
не чіпати `run.result` без перевірки.

У `scripts/generation/scorers.py`:

```python
from generation.judge_prompts import CITATION_SYSTEM, build_citation_prompt, parse_citation_response
from generation.sentences import sentence_at
from prophet_checker.models.domain import CitationRef, RetrievedPrediction


def _source_by_id(sources: list[RetrievedPrediction], prediction_id: str) -> RetrievedPrediction:
    for source in sources:
        if source.prediction.id == prediction_id:
            return source
    raise KeyError(f"джерело {prediction_id} відсутнє серед поданих")


def citation_coverage(refs: list[CitationRef], expected_ids: list[str]) -> float | None:
    if not expected_ids:
        return None
    cited = set()
    for ref in refs:
        cited.add(ref.prediction_id)
    expected = set(expected_ids)
    hit = 0
    for pid in expected:
        if pid in cited:
            hit += 1
    return hit / len(expected)


class CitationPrecisionScorer:
    name = "citation_precision"

    def __init__(self, judge: Judge) -> None:
        self._judge = judge

    async def score(self, run: EvalRun) -> ScoreCard:
        if run.result is None or not run.result.refs:
            return ScoreCard(scorer=self.name, score=None)
        supported = 0
        for ref in run.result.refs:
            sentence = sentence_at(run.result.answer, ref.offset)
            source = _source_by_id(run.result.sources, ref.prediction_id)
            raw = await self._judge.assess(
                build_citation_prompt(sentence, source), system=CITATION_SYSTEM
            )
            verdict, _ = parse_citation_response(raw)
            if verdict:
                supported += 1
        return ScoreCard(scorer=self.name, score=supported / len(run.result.refs))


class CitationCoverageScorer:
    name = "citation_coverage"

    async def score(self, run: EvalRun) -> ScoreCard:
        labels = run.case.labels
        if run.result is None or not labels.answerable:
            return ScoreCard(scorer=self.name, score=None)
        expected_ids = []
        for es in labels.expected_sources:
            expected_ids.append(es.prediction.id)
        return ScoreCard(
            scorer=self.name, score=citation_coverage(run.result.refs, expected_ids)
        )
```

`CitationCoverageScorer` не приймає суддю навмисно: метрика детермінована, і зайва залежність
лише приховала б це.

`KeyError` у `_source_by_id` доречний: `refs` будує `resolve` рівно з тих джерел, що йдуть далі,
тож розбіжність означала б баг у коді, а не поганий вхід — падати треба голосно.

- [ ] **Step 5: Запустити**

Run: `.venv/bin/python -m pytest tests/test_generation_metrics.py -v`
Expected: PASS.

- [ ] **Step 6: Коміт**

```bash
git add scripts/generation/scorers.py scripts/generation/metrics.py scripts/generation/gen_models.py tests/test_generation_metrics.py
git commit -m "feat(eval): метрики citation precision і coverage"
```

---

## Task 12: підключити scorer-и до прогону

**Скоуп:** Додати два scorer-и до наявного списку й увімкнути прапорець усередині скрипта — інакше eval міряв би відповіді без цитат.

**Files:**
- Modify: `scripts/generation/generation_eval.py`

- [ ] **Step 1: Увімкнути прапорець і передати джерела faithfulness-судді**

У `scripts/generation/generation_eval.py`:

```python
    orchestrator = AnswerOrchestrator(llm, citations_enabled=True)
```

У faithfulness-scorer-і (`scripts/generation/scorers.py`) замінити вхід судді на текст без маркерів:

```python
    prompt = build_faithfulness_prompt(drop_markers(run.result.answer, keep=set()), sources)
```

`drop_markers` з `keep=set()` прибирає **всі** `[n]`. Це і є `text_unmarked` з дизайну: суддя
бачить рівно те, що бачив у червні, і базлайн 0.947 лишається порівнюваним.

- [ ] **Step 2: Додати scorer-и в список**

У `generation_eval.py`, там, де вже збирається список scorer-ів, додати два нові. Суддя той самий
екземпляр, що вже створений для faithfulness — крос-родинний Claude, окремого не заводити:

```python
    scorers = [
        FaithfulnessScorer(judge),
        CompletenessScorer(judge),
        CitationPrecisionScorer(judge),
        CitationCoverageScorer(),
    ]
```

і розширити імпорт з `generation.scorers`:

```python
from generation.scorers import (
    CitationCoverageScorer,
    CitationPrecisionScorer,
    CompletenessScorer,
    FaithfulnessScorer,
)
```

- [ ] **Step 3: Прогнати на 5 кейсах**

Run: `.venv/bin/python scripts/generation/generation_eval.py --limit 5`
Expected: звіт містить `citation_precision` і `citation_coverage`, обидва не `None`; `faithfulness` у тому ж діапазоні, що й раніше.

- [ ] **Step 4: Коміт**

```bash
git add scripts/generation/generation_eval.py
git commit -m "feat(eval): підключити citation-scorer-и до прогону generation-eval"
```

---

## Task 13: повний прогін і рішення про прапорець

**Скоуп:** Зміряти на всьому gold і вирішити, чи вмикати фічу. Це гейт із дизайну, а не формальність.

**Files:** —

- [ ] **Step 1: Повний прогін**

Run: `.venv/bin/python scripts/generation/generation_eval.py`
Expected: 92 answerable кейси, звіт у `scripts/outputs/`.

- [ ] **Step 2: Звірити з порогами**

- `citation_precision_mean` ≥ 0.95
- `citation_coverage_mean` ≥ 0.90
- `faithfulness_mean` у районі 0.947 — якщо просіла, спершу зрозуміти чому, і лише потім рухатись далі

- [ ] **Step 3: Зафіксувати результат**

Дописати в `progress.md` запис із числами й датою, за конвенцією Notes.

- [ ] **Step 4: Рішення**

Пороги взяті → `CITATIONS_ENABLED=true` у прод-`.env` через `deploy/secrets.sh set`.
Не взяті → лишити `false`, занести розрив і гіпотезу в `progress.md`, тюнити промпт.

- [ ] **Step 5: Коміт**

```bash
git add progress.md
git commit -m "docs(progress): результати citation-евалу"
```
