# Історія модифікацій verification prompt (VERIFICATION_SYSTEM_V2)

**Контекст:** підбір production-промту для verifier-v2. Всі версії тестуються spot-run
одної моделі `gemini/gemini-3.1-flash-lite-preview` проти fresh gold (32 Arestovich
claims, `scripts/data/verification_gold_labels.json`, today=2026-05-23). Spot-run ≈ $0.003,
~1 хв.

> **Прогони детерміновані.** Eval ганяє temperature=0.0; два прогони ІДЕНТИЧНОГО промту
> дали **0/32** розбіжностей. Тобто всі відмінності між версіями — **реальні відтворювані
> ефекти зміни промту, а не sampling noise.**

## Gold baseline (для інтерпретації цифр)

| Поле | Розподіл gold (n=32) | Тривіальний baseline |
|---|---|---|
| status | 9 unresolved / 8 confirmed / 4 refuted / 11 premature | — |
| strength | 22 low / 10 medium / 0 high | always-low = **0.688** |
| value | 14 medium / 14 high / 4 low | — |

> **Strength:** accuracy **нижче 0.688 гірша за константу "завжди low"** — поле стає шумом.
>
> **Firm-gold status:** з 32 пунктів лише **12 мають gold-confidence > 0.55** (firm); решта
> 20 — borderline (ми самі непевні). Розбіжність моделі на borderline — слабкий сигнал.
> Тому головна метрика рішення — **firm-status** (accuracy лише на 12 firm пунктах).

## Зведена таблиця (всі цифри детерміновані)

| Версія | firm-status (n=12) | all-status | strength | value | Вердикт |
|---|---|---|---|---|---|
| V2 (baseline) | — | 0.625 | 0.562 | 0.438 | value зламаний (Gemini завжди "high") |
| V3 (+value rubric) | **0.833** | 0.625 | 0.469 | **0.844** | найкращий status+value; strength = шум |
| V4 (+6 fixes) | 0.667 | **0.344** ⛔ | **0.719** | 0.844 | strength fix, але status завалився (fail blocker) |
| V5 (V4 − status-killers) | 0.667 | 0.531 | **0.719** | 0.844 | тримає strength+value; 2 зайві firm confirmed-промахи |
| V6 (V5 − confidence timidity) | 0.667 | 0.594 | 0.688 | 0.750 | **dominated by V5** |
| V7 (V3 + лише strength-fix) | 0.667 | 0.562 | 0.656 | 0.719 | **dominated by V5** — довів, що tradeoff inherent |

---

## V2 — baseline (Task 19.5 foundation)

**Що це:** оригінальний EIGHT-output промт. Порядок: status(1) → confidence(2) →
strength(3) → value(4) → reasoning(5) → evidence(6) → retry_after(7) → max_horizon(8).
Проста секція value (3 приклади). Без CoT.

**Навіщо:** базова реалізація V2 schema-промту.

**Оцінка (Flash Lite):** status 0.625, strength 0.562, value **0.438**.

**Проблема:** value зламаний — Gemini завжди віддає `"high"`, мітку `"low"` не вживає.

---

## V3 — додано value rubric

**Навіщо:** полагодити зламаний value (0.438) — дати моделі явні критерії для low/medium.

**Diff (секція `prediction_value`):**
```diff
 4) prediction_value — assess the IMPORTANCE/RESONANCE of the predicted event ...
-   "high"   — major strategic/political/economic shift; widely consequential.
-              Example: "війна закінчиться у 2026" — outcome reshapes a region.
-   "medium" — moderate consequence; affects a sector, region, or institution.
-              Example: "новий уряд буде сформований до травня".
-   "low"    — minor or routine event; limited real-world resonance.
-              Example: "дипломати зустрінуться наступного тижня".
+   "high"   — outcome reshapes a country, region, or balance of power.
+              NOT high: process announcements, logistical events,
+              announcements of intent within an ongoing conflict.
+   "medium" — affects a sector, region, institution, or specific subgroup ...
+   "low"    — process/logistical/descriptive within a larger context;
+              tautology; calendar-bound certainty; announcement of intent;
+              vague slogan. Examples: "дипломати зустрінуться", "45 евакуаційних
+              автобусів поїдуть з міста", ...
```
*(V3 не комітився у git — transient working-tree edit, згодом перекритий V4. Показано
фінальний вигляд value-rubric як у V4, який еквівалентний.)*

