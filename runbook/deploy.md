# Runbook: залиття нової версії коду на AWS

Оновити код на **вже живому** боксі: підтягнути нову версію з репо, пересобрати образ,
дати міграціям накотитись, перевірити що воно живе. CI/CD нема — це ручний SSH-деплой.

Для розгортання **з нуля** (стеки `secrets` → `data` → `compute`) — [`first-ingest.md`](first-ingest.md),
розділ «RDS cutover». Для підйому **паузнутого** середовища — [`stop-env.md`](stop-env.md), розділ «Підйом».

## Однією командою (скрипт)

Механічну частину (Кроки 1–3 + Перевірка) робить [`../deploy/deploy.sh`](../deploy/deploy.sh):

```bash
./deploy/deploy.sh              # latest main, з підтвердженням
./deploy/deploy.sh -y           # без підтвердження
./deploy/deploy.sh --ref <sha>  # конкретна версія (тег/коміт/гілка)
./deploy/deploy.sh --dry-run    # показати, що виконає, нічого не роблячи
```

Скрипт резолвить бокс, жене `git pull` → підтягує свіжий `.env` з S3 → `up -d --build --force-recreate`,
перевіряє exit-code `migrate` і health-loop. Тобто деплой зводить бокс і на свіжий **код**, і на свіжі
**секрети** заразом. **Крок 0 (пуш) і відкат лишаються ручними** — далі покроково, коли треба контроль
або скрипт недоступний.

### Інші скрипти

- [`../deploy/refresh.sh`](../deploy/refresh.sh) — **лише секрети**: підтягнути свіжий `.env` з S3 і
  перезапустити застосунок, без деплою коду (напр. увімкнув бота чи поміняв ключ).
- [`../deploy/connect.sh`](../deploy/connect.sh) — інтерактивний shell у сервісі на боксі:
  `./deploy/connect.sh` (bash у `app`), `--box` (на хості), `-- <cmd>` (команда замість shell).
- [`../deploy/logs.sh`](../deploy/logs.sh) — логи застосунку;
  [`../deploy/status.sh`](../deploy/status.sh) — read-only статус середовища по шарах.

## Модель деплою (щоб розуміти, що робиш)

- **Бокс:** EC2, стек `prophet-compute`, тег `Name=prophet-checker`, eu-central-1. Код лежить
  у `/opt/app` (`git clone` **публічного** репо на bootstrap).
- **БД:** RDS (стек `prophet-data`), не контейнер. Застосунок конектиться через `DATABASE_URL` у `.env`.
- **Запуск на боксі:** `docker compose -f docker-compose.yml up -d --build` — one-shot `migrate`
  (`alembic upgrade head` проти RDS) → `app` (uvicorn на `127.0.0.1:8000`). Локального postgres на
  боксі **нема** (override навмисно не підхоплюється — див. Застереження).
- **Деплой нової версії = ** `git pull` у `/opt/app` + свіжий `.env` з S3 + той самий `up -d --build
  --force-recreate`. Образ пересобирається зі свіжим кодом, `migrate` накочує нові ревізії **автоматично**,
  `app` перезапускається на актуальних секретах.
- **Секрети (`.env`) живуть у S3.** Бокс копіює їх лише на bootstrap (user-data), тож `deploy.sh` (і
  `refresh.sh`) щоразу тягнуть свіжий `.env` з S3 — інакше правка секрета в S3 на живий бокс не долетить.

## Передумови

- **Env не на паузі.** RDS `available` і бокс `running`. Якщо середовище стоїть — спершу підійми
  ([`stop-env.md`](stop-env.md) «Підйом»: RDS → бокс), потім деплой. Міграції на старті чекають БД.
- **Твій IP у app-SG.** SSH пускає лише з довіреного IP. Змінився IP → онови ingress
  (див. [`stop-env.md`](stop-env.md) «Застереження»), інакше SSH не пустить.
- **Нова версія у ПУБЛІЧНОМУ репо, який клонує бокс** (Крок 0).
- **Локально не піднятий `python -m prophet_checker`** проти тієї ж Telethon-сесії (див. Застереження).

## Крок 0 — запушити нову версію

Бокс тягне **дефолтну гілку** публічного репо (`main`). Тож нова версія має бути на `main` і запушена.

```bash
# локально: злити фічу в main (мердж або PR — як зазвичай) і запушити
git checkout main
git merge feat/<твоя-гілка>           # або вже змерджено через PR
git push origin main
```

Деплоїш не останній `main`, а конкретну версію — запуш потрібний коміт у `main` (або
зафіксуй sha й зроби `git checkout <sha>` на боксі в Кроці 2 замість `git pull`).

> **Куди саме пушити.** Бокс клонує `https://github.com/evgeniy44/prediction-tracker.git`
> (параметр `RepoUrl` у `compute-stack.yaml`), а локальний `origin` може вказувати на інший
> неймспейс. Якщо `evgeniy44` — твій **старий перейменований** акаунт, GitHub редіректить і
> `git pull` на боксі підхопить push у новий origin. Якщо це **окреме** репо — запуш і туди,
> або онови `RepoUrl` стека. Перевірити, що реально тягне бокс — на Кроці 2:
> `sudo git -C /opt/app remote -v`.

## Крок 1 — зайти на бокс

Публічний IP змінюється при кожному stop/start (нема Elastic IP) — резолвимо динамічно.

