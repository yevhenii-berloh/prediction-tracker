# RDS-міграція БД — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Замінити Postgres-контейнер на RDS PostgreSQL (pgvector), щоб дані переживали перестворення EC2-боксу.

**Architecture:** Окремий CloudFormation `data`-стек з RDS; TLS-конект застосунку через config-флаг; Postgres лишається лише в локальному compose-override. Деталі й рішення — у [design](2026-07-10-rds-migration-design.md), тут на них лише посилаюсь.

**Tech Stack:** Python 3.14, SQLAlchemy 2 async + asyncpg 0.31, Alembic, Docker Compose, AWS CloudFormation, RDS PostgreSQL 16.5+.

**Порядок:** спершу код (TDD-абельне), далі інфра (cfn-lint), наприкінці docs. Кожна таска — самодостатня + коміт. Коміти в conventional-стилі, українською.

---

### Task 1: DB-engine хелпер (`ssl_connect_args` + `make_engine`)

**Скоуп:** Централізувати створення async-engine у крихітному модулі й додати TLS через `connect_args` (implement per design §B). Чиста `ssl_connect_args` — єдиний юніт-тест міграції.

**Files:**
- Create: `src/prophet_checker/storage/engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Написати падаючий тест**

```python
# tests/test_engine.py
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from prophet_checker.storage.engine import make_engine, ssl_connect_args


def test_ssl_connect_args_disable_returns_empty():
    assert ssl_connect_args("disable") == {}


def test_ssl_connect_args_require_passes_ssl_string():
    assert ssl_connect_args("require") == {"ssl": "require"}


def test_ssl_connect_args_verify_full_passes_ssl_string():
    assert ssl_connect_args("verify-full") == {"ssl": "verify-full"}


def test_ssl_connect_args_unknown_mode_raises():
    with pytest.raises(ValueError):
        ssl_connect_args("banana")


def test_make_engine_returns_async_engine_without_connecting():
    engine = make_engine("postgresql+asyncpg://u:p@localhost:5432/db", "disable")
    assert isinstance(engine, AsyncEngine)
    assert engine.url.database == "db"
```

- [ ] **Step 2: Прогнати — має впасти**

Run: `.venv/bin/python -m pytest tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prophet_checker.storage.engine'`

- [ ] **Step 3: Мінімальна реалізація**

```python
# src/prophet_checker/storage/engine.py
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_SSL_MODES = frozenset({"disable", "require", "verify-full"})


def ssl_connect_args(mode: str) -> dict[str, str]:
    # asyncpg читає TLS-режим з kwarg `ssl` (libpq-рядок), а не з `?sslmode=` в URL —
    # інакше при rds.force_ssl конект відхиляється. `disable` = без TLS (локаль).
    if mode not in _SSL_MODES:
        raise ValueError(f"unknown db_ssl_mode: {mode!r}")
    if mode == "disable":
        return {}
    return {"ssl": mode}


def make_engine(url: str, ssl_mode: str) -> AsyncEngine:
    return create_async_engine(url, echo=False, connect_args=ssl_connect_args(ssl_mode))
```

- [ ] **Step 4: Прогнати — має пройти**

Run: `.venv/bin/python -m pytest tests/test_engine.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Коміт**

```bash
git add src/prophet_checker/storage/engine.py tests/test_engine.py
git commit -m "feat(db): make_engine + ssl_connect_args для TLS-конекту до RDS"
```

---

### Task 2: Config-поле `db_ssl_mode`

**Скоуп:** Додати TLS-режим у `Settings` (implement per design §B). Чисте оголошення поля з дефолтом — окремий тест не пишемо (правило «не тестувати чисті Pydantic-моделі»), верифікуємо зеленою сюїтою.

**Files:**
- Modify: `src/prophet_checker/config.py`

- [ ] **Step 1: Додати поле**

У класі `Settings`, одразу після рядка `database_url: str = ...`, додати:

```python
    db_ssl_mode: str = "disable"  # disable | require | verify-full; require на RDS (rds.force_ssl=1)
```

- [ ] **Step 2: Верифікація — сюїта зелена**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (усі наявні 312 + 5 нових з Task 1 = 317 passed)

- [ ] **Step 3: Коміт**

```bash
git add src/prophet_checker/config.py
git commit -m "feat(config): db_ssl_mode для вибору TLS-режиму конекту до БД"
```

---

### Task 3: Підключити `make_engine` у `factory.py` (3 місця)

**Скоуп:** Замінити три дублікати `create_async_engine(...)` на `make_engine(...)` (implement per design §B). Поведінкова еквівалентність при дефолті `disable`.

**Files:**
- Modify: `src/prophet_checker/factory.py`

- [ ] **Step 1: Правка імпортів**

Замінити рядок:

```python
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
```

на:

```python
from sqlalchemy.ext.asyncio import async_sessionmaker