**Оцінка:** status 0.625, strength **0.469**, value **0.844**.

**Результат:** value полагоджено (0.438 → 0.844). АЛЕ strength протік 0.562 → 0.469
(багатослівна value-rubric змусила модель плутати важливість теми з якістю формулювання) —
**нижче baseline 0.688 → strength став шумом.**

---

## V4 — 6 фіксів (reasoning-first + vagueness + strength/value orthogonality)

**Навіщо:** V3 мав (а) strength-леакедж, (б) status-стелю. FIX 3+4 → strength,
FIX 1+2+6 → status, FIX 5 → калібрування.

**Diff (поверх V3, без value-секції):**
```diff
-Determine EIGHT outputs (all required in JSON response):
-
-1) status — exactly one of: ...
+═══════════════════════════════════════════════════════════════════
+HOW TO REASON (think step by step BEFORE assigning any verdict):
+STEP A — Identify the EXACT assertion. Verify what the claim literally
+   states, NOT a downstream consequence. ...                          # FIX 2
+STEP B — Is the claim falsifiable? If fundamentally vague ... it can
+   NEVER be confirmed or refuted — re-checking will not help.
+STEP C — Has the timeframe / trigger passed? ...
+═══════════════════════════════════════════════════════════════════
+EIGHT outputs (all required in JSON response):
+1) reasoning — 1-3 sentences. Fill this FIRST. ...                    # FIX 6
+2) status — exactly one of: ...
+   VAGUENESS RULE: a fundamentally vague claim is "unresolved", NOT
+   "premature" — EVEN IF the topic is still unfolding. ...            # FIX 1
-2) confidence — 0.0 to 1.0
-   Your certainty in the verdict.
+3) confidence — 0.0 to 1.0, calibrated to evidence quality:          # FIX 5
+   0.9-1.0 concrete record / 0.6-0.8 partial / 0.3-0.5 weak.
+   Do NOT default to high confidence. unresolved/premature ≤ 0.6.
+───────────────────────────────────────────────────────────────────
+prediction_strength and prediction_value are INDEPENDENT axes:        # FIX 4
+   - strength = HOW the claim is phrased   - value = HOW MUCH it matters
-3) prediction_strength — assess the CLAIM ITSELF: high/medium/low ...
+4) prediction_strength — HOW the claim is phrased (NOT how important): # FIX 3
+   "high" — RARE. Explicit numeric/dated threshold ...
+   "low"  — ... MOST political commentary is low.
+  (JSON reordered: "reasoning" first)                                 # FIX 6
```

**Оцінка:** status **0.344** ⛔, strength **0.719**, value 0.844.

**Результат:** strength полагоджено (0.469 → 0.719, найкраще). АЛЕ status обвалився
0.625 → 0.344 (fail blocker). VAGUENESS RULE + STEP A/B скинули 20/32 у `unresolved`,
з'їли клас `premature` (10→unresolved) і блокували `confirmed`. Непридатне.

---

## V5 — хірургічне видалення status-killers

**Навіщо:** ізолювати виграші strength+value від шкоди по status (vagueness rule + scaffold).

**Diff (поверх V4) — 3 правки:**
```diff
-═══════════════════════════════════════════════════════════════════
-HOW TO REASON (think step by step BEFORE assigning any verdict):
-STEP A — Identify the EXACT assertion. ...
-STEP B — Is the claim falsifiable? ...
-STEP C — Has the timeframe / trigger passed? ...
-═══════════════════════════════════════════════════════════════════
-EIGHT outputs (all required in JSON response):
-1) reasoning — 1-3 sentences. Fill this FIRST.
-   State the exact assertion, whether it is falsifiable, and your verdict logic.
+═══════════════════════════════════════════════════════════════════
+EIGHT outputs (all required in JSON response). Fill "reasoning" FIRST,
+then decide the rest:
+1) reasoning — 1-3 sentences.
+   State the exact assertion, then your verdict logic for status, strength, and value.

   "premature" — ... We should retry later.
-   VAGUENESS RULE: a fundamentally vague claim is "unresolved", NOT
-   "premature" — EVEN IF the topic is still unfolding. ...

-  "reasoning": "1-3 sentences: exact assertion, falsifiability, verdict logic",
+  "reasoning": "1-3 sentences: exact assertion + verdict logic",
```

