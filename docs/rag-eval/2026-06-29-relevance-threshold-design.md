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

## Передумова

Eval робить **живий vector-search по прод-корпусу** → потрібні embeddings у БД. **✅ Backfill уже виконано** — передумова задоволена, eval можна ганяти на проді одразу після збірки.

## Якість gold-питань — prediction-centric (data-передумова)

Поточний `retrieval/query_gold.json` (LLM-генерований через `build_query_gold.py`) має ~40% **форкастингових/фактичних** питань («чи звільнить Україна території до 2022?», «які навчання планували?») замість **ретроспективної перевірки прогнозу** («що прогнозували про звільнення територій до 2022?»). Корінь — промпт `build_query_prompt` оптимізував *findability*, а не інтент трекера, і має суперечливі приклади (один форкастинговий). Threshold-tuning на нереалістичних питаннях дав би **міскалібрований поріг**, тож це передумова A.

**Фікс (частина A):** переформулювати `build_query_prompt` на prediction-checking:
- рамка — користувач питає **РЕТРОСПЕКТИВНО**, що автор прогнозував (і чи справдилось); це трекер прогнозів, не оракул;
- заборонити форкастинг («чи станеться / звільнить / буде X?») і факти («що планували / відбулося»);
- акцент `claim_text` → «що [автор] прогнозував про [зміст] [період]?»; акцент `situation` → «які прогнози робив на тлі [обставини] [період]?»;
- prediction-centric запит лишається семантично близьким до прогнозу → retrieval-findability **не страждає**, реалізм зростає.

Ручні synthesis-питання (`generation/manual_questions.json`) **вже prediction-centric** — не чіпаємо.

**Регенерація (твоя інфра, LLM):** після фіксу промпту перегенерувати query_gold → каскадом generation-gold (single_source-питання = ці запити). За **конвенцією дат** (CLAUDE.md) нові файли дато-суфіксовані: `retrieval/query_gold_YYYY-MM-DD.json`, `generation/gold_YYYY-MM-DD.json`; консьюмери (`threshold_eval`, вхід `build_generation_gold`, `generation_eval`) беруть **явний дато-шлях** (константа/CLI), оновлений при регенерації; старі недато-файли лишаються (історичні). Далі threshold-sweep ганяється на реалістичних питаннях. Узгоджує інтент: питання ретроспективні ↔ відповідь ретроспективна (прогноз→вердикт).

## Рамка рішень

- **Об'єктив порога — trust-first:** max off-corpus-refusal за умови answerable retrieval-recall ≥ ~0.9. Краще зайва відмова, ніж впевнена відповідь на off-topic. Звіт — **повна крива** по всіх T; об'єктив лише обирає робочу точку.
- **Питання — prediction-centric:** ретроспективна перевірка прогнозу, не форкастинг/факт (див. секцію вище); промпт `build_query_gold.py` переформульовано + gold перегенеровано.
- **Gold — структура та сама, питання перегенеровані:** набір кейсів `generation/gold.json` (92 answerable з `expected_sources` + 20 off-corpus: 10 off_domain + 10 near_domain) лишається, але **single_source-питання перегенеровуються prediction-centric** (нова версія промпту, див. секцію вище) — стару форкастинг/факт-версію НЕ переюзуємо. synthesis (ручні) вже prediction-centric; off_domain/near_domain — refusal-проби (їхнє формулювання навмисне, не чіпаємо). A ганяється на дато-суфіксованій перегенерованій версії (`gold_YYYY-MM-DD.json`).
- **Поріг — у конфіг** (`Settings.relevance_threshold`); дефолт `None` = поточна поведінка (без порога). Eval передає `None` (сирий top-k для sweep); прод бере налаштоване.

## Eval — `scripts/rag/threshold_eval.py` (retrieval-only)

**Runner** (`eval_common.run_cases`): orchestrator збудований із `threshold=None`; на кожне gold-питання → `QueryOrchestrator.search(question, limit=N)` → `QueryResult` (ранжовані `RetrievedPrediction` з `distance`). Без генерації, без судді. N з запасом (напр. 20).

**Sweep** (`sweep_thresholds(runs, cases) -> ThresholdReport`).

`distance` — косинусна відстань (менше = ближче); поріг T — **максимальна збережена відстань**: при T лишаються matches з `distance ≤ T`. Збільшення T → проходить більше matches.

**Сітка T:** не довільний лінійний крок, а **відсортовані унікальні distance**, що реально спостерігаються в усіх matches (+ 0 і max). Метрики — східчасті функції T, що змінюються рівно на цих точках, тож така сітка дає **точну** криву без пропущених переходів.

**Метрики при кожному T** (denominator у дужках), окремо по category:
- **off-corpus refusal-rate(T)** = (off-corpus-кейси з **0** matches `≤ T`) / (усі off-corpus). Доповнення `1 − refusal-rate` = **false-answer** (відповіли на off-topic).
- **answerable answer-rate(T)** = (answerable з **≥1** match `≤ T`) / (усі answerable). Доповнення `1 − answer-rate` = **over-refusal** (відмовили валідному).
- **retrieval-recall(T)** = середнє по answerable від (**очікувані** джерела `expected_sources[].prediction.id`, що серед matches `≤ T`) / (усі очікувані цього кейса). single_source → бінарно 0/1; synthesis → частка знайдених із кількох очікуваних.

