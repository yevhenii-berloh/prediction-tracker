# End-to-end RAG eval (B) — Design

**Дата:** 2026-06-29
**Status:** 📋 designed — **PARKED** (друга задача; потребує порога з [Relevance threshold (A)](2026-06-29-relevance-threshold-design.md)).
**Спирається на:** A (поріг + backfill), [generation-eval v2](../generation/2026-06-27-generation-eval-v2-design.md) (scorer-и, gold), [`eval_common`](../eval-framework/2026-06-25-eval-pipeline-design.md).

---

## Мета

Виміряти якість **справжнього прод-шляху** end-to-end. Ізольований generation-eval довів: дано правильний контекст — генерація ~0.98. Але в проді контекст обирає retrieval. B ганяє реальний `AnswerOrchestrator.answer` (жива retrieval із налаштованим порогом → генерація) і міряє результат.

## Залежності (чому парк)

- **Поріг із A** — щоб refusal був детермінованим (інакше B покладається на self-refusal Gemini). Тож B стартує **після** A.
- **Backfill embeddings** на проді (як в A) — жива retrieval.

## Eval — `scripts/rag/e2e_eval.py`

**Runner:** `AnswerOrchestrator.answer(question, limit)` — жива retrieval **із порогом A** → генерація → `AnswerResult` (відповідь + знайдені sources, або `REFUSAL_NO_DATA`). Gold — той самий `generation_gold.json` (повні 112: answerable + off-corpus refusal-кейси знову в грі).

**Scorer-и** (`eval_common`, суддя Claude Opus):
- **RefusalScorer** — воскрешаємо з generation-eval v1: off-corpus → має відмовити; answerable → має відповісти. Дає refusal-accuracy / over-refusal / false-answer.
- **FaithfulnessScorer** — переюз generation-eval як є: claim-и відповіді проти **знайдених** `run.result.sources` (з фіксом status-авторитету). «Чи генерація не вигадала поза поданим».
- **CompletenessScorer (e2e-варіант, vs gold):** проти **`labels.expected_sources`** (не знайдених) → **end-to-end recall**: скільки з того, що МАЛО бути покрито, фінальна відповідь донесла. Свідомо конфаундовано retrieval-ом (не знайшов джерело → не покриє) — це й є сенс end-to-end. Відрізняється від v2-completeness (та судить **подане**); тож B-варіант — параметр джерела `gold` замість `fed`.

**Метрики `RagE2EMetrics`:** `n_total`, `n_errors`, `refusal_accuracy`, `over_refusal_rate`, `false_answer_rate`, `faithfulness_mean`, `hallucination_rate`, `end_to_end_recall_mean`, `by_category`.

## Декомпозиція (навіщо окремо від A)

`A.retrieval_recall` (retrieval **знайшов** очікуване) vs `B.end_to_end_recall` (відповідь **донесла** очікуване). Розрив = втрата на генерації. Якщо `A.retrieval_recall` низький — пляшкове горло саме retrieval, і B-якість обмежена згори. Так два числа локалізують втрату.

## Потік даних та краї

| Ситуація | Поведінка |
|----------|-----------|
| off-corpus, поріг відсік усе | `REFUSAL_NO_DATA`, refusal correct |
| answerable, очікуване не знайшлося | end-to-end recall падає (retrieval-винний; зіставити з A.retrieval_recall) |
| LLM/суддя впав | `EvalRun(result=None)` → scorer-и N/A; `n_errors++` |

## Тестування

- **RefusalScorer** — unit (FakeJudge / hard-path `REFUSAL_NO_DATA`): off vs answerable × refused vs answered.
- **CompletenessScorer e2e-варіант** — unit: судить проти `labels.expected_sources` (не fed); guard N/A коректно.
- **FaithfulnessScorer** — без нового тесту (переюз як є).
- **CLI** — без юніту (інтеграція; ручний прогін на проді з порогом A + backfill).

## Скоуп

**In:** `scripts/rag/e2e_eval.py` (живий answer + refusal/faithfulness/e2e-recall); e2e-варіант CompletenessScorer (джерело `gold`); воскресіння RefusalScorer; unit-тести.

**Out (deferred):**
- усе з A (поріг, прод-механізм) — окрема задача [A](2026-06-29-relevance-threshold-design.md);
- формальне κ-калібрування судді (окремий трек);
- hybrid-search / метадані-фільтри (Phase 2).

## Зв'язок

- Передумова: [Relevance threshold (A)](2026-06-29-relevance-threshold-design.md).
- Парк-джерело: чип `task_a358c756`.
- scorer-и/gold: [generation-eval v2](../generation/2026-06-27-generation-eval-v2-design.md). Каркас: [`eval_common`](../eval-framework/2026-06-25-eval-pipeline-design.md).
