# Runbook: перший реальний ingest на боксі

Засіяти джерело (@arestovich) і прогнати реальний ingestion-цикл на задеплоєному EC2.
Джерел-ендпоінта нема (live лише `GET /health` + `POST /ingest/run`), тож засівання —
два INSERT-и в БД боксу; далі `run_cycle` підхопить `enabled=true`-джерело.

**Бокс:** `ec2-user@18.197.68.220` (стек `prophet-compute`, eu-central-1).

## RDS cutover (порядок стеків)

1. `deploy` `secrets`-стек (якщо ще нема) — залити `.env` + `tg_session` у бакет.
2. `deploy` `data`-стек (VpcId, SubnetIds ≥2 AZ, SshIngressCidr, DbPassword). Дочекатись RDS `available`.
   Пароль піде в `DATABASE_URL`, тож **уникай `#`, `%`, `/`, `@`, пробілів** — вони або ламають URL, або відхиляються RDS (CFN `AllowedPattern` ловить `/ @ " ` + пробіл, але не `# %`).
3. Оновити `.env` у S3: `DATABASE_URL` на RDS-endpoint (Output `DbEndpoint`), додати `DB_SSL_MODE=require`.
4. `deploy` `compute`-стек (SecretsBucketName, DataStackName, KeyPairName, SubnetId — **у тій самій VPC, що й data-стек**, RepoUrl). Дочекатись cfn-signal.
5. SSH-тунель → `POST /ingest/run` (re-ingest наповнює RDS).

## Важливо про вартість — прочитати перед стартом

`POST /ingest/run` **не приймає ліміт** — збирає всі пости каналу від `last_collected_at`.
Уся історія @arestovich ≈ 5572 пости → стільки ж LLM-екстракцій (довго + $). Тому в
засіванні ставимо **вікно 3 дні** (`last_collected_at = now() - 3 days`) — перший прогін
дешевий і швидкий. Вікно розширюємо потім (Крок 5).

## Крок 1 — зайти на бокс

```bash
ssh -i ~/.ssh/prophet-checker-key.pem ec2-user@18.197.68.220
cd /opt/app

# На боксі нема локального postgres — psql проти RDS через одноразовий контейнер.
# DSN береться з .env: прибираємо +asyncpg, додаємо sslmode.
DB_DSN=$(grep '^DATABASE_URL=' .env | cut -d= -f2- | sed 's/+asyncpg//')
pg() { sudo docker run --rm -i postgres:16 psql "${DB_DSN}?sslmode=require" "$@"; }
```

## Крок 2 — засіяти Person + Source

```bash
pg <<'SQL'
WITH p AS (
  INSERT INTO persons (id, name, description)
  VALUES (gen_random_uuid()::text, 'Олексій Арестович', 'Ukrainian public figure')
  RETURNING id
)
INSERT INTO person_sources (id, person_id, source_type, source_identifier, enabled, last_collected_at)
SELECT gen_random_uuid()::text, p.id, 'telegram', '@arestovich', true, now() - interval '3 days'
FROM p;
SQL
```

Перевірити, що джерело з'явилось:

```bash
pg \
  -c "SELECT source_type, source_identifier, enabled, last_collected_at FROM person_sources;"
```

## Крок 3 — запустити ingest

Прогін синхронний, може тривати кілька хвилин (збір + LLM на кожен пост). Раджу дивитись
логи в другому SSH-вікні.

```bash
# вікно A — тригер (довгий timeout, щоб curl не обірвав):
curl -s -X POST --max-time 900 localhost:8000/ingest/run | tee /tmp/cycle.json; echo

# вікно B — живі логи:
sudo docker compose logs -f app
```

Очікуєш CycleReport із непорожнім `channels_processed` (collected/extracted лічильники).

## Крок 4 — верифікувати результат

```bash
pg -c "
  SELECT count(*) AS predictions FROM predictions;
  SELECT count(*) AS documents  FROM raw_documents;
"
# приклад кількох прогнозів:
pg -c "
  SELECT left(claim_text,80), prediction_date, status FROM predictions LIMIT 5;"
```

Успіх = `predictions > 0`. Курсор `last_collected_at` джерела просувається вперед
автоматично — наступний `/ingest/run` збиратиме лише новіші пости.

## Крок 5 (опційно) — розширити вікно

Щоб забрати глибшу історію — відмотати курсор назад і знову тригернути ingest:

```bash
pg \
  -c "UPDATE person_sources SET last_collected_at = now() - interval '30 days' WHERE source_identifier='@arestovich';"
# далі знову curl -X POST .../ingest/run  (більше вікно = більше $ і часу)
```

Повний backfill: `SET last_collected_at = NULL` — збере ВСЮ історію (~5572 пости, дорого).

## Пам'ятати

- **Верифікація прогнозів** — окремий крок (`verification/` пакет має CLI). Ingest лише
  витягує claims зі статусом `unresolved`.
- **Гроші течуть, поки бокс живий.** Коли завершив: `aws cloudformation delete-stack --stack-name prophet-compute`.
- Якщо `channels_processed` порожній попри засіяне джерело — дивись `docker compose logs app`
  на помилку Telethon/LLM (rate-limit, протухла сесія тощо).

## Схема (для довідки)

- `persons` — id (uuid-text), name, description.
- `person_sources` — person_id (FK), `source_type='telegram'`, `source_identifier='@arestovich'`
  (йде прямо в Telethon `get_entity`), `enabled` (фільтр `list_active_sources`), `last_collected_at` (курсор).
- Потік: `run_cycle` → `list_active_sources` (enabled=true) → `TelegramSource.collect` →
  extractor → `raw_documents` + `predictions`.
