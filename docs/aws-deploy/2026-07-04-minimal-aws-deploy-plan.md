# Мінімальний AWS-деплой — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Підняти один EC2-бокс, де в Docker Compose крутяться Postgres+pgvector і застосунок `prophet_checker`; БД мігрована, `/health` відповідає, `POST /ingest/run` смикається вручну через SSH-тунель.

**Architecture:** Реалізує [design](2026-07-04-minimal-aws-deploy-design.md). Контейнеризуємо застосунок (Dockerfile + два compose-сервіси `migrate`/`app`), потім описуємо інфру двома CloudFormation-стеками (`secrets` — приватний S3; `compute` — EC2+SG+IAM+user-data). Секрети тягнуться з S3 через IAM-роль. Доступ лише SSH-тунелем.

**Tech Stack:** Docker + Docker Compose, Python 3.14, FastAPI/uvicorn, Alembic, pgvector, AWS CloudFormation, Amazon Linux 2023.

**Порядок:** спершу локальні артефакти (Tasks 1–5) — вони дають робочий контейнерний стек, який верифікується **на локальній машині** ще до AWS. Потім інфра (Tasks 6–7). Наприкінці — доки (Task 8).

---

### Task 1: Config-поле `APP_HOST` + wiring у `__main__.py`

**Скоуп:** Дати змогу задавати bind-хост uvicorn через env. У контейнері треба `0.0.0.0` (інакше застосунок недосяжний); локально лишається `127.0.0.1`.

**Files:**
- Modify: `src/prophet_checker/config.py`
- Modify: `src/prophet_checker/__main__.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Написати падаючий тест**

Додати в кінець `tests/test_config.py`:

```python
def test_settings_app_host_default():
    settings = Settings(
        llm_api_key="key",
        telegram_bot_token="token",
        telegram_api_id=1,
        telegram_api_hash="hash",
    )
    assert settings.app_host == "127.0.0.1"


def test_settings_app_host_from_env(monkeypatch):
    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    settings = Settings(
        llm_api_key="key",
        telegram_bot_token="token",
        telegram_api_id=1,
        telegram_api_hash="hash",
    )
    assert settings.app_host == "0.0.0.0"
```

- [ ] **Step 2: Запустити тест — має впасти**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_settings_app_host_default tests/test_config.py::test_settings_app_host_from_env -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'app_host'`

- [ ] **Step 3: Додати поле в `config.py`**

У класі `Settings`, поряд із `log_level`, додати рядок:

```python
    app_host: str = "127.0.0.1"  # 0.0.0.0 у контейнері (compose), інакше застосунок недосяжний ззовні контейнера
```

- [ ] **Step 4: Запустити тест — має пройти**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (усі тести файлу)

- [ ] **Step 5: Використати поле в `__main__.py`**

У `src/prophet_checker/__main__.py` замінити `host="127.0.0.1",` на `host=settings.app_host,`:

```python
    uvicorn.run(
        "prophet_checker.app:app",
        host=settings.app_host,
        port=8000,
        log_level=settings.log_level.lower(),
    )
```

- [ ] **Step 6: Ruff + повна сюїта**

Run: `.venv/bin/ruff check src/prophet_checker/config.py src/prophet_checker/__main__.py && .venv/bin/python -m pytest tests/ -q`
Expected: ruff чисто; уся сюїта зелена.

- [ ] **Step 7: Commit**

```bash
git add src/prophet_checker/config.py src/prophet_checker/__main__.py tests/test_config.py
git commit -m "feat(config): APP_HOST для bind-адреси uvicorn (контейнер slухає 0.0.0.0)"
```

---

### Task 2: Alembic читає `DATABASE_URL`

**Скоуп:** Зараз `alembic/env.py` бере URL лише з хардкодженого `alembic.ini` (`localhost`). У контейнері `migrate` має піти на compose-сервіс `postgres`. Робимо, щоб env.py переоприділяв URL із `DATABASE_URL`, коли змінна задана. Побічно лагодить наявний дрейф (локальні міграції теж ігнорували `.env`).

