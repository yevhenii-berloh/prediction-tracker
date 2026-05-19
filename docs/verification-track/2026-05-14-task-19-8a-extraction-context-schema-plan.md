# Task 19.8a — Extraction Context Field Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Розширити extraction output полем `context` (verbatim quote з посту), додати валідатор substring у post-processing, перейменувати параметр `post_excerpt → context` у `build_verification_prompt_v2`. Schema/prompt/validator контракт для V2 extraction.

**Architecture:** Nullable `context` field на `Prediction` (Pydantic + DB Text column). EXTRACTION_TEMPLATE розширюється новим JSON output. `validate_context_in_post` як substring-check утиліта з whitespace normalize. `build_verification_prompt_v2` приймає `context=` замість `post_excerpt=` (template placeholder лишається). Alembic migration `add_prediction_context` chains після `8df4e2013c5a`.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2.0 async, Alembic, pytest. Working dir: `/Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker`. Use `.venv/bin/python` для команд. Ukrainian commit messages.

**Spec:** [`2026-05-14-task-19-8a-extraction-context-schema-design.md`](2026-05-14-task-19-8a-extraction-context-schema-design.md)

**Baseline:** 139 tests pass. Target: 150 (+11 нових, 1 fixture rename).

---

## Task 1: Domain field — `Prediction.context`

**Files:**
- Modify: `src/prophet_checker/models/domain.py`
- Test: `tests/test_models.py` (append)

- [ ] **Step 1: Написати failing test**

Append до `tests/test_models.py`:

```python
def test_prediction_has_context_field_default():
    from datetime import date
    from prophet_checker.models.domain import Prediction
    pred = Prediction(
        id="p1", document_id="d1", person_id="per1",
        claim_text="Test", prediction_date=date(2024, 1, 1),
    )
    assert pred.context is None
```

- [ ] **Step 2: Запустити тест — переконатись що fails**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_models.py::test_prediction_has_context_field_default -v
```

Expected: FAIL з `AttributeError: 'Prediction' object has no attribute 'context'`

- [ ] **Step 3: Додати поле в Prediction**

У `src/prophet_checker/models/domain.py`, всередині `class Prediction(BaseModel)`, відразу ПІСЛЯ рядка `claim_text: str`, додати:

```python
    context: str | None = None
```

- [ ] **Step 4: Запустити тест — passes**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_models.py::test_prediction_has_context_field_default -v
```

Expected: PASS

- [ ] **Step 5: Full suite check**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `140 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/models/domain.py tests/test_models.py && git commit -m "feat(models): додаю Prediction.context field для V2 extraction"
```

---

## Task 2: DB column — `PredictionDB.context`

**Files:**
- Modify: `src/prophet_checker/models/db.py`

Cтруктурна зміна без окремого юніт-тесту (mapper тести у Task 3 покриють поведінку).

- [ ] **Step 1: Додати колонку в PredictionDB**

У `src/prophet_checker/models/db.py`, всередині `class PredictionDB(Base)`, відразу ПІСЛЯ рядка `claim_text: Mapped[str] = mapped_column(Text, nullable=False)`, додати:

```python
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: Full suite check (нічого не повинно зламатися)**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `140 passed`

- [ ] **Step 3: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/models/db.py && git commit -m "feat(db): додаю context column на PredictionDB"
```

---

## Task 3: Mappers round-trip

**Files:**
- Modify: `src/prophet_checker/storage/postgres.py`
- Test: `tests/test_storage_postgres.py` (append 2 tests)

- [ ] **Step 1: Написати failing тести**

Append до `tests/test_storage_postgres.py`:

```python
def test_domain_to_prediction_db_includes_context():
    from datetime import date
    from prophet_checker.models.domain import Prediction
    from prophet_checker.storage.postgres import domain_to_prediction_db

    pred = Prediction(
        id="p1", document_id="d1", person_id="per1",
        claim_text="Test", prediction_date=date(2024, 1, 1),
        context="Verbatim quote from post",
    )
    db_obj = domain_to_prediction_db(pred)
    assert db_obj.context == "Verbatim quote from post"


def test_prediction_db_to_domain_includes_context():
    from datetime import date
    from prophet_checker.models.db import PredictionDB
    from prophet_checker.storage.postgres import prediction_db_to_domain

    db = PredictionDB(
        id="p1", document_id="d1", person_id="per1",
        claim_text="Test", prediction_date=date(2024, 1, 1),
        topic="", status="unresolved", confidence=0.0,
        verify_attempts=0,
        context="Verbatim quote from post",
    )
    pred = prediction_db_to_domain(db)
    assert pred.context == "Verbatim quote from post"
```

- [ ] **Step 2: Запустити тести — fail**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_storage_postgres.py::test_domain_to_prediction_db_includes_context tests/test_storage_postgres.py::test_prediction_db_to_domain_includes_context -v
```

Expected: обидва FAIL (mapper не передає `context`, поле буде None у db_obj або відсутнє у Prediction).

- [ ] **Step 3: Оновити `domain_to_prediction_db`**

У `src/prophet_checker/storage/postgres.py`, у функції `domain_to_prediction_db`, додати рядок у `return PredictionDB(...)` відразу ПІСЛЯ `claim_text=pred.claim_text,`:

```python
        context=pred.context,
```

- [ ] **Step 4: Оновити `prediction_db_to_domain`**

У тому ж файлі, у функції `prediction_db_to_domain`, додати рядок у `return Prediction(...)` відразу ПІСЛЯ `claim_text=db.claim_text,`:

```python
        context=db.context,
```

- [ ] **Step 5: Запустити тести — pass**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_storage_postgres.py::test_domain_to_prediction_db_includes_context tests/test_storage_postgres.py::test_prediction_db_to_domain_includes_context -v
```

Expected: PASS обидва

- [ ] **Step 6: Full suite check**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `142 passed`

- [ ] **Step 7: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/storage/postgres.py tests/test_storage_postgres.py && git commit -m "feat(storage): mapper round-trip context field"
```

---

## Task 4: Validator function `validate_context_in_post`

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py`
- Test: `tests/test_llm_prompts.py` (append 4 tests)

- [ ] **Step 1: Написати 4 failing тести**

Append до `tests/test_llm_prompts.py`:

```python
def test_validate_context_in_post_success():
    from prophet_checker.llm.prompts import validate_context_in_post
    post = "Сьогодні я думаю що війна закінчиться скоро. Це моя думка."
    ctx = "війна закінчиться скоро"
    assert validate_context_in_post(ctx, post) is True


def test_validate_context_in_post_normalizes_whitespace():
    from prophet_checker.llm.prompts import validate_context_in_post
    post = "Перше речення.\n\n   Друге  речення\tз багатьма пробілами."
    ctx = "Друге речення з багатьма пробілами"
    assert validate_context_in_post(ctx, post) is True


def test_validate_context_in_post_fails_on_hallucination():
    from prophet_checker.llm.prompts import validate_context_in_post
    post = "Реальний текст посту про економіку."
    ctx = "Цей текст модель вигадала і його у пості немає"
    assert validate_context_in_post(ctx, post) is False


def test_validate_context_in_post_rejects_empty_or_whitespace():
    from prophet_checker.llm.prompts import validate_context_in_post
    post = "Реальний текст посту."
    assert validate_context_in_post("", post) is False
    assert validate_context_in_post("   \n\t  ", post) is False
    assert validate_context_in_post("Реальний", "") is False
```

- [ ] **Step 2: Запустити тести — fail**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_llm_prompts.py -k validate_context -v
```

Expected: усі 4 FAIL з `ImportError: cannot import name 'validate_context_in_post'`

- [ ] **Step 3: Додати функцію у `prompts.py`**

У `src/prophet_checker/llm/prompts.py`, додати в КІНЦЬ файлу:

```python
def validate_context_in_post(context: str, raw_post: str) -> bool:
    if not context or not raw_post:
        return False
    norm_ctx = " ".join(context.split())
    if not norm_ctx:
        return False
    norm_post = " ".join(raw_post.split())
    return norm_ctx in norm_post