**Оцінка:** status 0.531 (firm **0.667**), strength **0.719**, value **0.844**.

**Результат:** status відновлено з 0.344, виграші strength+value втримано. Але на firm-gold
робить **2 зайві помилки проти V3** — усі типу `confirmed → unresolved/refuted` (напр.
7683:1 "електроенергія відсутня годинами, фронт тріщить", gold conf=0.85 → V5 `refuted`).
Модель надто скептична визнавати `confirmed`.

---

## V6 — видалено confidence timidity clause

**Навіщо:** гіпотеза — 2 firm `confirmed`-промахи V5 спричинені clause "Do NOT default to
high confidence". Спроба повернути ці вердикти.

**Diff (поверх V5) — 1 правка:**
```diff
 3) confidence — 0.0 to 1.0, calibrated to evidence quality:
    0.9-1.0 ... / 0.6-0.8 ... / 0.3-0.5 ...
-   Do NOT default to high confidence. unresolved/premature usually ≤ 0.6.
+   When a clear public record settles the claim, confidence SHOULD be high.
```

**Оцінка:** status 0.594 (firm **0.667**), strength 0.688, value **0.750**.

**Результат:** **гіпотеза не підтвердилась.** firm-status той самий 0.667 — ті самі 4 firm
помилки, що й у V5 (confirmed-промахи НЕ повернулись). Приріст all-status (0.531→0.594) — лише
на borderline-gold (слабкий сигнал). Натомість value впав 0.844 → 0.750 (реальний, не шум).
**V6 програє V5: однаковий firm-status, гірший value І strength → V6 відкинуто.**

---

## Висновок (детермінований, на firm-gold)

**Робастні, locked-in знахідки:**

| Знахідка | Ефект | Дія |
|---|---|---|
| value rubric (V2→V3) | value 0.438 → 0.844 | **залишити** |
| strength fix FIX 3+4 (V3→V4) | strength 0.47 → 0.72 (below→above baseline) | дає usable strength |
| vagueness rule FIX 1 | status 0.625 → 0.344 | **тримати поза промтом** |
| confidence timidity (V6) | value 0.844 → 0.750, status не виправив | **тримати поза промтом** |

**Реальний вибір — V3 vs V5** (V4, V6 відкинуті; обидва value 0.844):

| | firm-status | strength | пояснення |
|---|---|---|---|
| **V3** | **0.833** (2 firm errors) | 0.469 = шум | найкраще ловить `confirmed`; strength некорисний |
| **V5** | 0.667 (4 firm errors) | 0.719 = usable | usable strength ціною 2 пропущених `confirmed` |

Tradeoff: 2 зайві firm `confirmed`-промахи (V5) проти usable strength-поля.
Для prediction-tracker правильне визначення `confirmed` — core-функція; strength — метадані.

---

## V7 — best-of-all-worlds спроба (V3 + лише strength-fix)

**Навіщо:** перевірити, чи 2 confirmed-промахи спричинені reasoning-first / confidence-scale
(тоді їх відкат поверне status). V7 = V3 + strength-fix (high=RARE + orthogonality),
reasoning назад на #5, confidence → V2-simple.

**Diff (поверх V5):**
```diff
-EIGHT outputs ... Fill "reasoning" FIRST ...
-1) reasoning — 1-3 sentences. State the exact assertion ...
-2) status — ...
-3) confidence — 0.0 to 1.0, calibrated ... (anchored scale)
+Determine EIGHT outputs ...
+1) status — ... (V2 original wording)
+2) confidence — 0.0 to 1.0. Your certainty in the verdict.   # V2-simple
 ... [orthogonality + strength high=RARE + value rubric — ЗАЛИШЕНО] ...
+5) reasoning — 1-3 sentences. Explain the verdict, strength, and value.  # назад на #5
  (JSON: reasoning назад на 5-у позицію)
```

