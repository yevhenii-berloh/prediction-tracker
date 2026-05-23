# Task 19.8d — situation field (replaces context) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename `context` → `situation` наскрізь (Pydantic, DB, prompts, extractor, verifier) і змінити validation з substring (`validate_context_in_post`) на presence (`validate_situation`). situation — model-paraphrase, REQUIRED, drop prediction якщо відсутнє.

**Architecture:** Mechanical rename across 6 source files + 5 test files + 1 alembic migration (alter_column rename). Один logic change: substring validation → presence validation. Verifier prompt label updated (situation не excerpt). Backward compat не релевантний — prod даних немає, context field щойно доданий у 19.8a/c.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2.0, Alembic, pytest. Working dir: `/Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker`. Use `.venv/bin/python`. Ukrainian commit messages.

**Spec:** [`design.md`](design.md)

**Baseline:** 154 tests pass. Target: 154 (net: −4 substring tests, +3 presence tests, +1 alembic test, решта renames).

---

## File Structure

| File | Change |
|---|---|
| `src/prophet_checker/models/domain.py` | `Prediction.context` → `situation` |
| `src/prophet_checker/models/db.py` | `PredictionDB.context` → `situation` column |
| `src/prophet_checker/storage/postgres.py` | mappers context → situation |
| `src/prophet_checker/llm/prompts.py` | `validate_context_in_post`→`validate_situation`, EXTRACTION_TEMPLATE, `build_verification_prompt_v2`, VERIFICATION_TEMPLATE_V2 |
| `src/prophet_checker/analysis/extractor.py` | import, drop logic, field rename |
| `scripts/v2_extraction_run.py` | `serialize_v2_prediction` context → situation |
| `alembic/versions/<rev>_rename_context_to_situation.py` | NEW migration (alter_column) |
| 5 test files | rename + validate_situation tests + migration test |

---

## Task 1: Domain field rename

**Files:**
- Modify: `src/prophet_checker/models/domain.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Rename test**

У `tests/test_models.py`, знайти `test_prediction_has_context_field_default` і замінити повністю на:

```python
def test_prediction_has_situation_field_default():
    from datetime import date
    from prophet_checker.models.domain import Prediction
    pred = Prediction(
        id="p1", document_id="d1", person_id="per1",
        claim_text="Test", prediction_date=date(2024, 1, 1),
    )
    assert pred.situation is None
```

- [ ] **Step 2: Run test — verify FAIL**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_models.py::test_prediction_has_situation_field_default -v
```

Expected: FAIL з `AttributeError: 'Prediction' object has no attribute 'situation'`

- [ ] **Step 3: Rename field у domain.py**

У `src/prophet_checker/models/domain.py`, всередині `class Prediction`, замінити рядок `context: str | None = None` на:

```python
    situation: str | None = None
```

- [ ] **Step 4: Run test — verify PASS**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_models.py::test_prediction_has_situation_field_default -v
```

Expected: PASS

- [ ] **Step 5: Full suite — expect failures elsewhere (mappers/extractor still use context)**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -5
```

Expected: FAILURES у test_storage_postgres / test_analysis_extractor (вони ще шлють `context=` у Prediction). Це OK — наступні tasks їх виправлять. Зафіксуй кількість failures для контролю.

- [ ] **Step 6: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/models/domain.py tests/test_models.py && git commit -m "refactor(models): Prediction.context → situation"
```

---

## Task 2: DB column rename + migration

**Files:**
- Modify: `src/prophet_checker/models/db.py`
- Create: `alembic/versions/<rev>_rename_context_to_situation.py`
- Modify: `tests/test_alembic.py`

- [ ] **Step 1: Rename column у db.py**

У `src/prophet_checker/models/db.py`, всередині `class PredictionDB`, замінити рядок `context: Mapped[str | None] = mapped_column(Text, nullable=True)` на:

```python
    situation: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: Згенерувати revision UUID**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "import uuid; print(uuid.uuid4().hex[:12])"