```

- [ ] **Step 4: Запустити тести — pass**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_llm_prompts.py -k validate_context -v
```

Expected: усі 4 PASS

- [ ] **Step 5: Full suite check**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `146 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py && git commit -m "feat(llm): validate_context_in_post substring validator з whitespace normalize"
```

---

## Task 5: EXTRACTION_TEMPLATE expand з `context` field

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py`
- Test: `tests/test_llm_prompts.py` (append 2 tests)

- [ ] **Step 1: Написати 2 failing тести**

Append до `tests/test_llm_prompts.py`:

```python
def test_extraction_template_includes_context_field():
    from prophet_checker.llm.prompts import EXTRACTION_TEMPLATE
    assert "context: VERBATIM quote" in EXTRACTION_TEMPLATE
    assert '"context": "..."' in EXTRACTION_TEMPLATE


def test_parse_extraction_response_extracts_context():
    import json
    from prophet_checker.llm.prompts import parse_extraction_response
    response = json.dumps({
        "predictions": [
            {
                "claim_text": "Війна закінчиться у 2026",
                "prediction_date": "2024-01-15",
                "target_date": "2026-12-31",
                "topic": "війна",
                "context": "Я думаю що війна закінчиться у 2026",
            }
        ]
    })
    predictions = parse_extraction_response(response)
    assert len(predictions) == 1
    assert predictions[0]["context"] == "Я думаю що війна закінчиться у 2026"
```

- [ ] **Step 2: Запустити тести**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_llm_prompts.py::test_extraction_template_includes_context_field tests/test_llm_prompts.py::test_parse_extraction_response_extracts_context -v
```

Expected: перший FAIL (template без `context` field), другий PASS (parser передає весь dict, новий ключ потрапляє автоматично — але без template змін модель не повертатиме поле; тест на parser-only проходить бо ми feed-имо json вручну).

Якщо `test_parse_extraction_response_extracts_context` фейлить — щось зламано у parser. Не повинно бути.

- [ ] **Step 3: Розширити EXTRACTION_TEMPLATE**

У `src/prophet_checker/llm/prompts.py`, знайти рядок `EXTRACTION_TEMPLATE = """Analyze the following text...` і замінити повністю на:

```python
EXTRACTION_TEMPLATE = """Analyze the following text by {person_name} (published on {published_date}).
Extract all predictions — statements about future events that can later be verified.

Text:
---
{text}
---

For each prediction, extract:
- claim_text: the exact prediction (in original language)
- prediction_date: when the prediction was made (YYYY-MM-DD)
- target_date: when the predicted event should happen (YYYY-MM-DD or null if unclear)
- topic: category (e.g., "війна", "економіка", "політика", "міжнародні відносини")
- context: VERBATIM quote from the post (~300 chars max) that
  shows what the claim refers to. Pick the sentence(s) immediately
  surrounding the claim that explain the situation, persons, or
  preceding events. Must be EXACT text from the post (we validate
  programmatically that this is a substring).

Respond with JSON:
{{"predictions": [{{"claim_text": "...", "prediction_date": "...", "target_date": "...", "topic": "...", "context": "..."}}]}}

If no predictions found, respond: {{"predictions": []}}"""
```

- [ ] **Step 4: Запустити тести — pass**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_llm_prompts.py::test_extraction_template_includes_context_field tests/test_llm_prompts.py::test_parse_extraction_response_extracts_context -v
```

Expected: обидва PASS

- [ ] **Step 5: Full suite check**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `148 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py && git commit -m "feat(llm): EXTRACTION_TEMPLATE розширено context field"
```

---

## Task 6: Rename `post_excerpt → context` у `build_verification_prompt_v2`

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py`
- Modify: `tests/test_llm_prompts.py` (rename existing fixture + append 1 test)

- [ ] **Step 1: Перейменувати kwarg у existing fixture**

У `tests/test_llm_prompts.py`, знайти `test_build_verification_prompt_v2_substitutes_all_fields`. У виклику `build_verification_prompt_v2(...)` замінити `post_excerpt="Original post text"` на:

```python
        context="Original post text",
```

