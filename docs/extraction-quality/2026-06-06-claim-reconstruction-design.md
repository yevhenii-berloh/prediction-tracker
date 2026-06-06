# Реконструкція claim_text в екстракторі — Design

**Дата:** 2026-06-06
**Статус:** approved (очікує review spec)
**Трек:** extraction-quality
**Підхід:** 1 — тільки промт (без змін коду/схеми)

## Проблема

В БД з'явились низькоякісні claim-и — голі фрагменти списку, скопійовані дослівно:

```
"прекращение огня;"
"разведение войск;"
"вода в Крым;"
"возобновление междержавных связей на уровне парламентов, дипломатии и т.д.;"
```

Усі дев'ять — з одного посту `tg:@O_Arestovich_official:20` (2020-03-13), де Арестович перелічує *«Ожидаемые вехи на пути комиссии Ермак - Козак»* маркованим списком і далі прогнозує, що процес **провалиться** (*«я думаю что… не получится»*).

## Корінь (root cause)

1. **Промт наказує копіювати дослівно.** `EXTRACTION_TEMPLATE`: `claim_text: the exact prediction (in original language)`. Слово **«exact»** → модель бере буквальний текст пункту разом із `;`.
2. **Немає вимоги самодостатності.** Ніде не сказано, що claim — цілісне фальсифіковане речення (суб'єкт+присудок+час). Гола іменна група проходить.
3. **Немає інструкції для списків.** Модель атомізує перелік по пунктах, але не реконструює.
4. **Втрата позиції/полярності автора.** Пункти — це **чужа агенда** (Зеленський/Єрмак/Козак), яку Арестович прогнозує **провалити**. Витягуючи «вода в Крым» як окреме «це станеться», екстрактор **інвертує** реальний прогноз автора.

Тобто два дефекти: **форма** (фрагменти) і **вірність** (втрата рамки/полярності).

## Рішення

Правимо лише рядки промта у `src/prophet_checker/llm/prompts.py`. Спільні для prod + eval + smoke → eval автоматично провалідує. Без змін коду, схеми, тестів.

### Прийняті рішення
- **claim_text — реконструйоване самодостатнє твердження** (не дослівна цитата).
- **Мова — оригіналу посту** (RU-пост → RU-claim), узгоджено з контрактом `situation`.
- **Гранулярність списків — модель вирішує** (B): субстантивні пункти → окремі повні речення АБО згортання в одне твердження; тривіальні процесні — згорнути/пропустити.
- **Вірність полярності** — claim відображає реальну тезу автора (чия дія, СТАНЕТЬСЯ чи ПРОВАЛИТЬСЯ).

### Компонент A — `EXTRACTION_TEMPLATE` (поле claim_text)

Було:
```
- claim_text: the exact prediction (in original language)
```
Стане:
```
- claim_text: a SELF-CONTAINED reconstruction of the prediction, in the
  post's original language. Rewrite it as one complete, grammatical,
  falsifiable sentence — explicit subject + predicate + timeframe when known.
  Never copy a bare list item or fragment; never keep list punctuation. The
  sentence must state the AUTHOR'S OWN forecast with its correct polarity
  (whether the author expects the event to HAPPEN or to FAIL / NOT happen).
```

### Компонент B — `EXTRACTION_SYSTEM` (новий блок після секції G)

```
RECONSTRUCTION & FAITHFULNESS (how to phrase each extracted claim):

R1. Self-contained form. Each claim_text must be a standalone, grammatical,
    falsifiable sentence in the post's original language. Do NOT output bare
    list items, fragments, or noun phrases. Do NOT keep list punctuation
    (";", "—", trailing commas).

R2. Enumerated forecasts. When a forecast is given as a bulleted/numbered
    list, do NOT emit one claim per raw bullet. Reconstruct: either fold the
    list into a single higher-level claim, or restate the substantive items
    as full sentences — whichever faithfully captures what the author claims.

R3. Preserve the author's stance and polarity. Capture WHOSE action is
    predicted and WHETHER the author forecasts it will HAPPEN or FAIL. If the
    author lists the steps of a process they predict will FAIL, the
    prediction is the FAILURE of that process — do NOT extract each step as
    if the author forecasts its success.
```

### Компонент C — few-shot (реальний doc 20, у `EXTRACTION_SYSTEM`)

```
EXAMPLE (enumerated agenda the author predicts will fail):
Source: "Ожидаемые вехи на пути комиссии Ермак-Козак: — прекращение огня;
— вода в Крым; — выборы в ОРДЛО... Поэтому, я думаю что у Путина-Зеленского
не получится."
WRONG → ["прекращение огня;", "вода в Крым;", "выборы в ОРДЛО;"]
        (fragments; inverted polarity — author predicts these will NOT happen)
RIGHT → "Процесс поэтапного примирения с РФ через комиссию Ермак–Козак
        (прекращение огня, вода в Крым, выборы в ОРДЛО) в итоге провалится."
```

## Валідація (без юніт-тестів)

1. Перевитягти doc 20 → очікую 1–2 реконструйованих claim-и російською з вірною полярністю, без фрагментів.
2. Spot-check 2–3 інших списко-подібних постів.
3. Backfill: видалити 9 junk-claim-ів doc 20 і перевитягти (решта 13 у БД — ок).

## Ризики

- Контракт claim_text зсувається verbatim→reconstructed. Detection-benchmark gold може не співпасти по рядку, але extraction-quality — LLM-as-judge, реконструйовані claim-и мають отримати **не гірше**. Тримати на оці після перегону.

## Поза скоупом

- Програмний guard у `extractor.py` — лише якщо eval покаже протікання фрагментів (наразі YAGNI).
- Другий LLM-прохід-критик.
- Зміни схеми БД / нові юніт-тести (зміна тексту промта; наявні 205 тестів лишаються зеленими — `test_build_extraction_prompt` і `situation`-асерти не чіпаються).

## Обмеження проєкту

NO docstrings, NO inline comments у коді. Перевірка: `.venv/bin/python -m pytest tests/ -q` → 205 passed.