from prophet_checker.storage.engine import make_engine
```

- [ ] **Step 2: Замінити 3 виклики**

У кожній з трьох функцій (`build_orchestrator`, `build_verification_orchestrator`, `build_query_orchestrator`) замінити:

```python
    engine = create_async_engine(settings.database_url, echo=False)
```

на:

```python
    engine = make_engine(settings.database_url, settings.db_ssl_mode)
```

- [ ] **Step 3: Перевірити, що старого виклику не лишилось**

Run: `grep -n "create_async_engine" src/prophet_checker/factory.py`
Expected: жодного рядка (порожній вивід)

- [ ] **Step 4: Верифікація — сюїта зелена + імпорт**

Run: `.venv/bin/python -c "import prophet_checker.factory" && .venv/bin/python -m pytest tests/ -q`
Expected: імпорт без помилок; PASS (317 passed)

- [ ] **Step 5: Коміт**

```bash
git add src/prophet_checker/factory.py
git commit -m "refactor(factory): engine через make_engine (TLS-ready, DRY)"
```

---

### Task 4: TLS у `alembic/env.py`

**Скоуп:** Сервіс `migrate` теж мусить конектитись по TLS на RDS. Прокинути `DB_SSL_MODE` з env у `connect_args` (implement per design §B). Дефолт `disable` зберігає локальну поведінку.

**Files:**
- Modify: `alembic/env.py`

- [ ] **Step 1: Додати імпорт хелпера**

Після наявного рядка `from prophet_checker.models.db import Base` додати:

```python
from prophet_checker.storage.engine import ssl_connect_args
```

- [ ] **Step 2: Прочитати режим з env**

Одразу після блоку, що читає `DATABASE_URL`:

```python
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)
```

додати:

```python
_ssl_mode = os.environ.get("DB_SSL_MODE", "disable")
```

- [ ] **Step 3: Прокинути `connect_args` у engine**

У `run_migrations_online` замінити:

```python
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
```

на:

```python
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=ssl_connect_args(_ssl_mode),
    )
```

- [ ] **Step 4: Верифікація — локальна міграція проходить (режим disable)**

```bash
docker compose up -d postgres
.venv/bin/alembic upgrade head
```

Expected: міграції застосовуються без помилок (як і до зміни; `DB_SSL_MODE` не задано → `disable` → `connect_args={}`).

- [ ] **Step 5: Коміт**

```bash
git add alembic/env.py
git commit -m "feat(alembic): DB_SSL_MODE у migrate-конекті (TLS до RDS)"
```

---

### Task 5: `.env.example` — задокументувати `DB_SSL_MODE`

**Скоуп:** Додати новий ключ у приклад env, щоб `.env` на боксі знав про `require`.

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Додати ключ у секцію Database**

Під рядком `DATABASE_URL=...` у секції `# -- Database --` додати:

```
# TLS-режим конекту до БД: disable (локаль) | require (RDS, rds.force_ssl=1) | verify-full
DB_SSL_MODE=disable
```

- [ ] **Step 2: Коміт**

```bash
git add .env.example
git commit -m "docs(env): DB_SSL_MODE у .env.example"
```

---

### Task 6: Compose — base RDS-ready + локальний override

**Скоуп:** Прибрати Postgres з базового compose (щоб бокс його не піднімав) і повернути його в `docker-compose.override.yml` для локалі (implement per design §D).

**Files:**
- Modify: `docker-compose.yml`
- Create: `docker-compose.override.yml`

- [ ] **Step 1: Переписати `docker-compose.yml` (без Postgres, DATABASE_URL з `.env`)**

```yaml
services:
  migrate:
    build: .
    env_file: .env
    command: ["alembic", "upgrade", "head"]
    restart: "no"

  app:
    build: .
    depends_on:
      migrate:
        condition: service_completed_successfully
    env_file: .env
    environment:
      APP_HOST: 0.0.0.0
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./tg_session.session:/app/tg_session.session
    restart: unless-stopped
```

- [ ] **Step 2: Створити `docker-compose.override.yml` (локальний Postgres + wiring)**

