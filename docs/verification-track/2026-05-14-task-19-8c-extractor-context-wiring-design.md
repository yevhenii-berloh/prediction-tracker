# Task 19.8c — Wire context into PredictionExtractor

**Status:** draft 2026-05-14
**Task:** 19.8c (production extractor context wiring — closes 19.8a gap)
**Prerequisites:** ✅ Task 19.8a landed (`context` field + validate_context_in_post + EXTRACTION_TEMPLATE)
**Impacts:** Task 19.8b plan (Stage 1 revision — remove extractor bypass)

---

## TL;DR

Task 19.8a додав `context` field на Prediction/PredictionDB, розширив EXTRACTION_TEMPLATE, і додав `validate_context_in_post` — але **не оновив** `PredictionExtractor.extract()` mapping logic. Extractor досі створює Prediction objects без context, ігноруючи `raw.get("context")`. Через це 19.8b plan вимушений bypass'ити extractor (code smell, дублювання валідації).

19.8c закриває gap: extractor дістає context з parsed dict, validate'ить його substring проти raw post, і **drop'ить весь prediction** якщо context invalid/missing. Та сама поведінка для production і eval — single source of truth. Після цього 19.8b використовує extractor напряму (revision plan doc).

---

## Architectural decisions

| # | Рішення | Обґрунтування |
|---|---|---|
| Q1 | **Drop весь prediction на invalid context** (unified production + eval) | Eval — це вимірювання production behavior. Якщо розходяться, міряємо не те що працюватиме. Gold (validated-context only) має matchити production data. Single code path = DRY. |
| Q2 | **Validation у PredictionExtractor** (не в eval script) | Single source of truth. 19.8b bypass існував тільки тому що extractor не вмів context — як тільки вміє, bypass зникає. |
| Q3 | **Missing context = invalid** (природний drop path) | `validate_context_in_post` вже повертає False для None/empty/whitespace — без спец-casing. |
| Q4 | **Return type `list[Prediction]` незмінний** | Backward compat для evaluate_detection + extraction_quality_eval callers. |
| Q5 | **19.8c scope = extractor + fixture updates + revise 19.8b plan doc** | 19.8b stays valid але тоншим; обидва docs consistent. |

---

## Core change: `src/prophet_checker/analysis/extractor.py`

Два додавання у `extract()`:

```python
from prophet_checker.llm.prompts import (
    build_extraction_prompt,
    get_extraction_system,
    parse_extraction_response,
    validate_context_in_post,   # NEW import
)

# ... всередині for raw in raw_predictions loop, ПІСЛЯ claim non-empty check:
            claim = raw.get("claim_text", "").strip()
            if not claim:
                continue

            context = raw.get("context")                          # NEW
            if not validate_context_in_post(context, text):       # NEW
                logger.warning(                                   # NEW
                    "Drop prediction — invalid/missing context: %r", claim[:60]
                )
                continue                                          # NEW

            # ... existing target_date / prediction_date parsing ...

            predictions.append(
                Prediction(
                    id=str(uuid4()), person_id=person_id, document_id=document_id,
                    claim_text=claim,
                    context=context,                              # NEW
                    prediction_date=prediction_date, target_date=target_date,
                    topic=raw.get("topic", ""), status=PredictionStatus.UNRESOLVED,
                    confidence=0.0, evidence_url=None, evidence_text=None, embedding=None,
                )
            )
```

**Ключове:** `validate_context_in_post(context, text)` — `text` param вже є у signature extract() (raw post). Missing context (`None`) → validator returns False → drop. No special-casing.

---

## Fixture updates: `tests/test_analysis_extractor.py`

Existing `LLM_RESPONSE_ONE` без context → claims drop'нуться → 3 existing тести fail. Fix:

```python
LLM_RESPONSE_ONE = json.dumps({
    "predictions": [
        {
            "claim_text": "Контрнаступ почнеться влітку 2023 року",
            "prediction_date": "2023-01-15",
            "target_date": "2023-06-01",
            "topic": "війна",
            "context": "Контрнаступ почнеться влітку 2023 року",
        }
    ]
})
```

`context` має бути substring of test's `text` param. У `test_extract_returns_predictions` text = `"Контрнаступ почнеться влітку 2023 року"` — context дорівнює claim, що є substring → validation passes.

Додати assertion у `test_extract_returns_predictions`:
```python
assert p.context == "Контрнаступ почнеться влітку 2023 року"
```

`test_extract_no_predictions` (empty list) і `test_extract_llm_error_returns_empty` (exception) — не зачіпаються (не доходять до validation).

### Нові тести (2):