**answer-rate vs recall — ключова різниця:** answer-rate міряє «знайшлось **бодай якесь** джерело» (може бути НЕ те → відповідь з хибного контексту); recall міряє «знайшлось **саме очікуване**». Завжди `recall ≤ answer-rate`. Саме **recall** = «система може відповісти ПРАВИЛЬНО», тож він — в умові вибору, не answer-rate.

**Як рухаються по T:** малий T (суворо) → ↑off-corpus-refusal (добре), але ↓recall/answer-rate (over-refusal); великий T (м'яко) → ↑recall, але ↓off-corpus-refusal (false-answer/галюцинація). Sweep трасує цей компроміс — це і є крива.

**Розбивка по category дає діагностику:** single_source vs synthesis (recall для багатоджерельних — складніший); **off_domain** (явно off-topic, легко відмовити) vs **near_domain** (суміжне-але-непокрите — найжорсткіший дискримінатор: його refusal-rate при обраному T — реальний сигнал робастності порога).

**Вибір T (trust-first):** найбільший off-corpus-refusal-rate за умови `retrieval-recall ≥ 0.9`. Якщо умова недосяжна за жодного T — звіт це показує: значить **retrieval сам слабкий** (очікуване джерело не в top-N навіть без порога) → лагодити retrieval, не поріг; це сигнал для задачі B та можливого hybrid-search.

### Приклад (іграшковий)

5 кейсів; matches показано як `id@distance`, **очікувані** джерела жирним:

| кейс | category | matches (`id@dist`) | очікувані |
|------|----------|---------------------|-----------|
| a001 | single | **`p1@0.20`**, `p9@0.55` | p1 |
| a002 | single | `p7@0.30`, **`p2@0.45`**, `p5@0.60` | p2 |
| s001 | synthesis | **`p4@0.25`**, **`p6@0.48`**, `p8@0.70` | p4, p6 |
| o001 | off_domain | `p3@0.85` | — |
| n001 | near_domain | `p5@0.52` | — |

Метрики при трьох порогах:

| T | a001 | a002 | s001 | o001 | n001 | answer-rate | recall | off-refusal |
|------|------|------|------|------|------|:---:|:---:|:---:|
| 0.40 | recall 1 | `p7` лише → відповідь, recall 0 | `p4` лише → recall ½ | відмова ✓ | відмова ✓ | 1.0 | **0.50** | 1.0 |
| **0.48** | recall 1 | `p2` ✓ recall 1 | `p4,p6` ✓ recall 1 | відмова ✓ | відмова ✓ | 1.0 | **1.0** | **1.0** |
| 0.52 | recall 1 | recall 1 | recall 1 | відмова ✓ | `p5` ≤T → **ВІДПОВІДЬ** (false-answer) | 1.0 | 1.0 | **0.50** |

Читання:
- **T=0.40 — надто суворо:** off-refusal 1.0, але recall лише 0.5. a002 знайшло тільки чуже `p7` (`answer-rate` рахує «відповіли», `recall` — ні: очікуване `p2@0.45` не пройшло), s001 знайшло лише `p4` (1 з 2). recall <0.9 → trust-first відкидає.
- **T=0.48 — солодка точка:** recall 1.0 **і** off-refusal 1.0. Усі очікувані (макс `@0.48`) пройшли; near-дистрактор `n001@0.52` ще за порогом.
- **T=0.52 — задалеко:** recall не зріс (вже 1.0), а near_domain `n001@0.52` протік → false-answer, off-refusal впав до 0.5.

→ trust-first обирає **T=0.48** (мінімальний T із recall≥0.9; off-refusal при цьому максимальний).

> Якби near-дистрактор був **ближчим за очікуване джерело** (напр. `n001@0.46` < `s001 p6@0.48`) — **жоден** T не дав би одночасно recall≥0.9 і відмову на near. Звіт показав би цей конфлікт прямо → сигнал, що retrieval не розрізняє суміжне (лагодити retrieval/репрезентацію, а не поріг).

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
- **`build_query_prompt`** — guard-тест: промпт містить prediction-checking рамку + приклади й НЕ містить форкастингових директив (регрес-запобіжник, як guard у RAG-промпті).
- **CLI** — без юніту (інтеграція; ручний прогін на проді з backfill).

## Скоуп

**In:** переформулювання query-gen промпту (`build_query_gold.py`) на prediction-centric + регенерація `query_gold`/`generation/gold.json`; `scripts/rag/threshold_eval.py` (sweep + вибір + звіт); прод-поріг (`Settings` + `QueryOrchestrator`); unit-тести; виставлення налаштованого значення в конфіг.

**Out (deferred):**
- end-to-end якість (refusal/faithfulness/e2e-recall на живому answer) — задача [B](2026-06-29-rag-e2e-eval-design.md);
- розширення off-corpus gold (зараз 20 — мало для робастного порога) — окремий data-крок, якщо крива шумна;
- hybrid-search / метадані-фільтри (Phase 2);
- автоматичне виставлення порога (зараз ручний крок за звітом).

## Зв'язок

- Парк-джерело: чип `task_a358c756`.
- Наступна задача (споживач порога): [end-to-end RAG-eval (B)](2026-06-29-rag-e2e-eval-design.md).
- gold: [generation-eval v2](../generation/2026-06-27-generation-eval-v2-design.md). Каркас: [`eval_common`](../eval-framework/2026-06-25-eval-pipeline-design.md).