```yaml
# Локальний dev-оверрайд. Compose авто-підхоплює його при `docker compose up`.
# Бокс запускає `docker compose -f docker-compose.yml up` (явно → без цього файлу → без Postgres).
services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: prophet_postgres
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-prophet}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-prophet}
      POSTGRES_DB: ${POSTGRES_DB:-prophet_checker}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-prophet} -d ${POSTGRES_DB:-prophet_checker}"]
      interval: 5s
      timeout: 3s
      retries: 5

  migrate:
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-prophet}:${POSTGRES_PASSWORD:-prophet}@postgres:5432/${POSTGRES_DB:-prophet_checker}

  app:
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-prophet}:${POSTGRES_PASSWORD:-prophet}@postgres:5432/${POSTGRES_DB:-prophet_checker}

volumes:
  pgdata:
```

- [ ] **Step 3: Верифікація — база без Postgres, merged з Postgres**

Run: `docker compose -f docker-compose.yml config --services`
Expected: рівно два рядки — `migrate`, `app` (без `postgres`)

Run: `docker compose config --services`
Expected: три — `app`, `migrate`, `postgres` (override змержився)

- [ ] **Step 4: Верифікація — локальний bring-up зелений**

```bash
docker compose up -d postgres
.venv/bin/alembic upgrade head
```

Expected: postgres healthy; міграції проходять. (Для host-dev застосунку `docker compose up -d postgres` піднімає лише БД, як і раніше.)

- [ ] **Step 5: Коміт**

```bash
git add docker-compose.yml docker-compose.override.yml
git commit -m "build(compose): base RDS-ready; Postgres у локальному override"
```

---

### Task 7: CloudFormation `data`-стек (RDS + SG)

**Скоуп:** Новий довгоживучий стек з RDS, DB subnet group, app-SG і db-SG (implement per design §A). SG GroupDescription — ASCII (AWS відхиляє кирилицю, знахідка з `aws-deploy/README.md`).

**Files:**
- Create: `deploy/cloudformation/data-stack.yaml`

- [ ] **Step 1: Написати шаблон**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: prophet-checker - RDS PostgreSQL (pgvector) + app/db security groups

Parameters:
  VpcId:
    Type: AWS::EC2::VPC::Id
  SubnetIds:
    Type: List<AWS::EC2::Subnet::Id>
    Description: Two or more subnets in different AZs for the DB subnet group
  SshIngressCidr:
    Type: String
    Description: Your IP in CIDR form, e.g. 203.0.113.7/32
  DbInstanceClass:
    Type: String
    Default: db.t4g.micro
  DbAllocatedStorage:
    Type: Number
    Default: 20
  DbEngineVersion:
    Type: String
    Default: "16.8"
    Description: RDS PostgreSQL version; must be >= 16.5 for pgvector 0.8.0
  DbName:
    Type: String
    Default: prophet_checker
  DbUsername:
    Type: String
    Default: prophet
  DbPassword:
    Type: String
    NoEcho: true
    MinLength: 16
    Description: Master password; also goes into the S3 .env DATABASE_URL

Resources:
  AppSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: prophet-checker app box - SSH only from trusted IP
      VpcId: !Ref VpcId
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 22
          ToPort: 22
          CidrIp: !Ref SshIngressCidr

  DbSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: prophet-checker RDS - 5432 from app SG only
      VpcId: !Ref VpcId
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 5432
          ToPort: 5432
          SourceSecurityGroupId: !Ref AppSecurityGroup

  DbSubnetGroup:
    Type: AWS::RDS::DBSubnetGroup
    Properties:
      DBSubnetGroupDescription: prophet-checker RDS subnet group
      SubnetIds: !Ref SubnetIds

  DbInstance:
    Type: AWS::RDS::DBInstance
    DeletionPolicy: Snapshot
    UpdateReplacePolicy: Snapshot
    Properties:
      Engine: postgres
      EngineVersion: !Ref DbEngineVersion
      DBInstanceClass: !Ref DbInstanceClass
      AllocatedStorage: !Ref DbAllocatedStorage
      StorageType: gp3
      MultiAZ: false
      PubliclyAccessible: false
      StorageEncrypted: true
      BackupRetentionPeriod: 7
      DBName: !Ref DbName
      MasterUsername: !Ref DbUsername
      MasterUserPassword: !Ref DbPassword
      DBSubnetGroupName: !Ref DbSubnetGroup
      VPCSecurityGroups:
        - !Ref DbSecurityGroup