**Files:**
- Modify: `alembic/env.py`

- [ ] **Step 1: Додати override URL**

У `alembic/env.py`, одразу після блоку `if config.config_file_name is not None: fileConfig(...)` (тобто після рядка 12), додати:

```python
import os

_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)
```

(Розмістити `import os` до інших рядків або на початку файлу разом з іншими import-ами — за смаком ruff.)

- [ ] **Step 2: Ruff**

Run: `.venv/bin/ruff check alembic/env.py`
Expected: чисто (import на початку файлу).

- [ ] **Step 3: Локальна поведінкова перевірка**

Підняти локальний Postgres і прогнати міграції з явним `DATABASE_URL`, що вказує на нестандартний хост-alias, аби довести, що env.py бере саме його. Найпростіше — переконатись, що зі звичайним локальним URL міграції проходять:

Run:
```bash
docker compose up -d postgres
DATABASE_URL="postgresql+asyncpg://prophet:prophet@localhost:5432/prophet_checker" .venv/bin/alembic upgrade head
```
Expected: `Running upgrade ... ` без помилок; `alembic current` показує head.

Далі негативна перевірка (доводить, що читається саме env, а не ini):
```bash
DATABASE_URL="postgresql+asyncpg://prophet:prophet@nonexistent-host:5432/prophet_checker" .venv/bin/alembic upgrade head
```
Expected: FAIL з мережевою помилкою резолву `nonexistent-host` — тобто env.py справді взяв URL з `DATABASE_URL`, а не localhost з ini.

- [ ] **Step 4: Commit**

```bash
git add alembic/env.py
git commit -m "fix(alembic): читати DATABASE_URL з env (контейнерний migrate йде на postgres, не localhost)"
```

---

### Task 3: Dockerfile + .dockerignore

**Скоуп:** Образ застосунку. Код запікається (`pip install .`), alembic-файли копіюються для сервісу `migrate`. Секрети в образ не потрапляють.

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Modify: `pyproject.toml` (явна package-discovery для чистого wheel-білда)

- [ ] **Step 1: Явна package-discovery в `pyproject.toml`**

Після секції `[build-system]` додати:

```toml
[tool.setuptools.packages.find]
where = ["src"]
```

Причина: editable-install толерантний до автодискаверу; чистий `pip install .` у контейнері робимо детермінованим.

- [ ] **Step 2: Створити `.dockerignore`**

```
.git
.venv
.env
.env.*
tg_session*
scripts/outputs/
docs/
tests/
**/__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
```

- [ ] **Step 3: Створити `Dockerfile`**

```dockerfile
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Спершу метадані+код пакета, потім install — код запікається (не editable)
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# Alembic-файли потрібні сервісу migrate (у пакет не входять — лежать у корені репо)
COPY alembic.ini ./
COPY alembic ./alembic

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD ["python", "-m", "prophet_checker"]
```

- [ ] **Step 4: Зібрати образ**

Run: `docker build -t prophet-checker:local .`
Expected: білд успішний; останній рядок `naming to docker.io/library/prophet-checker:local`.

- [ ] **Step 5: Перевірити, що застосунок імпортується в образі**

Run: `docker run --rm prophet-checker:local python -c "import prophet_checker.app; import prophet_checker.__main__; print('import ok')"`
Expected: `import ok`.

- [ ] **Step 6: Перевірити, що alembic на місці в образі**

Run: `docker run --rm prophet-checker:local alembic --help`
Expected: alembic usage-текст (команда доступна на PATH).