```

Expected: 12-char hex (приклад `7b2e9c4f1a3d`). **Запам'ятай** — далі `<REVISION>`.

- [ ] **Step 3: Написати failing alembic test**

Append до `tests/test_alembic.py`:

```python
def test_rename_context_migration_loads_cleanly():
    versions = pathlib.Path("alembic/versions")
    files = list(versions.glob("*rename_context_to_situation*"))
    assert len(files) == 1, f"expected 1 migration file, got {files}"

    spec = importlib.util.spec_from_file_location("rename_context_migration", files[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
    assert hasattr(module, "revision")
    assert module.down_revision == "2c09afbbdcdf"
```

- [ ] **Step 4: Run test — verify FAIL**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_alembic.py::test_rename_context_migration_loads_cleanly -v
```

Expected: FAIL з `expected 1 migration file, got []`

- [ ] **Step 5: Створити migration файл**

Створити `alembic/versions/<REVISION>_rename_context_to_situation.py` (заміни `<REVISION>` на UUID зі Step 2 — у filename та `revision`):

```python
"""rename context to situation

Revision ID: <REVISION>
Revises: 2c09afbbdcdf
Create Date: 2026-05-14

"""
from alembic import op


revision = '<REVISION>'
down_revision = '2c09afbbdcdf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("predictions", "context", new_column_name="situation")


def downgrade() -> None:
    op.alter_column("predictions", "situation", new_column_name="context")
```

- [ ] **Step 6: Run test — verify PASS**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_alembic.py::test_rename_context_migration_loads_cleanly -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/models/db.py alembic/versions/*_rename_context_to_situation.py tests/test_alembic.py && git commit -m "refactor(db): PredictionDB.context → situation + alembic rename migration"
```

---

## Task 3: Mappers rename

**Files:**
- Modify: `src/prophet_checker/storage/postgres.py`
- Modify: `tests/test_storage_postgres.py`

- [ ] **Step 1: Rename 2 mapper tests**

У `tests/test_storage_postgres.py`, знайти `test_domain_to_prediction_db_includes_context` і `test_prediction_db_to_domain_includes_context`, замінити повністю на:

```python
def test_domain_to_prediction_db_includes_situation():
    from datetime import date
    from prophet_checker.models.domain import Prediction
    from prophet_checker.storage.postgres import domain_to_prediction_db

    pred = Prediction(
        id="p1", document_id="d1", person_id="per1",
        claim_text="Test", prediction_date=date(2024, 1, 1),
        situation="У відповідь на ситуацію X",
    )
    db_obj = domain_to_prediction_db(pred)
    assert db_obj.situation == "У відповідь на ситуацію X"


def test_prediction_db_to_domain_includes_situation():
    from datetime import date
    from prophet_checker.models.db import PredictionDB
    from prophet_checker.storage.postgres import prediction_db_to_domain

    db = PredictionDB(
        id="p1", document_id="d1", person_id="per1",
        claim_text="Test", prediction_date=date(2024, 1, 1),
        topic="", status="unresolved", confidence=0.0,
        verify_attempts=0,
        situation="У відповідь на ситуацію X",
    )
    pred = prediction_db_to_domain(db)
    assert pred.situation == "У відповідь на ситуацію X"
```

- [ ] **Step 2: Run tests — verify FAIL**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_storage_postgres.py::test_domain_to_prediction_db_includes_situation tests/test_storage_postgres.py::test_prediction_db_to_domain_includes_situation -v
```

Expected: FAIL (mappers ще використовують context).

- [ ] **Step 3: Rename у domain_to_prediction_db**

У `src/prophet_checker/storage/postgres.py`, у `domain_to_prediction_db`, замінити `context=pred.context,` на:

```python
        situation=pred.situation,
```

- [ ] **Step 4: Rename у prediction_db_to_domain**

У тому ж файлі, у `prediction_db_to_domain`, замінити `context=db.context,` на:

```python
        situation=db.situation,
```

- [ ] **Step 5: Run tests — verify PASS**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_storage_postgres.py -v 2>&1 | tail -15
```

Expected: усі mapper тести PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/storage/postgres.py tests/test_storage_postgres.py && git commit -m "refactor(storage): mapper context → situation"
```

---

## Task 4: prompts.py — validate_situation + EXTRACTION_TEMPLATE + verifier

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py`
- Modify: `tests/test_llm_prompts.py`

- [ ] **Step 1: Видалити 4 validate_context тести, додати 3 validate_situation тести**

У `tests/test_llm_prompts.py`, знайти і ВИДАЛИТИ ці 4 тести: `test_validate_context_in_post_success`, `test_validate_context_in_post_normalizes_whitespace`, `test_validate_context_in_post_fails_on_hallucination`, `test_validate_context_in_post_rejects_empty_or_whitespace`.

Додати замість них:

```python
def test_validate_situation_accepts_non_empty():
    from prophet_checker.llm.prompts import validate_situation
    assert validate_situation("У відповідь на іранські погрози") is True


def test_validate_situation_rejects_empty_and_none():
    from prophet_checker.llm.prompts import validate_situation
    assert validate_situation("") is False
    assert validate_situation(None) is False


def test_validate_situation_rejects_whitespace_only():
    from prophet_checker.llm.prompts import validate_situation
    assert validate_situation("   \n\t  ") is False
```

- [ ] **Step 2: Оновити EXTRACTION_TEMPLATE test**

У `tests/test_llm_prompts.py`, знайти `test_extraction_template_includes_context_field` і замінити повністю на:

```python
def test_extraction_template_includes_situation_field():
    from prophet_checker.llm.prompts import EXTRACTION_TEMPLATE
    assert "situation: 1-2 sentences" in EXTRACTION_TEMPLATE
    assert '"situation": "..."' in EXTRACTION_TEMPLATE
```

- [ ] **Step 3: Оновити build_verification_prompt_v2 тести**

У `tests/test_llm_prompts.py`, знайти `test_build_verification_prompt_v2_substitutes_all_fields`. У виклику замінити `context="Original post text",` на:

```python
        situation="Original post text",
```

Знайти `test_build_verification_prompt_v2_accepts_context_kwarg` і замінити повністю на:

```python
def test_build_verification_prompt_v2_accepts_situation_kwarg():
    import pytest
    from prophet_checker.llm.prompts import build_verification_prompt_v2

    prompt = build_verification_prompt_v2(
        claim="X",
        prediction_date="2024-01-01",
        target_date=None,
        today="2026-05-14",
        situation="Verbatim quote",
    )
    assert "Verbatim quote" in prompt

    with pytest.raises(TypeError):
        build_verification_prompt_v2(
            claim="X",
            prediction_date="2024-01-01",
            target_date=None,
            today="2026-05-14",
            context="should fail under new signature",
        )
```

- [ ] **Step 4: Run tests — verify FAIL**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_llm_prompts.py -k "situation or extraction_template or verification_prompt_v2" -v
```

Expected: нові situation тести FAIL (`validate_situation` не існує, EXTRACTION_TEMPLATE без situation, build_verification_prompt_v2 ще приймає context).

- [ ] **Step 5: Замінити validate_context_in_post на validate_situation**

У `src/prophet_checker/llm/prompts.py`, знайти функцію `validate_context_in_post` (substring check, в кінці файлу) і замінити повністю на:

```python
def validate_situation(situation: str | None) -> bool:
    return bool(situation and situation.strip())
```

- [ ] **Step 6: Оновити EXTRACTION_TEMPLATE**

У `src/prophet_checker/llm/prompts.py`, у `EXTRACTION_TEMPLATE`, замінити bullet:

```
- context: VERBATIM quote from the post (~300 chars max) that
  shows what the claim refers to. Pick the sentence(s) immediately
  surrounding the claim that explain the situation, persons, or
  preceding events. Must be EXACT text from the post (we validate
  programmatically that this is a substring).
```

на:

```
- situation: 1-2 sentences (in the post's language) summarizing the
  events or circumstances the author was responding to when making
  this prediction. Answer "in response to what situation was this
  forecast made?". Synthesize from the whole post — capture preceding
  setup, triggering events, persons involved. This is YOUR summary,
  NOT a verbatim quote.
```

І у JSON example рядку замінити `"context": "..."` на `"situation": "..."`.

- [ ] **Step 7: Оновити build_verification_prompt_v2**

У `src/prophet_checker/llm/prompts.py`, замінити функцію `build_verification_prompt_v2` повністю на:

```python
def build_verification_prompt_v2(
    claim: str,
    prediction_date: str,
    target_date: str | None,
    today: str,
    situation: str,
) -> str:
    return VERIFICATION_TEMPLATE_V2.format(
        claim=claim,
        prediction_date=prediction_date,
        target_date=target_date or "not specified",
        today=today,
        situation=situation,
    )
```

- [ ] **Step 8: Оновити VERIFICATION_TEMPLATE_V2 placeholder + label**

У `src/prophet_checker/llm/prompts.py`, у `VERIFICATION_TEMPLATE_V2`, знайти блок:

```
Original post excerpt (for context):
---
{post_excerpt}
---
```

замінити на:

```
Situation that prompted the claim:
---
{situation}
---
```

- [ ] **Step 9: Run tests — verify PASS**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_llm_prompts.py -v 2>&1 | tail -20
```

Expected: усі test_llm_prompts тести PASS (situation tests + verification v2 parser tests незмінні).

- [ ] **Step 10: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py && git commit -m "refactor(llm): context→situation — validate_situation (presence), EXTRACTION_TEMPLATE, verifier prompt"
```

---

## Task 5: extractor.py — situation drop logic

**Files:**
- Modify: `src/prophet_checker/analysis/extractor.py`
- Modify: `tests/test_analysis_extractor.py`

- [ ] **Step 1: Оновити LLM_RESPONSE_ONE fixture**

У `tests/test_analysis_extractor.py`, знайти `LLM_RESPONSE_ONE` і замінити повністю на:

```python
LLM_RESPONSE_ONE = json.dumps({
    "predictions": [
        {
            "claim_text": "Контрнаступ почнеться влітку 2023 року",
            "prediction_date": "2023-01-15",
            "target_date": "2023-06-01",
            "topic": "війна",
            "situation": "Обговорення планів ЗСУ на літню кампанію 2023",
        }
    ]
})
```

- [ ] **Step 2: Оновити assertion у test_extract_returns_predictions**

У `tests/test_analysis_extractor.py`, у `test_extract_returns_predictions`, замінити рядок `assert p.context == "Контрнаступ почнеться влітку 2023 року"` на:

```python
    assert p.situation == "Обговорення планів ЗСУ на літню кампанію 2023"
```

- [ ] **Step 3: Замінити 2 drop тести**

У `tests/test_analysis_extractor.py`, знайти `test_extract_drops_prediction_with_hallucinated_context` і `test_extract_drops_prediction_with_missing_context`, замінити повністю на:

```python
async def test_extract_drops_prediction_with_empty_situation():
    response = json.dumps({"predictions": [{
        "claim_text": "Війна закінчиться скоро",
        "prediction_date": "2023-01-15", "target_date": None, "topic": "війна",
        "situation": "",
    }]})
    llm = make_llm(response)
    extractor = PredictionExtractor(llm)
    predictions = await extractor.extract(
        text="Реальний пост: Війна закінчиться скоро, я впевнений.",
        person_id="p1", document_id="d1", person_name="Арестович",
        published_date="2023-01-15",
    )
    assert predictions == []


async def test_extract_drops_prediction_with_missing_situation():
    response = json.dumps({"predictions": [{
        "claim_text": "Щось станеться",
        "prediction_date": "2023-01-15", "target_date": None, "topic": "війна",
    }]})
    llm = make_llm(response)
    extractor = PredictionExtractor(llm)
    predictions = await extractor.extract(
        text="Реальний пост без situation.",
        person_id="p1", document_id="d1", person_name="Арестович",
        published_date="2023-01-15",
    )
    assert predictions == []
```

- [ ] **Step 4: Run tests — verify FAIL**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_analysis_extractor.py -v
```

Expected: FAILURES (extractor ще використовує context + validate_context_in_post).

- [ ] **Step 5: Оновити import у extractor.py**

У `src/prophet_checker/analysis/extractor.py`, у import block з `prophet_checker.llm.prompts`, замінити `validate_context_in_post` на:

```python
    validate_situation,
```

- [ ] **Step 6: Оновити drop logic + field у loop**

У `src/prophet_checker/analysis/extractor.py`, знайти block:

```python
            context = raw.get("context")
            if not validate_context_in_post(context, text):
                logger.warning(
                    "Drop prediction — invalid/missing context: %r", claim[:60]
                )
                continue
```

замінити на:

```python
            situation = raw.get("situation")
            if not validate_situation(situation):
                logger.warning(
                    "Drop prediction — missing/empty situation: %r", claim[:60]
                )
                continue
```

Потім у `Prediction(...)` construction замінити `context=context,` на:

```python
                    situation=situation,
```

- [ ] **Step 7: Run tests — verify PASS**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_analysis_extractor.py -v
```

Expected: усі 5 тестів PASS.

- [ ] **Step 8: Full suite check**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `154 passed`

- [ ] **Step 9: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add src/prophet_checker/analysis/extractor.py tests/test_analysis_extractor.py && git commit -m "refactor(analysis): extractor context → situation, drop on missing situation"
```

---

## Task 6: v2_extraction_run.py serializer rename

**Files:**
- Modify: `scripts/v2_extraction_run.py`

- [ ] **Step 1: Rename у serialize_v2_prediction**

У `scripts/v2_extraction_run.py`, у функції `serialize_v2_prediction`, замінити `"context": p.context,` на:

```python
        "situation": p.situation,
```

- [ ] **Step 2: Smoke — script завантажується**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/v2_extraction_run.py --help
```

Expected: argparse help text (no error). select_posts тести у test_v2_extraction_run не зачіпаються (не торкаються situation).

- [ ] **Step 3: Full suite check**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `154 passed`

- [ ] **Step 4: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/v2_extraction_run.py && git commit -m "refactor(scripts): v2_extraction_run serialize context → situation"
```

---

## Task 7: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full suite**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `154 passed`

- [ ] **Step 2: Verify no lingering `context` references у джерелах**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && grep -rn "\bcontext\b\|validate_context_in_post\|post_excerpt" src/prophet_checker/ scripts/v2_extraction_run.py 2>&1 | grep -v "__pycache__"
```

Expected: жодних згадок `context` (як field), `validate_context_in_post`, `post_excerpt`. (Інші слова з "context" у coментарях/назвах НЕ повинні існувати — pet project no comments. Якщо grep щось знаходить — investigate.)

- [ ] **Step 3: Git log — 6 commits**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git log --oneline | head -7
```

Expected (most recent 6):
- `refactor(scripts): v2_extraction_run serialize context → situation`
- `refactor(analysis): extractor context → situation, drop on missing situation`
- `refactor(llm): context→situation — validate_situation (presence), EXTRACTION_TEMPLATE, verifier prompt`
- `refactor(storage): mapper context → situation`
- `refactor(db): PredictionDB.context → situation + alembic rename migration`
- `refactor(models): Prediction.context → situation`

- [ ] **Step 4: Smoke — situation flow end-to-end**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "
import asyncio, json
from unittest.mock import AsyncMock, MagicMock
from prophet_checker.analysis.extractor import PredictionExtractor
from prophet_checker.llm.prompts import build_verification_prompt_v2, validate_situation

def make_llm(resp):
    llm = MagicMock(); llm.complete = AsyncMock(return_value=resp); return llm

async def main():
    print('validate_situation non-empty:', validate_situation('текст'))
    print('validate_situation empty:', validate_situation(''))
    print('validate_situation None:', validate_situation(None))

    resp = json.dumps({'predictions': [{
        'claim_text': 'X станеться', 'prediction_date': '2024-01-01',
        'target_date': None, 'topic': 'політика',
        'situation': 'У відповідь на події Y',
    }]})
    ex = PredictionExtractor(make_llm(resp))
    preds = await ex.extract(text='Пост.', person_id='p', document_id='d',
        person_name='A', published_date='2024-01-01')
    print('Extracted with situation:', preds[0].situation if preds else None)

    prompt = build_verification_prompt_v2(claim='X', prediction_date='2024-01-01',
        target_date=None, today='2026-05-14', situation='У відповідь на події Y')
    print('Verifier prompt has situation:', 'У відповідь на події Y' in prompt)
    print('Verifier label updated:', 'Situation that prompted the claim' in prompt)

asyncio.run(main())
"
```

Expected:
```
validate_situation non-empty: True
validate_situation empty: False
validate_situation None: False
Extracted with situation: У відповідь на події Y
Verifier prompt has situation: True
Verifier label updated: True
```

---

## Done criteria

- ✅ 154 tests pass
- ✅ 6 commits (refactor scope, Ukrainian)
- ✅ No `context`/`validate_context_in_post`/`post_excerpt` lingering у src + v2_extraction_run.py
- ✅ Extractor drops on missing/empty situation
- ✅ Verifier prompt has situation + updated label
- ✅ Alembic rename migration (down_revision 2c09afbbdcdf)

---

## Caveats для implementer

1. **Task 1 Step 5 — intentional cross-file failures.** Після rename domain field, mappers/extractor ще шлють `context=` → Prediction validation errors. Це expected proht — наступні tasks (3, 5) виправляють. Не панікувати; кожен task закриває свою частину.

2. **validate_situation НЕ приймає raw_post.** На відміну від старого validate_context_in_post(context, raw_post), нова функція presence-only: `validate_situation(situation)`. Extractor НЕ передає text у неї.

3. **VERIFICATION_TEMPLATE_V2 — placeholder name змінюється** на `{situation}` (на відміну від 19.8c де placeholder лишався post_excerpt). Build функція тепер `.format(situation=situation)`.

4. **Re-run 19.8b після цього** — operational, окремо. Existing v2_extraction_outputs.json (з context field) invalidated. Не частина цього плану.

5. **Migration alter_column** — НЕ запускається у DB (sanity test тільки module load). Реальний alembic upgrade — окремо коли є docker postgres.