```bash
REGION=eu-central-1
BOX=$(aws ec2 describe-instances --region $REGION \
  --filters "Name=tag:Name,Values=prophet-checker" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)
IP=$(aws ec2 describe-instances --region $REGION --instance-ids "$BOX" \
  --query 'Reservations[].Instances[].PublicIpAddress' --output text)
echo "box=$BOX  ip=$IP"

# -L прокидає localhost:8000 боксу до себе — щоб перевіряти /health з ноута
ssh -i ~/.ssh/prophet-checker-key.pem -L 8000:localhost:8000 ec2-user@$IP
```

(Нема локального AWS CLI — візьми IP з EC2-консолі й підстав вручну.)

## Крок 2 — підтягнути код

Репо в `/opt/app` склоноване root-ом (user-data), тож `sudo`.

```bash
cd /opt/app
sudo git -C /opt/app remote -v            # звірка: це той публічний репо, куди ти пушив?
sudo git rev-parse --abbrev-ref HEAD      # має бути main (не detached після відкату)
sudo git pull
sudo git log --oneline -5                 # переконайся, що твій коміт приїхав
```

`.env` і `tg_session.session` — gitignored, `git pull` їх **не чіпає** (секрети й chown лишаються).
Свіжі секрети з S3 — окремий крок: скриптом (`deploy.sh`/`refresh.sh`) або вручну перед `up -d`:
`sudo aws s3 cp s3://<secrets-bucket>/.env /opt/app/.env` (роль інстансу читає бакет).

## Крок 3 — пересобрати і підняти

```bash
sudo docker compose -f docker-compose.yml up -d --build
```

`--build` пересобирає образ (новий код + залежності з `pyproject.toml`), `migrate` жене
`alembic upgrade head` проти RDS, `app` перезапускається. На t3.small збірка небистра — це норма.

Простеж, що міграції пройшли (не мовчазний провал):

```bash
sudo docker compose -f docker-compose.yml logs migrate    # очікуєш running upgrade ... і вихід 0
```

## Перевірка

```bash
# 1) стан сервісів: migrate = Exited (0), app = Up
sudo docker compose -f docker-compose.yml ps

# 2) застосунок живий (на боксі, або через тунель з ноута)
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/health          # -> 200

# 3) яка ревізія схеми накотилась (опційно)
sudo docker compose -f docker-compose.yml run --rm migrate alembic current

# 4) у логах застосунку нема трейсбеків
sudo docker compose -f docker-compose.yml logs app | tail -30
```

Глибший смоук (мозок відповідей на наявних даних, без інжесту) — `POST /answer` з
[`bot.md`](bot.md) «Перевірка», п.2. Успіх = `/health`=200 **і** `migrate` вийшов 0 **і** app `Up`.

## Відкат

```bash
cd /opt/app
sudo git log --oneline -8            # знайти попередній добрий sha
sudo git checkout <sha>
sudo docker compose -f docker-compose.yml up -d --build
# коли розібрався — повернутись на гілку: sudo git checkout main
```

> **⚠️ Міграції самі не відкочуються.** `git checkout` вертає лише код. Якщо погана версія
> додала Alembic-ревізію, схема RDS лишиться новою, і старий код може впасти на новій колонці
> (або навпаки). Для схемних змін надійніше **котити вперед** (фікс + новий деплой), ніж відкочувати.
> Свідомий даунґрейд (лише якщо `downgrade` написаний і безпечний):
> `sudo docker compose -f docker-compose.yml run --rm migrate alembic downgrade -1`.

## Застереження

| Ризик | Дія |
|-------|-----|
| Забув `-f docker-compose.yml` | Compose підхопить `docker-compose.override.yml` і спробує підняти **локальний postgres**, якого на боксі не має бути. Прапорець `-f` — **завжди явно**, кожну команду. |
| Telethon single-session | Не піднімай локальний `python -m prophet_checker` проти тієї ж сесії, поки бокс живий: Telegram уб'є auth-key (`AuthKeyDuplicatedError` — вже палили 2026-07-12, релогін ручний). Локальний `.env` має `telegram_source_enabled=false`, але не покладайся сліпо. |
| Перезалив `tg_session` із S3 | `git pull` сесію не чіпає. Але якщо колись зробиш `aws s3 cp .../tg_session.session` — знову `sudo chown 1000:1000 tg_session.session`, інакше Telethon кине «readonly database» (uid контейнера = 1000). |
| IP боксу змінився | Публічний IP не стабільний (нема Elastic IP) — резолв щоразу (Крок 1). |
| Твій IP змінився | SSH не пустить (app-SG ingress = старий IP) — онови (див. [`stop-env.md`](stop-env.md)). |
| Free-tier горить | t3.small їсть CPU-кредити (build — сплеск), RDS backup retention=1. Не тримай бокс живим без потреби — після деплою або лиши свідомо, або на паузу ([`stop-env.md`](stop-env.md)). |
| Змінив інфру, не код | Правки CFN-стеків — це `aws cloudformation update-stack`, **не** цей runbook. Тут лише код. |

---

_Складено 2026-07-12 зі `deploy/cloudformation/{compute,data}-stack.yaml`, `docker-compose.yml`,
`Dockerfile` і наявних runbook-ів. End-to-end на боксі цієї сесії **не ганявся** (нема AWS-креденшелів;
box-деплой — за користувачем, як і решта AWS-кроків у `progress.md`). Команди звірені зі стеками й
compose, живого прогону чекають._

_Оновлено 2026-07-13: `deploy.sh` тепер рефрешить `.env` з S3 (+`--force-recreate`); додано
`deploy/refresh.sh` (лише секрети) і `deploy/connect.sh` (shell у сервісі)._
