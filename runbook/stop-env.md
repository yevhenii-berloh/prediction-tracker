# Runbook: зупинка середовища (пауза, НЕ знесення)

Тимчасово зупинити прод, щоб не палити кредити/готівку, зберігши **все** (дані RDS,
код і `tg_session` на боксі, стеки). Підйом — за хвилини. Це **не** `delete-stack`.

**Ресурси (станом на 2026-07-10, eu-central-1):**
- EC2 бокс: `i-0b2811b60cb09de92` (t3.small), тег `Name=prophet-checker`
- RDS: `prophet-data-dbinstance-uq42k7iljvbn` (db.t4g.micro)
- app-SG: `sg-0270bcebe877d72be` (SSH-ingress = твій IP)

> Id-и боксу/RDS змінюються при пересозданні стеків — нижче резолвимо їх динамічно, не хардкодимо.

## Резолв id-ів

```bash
REGION=eu-central-1
BOX=$(aws ec2 describe-instances --region $REGION \
  --filters "Name=tag:Name,Values=prophet-checker" \
            "Name=instance-state-name,Values=running,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)
RDS=$(aws rds describe-db-instances --region $REGION \
  --query "DBInstances[?starts_with(DBInstanceIdentifier,'prophet-data')].DBInstanceIdentifier" \
  --output text)
echo "box=$BOX  rds=$RDS"
```

## Зупинка

```bash
# 1) застосунок-бокс
aws ec2 stop-instances --region $REGION --instance-ids "$BOX"
# 2) RDS (можна тримати зупиненою до 7 днів — див. застереження)
aws rds stop-db-instance --region $REGION --db-instance-identifier "$RDS"
```

Підтвердити (очікуєш `stopped` / `stopped`):

```bash
aws ec2 describe-instances --region $REGION --instance-ids "$BOX" \
  --query 'Reservations[].Instances[].State.Name' --output text
aws rds describe-db-instances --region $REGION --db-instance-identifier "$RDS" \
  --query 'DBInstances[].DBInstanceStatus' --output text
```

## Підйом

Порядок важливий: **спершу RDS** (застосунок при старті чекає БД), потім бокс.

```bash
# 1) RDS
aws rds start-db-instance --region $REGION --db-instance-identifier "$RDS"
aws rds wait db-instance-available --region $REGION --db-instance-identifier "$RDS"

# 2) бокс
aws ec2 start-instances --region $REGION --instance-ids "$BOX"
aws ec2 wait instance-running --region $REGION --instance-ids "$BOX"

# новий публічний IP (змінюється при кожному старті):
aws ec2 describe-instances --region $REGION --instance-ids "$BOX" \
  --query 'Reservations[].Instances[].PublicIpAddress' --output text
```

Застосунок піднімається сам: docker увімкнено на буті, `app`-контейнер має
`restart: unless-stopped`. Перевірка:

```bash
ssh -i ~/.ssh/prophet-checker-key.pem ec2-user@<новий-IP> \
  'curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/health'   # очікуєш 200
```

## Застереження

- **RDS stop — максимум 7 днів.** Далі AWS сам її підніме. Щоб тримати довше — зупини знову
  (або постав cron на `stop-db-instance`).
- **Публічний IP боксу змінюється** при stop/start (нема Elastic IP). SSH-таргет бери з команди вище.
- **Якщо змінився твій IP** — SSH не пустить (app-SG ingress = старий IP). Онови:
  ```bash
  MYIP=$(curl -s https://checkip.amazonaws.com)/32
  aws ec2 authorize-security-group-ingress --region $REGION \
    --group-id sg-0270bcebe877d72be --protocol tcp --port 22 --cidr "$MYIP"
  ```
  (старе правило прибери `revoke-security-group-ingress`, або онови param `SshIngressCidr` у стеку `prophet-data`).
- **Нічого не губиться:** stop ≠ terminate — EBS боксу (код, `tg_session`) і storage RDS лишаються.
- `migrate` (one-shot, `restart: no`) при старті не перезапускається — схема вже накатана, це норм.

## Скільки коштує в паузі

| Ресурс | У паузі | ~$/міс |
|--------|---------|--------|
| EC2 (stopped) | compute $0, платиш лише за EBS 30GB | ~$2.9 |
| RDS (stopped) | instance $0, платиш за storage 20GB + бекапи | ~$0 (free-tier) |
| S3 secrets | дрібниця | ~$0 |

Зупинка знімає головного пожирача — **t3.small compute**. Кредити/готівка майже не течуть.

## Пауза ≠ знесення

- **Пауза (цей runbook):** усе зберігається, підйом за хвилини.
- **Знесення (інша операція):** `delete-stack prophet-compute` (бокс+EBS зникають) і/або
  `delete-stack prophet-data` (RDS зі snapshot через `DeletionPolicy: Snapshot`). Дані RDS
  переживають знесення `compute` — це і є сенс durability-спліту.