Outputs:
  DbEndpoint:
    Description: RDS endpoint address (put into S3 .env DATABASE_URL)
    Value: !GetAtt DbInstance.Endpoint.Address
  DbPort:
    Value: !GetAtt DbInstance.Endpoint.Port
  DbName:
    Value: !Ref DbName
  AppSecurityGroupId:
    Description: App SG id - imported by the compute stack
    Value: !Ref AppSecurityGroup
    Export:
      Name: !Sub "${AWS::StackName}-AppSecurityGroupId"
```

- [ ] **Step 2: Верифікація — cfn-lint чистий**

Run: `uvx cfn-lint deploy/cloudformation/data-stack.yaml`
Expected: без помилок (порожній вивід, exit 0)

- [ ] **Step 3: Звірити доступну engine-версію (перед реальним deploy)**

Run: `aws rds describe-db-engine-versions --engine postgres --query "DBEngineVersions[?starts_with(EngineVersion, '16.')].EngineVersion" --output text`
Очікування: у списку є `DbEngineVersion` дефолт (`16.8`) або ≥16.5. Якщо ні — оновити `Default` на доступну ≥16.5. (Крок операційний; не блокує коміт, якщо `aws` CLI не налаштований — тоді звірити при cutover.)

- [ ] **Step 4: Коміт**

```bash
git add deploy/cloudformation/data-stack.yaml
git commit -m "feat(deploy): CloudFormation data-стек (RDS PostgreSQL + pgvector, SG-to-SG)"
```

---

### Task 8: Делта `compute`-стека

**Скоуп:** Прибрати локальний SSH-SG (переїхав у `data`), імпортувати app-SG, і запускати compose явним base-файлом (без Postgres) (implement per design §C).

**Files:**
- Modify: `deploy/cloudformation/compute-stack.yaml`

- [ ] **Step 1: Прибрати параметри `VpcId` і `SshIngressCidr`, додати `DataStackName`**

У блоці `Parameters` видалити:

```yaml
  SshIngressCidr:
    Type: String
    Description: Твій IP у форматі CIDR, напр. 203.0.113.7/32
```

і

```yaml
  VpcId:
    Type: AWS::EC2::VPC::Id
```

Додати замість них:

```yaml
  DataStackName:
    Type: String
    Description: Ім'я data-стека (для ImportValue app-SG)
```

- [ ] **Step 2: Видалити ресурс `SecurityGroup`**

Видалити весь блок:

```yaml
  SecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: prophet-checker - SSH only from trusted IP
      VpcId: !Ref VpcId
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 22
          ToPort: 22
          CidrIp: !Ref SshIngressCidr
```

- [ ] **Step 3: Instance посилається на імпортований app-SG**

У `Instance.Properties` замінити:

```yaml
      SecurityGroupIds: [!Ref SecurityGroup]
```

на:

```yaml
      SecurityGroupIds:
        - !ImportValue
            Fn::Sub: "${DataStackName}-AppSecurityGroupId"
```

- [ ] **Step 4: bootstrap — явний base-compose (без Postgres)**

У user-data замінити рядок:

```bash
          docker compose up -d --build
```

на:

```bash
          docker compose -f docker-compose.yml up -d --build
```

- [ ] **Step 5: Верифікація — cfn-lint чистий**

Run: `uvx cfn-lint deploy/cloudformation/compute-stack.yaml`
Expected: без помилок (exit 0)

- [ ] **Step 6: Коміт**

```bash
git add deploy/cloudformation/compute-stack.yaml
git commit -m "feat(deploy): compute імпортує app-SG з data-стека; compose без Postgres на боксі"
```

---

### Task 9: Docs — README треку, progress, runbook

**Скоуп:** Оновити індекс треку, progress-лог і runbook під RDS-cutover. Джерело правди по статусу — `progress.md`.

**Files:**
- Modify: `docs/aws-deploy/README.md`
- Modify: `progress.md`
- Modify: `runbook/first-ingest.md`

- [ ] **Step 1: `docs/aws-deploy/README.md` — додати рядок про RDS-пару**

У таблицю документів додати два рядки:

```markdown
| [`2026-07-10-rds-migration-design.md`](2026-07-10-rds-migration-design.md) | Design: RDS-міграція БД (Phase B durability) |
| [`2026-07-10-rds-migration-plan.md`](2026-07-10-rds-migration-plan.md) | Implementation plan — 9 задач |
```

Оновити рядок «Поза скоупом»: прибрати «RDS» зі списку майбутнього (тепер у скоупі).

- [ ] **Step 2: `progress.md` — Phase 6 + Notes**

У таблиці «Phase 6: AWS deploy + CI» замінити рядок Task 23 на:

```markdown
| 23 — AWS RDS PostgreSQL + pgvector | 🟢 код+CFN готові (`data`-стек, TLS-конект), box-деплой за користувачем |
```

Додати bullet у розділ `## Notes` (наприкінці):