- [ ] **Step 2: Запустити перейменовану fixture — fail**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_llm_prompts.py::test_build_verification_prompt_v2_substitutes_all_fields -v
```

Expected: FAIL з `TypeError: build_verification_prompt_v2() got an unexpected keyword argument 'context'`

- [ ] **Step 3: Написати новий test**

Append до `tests/test_llm_prompts.py`:

```python
def test_build_verification_prompt_v2_accepts_context_kwarg():
    import pytest
    from prophet_checker.llm.prompts import build_verification_prompt_v2

    prompt = build_verification_prompt_v2(
        claim="X",
        prediction_date="2024-01-01",
        target_date=None,
        today="2026-05-14",
        context="Verbatim quote",
    )
    assert "Verbatim quote" in prompt

    with pytest.raises(TypeError):
        build_verification_prompt_v2(
            claim="X",
            prediction_date="2024-01-01",
            target_date=None,
            today="2026-05-14",
            post_excerpt="should fail under new signature",
        )
```

- [ ] **Step 4: Запустити новий test — fail**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_llm_prompts.py::test_build_verification_prompt_v2_accepts_context_kwarg -v
```

Expected: FAIL з TypeError (наразі функція приймає `post_excerpt`, не `context`)

- [ ] **Step 5: Оновити сигнатуру `build_verification_prompt_v2`**

У `src/prophet_checker/llm/prompts.py`, знайти функцію `build_verification_prompt_v2` і замінити повністю на:

```python
def build_verification_prompt_v2(
    claim: str,
    prediction_date: str,
    target_date: str | None,
    today: str,
    context: str,
) -> str:
    return VERIFICATION_TEMPLATE_V2.format(
        claim=claim,
        prediction_date=prediction_date,
        target_date=target_date or "not specified",
        today=today,
        post_excerpt=context,
    )
```

Зауваж: внутрішній `.format(post_excerpt=context, ...)` ЗБЕРІГАЄ template placeholder name — `VERIFICATION_TEMPLATE_V2` змінювати НЕ ТРЕБА. Тільки feed context value у старий placeholder.

- [ ] **Step 6: Запустити обидва тести — pass**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_llm_prompts.py::test_build_verification_prompt_v2_substitutes_all_fields tests/test_llm_prompts.py::test_build_verification_prompt_v2_accepts_context_kwarg -v
```

Expected: PASS обидва

- [ ] **Step 7: Full suite check**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `149 passed`

- [ ] **Step 8: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py && git commit -m "feat(llm): build_verification_prompt_v2 приймає context= замість post_excerpt="
```

---

## Task 7: Alembic migration `add_prediction_context`

**Files:**
- Create: `alembic/versions/<revision>_add_prediction_context.py` (revision UUID генерується у Step 1)
- Test: `tests/test_alembic.py` (append 1 test)

