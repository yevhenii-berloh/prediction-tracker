# Task 19.9 — Split Verifier (2-call) — Design

**Дата:** 2026-05-31
**Статус:** Spec ready
**Залежності:** 19.8d (situation field), 19.7b (eval + model decision). Розблоковує Task 20.

---

## Мета

Production-верифікатор для verifier-v2, що оцінює прогноз **двома окремими LLM-викликами**
замість одного, і об'єднує результат у єдиний dict, який Task 20 orchestrator мапить на
`Prediction`. Модель — `gemini/gemini-3.1-flash-lite-preview` (залочена у 19.7b).

## Чому 2 виклики (емпірична основа)

Повна історія тюнінгу: `../19-7b-verification-eval/prompt-history.md`. Стисло:

Single-call впирається в **інхерентний tradeoff** — фреймінг, що робить `prediction_strength`
якісним ("high = RARE, most commentary is low"), робить модель скептичною і **протікає у status**
(firm-status 0.833 → 0.667). Тобто в одному виклику можна мати щонайбільше 2 з 3 полів якісними.

**Декомпозиція розриває tradeoff:** кожен виклик отримує свій оптимальний фреймінг без
cross-contamination. Валідовано детерміновано (temp=0, два ідентичні прогони → 0/32 розбіжностей)
на 32 gold claims:

| Архітектура | firm-status | strength | value |
|---|---|---|---|
| single-call V3 | 0.833 | 0.469 (шум) | 0.844 |
| single-call V5 | 0.667 | 0.719 | 0.844 |
| **2-call (verdict + assessment)** | **0.833** | **0.719** | **0.844** |

2-call досягає максимуму **всіх трьох** полів одночасно.

> **Примітка про value 0.844 vs 0.812:** число 0.844 у таблиці — з валідації config-A, що
> використовувала **оригінальний** V3-промт. Його точний текст було перезаписано під час
> експериментів V4–V7, тож реконструкція дає value 0.812 (−1 пункт). Production-текст verdict-промту
> фіналізується у плані зі spot-test; ціль value — у діапазоні 0.81–0.84.

**Чому verdict-виклик тримає обидва strength+value (а не лише status):**
ізоляційний тест (той самий промт, лише strength-output присутній/відсутній):

| verdict-промт | firm-status | value |
|---|---|---|
| V3-FULL (зі strength-output) | 0.833 | 0.812 |
| V3 без strength-output | 0.750 | 0.812 |

Видалення strength-output **не торкається value**, але **роняє status 0.833 → 0.750**. Тобто
сам акт оцінки strength допомагає моделі краще міркувати про verdict. Аналогічно value (винесення
value у call 2 роняло status 0.833 → 0.750). Тому **verdict-виклик містить обидва strength+value
outputs**, хоча його strength (плаский, 0.469) відкидається на користь strength з call 2.

Ключове розрізнення: **плаский** strength у call 1 (V3: "high = concrete falsifiable") допомагає
status; **агресивний** strength-фреймінг ("high = RARE") псує status і тому ізольований у call 2.

## Архітектура

Два LLM-виклики на прогноз, об'єднані в один result dict:

- **Call 1 — verdict** (`get_verification_system_v2`, чистий V3): повний 8-output промт. Беремо
  `status, confidence, prediction_value, reasoning, evidence, retry_after, max_horizon`.
- **Call 2 — assessment** (новий `get_assessment_system_v2`): strength+value промт з orthogonality
  block + high=RARE strength + value rubric + reasoning-first. Беремо лише `prediction_strength`.
- **Merge:** verdict dict з перевизначеним `prediction_strength` із call 2. Форма результату —
  така сама, як уже повертає `parse_verification_response_v2`, тож Task 20 мапить її на
  `Prediction` без змін.
- **`Verifier` клас** (`analysis/verifier.py`, відновлений) тримає `LLMClient`, виконує обидва
  виклики паралельно (`asyncio.gather`), робить merge.

```
Task 20 orchestrator
   └─ Verifier(llm).verify(claim, situation, prediction_date, target_date, today)
        ├─ build_verification_prompt_v2(...)            # один user-промт на обидва виклики
        ├─ asyncio.gather(
        │     llm.complete(user, system=get_verification_system_v2(today)),   # verdict
        │     llm.complete(user, system=get_assessment_system_v2(today)))     # assessment
        ├─ verdict = parse_verification_response_v2(verdict_raw)
        ├─ assessment = parse_assessment_response_v2(assess_raw)
        ├─ verdict["prediction_strength"] = assessment["prediction_strength"]
        └─ return verdict   # {status, confidence, prediction_strength, prediction_value,
                            #   reasoning, evidence, retry_after, max_horizon}
```

