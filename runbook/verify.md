# Runbook — верифікація прогнозів (на боксі)

Прогнати один цикл верифікації на живому AWS-боксі: взяти unverified прогнози з
прод-БД, проганяти через Verifier (Gemini Flash Lite), записати статус/confidence/
evidence назад. Побратим до [`ingest.md`](ingest.md) — той наповнює БД, цей верифікує.

## Одна команда

```
./deploy/verify.sh                 # весь бэклог unverified, з підтвердженням
./deploy/verify.sh --limit 5       # спершу мала партія (розумно перед повним прогоном)
./deploy/verify.sh -y              # без підтвердження
./deploy/verify.sh --dry-run       # надрукувати план, нічого не робити
```

Скрипт резолвить бокс (tag `Name=prophet-checker`, running) → SSH → `curl -X POST
localhost:8000/verify/run` **на боксі** (порт 8000 лише на localhost боксу) → синхронно
чекає `VerificationCycleReport` і друкує підсумок (`verified` / `failed` / `skipped`,
плюс прогнози, де верифікація впала).

## ⚠️ Мутує прод

- Пише `status` / `confidence` / `evidence` / `verified_at` у прод-БД.
- Палить LLM-гроші: Verifier робить **2 виклики на прогноз** (Flash Lite, дешево, але не безкоштовно).

Тому за замовчуванням питає підтвердження (як `deploy.sh`); `-y` пропускає. **Спершу
став `--limit 5`**, глянь вердикти, тоді запускай на весь бэклог.

## Що потрапляє в цикл

- Кандидати — прогнози без статусу (`get_unverified`) з `verify_attempts < 5` (attempt-cap
  відсіює «мертві», що вже кілька разів падали).
- `--limit N` обрізає до перших N кандидатів. Без ліміту — усі.
- Це **first-pass** верифікація. Recheck-луп (повторна перевірка `premature` за
  `next_check_at`) — окремий, ще запаркований трек.

## Коди відповіді

| Код | Значення | Дія |
|-----|----------|-----|
| 200 | цикл пройшов | друкує підсумок |
| 503 | оркестратор ще не готовий (бокс щойно піднявся) | зачекай, глянь `status.sh` |
| 500 | катастрофічний збій циклу | `./deploy/logs.sh` |
| 000 / нема маркера | curl не достукався до app / SSH не дійшов | `status.sh`, `logs.sh` |

## Конфіг (env, є дефолти)

`REGION`, `SSH_KEY`, `SSH_USER`, `BOX_TAG`, `SSH_OPTS`, `TIMEOUT` (сек, дефолт 900 —
довгий цикл підійми `--timeout`).

## Локальний еквівалент

Без боксу той самий цикл — CLI-скрипт (не через HTTP):

```
.venv/bin/python scripts/verification/run_verification_cycle.py --limit 5
```

Прогнози спершу мають бути в БД — див. [`ingest.md`](ingest.md).