- [ ] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore pyproject.toml
git commit -m "build(docker): Dockerfile + .dockerignore для образу застосунку"
```

---

### Task 4: Розширити docker-compose.yml — сервіси `migrate` + `app`

**Скоуп:** Додати поряд із наявним `postgres` два сервіси. `migrate` проганяє alembic і виходить; `app` піднімає uvicorn після успішного `migrate`. DB-хост і `APP_HOST` інжектяться через `environment` (не залежимо від хоста в `.env`).

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Додати сервіси**

У `docker-compose.yml`, у секцію `services:` (після блоку `postgres:`), додати:

```yaml
  migrate:
    build: .
    depends_on:
      postgres:
        condition: service_healthy
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-prophet}:${POSTGRES_PASSWORD:-prophet}@postgres:5432/${POSTGRES_DB:-prophet_checker}
    command: ["alembic", "upgrade", "head"]
    restart: "no"

  app:
    build: .
    depends_on:
      migrate:
        condition: service_completed_successfully
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-prophet}:${POSTGRES_PASSWORD:-prophet}@postgres:5432/${POSTGRES_DB:-prophet_checker}
      APP_HOST: 0.0.0.0
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./tg_session.session:/app/tg_session.session
    restart: unless-stopped
```

**Примітки (WHY):**
- `environment` перекриває `env_file` у compose — тож DB-хост завжди compose-internal `postgres`, незалежно від того, що в `.env` (там може лишатись `localhost` для venv-dev).
- Порт мапиться **тільки на loopback** боксу — публічної поверхні нема, SSH-тунель дістає.
- Session-файл монтується **read-write** (без `:ro`): Telethon під час збору оновлює стан сесії у своєму sqlite; ro-монт зламав би збір. Оновлення лишаються локально на боксі, назад у S3 не пишуться (сесія лишається валідною).

- [ ] **Step 2: Провалідувати compose**

Run: `docker compose config >/dev/null && echo "compose ok"`
Expected: `compose ok` (нема YAML/schema-помилок). Якщо локально ще нема `tg_session.session`, для валідації достатньо, що файл існує — він є (`ls tg_session.session`).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "build(compose): сервіси migrate + app; порт на loopback, DB-хост через environment"
```

---

### Task 5: Локальний end-to-end bring-up (acceptance перед AWS)

**Скоуп:** Довести на локальній машині, що весь контейнерний стек піднімається: Postgres healthy → migrate exit 0 → app up → `/health` = 200. Це той самий шлях, що виконає bootstrap на боксі. Тестового коду нема — верифікація поведінкова.

**Files:** (жодних змін — лише запуск)

- [ ] **Step 1: Чистий підйом стека**

Run:
```bash
docker compose down -v
docker compose up -d --build
```
Expected: усі три сервіси стартують; білд без помилок.

- [ ] **Step 2: Перевірити статуси сервісів**

Run: `docker compose ps`
Expected: `postgres` — `healthy`; `migrate` — `Exited (0)`; `app` — `Up`.

- [ ] **Step 3: Перевірити, що міграції реально пройшли**

Run: `docker compose logs migrate`
Expected: рядки alembic `Running upgrade ...`, без traceback.

- [ ] **Step 4: Перевірити `/health` через loopback**

Run: `curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/health`
Expected: `200`.

- [ ] **Step 5: Прибрати за собою**

Run: `docker compose down`
Expected: сервіси зупинені (volume лишаємо або ні — не критично для локалі).

- [ ] **Step 6: Зафіксувати результат у прогресі**

Це не commit коду — просто переконайся, що кроки 1–4 зелені, перш ніж іти в AWS. Якщо `/health` не 200 — стоп, дебажимо (див. `docker compose logs app`), не рухаємось до інфри.

---

### Task 6: CloudFormation стек `secrets`

**Скоуп:** Приватний S3-бакет для `.env` + `tg_session.session`. Живе окремо від compute-стека, тож переживає його перестворення.

**Files:**
- Create: `deploy/cloudformation/secrets-stack.yaml`

- [ ] **Step 1: Створити шаблон**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: prophet-checker — приватний бакет для секретів (.env, tg_session)

Resources:
  SecretsBucket:
    Type: AWS::S3::Bucket
    DeletionPolicy: Retain
    Properties:
      BucketEncryption:
        ServerSideEncryptionConfiguration:
          - ServerSideEncryptionByDefault:
              SSEAlgorithm: AES256
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      VersioningConfiguration:
        Status: Enabled

Outputs:
  SecretsBucketName:
    Description: Ім'я бакета — передати параметром у compute-стек
    Value: !Ref SecretsBucket
