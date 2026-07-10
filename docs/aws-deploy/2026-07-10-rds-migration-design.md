# RDS-міграція БД — design

**Дата:** 2026-07-10
**Статус:** design (узгоджено на брейнштормі, план ще не написано)
**Трек:** `aws-deploy` (Phase 6, підмножина — «Phase B: durability»)

---

## Ціль

Замінити Postgres-контейнер на **RDS PostgreSQL (pgvector)** так, щоб дані
переживали перестворення EC2-боксу. Це запланований «Phase B» з
[мінімального деплою](2026-07-04-minimal-aws-deploy-design.md) — durability,
яку той свідомо відклав.

Скоуп свідомо вузький: **тільки БД**. Доступ (SSH-only), ручний ingest, S3-секрети
і сам EC2 лишаються без змін. Автоматизація ingest і публічний доступ — окремі
майбутні ітерації.

## Рішення (зафіксовані на брейнштормі)

| # | Рішення | Чому |
|---|---------|------|
| 1 | RDS в **окремому `data`-стеку**, не в `compute` | БД мусить пережити знесення боксу — інакше durability безсенсовна. Дзеркалить наявний `secrets/compute` спліт. |
| 2 | RDS PostgreSQL **16.x ≥16.5**, `db.t4g.micro`, single-AZ | Збіг із pg16-контейнером; **pgvector 0.8.0 доступний на RDS з 16.5** (звірено). Найдешевше під пет-проєкт. |
| 3 | RDS **не** publicly accessible; доступ лише від app-SG | Нульова публічна поверхня БД. Least-privilege замість CIDR-ingress. |
| 4 | TLS до RDS через config-флаг, не хардкод | `rds.force_ssl=1` дефолтний → конект мусить шифруватись. Флаг лишає локаль без TLS. |
| 5 | Секрети лишаються **`.env` у приватному S3** | Нуль нової інфри, консистентно з наявним патерном. Secrets Manager — over-engineering тут. |
| 6 | **Свіжий старт** — re-ingest, без міграції даних | Бокс не задеплоєний (AWS $0), продакшн-даних нема; корпус регенерується. |

## Прийняті компроміси

- **Пароль RDS лежить у `.env` на боксі відкрито** (як і решта секретів). Прийнятно:
  публічної поверхні нема, порт 22 лише з твого IP. Ротація через Secrets Manager — на майбутнє.
- **Single-AZ, без read-репліки.** Прийнятно для пет-проєкту; durability дають автобекапи, не HA.
- **TLS-режим `require`** (шифрує, без перевірки CA). `verify-full` з RDS CA-бандлом — майбутнє загартування.
- **Інфра-код не покривається юніт-тестами.** Верифікація поведінкова (див. acceptance).

---

## Що вже є / що будуємо

| Компонент | Стан |
|-----------|------|
| `secrets`-стек (S3) | ✅ є — не чіпаємо |
| `compute`-стек (EC2, SSH-only) | ✅ є — невеликий делта (§C) |
| `data`-стек (RDS) | ❌ будуємо (§A) |
| TLS-конект застосунку до БД | ❌ будуємо (§B) |
| Compose без Postgres на боксі | ❌ будуємо (§D) |

Чотири артефакти: **`data`-стек**, **TLS-хелпер у коді**, **делта `compute`-стека**,
**compose-розділення local/box**.

---

## A. Артефакт 1 — `data`-стек (`deploy/cloudformation/data-stack.yaml`)

Окремий стек, що живе довго (як `secrets`). Знесення/перестворення `compute` його не чіпає.

### Параметри

`VpcId`, `SubnetIds` (список, ≥2 AZ — вимога DB subnet group), `SshIngressCidr`
(твій IP/32 — переїхав сюди разом з app-SG), `DbInstanceClass` (дефолт `db.t4g.micro`),
`DbAllocatedStorage` (дефолт 20), `DbEngineVersion` (дефолт — свіжий 16.x, **мінімум 16.5** для pgvector 0.8.0),
`DbName` (дефолт `prophet_checker`), `DbUsername` (дефолт `prophet`), `DbPassword` (`NoEcho`).

