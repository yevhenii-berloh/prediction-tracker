# Мінімальний AWS-деплой — design

**Дата:** 2026-07-04
**Статус:** design (узгоджено на брейнштормі, план ще не написано)
**Трек:** `aws-deploy` (Phase 6, підмножина — «воно живе»)

---

## Ціль

Один EC2-бокс, на якому в Docker Compose крутяться Postgres+pgvector і застосунок
`prophet_checker`. БД мігрована, `/health` відповідає, `POST /ingest/run`
запускається вручну через SSH-тунель.

Це свідомо **мінімальний** зріз Phase 6, не автономний сервіс. Немає розкладу,
немає публічного HTTP, немає RAG-ендпоінтів назовні. Автоматизація ingestion,
RDS і публічний доступ — окремі майбутні ітерації (Phase B/C).

## Рішення (зафіксовані на брейнштормі)

| # | Рішення | Чому |
|---|---------|------|
| 1 | Postgres у контейнері на тому ж EC2, не RDS | Найдешевше, lift-and-shift наявного compose. RDS — коли підемо в автономний режим і durability стане важливою. |
| 2 | Доступ лише через SSH-тунель | Нульова публічна поверхня при живому Telegram-акаунті на борту. Не треба ALB/домен/TLS. |
| 3 | Секрети з приватного S3 через IAM instance role | Відтворюваність: перестворив бокс → user-data підтягнув. Ключів на диску нема. |
| 4 | Код через `git clone` публічного репо + `docker compose up --build` | Найкоротший шлях, нуль додаткової інфри (ECR/AMI — коли збірка на боксі почне заважати). |
| 5 | Інфра як код — CloudFormation | Відтворюваність інфри, нативний AWS, лягає на spec-driven стиль. |

## Прийняті компроміси

- **Дані Postgres на EBS інстансу — при термінації боксу губляться.** Прийнятно:
  корпус регенерується повторним ingestion. Durability приходить у Phase B з RDS.
- **Секрети після завантаження лежать на боксі відкрито.** Прийнятно: порт 22 лише
  з твого IP, публічної поверхні нема.
- **Інфра-код не покривається юніт-тестами.** Верифікація поведінкова (див. нижче).

---

## Що вже є / що будуємо

| Компонент | Стан |
|-----------|------|
| Postgres+pgvector у compose | ✅ є (`docker-compose.yml`) |
| Застосунок у контейнері | ❌ нема Dockerfile — будуємо |
| Compose-сервіси `app` + `migrate` | ❌ будуємо |
| CloudFormation-стеки `secrets` + `compute` | ❌ будуємо |
| Bootstrap (user-data) скрипт | ❌ будуємо |

Три нові артефакти: **контейнеризація застосунку**, **CloudFormation-стеки**,
**bootstrap-скрипт**.

---

## Артефакт 1 — контейнеризація застосунку

### Dockerfile

- База `python:3.14-slim`, x86 (збігається з локальним dev — без ARM-сюрпризів при збірці).
- `pip install .` з `pyproject.toml` + `src/` — код запікається в образ (не editable).
- Alembic-конфіг і `migrations/` — в образ (потрібні сервісу `migrate`).
- Non-root user.
- Entrypoint запускає uvicorn.

### .dockerignore

Виключає з build-контексту: `.venv`, `.env`, `tg_session*`, `scripts/outputs/`,
`docs/`, тести, `.git`.

**Контракт:** секрети ніколи не потрапляють в образ. `.env` і `tg_session`
монтуються з боксу в рантаймі.

### Bind-адреса (єдина зміна в application-коді)

Проблема: зараз `__main__.py` слухає `127.0.0.1:8000` — усередині контейнера це
робить застосунок недосяжним.

Рішення:
- host для uvicorn береться з config (env `APP_HOST`); дефолт `127.0.0.1` для
  локалі, `0.0.0.0` у compose;
- compose мапить порт **тільки на loopback боксу**: `127.0.0.1:8000:8000`.

Наслідок: назовні порт закритий (SG і так пускає лише 22). SSH-тунель дістає
`localhost:8000` → loopback боксу → контейнер. Публічної поверхні нуль.

### Розширення docker-compose.yml

Поряд із наявним `postgres` — два сервіси:

| Сервіс | Роль | Залежність |
|--------|------|-----------|
| `migrate` | one-shot `alembic upgrade head`, виходить | `postgres` → `service_healthy` |
| `app` | uvicorn; монтує `./tg_session` (ro); `env_file: .env` | `migrate` → `service_completed_successfully` |

Порядок старту виходить сам: Postgres здоровий → міграції пройшли → застосунок піднявся.