```markdown
- **RDS-міграція (2026-07-10):** durability-свап — Postgres-контейнер → RDS PostgreSQL 16.5+ (pgvector 0.8.0). Окремий CloudFormation `data`-стек (RDS + DB subnet group + SG-to-SG app→db), TLS-конект застосунку через `db_ssl_mode`/`make_engine` (asyncpg `ssl=require`, бо `rds.force_ssl=1`), Postgres лишився лише в локальному compose-override. Свіжий старт (re-ingest, без міграції даних). Юніт лише на `ssl_connect_args`; решта — cfn-lint + acceptance з durability-proof (знести/перестворити `compute` → дані в RDS живі). Design+plan: [`docs/aws-deploy/2026-07-10-rds-migration-design.md`](docs/aws-deploy/2026-07-10-rds-migration-design.md) + `-plan.md`. **Box-деплой за користувачем** (немає AWS-креденшелів у сесії).
```

- [ ] **Step 3: `runbook/first-ingest.md` — cutover-примітка**

Додати на початок (або окремою секцією) блок про порядок деплою під RDS:

```markdown
## RDS cutover (порядок стеків)

1. `deploy` `secrets`-стек (якщо ще нема) — залити `.env` + `tg_session` у бакет.
2. `deploy` `data`-стек (VpcId, SubnetIds ≥2 AZ, SshIngressCidr, DbPassword). Дочекатись RDS `available`.
3. Оновити `.env` у S3: `DATABASE_URL` на RDS-endpoint (з Output `DbEndpoint`), додати `DB_SSL_MODE=require`.
4. `deploy` `compute`-стек (SecretsBucketName, DataStackName, KeyPairName, SubnetId, RepoUrl). Дочекатись cfn-signal.
5. SSH-тунель → `POST /ingest/run` (re-ingest наповнює RDS).
```

- [ ] **Step 4: Верифікація — посилання цілі**

Run: `ls docs/aws-deploy/2026-07-10-rds-migration-design.md docs/aws-deploy/2026-07-10-rds-migration-plan.md`
Expected: обидва файли існують (посилання в README не биті).

- [ ] **Step 5: Коміт**

```bash
git add docs/aws-deploy/README.md progress.md runbook/first-ingest.md
git commit -m "docs(aws-deploy): RDS-міграція у README/progress/runbook"
```

---

## Self-Review

**Spec coverage (design → task):**
- §A `data`-стек (RDS + subnet group + app-SG + db-SG + outputs, SG-cycle break) → **Task 7** ✅
- §B TLS-конект (`ssl_connect_args`, `make_engine`, `db_ssl_mode`, factory ×3, alembic) → **Tasks 1–4** ✅
- §C делта `compute` (drop SG/VpcId/SshIngressCidr, import app-SG, compose `-f`) → **Task 8** ✅
- §D compose split (base без Postgres, override з Postgres) → **Task 6** ✅
- `.env.example` `DB_SSL_MODE` → **Task 5** ✅
- Cutover + docs + progress → **Task 9** ✅
- Acceptance / durability-proof — операційне, на реальному AWS (не таска коду); задокументовано в design §Верифікація + runbook (Task 9). ✅

**Placeholder scan:** без TBD/TODO; кожен крок коду має повний код; кожен verify-крок має команду + expected. Task 7 Step 3 і acceptance — свідомо операційні (потрібні AWS-креденшели), позначені як такі, не placeholder. ✅

**Type consistency:** `ssl_connect_args(mode: str) -> dict[str,str]` і `make_engine(url, ssl_mode)` однакові в Task 1 (визначення), Task 3 (`make_engine(settings.database_url, settings.db_ssl_mode)`), Task 4 (`ssl_connect_args(_ssl_mode)`). Config-поле `db_ssl_mode` (Task 2) ↔ env `DB_SSL_MODE` (Task 4 alembic, `.env.example` Task 5, compose). CFN export `${AWS::StackName}-AppSecurityGroupId` (Task 7) ↔ `${DataStackName}-AppSecurityGroupId` import (Task 8). ✅

**Test-count arithmetic:** база 312 (progress) + 5 нових (Task 1) = 317 — узгоджено в Tasks 2–3 verify-кроках.