```

**WHY `DeletionPolicy: Retain`:** секрети не мають зникати разом зі стеком через випадковий `delete-stack`.

- [ ] **Step 2: Лінт шаблону**

Run: `cfn-lint deploy/cloudformation/secrets-stack.yaml`
Expected: без помилок. (Якщо `cfn-lint` не встановлено: `pipx install cfn-lint` або `.venv/bin/pip install cfn-lint`.)

- [ ] **Step 3: (Опційно, реальний AWS) Задеплоїти й залити секрети**

Run:
```bash
aws cloudformation deploy \
  --stack-name prophet-secrets \
  --template-file deploy/cloudformation/secrets-stack.yaml

BUCKET=$(aws cloudformation describe-stacks --stack-name prophet-secrets \
  --query "Stacks[0].Outputs[?OutputKey=='SecretsBucketName'].OutputValue" --output text)

aws s3 cp .env "s3://$BUCKET/.env"
aws s3 cp tg_session.session "s3://$BUCKET/tg_session.session"
```
Expected: стек `CREATE_COMPLETE`; обидва `cp` успішні.

- [ ] **Step 4: Commit**

```bash
git add deploy/cloudformation/secrets-stack.yaml
git commit -m "feat(deploy): CloudFormation стек secrets (приватний S3-бакет)"
```

---

### Task 7: CloudFormation стек `compute` + bootstrap

**Скоуп:** EC2 + SG (лише SSH з твого IP) + IAM-роль (GetObject на бакет) + user-data, що ставить docker, клонить репо, тягне секрети з S3, піднімає compose і сигналить CloudFormation.

**Files:**
- Create: `deploy/cloudformation/compute-stack.yaml`

- [ ] **Step 1: Створити шаблон**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: prophet-checker — EC2 з Docker Compose, доступ лише SSH

Parameters:
  SecretsBucketName:
    Type: String
    Description: Ім'я бакета з Output секрет-стека
  SshIngressCidr:
    Type: String
    Description: Твій IP у форматі CIDR, напр. 203.0.113.7/32
  KeyPairName:
    Type: AWS::EC2::KeyPair::KeyName
  VpcId:
    Type: AWS::EC2::VPC::Id
  SubnetId:
    Type: AWS::EC2::Subnet::Id
  InstanceType:
    Type: String
    Default: t3.small
  RepoUrl:
    Type: String
    Default: https://github.com/<owner>/prediction-tracker.git
    Description: HTTPS-URL публічного репо для git clone
  LatestAmiId:
    Type: AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>
    Default: /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64

Resources:
  InstanceRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal: { Service: ec2.amazonaws.com }
            Action: sts:AssumeRole
      Policies:
        - PolicyName: read-secrets-bucket
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action: [s3:GetObject]
                Resource: !Sub "arn:aws:s3:::${SecretsBucketName}/*"
              - Effect: Allow
                Action: [s3:ListBucket]
                Resource: !Sub "arn:aws:s3:::${SecretsBucketName}"

  InstanceProfile:
    Type: AWS::IAM::InstanceProfile
    Properties:
      Roles: [!Ref InstanceRole]

  SecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: prophet-checker — лише SSH з довіреного IP
      VpcId: !Ref VpcId
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 22
          ToPort: 22
          CidrIp: !Ref SshIngressCidr

  Instance:
    Type: AWS::EC2::Instance
    CreationPolicy:
      ResourceSignal:
        Timeout: PT15M
    Properties:
      ImageId: !Ref LatestAmiId
      InstanceType: !Ref InstanceType
      KeyName: !Ref KeyPairName
      SubnetId: !Ref SubnetId
      SecurityGroupIds: [!Ref SecurityGroup]
      IamInstanceProfile: !Ref InstanceProfile
      BlockDeviceMappings:
        - DeviceName: /dev/xvda
          Ebs:
            VolumeType: gp3
            VolumeSize: 30
            DeleteOnTermination: true
      Tags:
        - Key: Name
          Value: prophet-checker
      UserData:
        Fn::Base64: !Sub |
          #!/bin/bash
          set -o pipefail
          exec > /var/log/user-data.log 2>&1
          set -x

          signal() {
            /opt/aws/bin/cfn-signal -e "$1" \
              --stack ${AWS::StackName} --resource Instance --region ${AWS::Region}
          }
          trap 'signal 1' ERR

          dnf install -y docker git aws-cfn-bootstrap
          systemctl enable --now docker
          usermod -aG docker ec2-user

          # docker compose v2 як CLI-плагін (AL2023 docker-пакет його не несе)
          mkdir -p /usr/local/lib/docker/cli-plugins
          curl -SL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64" \
            -o /usr/local/lib/docker/cli-plugins/docker-compose
          chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

          git clone ${RepoUrl} /opt/app
          cd /opt/app

          aws s3 cp "s3://${SecretsBucketName}/.env" /opt/app/.env
          aws s3 cp "s3://${SecretsBucketName}/tg_session.session" /opt/app/tg_session.session

          docker compose up -d --build

          signal 0

Outputs:
  PublicIp:
    Description: Публічний IP боксу (для ssh -L тунелю)
    Value: !GetAtt Instance.PublicIp
  SshTunnelHint:
    Value: !Sub "ssh -i <key>.pem -L 8000:localhost:8000 ec2-user@${Instance.PublicIp}"
```