### Ресурси

| Ресурс | Деталі |
|--------|--------|
| `AppSecurityGroup` | **переїхав сюди з `compute`.** Inbound tcp/22 з `SshIngressCidr` |
| `DbSecurityGroup` | Inbound tcp/5432 **лише від `AppSecurityGroup`** (SG-to-SG) |
| `DbSubnetGroup` | з `SubnetIds` |
| `DbInstance` (`AWS::RDS::DBInstance`) | engine `postgres`, gp3, single-AZ, `PubliclyAccessible: false`, `StorageEncrypted: true`, `BackupRetentionPeriod: 7`, VPC-SG = `DbSecurityGroup`, `DeletionPolicy: Snapshot`, `UpdateReplacePolicy: Snapshot` |

### Outputs (з `Export`)

`DbEndpoint` (Address), `DbPort`, `AppSecurityGroupId` (експорт → імпортує `compute`), `DbName`.

### Розрив SG-циклу

RDS SG має пускати бокс, але бокс — у `compute`, який залежить від `data` → цикл.
Ламаємо перенесенням **app-SG у `data`-стек**: RDS SG посилається на app-SG у тому ж
стеку (циклу нема), а `compute` лише імпортує готовий app-SG-id і вішає на інстанс.

---

## B. Артефакт 2 — TLS-конект до RDS (єдина зміна в application-коді)

Проблема: RDS PG 15+ дефолтно `rds.force_ssl=1` → конект мусить бути по TLS. Зараз
engine будується без TLS у **4 місцях** (3× `factory.py`, 1× `alembic/env.py`), кожне
дублює `create_async_engine(url, echo=False)`.

Рішення — централізувати engine у крихітному хелпері (rule-of-three: дублювання вже є)
і додати TLS через config.

### Інтерфейси (тіла — у плані)

Новий модуль `src/prophet_checker/storage/engine.py`:

- `ssl_connect_args(mode: str) -> dict` — чиста функція. `{}` для `disable`,
  інакше `{"ssl": mode}` (asyncpg приймає libpq-style `require`/`verify-full`). **Це юніт TDD.**
- `make_engine(url: str, ssl_mode: str) -> AsyncEngine` — обгортка
  `create_async_engine(url, echo=False, connect_args=ssl_connect_args(ssl_mode))`.

Нове поле config (`config.py`):

- `Settings.db_ssl_mode: str = "disable"` — значення `disable | require | verify-full`.
  Дефолт `disable` (локаль без TLS); на боксі `.env` ставить `require`.

Оновлені call-сайти:

- `factory.py` ×3 — `create_async_engine(...)` → `make_engine(settings.database_url, settings.db_ssl_mode)`.
- `alembic/env.py` — читає `DB_SSL_MODE` з `os.environ` (як уже читає `DATABASE_URL`),
  передає `connect_args=ssl_connect_args(mode)` у `async_engine_from_config`.

`.env.example` — додати `DB_SSL_MODE=disable` з коментарем (`require` на RDS).

---

## C. Артефакт 3 — делта `compute`-стека

| Зміна | Було | Стало |
|-------|------|-------|
| SSH SecurityGroup | створювався тут | **прибрано** — імпортуємо `AppSecurityGroupId` з `data` |
| `SshIngressCidr` param | тут | переїхав у `data`-стек |
| `Instance.SecurityGroupIds` | локальний SG | `!ImportValue <data-export AppSecurityGroupId>` |
| bootstrap compose-команда | `docker compose up -d --build` | `docker compose -f docker-compose.yml up -d --build` (явний файл → без override → без Postgres) |

Endpoint RDS у `compute` **не** приходить через CFN — він у `.env` (S3). Тож `compute`
лишається слабо зв'язаним: імпортує тільки app-SG-id. Решта bootstrap незмінна.

---

## D. Артефакт 4 — compose: Postgres лише локально

