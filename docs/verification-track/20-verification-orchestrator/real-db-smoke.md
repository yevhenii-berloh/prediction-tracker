# Real-DB Smoke — VerificationOrchestrator (Task 20)

Ручний end-to-end smoke `VerificationOrchestrator` проти **реальної** БД + Gemini Flash Lite.
Юніт-тести (190→198) ганяють fakes; цей runbook перевіряє справжній шлях:
`get_unverified` (Postgres) → `Verifier` (реальний LLM) → `update()` write-back.

**Коштує** ~$0.006 × N прогнозів (Flash Lite, 2 виклики на прогноз). **Час** ~хвилина на N≈5.

---

## Передумови

- Docker запущений.
- `.env` містить:
  - `GEMINI_API_KEY=...` — **обов'язково** (verifier). Нове поле з Task 20 (`settings.gemini_api_key`).
  - Для наповнення БД через ingestion (Варіант A): `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`,
    логін-сесія `tg_session*`, `OPENAI_API_KEY` (embeddings) + `LLM_*` для extractor.
  - Варіант B (seed без Telegram) потребує лише `GEMINI_API_KEY` + `DATABASE_URL` (default локальний).

---

## Step 0 — БД + міграції

- [ ] Підняти Postgres (pgvector):

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
docker compose up -d
docker compose ps          # очікувати prophet_postgres = healthy
```

- [ ] Застосувати міграції:

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/alembic upgrade head
```

Очікувано: міграції застосовано без помилок (`status` лишається `String(20)` — PREMATURE без міграції).

---

## Step 1 — наповнити БД unverified-прогнозами

### Варіант A — реальний ingestion (рекомендовано, e2e)

- [ ] Прогнати ingestion на кількох постах (extract → save):

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 5
```

Очікувано: рядок `saved=N` (N прогнозів збережено, status=`unresolved`, verified_at=NULL).

### Варіант B — seed без Telegram (мінімальний)

- [ ] Створити person → raw_document → prediction через реальні repo (правильні FK + мапери):

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python - <<'PY'
import asyncio
from datetime import UTC, date, datetime
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from prophet_checker.config import Settings
from prophet_checker.models.domain import Person, RawDocument, Prediction, SourceType
from prophet_checker.storage.postgres import (
    PostgresPersonRepository, PostgresSourceRepository, PostgresPredictionRepository,
)

async def main():
    engine = create_async_engine(Settings().database_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    await PostgresPersonRepository(sf).save(Person(id="seed-person", name="Арестович"))
    await PostgresSourceRepository(sf).save_document(RawDocument(
        id="seed-doc", person_id="seed-person", source_type=SourceType.TELEGRAM,
        url="seed://1", published_at=datetime(2022, 3, 1, tzinfo=UTC), raw_text="seed"))
    await PostgresPredictionRepository(sf).save(Prediction(
        id="seed-pred-1", document_id="seed-doc", person_id="seed-person",
        claim_text="Контрнаступ ЗСУ почнеться влітку 2023",
        situation="Обговорення планів літньої кампанії ЗСУ на початку 2023",
        prediction_date=date(2023, 1, 15)))
    await engine.dispose()
    print("seeded 1 prediction")

asyncio.run(main())
PY
```

- [ ] Підтвердити, що є eligible-прогнози:

```bash
docker exec prophet_postgres psql -U prophet -d prophet_checker -c \
  "SELECT count(*) FROM predictions WHERE status='unresolved' AND verified_at IS NULL;"
```

Очікувано: count ≥ 1.

---

## Step 2 — прогнати verification cycle

- [ ] Запустити orchestrator:

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/run_verification_cycle.py
```

Очікувано (stdout):

```
verified=<N> failed=<0..> skipped=0
  <prediction_id>: confirmed|refuted|unresolved|premature
  ...
```

`verified` ≈ N; `failed` має бути малим (0–1). `skipped=0` (нічого не досягло attempt-cap=5).

---

## Step 3 — перевірити write-back у БД

- [ ] Подивитися результати:

```bash
docker exec prophet_postgres psql -U prophet -d prophet_checker -c \
  "SELECT left(id,28) AS id, status, confidence AS conf, prediction_strength AS strength,
          prediction_value AS value, (verified_at IS NOT NULL) AS verified,
          next_check_at, verify_attempts AS att
   FROM predictions ORDER BY verified_at DESC NULLS LAST LIMIT 20;"
```

Перевірки (acceptance):
- [ ] Верифіковані рядки мають `verified=t`, `status` ∈ {confirmed,refuted,unresolved,premature}, заповнені `strength` + `value`, `att=1`.
- [ ] `premature`-рядки мають **`next_check_at` НЕ NULL** (urgency-поле записане):

```bash
docker exec prophet_postgres psql -U prophet -d prophet_checker -c \
  "SELECT count(*) FROM predictions WHERE status='premature' AND next_check_at IS NOT NULL;"
```

- [ ] `confirmed`/`refuted`-рядки мають непорожній `evidence_text`:

```bash
docker exec prophet_postgres psql -U prophet -d prophet_checker -c \
  "SELECT count(*) FROM predictions WHERE status IN ('confirmed','refuted') AND evidence_text IS NOT NULL;"
```

---

## Step 4 — failure-path (опційно)

- [ ] Тимчасово зламати ключ і прогнати:

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && GEMINI_API_KEY=invalid .venv/bin/python scripts/run_verification_cycle.py
```

Очікувано: `failed=<усі eligible>`. У БД ці рядки: `verify_attempts` зріс, `last_verify_error` заповнено,
`verified_at` лишився **NULL** (retry-eligible):

```bash
docker exec prophet_postgres psql -U prophet -d prophet_checker -c \
  "SELECT left(id,28) AS id, verify_attempts AS att, (verified_at IS NULL) AS unverified,
          left(last_verify_error,40) AS err FROM predictions WHERE last_verify_error IS NOT NULL;"
```

(Відновити нормальний `GEMINI_API_KEY` у `.env` перед повторним прогоном.)

---

## Step 5 — ідемпотентність (re-run)

- [ ] Прогнати цикл ще раз (з валідним ключем):

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/run_verification_cycle.py
```

Очікувано: `verified=0` для вже-верифікованих (verified_at set → не підхоплюються `get_unverified`).
Якщо у Step 4 були failures без повторної верифікації — вони знову eligible і підхопляться тут.

---

## Teardown

- [ ] Зупинити + видалити volume (повний reset):

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && docker compose down -v
```

---

## Acceptance (smoke pass)

1. ✅ Verification cycle відпрацьовує без краху; `verified ≥ 1`.
2. ✅ Верифіковані прогнози у БД мають усі V2-поля (status/confidence/strength/value/verified_at).
3. ✅ `premature` → `next_check_at` записано.
4. ✅ Re-run ідемпотентний (`verified=0` на вже-верифікованих).
5. ✅ (опц.) Induced failure → `verify_attempts`↑ + `last_verify_error` + `verified_at` NULL.

> Якщо Acceptance проходить — first-pass orchestrator готовий до production. Наступний крок —
> recheck-луп (перевірка `premature` за `next_check_at`) або AWS deploy.