**DB-хост.** У compose застосунок конектиться на `postgres:5432` (ім'я сервісу),
не `localhost`. Це приходить із `.env` (той, що в S3) — коду міняти не треба, лише
переконуємось, що `.env` на боксі має compose-internal хост.

**Обсяг зміни в коді.** Дві невеликі правки:
1. Config-поле `APP_HOST` (нове поле `Settings` у `config.py`), яке зчитує `__main__.py`
   при старті uvicorn.
2. `alembic/env.py` — переоприділяти `sqlalchemy.url` з `DATABASE_URL`, коли змінна
   задана. Причина: зараз env.py бере URL лише з хардкодженого `alembic.ini`
   (`localhost`), тож у контейнері `alembic upgrade head` пішов би на `localhost`
   замість compose-сервісу `postgres` і впав. Фікс також усуває наявний дрейф —
   локальні міграції теж ігнорували `DATABASE_URL` з `.env`.

Решта (DB-хост застосунку тощо) — через `.env`, коду не торкається.

---

## Артефакт 2 — CloudFormation (два стеки)

Розділення стеків прямо реалізує рішення #3: секрети переживають перестворення боксу.

### Стек `secrets` (створюється раз, рідко чіпається)

- Приватний S3-бакет, block-public-access увімкнено повністю.
- Після `create` — вручну заливаються `.env` + `tg_session` (один раз).
- Знесення/перестворення compute-стека секретів не чіпає.

### Стек `compute` (зноситься/піднімається вільно)

| Ресурс | Деталі |
|--------|--------|
| EC2 | Amazon Linux 2023, t3.small, gp3 root ~30 GB (`DeleteOnTermination`) |
| Security Group | inbound tcp/22 з `SshIngressCidr`; outbound весь |
| IAM role + instance profile | тільки `s3:GetObject` / `s3:ListBucket` на бакет секретів |
| user-data | bootstrap-скрипт (Артефакт 3) |

**Параметри стека:** `SshIngressCidr` (твій IP/32), `KeyPairName`, `VpcId`,
`SubnetId` (default VPC), `SecretsBucketName`, `InstanceType` (дефолт t3.small).

**`CreationPolicy` + `cfn-signal`** наприкінці user-data — CloudFormation вважає
стек успішним лише коли bootstrap реально відпрацював. Без цього зелений стек ≠
живий застосунок.

---

## Артефакт 3 — bootstrap (user-data)

Виконується на першому старті боксу. Логи в `/var/log/user-data.log`.

1. `dnf install` docker + git; enable docker; docker compose plugin
   (AWS CLI вже в AL2023).
2. `git clone` публічного репо в `/opt/app`.
3. `aws s3 cp` `.env` і `tg_session` із бакета в `/opt/app` — через instance-роль,
   ключів на диску нема.
4. `docker compose up -d --build` — compose сам проганяє `migrate` → піднімає `app`.
5. `cfn-signal` про успіх/невдачу.

---

## Операційний runbook

**Розгортання з нуля:**
1. `deploy` стек `secrets` → залити `.env` + `tg_session` у бакет.
2. `deploy` стек `compute` (передати IP, key-pair, bucket-name).
3. Дочекатись `cfn-signal` success.

**Доступ:**
```
ssh -i key.pem -L 8000:localhost:8000 ec2-user@<public-ip>
curl localhost:8000/health
curl -X POST localhost:8000/ingest/run
```

**Оновлення коду:** `ssh in → cd /opt/app → git pull → docker compose up -d --build`.

**Знесення:** `delete-stack compute` — бокс і EBS зникають, платіж спиняється;
секрети лишаються в S3.

---

## Верифікація «воно живе» (acceptance)

| Крок | Очікування |
|------|-----------|
| Стек `compute` | `CREATE_COMPLETE` + cfn-signal success |
| `docker compose ps` | `postgres` healthy, `migrate` exited 0, `app` up |
| `curl localhost:8000/health` (через тунель) | `200` |
| `curl -X POST .../ingest/run` | повертає `CycleReport` (реальний Telegram+LLM цикл) |
| `SELECT count(*) FROM predictions` | > 0 — дані персистнули |

**Тестова стратегія.** Інфра-код юніт-тестами не покривається. Локальний рівень:
`cfn-lint` на шаблон + перевірка, що `docker compose up --build` піднімає стек і
`/health` відповідає **на локальній машині** ще до AWS. Реальний рівень:
acceptance-таблиця вище на боксі. Application-код цей деплой не міняє (крім
bind-адреси з config), тож наявна pytest-сюїта (312) лишається зеленою.

---

## Поза скоупом (майбутні ітерації)

- Розклад/автоматизація ingestion (EventBridge/cron) — Phase B.
- RDS PostgreSQL + pgvector замість контейнерної БД — Phase B (коли durability стане важливою).
- Публічний доступ до RAG-ендпоінтів / Telegram-бот — Phase C.
- ECR/AMI-збірка замість build-на-боксі — коли збірка на t3.small почне заважати.
- GitHub Actions CI.