```python
async def test_extract_drops_prediction_with_hallucinated_context():
    response = json.dumps({"predictions": [{
        "claim_text": "Війна закінчиться скоро",
        "prediction_date": "2023-01-15", "target_date": None, "topic": "війна",
        "context": "цього тексту немає в оригінальному пості взагалі",
    }]})
    llm = make_llm(response)
    extractor = PredictionExtractor(llm)
    predictions = await extractor.extract(
        text="Реальний пост: Війна закінчиться скоро, я впевнений.",
        person_id="p1", document_id="d1", person_name="Арестович",
        published_date="2023-01-15",
    )
    assert predictions == []


async def test_extract_drops_prediction_with_missing_context():
    response = json.dumps({"predictions": [{
        "claim_text": "Щось станеться",
        "prediction_date": "2023-01-15", "target_date": None, "topic": "війна",
    }]})
    llm = make_llm(response)
    extractor = PredictionExtractor(llm)
    predictions = await extractor.extract(
        text="Реальний пост без потрібного context.",
        person_id="p1", document_id="d1", person_name="Арестович",
        published_date="2023-01-15",
    )
    assert predictions == []
```

**Tests delta:** +2 нових, 1 fixture update, +1 assertion. 150 → 152.

---

## 19.8b plan revision (doc edit)

Після 19.8c, `scripts/v2_extraction_run.py` (Task 1 у 19.8b plan) не bypass'ить extractor.

**Видаляємо з v2_extraction_run.py spec:**
- `extract_v2()` (bypass що викликав parse_extraction_response напряму)
- `validate_and_drop()` (валідація тепер в extractor)
- import `validate_context_in_post`
- 2 тести `test_validate_and_drop_*` з `tests/test_v2_extraction_run.py` (логіка переїхала в test_analysis_extractor)

**Замінюємо на:**
- `build_llm_client` → `PredictionExtractor(LLMClient(...))`
- `run_extraction()` викликає `extractor.extract(...)` → `list[Prediction]` (вже validated, context populated)
- New serializer:
  ```python
  def serialize_v2_prediction(p) -> dict:
      return {
          "claim_text": p.claim_text,
          "context": p.context,
          "prediction_date": p.prediction_date.isoformat() if p.prediction_date else None,
          "target_date": p.target_date.isoformat() if p.target_date else None,
          "topic": p.topic,
      }
  ```

**Втрачаємо:** точний `claims_hallucinated_drop` stat у metadata (extractor логує warnings, не повертає structured count). Не критично — decision rule використовує judge's `hallucination_rate` (claim-level), не context-drop count. Metadata залишає `claims_kept` (= len returned).

**Залишається:** `select_posts_for_v2()` + його 2 тести (post filtering — не зачіпається).

19.8b test count delta після revision: +2 (select_posts ×2) замість +4. Final pytest count для 19.8b: 152 → 154.

---

## Backward compatibility

Return type `list[Prediction]` незмінний. Existing callers:

| Caller | Вплив |
|---|---|
| `evaluate_detection.py` `classify_post` | `len(predictions) > 0` — працює. Behavior shift: post де всі claims мають invalid context класифікується як "no prediction". Task 13 artifact done, re-run опціональний. |
| `extraction_quality_eval.py` `run_stage1` | `[_serialize_prediction(p) for p in preds]` — працює. `_serialize_prediction` не включає context (Task 13.5 V1 — OK). Artifact done, не re-run. |

**Концептуальна note:** EXTRACTION_TEMPLATE (V2) тепер вимагає context від усіх моделей. Re-run Task 13/13.5 проганятиме всі extractions через context validation — consistent, але результати можуть зсунутися vs старі artifacts. Не блокер (artifacts immutable, re-run свідомий вибір).

---

## File list

**Modify:**
- `src/prophet_checker/analysis/extractor.py` — import + validation drop + context field
- `tests/test_analysis_extractor.py` — fixture (+context), +1 assertion, +2 drop tests
- `docs/verification-track/2026-05-14-task-19-8b-v2-extraction-rerun-plan.md` — Stage 1 revision (remove bypass)

**No new files.**

---

## Out of scope

- ❌ `_serialize_prediction` context field у extraction_quality_eval.py (19.8b plan concern)
- ❌ Re-run Task 13 / Task 13.5 на оновленому extractor
- ❌ Production orchestrator wiring (Task 20)
- ❌ Surfacing structured drop-count з extract() (return type stays `list[Prediction]`)
- ❌ Зміни у validate_context_in_post (вже landed у 19.8a)

---

## Tests summary

| Test | Status |
|---|---|
| `test_extract_returns_predictions` | Modified (fixture +context, +1 assertion) |
| `test_extract_no_predictions` | Unchanged |
| `test_extract_llm_error_returns_empty` | Unchanged |
| `test_extract_drops_prediction_with_hallucinated_context` | NEW |
| `test_extract_drops_prediction_with_missing_context` | NEW |

**Delta:** +2 tests. 150 → 152.

---

## Cross-references

- **Task 19.8a (schema + prompt + validator):** [`2026-05-14-task-19-8a-extraction-context-schema-design.md`](2026-05-14-task-19-8a-extraction-context-schema-design.md)
- **Task 19.8b (V2 run + fresh gold):** [`2026-05-14-task-19-8b-v2-extraction-rerun-design.md`](2026-05-14-task-19-8b-v2-extraction-rerun-design.md) — plan revised by this task
- **Extractor:** `src/prophet_checker/analysis/extractor.py`
- **Validator:** `validate_context_in_post` у `src/prophet_checker/llm/prompts.py`