- [ ] **Step 1: Згенерувати revision UUID**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "import uuid; print(uuid.uuid4().hex[:12])"
```

Expected: 12-character hex string (приклад: `4f8a2bcd1e3f`). **ЗАПАМ'ЯТАЙ це значення** — далі позначається `<REVISION>`.

- [ ] **Step 2: Написати failing test**

Append до `tests/test_alembic.py`:

```python
def test_prediction_context_migration_loads_cleanly():
    versions = pathlib.Path("alembic/versions")
    files = list(versions.glob("*add_prediction_context*"))
    assert len(files) == 1, f"expected 1 migration file, got {files}"

    spec = importlib.util.spec_from_file_location("prediction_context_migration", files[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
    assert hasattr(module, "revision")
    assert module.down_revision == "8df4e2013c5a"
```

- [ ] **Step 3: Запустити тест — fail**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_alembic.py::test_prediction_context_migration_loads_cleanly -v
```

Expected: FAIL з `expected 1 migration file, got []`

- [ ] **Step 4: Створити файл міграції**

Створити `alembic/versions/<REVISION>_add_prediction_context.py` (замінити `<REVISION>` на UUID зі Step 1 — і в назві файлу, і в `revision = '...'`):

```python
"""add prediction_context

Revision ID: <REVISION>
Revises: 8df4e2013c5a
Create Date: 2026-05-14

"""
from alembic import op
import sqlalchemy as sa


revision = '<REVISION>'
down_revision = '8df4e2013c5a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("context", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("predictions", "context")
```

- [ ] **Step 5: Запустити тест — pass**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_alembic.py::test_prediction_context_migration_loads_cleanly -v
```

Expected: PASS

- [ ] **Step 6: Full suite check**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `150 passed`

- [ ] **Step 7: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add alembic/versions/*_add_prediction_context.py tests/test_alembic.py && git commit -m "feat(db): alembic migration add_prediction_context column"
```

---

## Task 8: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full suite final check**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `150 passed, X warnings in Y.Ys`

- [ ] **Step 2: Перевірити git log на 7 нових commits**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git log --oneline | head -10
```

Expected: 7 нових commits з префіксами `feat(models):`, `feat(db):` × 2, `feat(storage):`, `feat(llm):` × 3, у Ukrainian.

- [ ] **Step 3: Manual smoke — Pydantic field round-trip**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "
from datetime import date
from prophet_checker.models.domain import Prediction
p = Prediction(
    id='p1', document_id='d1', person_id='per1',
    claim_text='Test claim', prediction_date=date(2024, 1, 1),
    context='Verbatim quote з посту'
)
print('Pydantic round-trip OK:', p.context)
"
```

Expected: `Pydantic round-trip OK: Verbatim quote з посту`

- [ ] **Step 4: Manual smoke — validator**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "
from prophet_checker.llm.prompts import validate_context_in_post
post = 'Реальний пост.\n\n   Багато  пробілів\tі newlines.'
ctx = 'Багато пробілів і newlines'
print('Validator success:', validate_context_in_post(ctx, post))
print('Validator hallucination:', validate_context_in_post('fake text', post))
print('Validator empty:', validate_context_in_post('', post))
"
```

Expected:
```
Validator success: True
Validator hallucination: False
Validator empty: False
```

- [ ] **Step 5: Manual smoke — verifier prompt build з новим kwarg**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "
from prophet_checker.llm.prompts import build_verification_prompt_v2
prompt = build_verification_prompt_v2(
    claim='X буде наступного року',
    prediction_date='2025-01-01',
    target_date='2026-01-01',
    today='2026-05-14',
    context='Verbatim quote з оригінального посту',
)
assert 'Verbatim quote з оригінального посту' in prompt
print('Verifier prompt build OK з context= kwarg')
"
```

Expected: `Verifier prompt build OK з context= kwarg`

- [ ] **Step 6: Smoke — Alembic міграція chain доступна**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && ls -1 alembic/versions/*.py | grep -v __pycache__
```

Expected: 4 файли (initial schema, V2 metadata, prediction_value, prediction_context).

---

## Done criteria

- ✅ 150 tests pass
- ✅ 7 нових commits з Ukrainian messages у git log
- ✅ Manual smoke (Pydantic round-trip, validator, prompt build, alembic chain) — усі OK
- ✅ Файл `alembic/versions/*_add_prediction_context.py` існує, links to down_revision `8df4e2013c5a`

---

## Caveats та notes для implementer

1. **Migration не запускається у DB** — Task 19.8a лише додає файл. Реальний `alembic upgrade head` робиться у Task 19.8b (operational run з docker postgres). Якщо docker недоступний — це OK, sanity test перевіряє лише завантаження модуля.

2. **Parser не змінюється** — `parse_extraction_response` уже бере `data.get("predictions", [])` як list of dicts. Новий ключ `context` потрапляє автоматично через dict. Тест `test_parse_extraction_response_extracts_context` — sanity check, не behavior change.

3. **VERIFICATION_TEMPLATE_V2 не змінюємо** — внутрішній placeholder `{post_excerpt}` залишається. Тільки function param + .format() kwarg рятуємо. Це навмисно — щоб не торкатися template (де є складна system message).

4. **При conflict на rebase** з паралельними змінами в `prompts.py` — merge ручний. EXTRACTION_TEMPLATE expand і validate_context_in_post — незалежні від інших змін.