**Примітки:**
- `RepoUrl` дефолт має `<owner>` — замінити на реальний GitHub-owner перед деплоєм.
- Версію `docker-compose` (v2.29.7) можна оновити; пін тримає bootstrap відтворюваним.
- `trap ... ERR` + `signal` гарантують, що при падінні bootstrap стек піде в `CREATE_FAILED`, а не «зелений але мертвий».

- [ ] **Step 2: Лінт шаблону**

Run: `cfn-lint deploy/cloudformation/compute-stack.yaml`
Expected: без помилок.

- [ ] **Step 3: (Реальний AWS) Деплой**

Run:
```bash
aws cloudformation deploy \
  --stack-name prophet-compute \
  --template-file deploy/cloudformation/compute-stack.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    SecretsBucketName=<з-Output-secrets-стека> \
    SshIngressCidr=<твій-IP>/32 \
    KeyPairName=<твоя-key-pair> \
    VpcId=<vpc-id> \
    SubnetId=<public-subnet-id> \
    RepoUrl=https://github.com/<owner>/prediction-tracker.git
```
Expected: стек `CREATE_COMPLETE` (означає, що cfn-signal 0 прийшов — bootstrap відпрацював).

- [ ] **Step 4: Commit**

```bash
git add deploy/cloudformation/compute-stack.yaml
git commit -m "feat(deploy): CloudFormation стек compute (EC2 + SSH-only SG + IAM + bootstrap)"
```

---

### Task 8: Acceptance на боксі + доки

**Скоуп:** Прогнати acceptance-таблицю з design на реальному боксі й оновити tracking-доки.

**Files:**
- Create: `docs/aws-deploy/README.md`
- Modify: `docs/README.md` (додати рядок треку `aws-deploy`)
- Modify: `progress.md` (Phase 6 — частину «воно живе» → done)
- Modify: `.env.example` (додати `APP_HOST`)

- [ ] **Step 1: Acceptance на боксі**

Через тунель (`ssh -i <key>.pem -L 8000:localhost:8000 ec2-user@<PublicIp>`), у другому терміналі:

```bash
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/health          # → 200
curl -s -X POST localhost:8000/ingest/run                                # → CycleReport JSON
```

На боксі (`ssh` без тунелю):
```bash
cd /opt/app && docker compose ps                                         # postgres healthy, migrate Exited(0), app Up
docker compose exec postgres psql -U prophet -d prophet_checker \
  -c "SELECT count(*) FROM predictions;"                                 # > 0
```
Expected: усе за таблицею acceptance з design-доку.

- [ ] **Step 2: Додати `APP_HOST` у `.env.example`**

У секцію після `TG_SESSION_PATH` додати:

```
# -- App server --
# Bind-хост uvicorn. Локально 127.0.0.1; у Docker Compose перекривається на 0.0.0.0.
APP_HOST=127.0.0.1
```

- [ ] **Step 3: Створити `docs/aws-deploy/README.md`**

```markdown
# `aws-deploy/` — мінімальний деплой на AWS

Статус: «воно живе» — один EC2 з Docker Compose (Postgres+pgvector+app),
доступ лише SSH-тунелем, секрети з приватного S3, інфра як CloudFormation.

| Документ | Призначення |
|----------|-------------|
| [`2026-07-04-minimal-aws-deploy-design.md`](2026-07-04-minimal-aws-deploy-design.md) | Design: рішення, компроміси, acceptance |
| [`2026-07-04-minimal-aws-deploy-plan.md`](2026-07-04-minimal-aws-deploy-plan.md) | Implementation plan — 8 задач |

Артефакти: `Dockerfile`, `docker-compose.yml` (сервіси migrate/app),
`deploy/cloudformation/{secrets,compute}-stack.yaml`.

Поза скоупом (майбутнє): розклад ingestion, RDS, публічний RAG/бот — Phase B/C.
```

- [ ] **Step 4: Додати рядок у `docs/README.md`**

У таблицю/список треків додати посилання на `aws-deploy/` (за наявним форматом сусідніх рядків).

- [ ] **Step 5: Оновити `progress.md`**

У «Phase 6: AWS deploy + CI» позначити частину «воно живе» виконаною (EC2+compose+SSH-доступ), лишивши RDS/автоматизацію/CI у `QUEUED`. Додати рядок у Notes про завершення мінімального деплою й посилання на `docs/aws-deploy/`.

- [ ] **Step 6: Ruff + повна сюїта (переконатись, що доки/приклади нічого не зламали)**

Run: `.venv/bin/ruff check . 2>&1 | tail -1; .venv/bin/python -m pytest tests/ -q`
Expected: сюїта зелена (312+2 нових = 314). Ruff-дебт по дереву лишається як був (не наша задача).

- [ ] **Step 7: Commit**

```bash
git add docs/aws-deploy/README.md docs/README.md progress.md .env.example
git commit -m "docs(aws-deploy): closeout мінімального деплою + APP_HOST у .env.example"
```

---

## Self-review (виконано автором плану)

**Spec coverage** — кожне рішення design покрите:
- Postgres у контейнері (#1) → Task 4 (compose) + Task 5 (bring-up).
- SSH-only доступ (#2) → Task 4 (порт на loopback) + Task 7 (SG лише 22).
- Секрети з S3 через IAM (#3) → Task 6 (бакет) + Task 7 (роль + `aws s3 cp` у bootstrap).
- git clone + compose up (#4) → Task 7 bootstrap.
- CloudFormation (#5) → Tasks 6–7.
- Контейнеризація застосунку → Tasks 3–4; bind-адреса → Task 1; alembic DB-хост → Task 2.
- Acceptance-таблиця → Task 5 (локально) + Task 8 (на боксі).

**Placeholder scan** — реальних плейсхолдерів нема. Свідомі підстановки, які деплоєр заповнює під своє оточення (`<owner>`, `<твій-IP>`, `<vpc-id>`, `<key>`), позначені кутовими дужками й описані — це не TODO в коді, а параметри середовища.

**Type/interface consistency** — `app_host` (Task 1) узгоджено між `config.py`, `__main__.py`, тестом і compose-env `APP_HOST`. `DATABASE_URL` (Task 2) узгоджено з compose-env (Task 4). Ім'я бакета тече через Output secrets-стека → параметр compute-стека (Tasks 6→7). Session-файл усюди `tg_session.session`.

**Відхилення від design (свідомі, дрібні):**
- Session-монт **rw**, не `:ro` як у design-тексті — інакше Telethon не оновить стан сесії й збір впаде. Durability це не зачіпає (файл лишається на боксі).
- DB-хост інжектиться через compose `environment`, а не через хост у `.env` (design припускав правку `.env`) — чистіше й лишає `.env` придатним для venv-dev.