## Компоненти та файли

### `src/prophet_checker/llm/prompts.py`

- `VERIFICATION_SYSTEM_V2` — **відкотити** з некомітнутого V7-експерименту до чистого V3
  (verdict-промт). Це також прибирає working-tree безлад після тюнінгу V4–V7. Структура V3:
  V2-style status-визначення, проста confidence, плаский strength (V2-формулювання), value rubric
  (детальна), reasoning, evidence, retry_after, max_horizon. Має `{today}`.
- `ASSESSMENT_SYSTEM_V2` (новий) — промт strength+value: orthogonality block ("strength = HOW
  phrased; value = HOW MUCH matters") + strength "high = RARE … MOST political commentary is low"
  + value rubric + reasoning-first. Outputs: `reasoning, prediction_strength, prediction_value`.
  Має `{today}`.
- `get_assessment_system_v2(today: str) -> str` (новий) — дзеркалить `get_verification_system_v2`.
- `parse_assessment_response_v2(response: str) -> dict` (новий) — strip code-fence + `json.loads`;
  вимагати `prediction_strength`; enum-валідація {low, medium, high}; повернути
  `{"prediction_strength": ...}` (value/reasoning з call 2 ігноруються). Кидає `ValueError` на
  відсутність/невалідне значення.
- Перевикористати `VERIFICATION_TEMPLATE_V2` + `build_verification_prompt_v2` для **обох**
  викликів (однаковий user-промт). `parse_verification_response_v2` — без змін.

### `src/prophet_checker/analysis/verifier.py` (новий)

```python
class Verifier:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def verify(self, claim, situation, prediction_date, target_date, today) -> dict:
        user = build_verification_prompt_v2(
            claim=claim, prediction_date=prediction_date,
            target_date=target_date, today=today, situation=situation)
        verdict_raw, assess_raw = await asyncio.gather(
            self._llm.complete(user, system=get_verification_system_v2(today)),
            self._llm.complete(user, system=get_assessment_system_v2(today)))
        verdict = parse_verification_response_v2(verdict_raw)
        assessment = parse_assessment_response_v2(assess_raw)
        verdict["prediction_strength"] = assessment["prediction_strength"]
        return verdict
```

- `src/prophet_checker/analysis/__init__.py` — експортувати `Verifier`.

## Потік даних та обробка помилок

- Task 20 → `Verifier(llm).verify(...)` → 2 паралельні виклики → merged dict (та сама 8-польова
  форма) → orchestrator мапить на `Prediction` (status, confidence, evidence_text,
  prediction_strength, prediction_value, max_horizon, next_check_at ← retry_after, тощо).
- **All-or-nothing:** `verify` не ловить винятки — будь-яка parse/infra помилка в будь-якому з
  викликів пропагується. Task 20 ловить, записує `last_verify_error` + інкрементує
  `verify_attempts` (це відповідальність Task 20, не цього спеку).
- **Без змін БД/домену:** `Prediction` уже містить усі поля (19.5 + PredictionValue + 19.8d).

## Тестування

- `tests/test_llm_prompts.py` (додати):
  - `parse_assessment_response_v2`: happy-path; невалідний enum → `ValueError`; відсутній
    `prediction_strength` → `ValueError`; code-fence strip.
  - `get_assessment_system_v2` інжектить `today`.
- `tests/test_analysis_verifier.py` (новий):
  - `Verifier.verify` зі stub `LLMClient`, що повертає різні canned JSON залежно від `system`
    (verdict vs assessment) → assert merge: status/value з call 1, strength перевизначений із
    call 2.
  - All-or-nothing: невалідний assessment-response → `verify` кидає.
  - Stub розрізняє виклики за маркером у system-промті (стабільно за умови `asyncio.gather`).
- Точний текст verdict + assessment промтів фіналізується у **плані** з підтверджувальним
  spot-test (~$0.006) на ціль firm-status 0.833 / strength 0.719 / value ~0.81–0.84.

## Скоуп

**У скоупі:** 2 промти + новий parser + `Verifier` клас + tests + відкат `VERIFICATION_SYSTEM_V2`
до V3.

**Поза скоупом:**
- Task 20 orchestrator (вибірка з БД, batch, write-back, urgency triggers) — окремий спек.
- Міграція `verification_eval.py` на 2-call — не потрібна (Flash Lite залочено, eval своє відпрацював).
- Зміни БД/домену — не потрібні.

## Очікувані метрики (Flash Lite, 32 gold)

firm-status **0.833** · strength **0.719** · value **~0.81–0.84** — максимум усіх трьох полів,
недосяжний для single-call. Latency ~1.2s (паралельні виклики). Вартість ~$0.006/прогноз.