- Базовий `docker-compose.yml` → **RDS-ready**: тільки `migrate` + `app`, без сервісу
  `postgres` і без `depends_on: postgres`; БД — з `DATABASE_URL`.
- Новий `docker-compose.override.yml` (compose авто-підхоплює локально) **повертає**
  `postgres` + `depends_on` для dev-циклу.
- Бокс запускає `docker compose -f docker-compose.yml up` (явно → ігнорує override → без Postgres).

Чому не навпаки (override для боксу): compose-merge не вміє **видаляти** сервіс чи
`depends_on`, тож Postgres усе одно стартував би на боксі.

---

## Cutover (операційний порядок)

1. `deploy` `data`-стек (передати VpcId, SubnetIds, IP, DbPassword). Дочекатись RDS `available`.
2. Оновити `.env` у S3: `DATABASE_URL` на RDS-endpoint, додати `DB_SSL_MODE=require`.
3. (Пере)підняти `compute`-стек — bootstrap тягне новий `.env`, `migrate` накатує
   схему + `CREATE EXTENSION vector` на RDS, `app` піднімається.
4. Ручний re-ingest через SSH-тунель (`POST /ingest/run`).

**Знесення:** `delete-stack compute` — бокс зникає, **RDS лишається** (окремий стек);
знесення `data` робить фінальний снапшот (`DeletionPolicy: Snapshot`).

---

## Ризики / краєві випадки

| Ситуація | Наслідок | Мітигація |
|----------|----------|-----------|
| `.env` без `DB_SSL_MODE=require` при `force_ssl` | `migrate` не конектиться, падає | Явний крок cutover #2; acceptance ловить |
| Engine-версія < 16.5 | `CREATE EXTENSION vector` (0.8.0) падає у `migrate` | `DbEngineVersion` ≥16.5 (pgvector 0.8.0 з 16.5 — звірено) |
| DB-SG не пускає бокс (не той app-SG) | `migrate` таймаутить | SG-to-SG на app-SG; box і RDS в одному VpcId |
| bootstrap-крок упав | чесний `CREATE_FAILED` | наявний `set -eo pipefail` + `trap → cfn-signal 1` |
| DB subnet group з 1 AZ | CFN відхиляє стек | `SubnetIds` — ≥2 AZ (валідуємо cfn-lint + деплой) |

---

## Верифікація (acceptance)

| Крок | Очікування |
|------|-----------|
| `data`-стек | `CREATE_COMPLETE`; RDS `available`, `PubliclyAccessible=false` |
| Юніт `ssl_connect_args` | `disable`→`{}`, `require`→`{"ssl":"require"}` |
| Наявна pytest-сюїта | **312** лишаються зелені (локальний дефолт `disable` = стара поведінка) |
| `migrate` на боксі | конект по TLS ок; схема + `vector` створені на RDS |
| `curl localhost:8000/health` (тунель) | `200` |
| `POST /ingest/run` | `CycleReport`; `SELECT count(*) FROM predictions` > 0 на RDS |
| **Durability-proof** | знести й перестворити `compute` → `SELECT count(*)` **незмінний** (дані пережили бокс) |

**Тестова стратегія.** Юніт — лише чиста `ssl_connect_args`. Інфра — `cfn-lint` на
`data-stack.yaml` + локальна перевірка, що `docker compose -f docker-compose.yml up`
(з локальним RDS-сурогатом або відкладено) не тягне Postgres, а `docker compose up`
(override) тягне. Реальний рівень — acceptance-таблиця на боксі, з durability-proof як
головним доказом.

---

## Поза скоупом (майбутні ітерації)

- Автоматизація/розклад ingestion (EventBridge/cron).
- Публічний доступ до RAG-ендпоінтів / Telegram-бот.
- Secrets Manager / RDS-managed master password + ротація.
- Multi-AZ, read-репліка.
- `verify-full` TLS з RDS CA-бандлом.
- Міграція наявних даних (pg_dump/restore) — свідомо не робимо (свіжий старт).
- ECR/AMI замість build-на-боксі.
