# Relevance threshold (A) — Design

**Дата:** 2026-06-29
**Status:** 📋 designed — pre-implementation
**Контур:** перша з двох задач розпарку `task_a358c756` (друга — [end-to-end RAG-eval (B)](2026-06-29-rag-e2e-eval-design.md)).
**Спирається на:** [generation gold](../generation/2026-06-27-generation-eval-v2-design.md), [retrieval-eval](../retrieval-eval/2026-06-19-retrieval-eval-design.md), [`eval_common`](../eval-framework/2026-06-25-eval-pipeline-design.md).

---

## Мета

Зробити refusal **детермінованим retrieval-рішенням**. Зараз `QueryOrchestrator.search` повертає top-k без порога → на off-topic питання система отримує найближчі-але-нерелевантні прогнози й покладається на self-refusal Gemini (недетерміновано). **Поріг** на `distance` обрізає слабкі матчі → для off-topic нічого не лишається → `REFUSAL_NO_DATA`.

Два деліверабли:
1. **Офлайн-eval** (`scripts/rag/threshold_eval.py`) — sweep порогів на gold → рекомендоване значення + крива + перший вимір **абсолютного** retrieval-recall.
2. **Прод-механізм** (`src/`) — `Settings.relevance_threshold` + фільтр у `QueryOrchestrator.search`; виставити налаштоване значення.

## Передумова (критична)

Eval робить **живий vector-search по прод-корпусу** → потрібні **backfill embeddings** (історично всі `embedding IS NULL`). Backfill-скрипт ідемпотентний (`is_embedding_present`); прогнати на проді — крок 0.

## Рамка рішень

- **Об'єктив порога — trust-first:** max off-corpus-refusal за умови answerable retrieval-recall ≥ ~0.9. Краще зайва відмова, ніж впевнена відповідь на off-topic. Звіт — **повна крива** по всіх T; об'єктив лише обирає робочу точку.
- **Переюз gold:** наявний `generation_gold.json` (92 answerable з `expected_sources` + 20 off-corpus: 10 off_domain + 10 near_domain).
- **Поріг — у конфіг** (`Settings.relevance_threshold`); дефолт `None` = поточна поведінка (без порога). Eval передає `None` (сирий top-k для sweep); прод бере налаштоване.

## Eval — `scripts/rag/threshold_eval.py` (retrieval-only)

**Runner** (`eval_common.run_cases`): orchestrator збудований із `threshold=None`; на кожне gold-питання → `QueryOrchestrator.search(question, limit=N)` → `QueryResult` (ранжовані `RetrievedPrediction` з `distance`). Без генерації, без судді. N з запасом (напр. 20).

**Sweep** (`sweep_thresholds(runs, cases) -> ThresholdReport`): для сітки T рахуємо, з розбивкою по category:
- **off-corpus refusal-rate(T)** = частка off-corpus із 0 matches `distance ≤ T`;
- **answerable answer-rate(T)** = частка answerable із ≥1 match `≤ T`;
- **retrieval-recall(T)** = частка answerable, де **очікуване** джерело (`expected_sources[].prediction.id`) серед matches `≤ T`.

**Вибір T:** найбільший off-corpus-refusal-rate за умови `retrieval-recall ≥ 0.9`. Якщо умова недосяжна за жодного T — звіт це показує (retrieval сам слабкий → лагодити retrieval, не поріг; це й сигнал для задачі B та можливого hybrid-search).

**Без scorer-ів:** sweep — агрегатна операція над усіма distance, не per-case вердикт. Тож A = `run_cases` + чиста `sweep_thresholds` + markdown/JSON-звіт. Повністю детерміновано (ембединги фіксовані, нуль LLM).

**Вихід:** `ThresholdReport` — крива {T → метрики by_category} + обраний `relevance_threshold`.

## Прод-механізм (`src/`)

- `Settings.relevance_threshold: float | None = None` (None = поточний top-k без порога).
- `QueryOrchestrator` приймає `relevance_threshold` (через `factory` із `Settings`); `search` після `search_similar` **відкидає matches з `distance > threshold`**. Порожньо → `QueryResult.results == []` → `AnswerOrchestrator` → `REFUSAL_NO_DATA`.
- Сигнатури не ламаються (поріг опціональний, дефолт `None`).
- Послідовність: спершу механізм (дефолт `None` = no-op), потім eval-прогін → значення → виставити в конфіг.

## Потік даних та краї

| Ситуація | Поведінка |
|----------|-----------|
| embeddings не backfill'нуті | retrieval порожній → усе «відмова» → звіт явно деградований (передумова не виконана) |
| off-corpus, 0 matches ≤ T | правильна відмова |
| answerable, очікуване не в top-N | retrieval-recall miss (сигнал слабкого retrieval) |
| усі distance > T (надто суворий поріг) | over-refusal — видно на кривій |

## Тестування

- **`sweep_thresholds`** — unit на фікстурах (синтетичні runs+distances): refusal-rate / answer-rate / retrieval-recall на відомих порогах; вибір T за trust-first-правилом; крайові (усі ≤ T; усі > T; recall ≥0.9 недосяжний).
- **`QueryOrchestrator` threshold** — unit (FakeVectorStore): matches з `distance > threshold` відкидаються; `None` → без фільтра (поточна поведінка незмінна).
- **CLI** — без юніту (інтеграція; ручний прогін на проді з backfill).

## Скоуп

**In:** `scripts/rag/threshold_eval.py` (sweep + вибір + звіт); прод-поріг (`Settings` + `QueryOrchestrator`); unit-тести; виставлення налаштованого значення в конфіг.

**Out (deferred):**
- end-to-end якість (refusal/faithfulness/e2e-recall на живому answer) — задача [B](2026-06-29-rag-e2e-eval-design.md);
- розширення off-corpus gold (зараз 20 — мало для робастного порога) — окремий data-крок, якщо крива шумна;
- hybrid-search / метадані-фільтри (Phase 2);
- автоматичне виставлення порога (зараз ручний крок за звітом).

## Зв'язок

- Парк-джерело: чип `task_a358c756`.
- Наступна задача (споживач порога): [end-to-end RAG-eval (B)](2026-06-29-rag-e2e-eval-design.md).
- gold: [generation-eval v2](../generation/2026-06-27-generation-eval-v2-design.md). Каркас: [`eval_common`](../eval-framework/2026-06-25-eval-pipeline-design.md).