**Оцінка:** firm-status **0.667**, strength **0.656**, value **0.719**.

**Результат:** **best-of-all-worlds неможливий.** firm-status НЕ відновився (0.667, ті самі
confirmed-промахи) — отже їх причина саме **strength-fix** (єдине, що V4–V7 мають, а V3 ні).
До того ж V7 гірший за V5 і на strength (0.719→0.656), і на value (0.844→0.719) → доводить,
що **reasoning-first CoT реально допомагає** strength+value. V7 dominated by V5 → відкинуто.

---

## Фінальний висновок

**Доведено детерміновано:**
- **strength-fix (high=RARE + orthogonality) ⟹ −2 firm `confirmed`-вердикти.** Інхерентний
  tradeoff: usable strength неможливий без втрати status. (V3 vs V7 — єдина різниця strength-fix,
  firm-status 0.833 → 0.667.)
- **reasoning-first CoT допомагає** strength+value (V5 > V7). Залишати.
- **vagueness rule** (FIX 1) і **confidence timidity** (V6) — шкідливі, тримати поза промтом.

**Реальний фінальний вибір — V3 vs V5** (обидва value 0.844; V4/V6/V7 dominated):

| | firm-status | strength | для продукту |
|---|---|---|---|
| **V3** | **0.833** (2 firm errors) | 0.469 = шум (нижче baseline) | найкраще ловить `confirmed` — core-функція tracker'а |
| **V5** | 0.667 (4 firm errors) | 0.719 = usable | usable strength ціною 2 пропущених `confirmed` |

**Рекомендація (для single-call): V3.** Для prediction-tracker правильне визначення
`confirmed`/`refuted` — це сам продукт; strength — другорядні метадані.

---

## Декомпозиція на 2 виклики — розв'язує tradeoff

**Ідея:** причина tradeoff — cross-contamination в одному виклику (strength-фреймінг
"high=RARE" робить модель скептичною → протікає у status). Якщо рознести outputs на ОКРЕМІ
виклики, кожен отримує свій оптимальний фреймінг без взаємовпливу.

Прогони детерміновані й незалежні → можна **скласти найкращий виклик з кожного експерименту
без повторного прогону** (мердж еквівалентний end-to-end, бо виклики не мають спільного стану).

| Конфігурація | firm-status | strength | value |
|---|---|---|---|
| SPLIT (verdict \| str+val) | 0.750 | 0.719 | 0.812 |
| SPLIT2 (verdict+val \| str-only) | 0.833 | 0.688 | 0.781 |
| **A: V3-verdict + (str+val)-call** | **0.833** | **0.719** | **0.844** |

**Знахідки декомпозиції:**
- Прибрати value з verdict-виклику → status падає (0.833→0.750): **value допомагає status**, тримати разом.
- strength-only виклик дає 0.688; strength+value виклик (orthogonality contrast) дає **0.719** → у
  виділеному виклику strength краще з value-контекстом.
- **Конфігурація A досягає максимуму ВСІХ трьох полів одночасно** (0.833 / 0.719 / 0.844) —
  неможливе для single-call.

**🎯 Production-архітектура (2 виклики):**
- **Call 1 (verdict):** промт V3 → беремо `status`, `confidence`, `prediction_value`,
  `evidence`, `retry_after`, `max_horizon`.
- **Call 2 (strength):** промт strength+value (orthogonality + high=RARE + reasoning-first) →
  беремо лише `prediction_strength`.

Вартість/latency: для Flash Lite 2× ≈ $0.006 / ~2.4s послідовно (або ~1.2s паралельно) — прийнятно.
Складність: 2 system prompts + merge-parser + orchestrator робить 2 виклики.

**Відкрите:** інтеграція (prompts.py + parser + eval pipeline + Task 20 orchestrator) — окремий
таск. Питання: чи переганяти 9-model eval на split-архітектурі (валідовано лише на Flash Lite).
