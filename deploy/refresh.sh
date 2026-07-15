#!/usr/bin/env bash
#
# refresh.sh — підтягнути свіжі секрети з S3 на живий бокс і перезапустити застосунок.
#
# Драйвить бокс по SSH: резолвить бокс (як deploy.sh) → тягне `.env` і
# `tg_session.session` із S3-бакета в `/opt/app/` → `docker compose up -d
# --force-recreate` → перевіряє exit-code migrate і health-loop. Потрібен, бо бокс
# копіює секрети з S3 ЛИШЕ на bootstrap (user-data), а `deploy.sh` їх не чіпає —
# тож правка в S3 сама собою на живий бокс не долітає (див. runbook/bot.md «Прод»).
#
# `tg_session.session` тягнеться після релогіну (S3 = джерело правди): root пише
# файл → `chown 1000:1000` під uid контейнера (інакше Telethon «readonly database»).
# Нема обʼєкта в бакеті — не фатально, лишає наявну сесію на боксі.
#
# Приклади:
#   ./deploy/refresh.sh                # підтягнути секрети + рестарт, з підтвердженням
#   ./deploy/refresh.sh -y             # без підтвердження
#   ./deploy/refresh.sh --dry-run      # надрукувати віддалений блок, нічого не робити
#
# Конфіг через env (є дефолти): REGION, SSH_KEY, SSH_USER, BOX_TAG, SSH_OPTS,
#   SECRETS_STACK, SECRETS_BUCKET (обхід резолву).

set -euo pipefail

REGION="${REGION:-eu-central-1}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/prophet-checker-key.pem}"
SSH_USER="${SSH_USER:-ec2-user}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
# IP боксу змінюється при кожному stop/start → не тримаємо known_hosts (інакше warning на зміну ключа).
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR}"
SECRETS_STACK="${SECRETS_STACK:-prophet-secrets}"

ASSUME_YES=0
DRY_RUN=0

usage() { sed -n '3,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 1; }

# --- аргументи ---
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)     ASSUME_YES=1 ;;
    -n|--dry-run) DRY_RUN=1 ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# --- віддалений блок (виконується на боксі; $1 = BUCKET) ---
# Лапкований heredoc: нічого не розкривається локально, усе — на боксі.
read -r -d '' REMOTE <<'REMOTE_EOF' || true
set -euo pipefail
BUCKET="${1:-}"
[ -n "$BUCKET" ] || { echo "нема BUCKET у віддаленому блоці" >&2; exit 1; }
compose() { sudo docker compose -f docker-compose.yml "$@"; }

cd /opt/app

echo "== свіжі секрети з S3 (роль інстансу читає бакет) =="
sudo aws s3 cp "s3://$BUCKET/.env" /opt/app/.env

echo "== свіжа Telethon-сесія з S3 (root пише → chown на uid контейнера 1000) =="
# aws s3 cp на провал НЕ чіпає призначення, тож наявна сесія переживе відсутність обʼєкта.
if sudo aws s3 cp "s3://$BUCKET/tg_session.session" /opt/app/tg_session.session 2>/dev/null; then
  sudo chown 1000:1000 /opt/app/tg_session.session
  echo "tg_session.session оновлено"
else
  echo "note: tg_session.session нема в бакеті — лишаю наявну на боксі" >&2
fi

echo "== recreate, щоб перечитати env_file (на t3.small небистро) =="
if ! compose up -d --force-recreate; then
  echo "compose up впав — логи migrate:" >&2
  compose logs migrate | tail -50 >&2
  exit 1
fi

echo "== перевірка migrate (не мовчазний провал) =="
mig_cid="$(compose ps -aq migrate)"
[ -n "$mig_cid" ] || { echo "нема контейнера migrate" >&2; exit 1; }
mig_code="$(sudo docker inspect -f '{{.State.ExitCode}}' "$mig_cid")"
echo "migrate exit code: $mig_code"
if [ "$mig_code" != "0" ]; then
  echo "migrate НЕ вийшов 0 — логи:" >&2
  compose logs migrate | tail -50 >&2
  exit 1
fi

echo "== health-loop =="
code=""
for i in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' localhost:8000/health || true)"
  [ "$code" = "200" ] && { echo "health: 200 (спроба $i)"; break; }
  sleep 2
done
if [ "$code" != "200" ]; then
  echo "health != 200 (останнє: ${code:-none}) — логи app:" >&2
  compose logs app | tail -50 >&2
  exit 1
fi

echo "OK: секрети оновлено й застосунок перезапущено на боксі"
REMOTE_EOF

# --- резолв секрет-бакета (як secrets.sh: CFN-output, з обходом через SECRETS_BUCKET) ---
resolve_bucket() {
  if [ -n "${SECRETS_BUCKET:-}" ]; then echo "$SECRETS_BUCKET"; return; fi
  local b
  b="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$SECRETS_STACK" \
        --query "Stacks[0].Outputs[?OutputKey=='SecretsBucketName'].OutputValue" \
        --output text 2>/dev/null || true)"
  { [ -n "$b" ] && [ "$b" != "None" ]; } || \
    die "не резолвиться бакет — задай SECRETS_BUCKET=... або перевір стек $SECRETS_STACK"
  echo "$b"
}

# --- dry-run: надрукувати й вийти (без AWS/SSH, працює будь-де) ---
if [ "$DRY_RUN" -eq 1 ]; then
  echo "# DRY RUN — нічого не виконується"
  echo "# region=$REGION  box_tag=$BOX_TAG  ssh_user=$SSH_USER  secrets_stack=$SECRETS_STACK"
  echo "# резолв бакета: aws cloudformation describe-stacks (SecretsBucketName) або SECRETS_BUCKET=..."
  echo "# резолв боксу: aws ec2 describe-instances (Name=$BOX_TAG, state=running) → IP"
  echo "# потім: printf '%s' \"\$REMOTE\" | ssh $SSH_OPTS -i $SSH_KEY $SSH_USER@<ip> bash -s -- '<bucket>'"
  echo "# --- віддалений блок ---"
  printf '%s\n' "$REMOTE"
  exit 0
fi

# --- preflight ---
command -v aws >/dev/null || die "нема aws CLI"
command -v ssh >/dev/null || die "нема ssh"
[ -f "$SSH_KEY" ] || die "нема SSH-ключа: $SSH_KEY (задай через SSH_KEY=...)"

# --- резолв бакета ---
BUCKET="$(resolve_bucket)"
echo "bucket=$BUCKET"

# --- резолв боксу (як deploy.sh: describe-instances → id → IP) ---
echo "Резолв боксу (tag Name=$BOX_TAG, $REGION)..."
BOX="$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$BOX_TAG" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
{ [ -n "$BOX" ] && [ "$BOX" != "None" ]; } || \
  die "нема живого боксу '$BOX_TAG'. Env на паузі? Див. runbook/stop-env.md «Підйом»."
[ "$(printf '%s' "$BOX" | wc -w)" -eq 1 ] || die "кілька живих боксів: $BOX — не вгадую."

IP="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$BOX" \
  --query 'Reservations[].Instances[].PublicIpAddress' --output text)"
{ [ -n "$IP" ] && [ "$IP" != "None" ]; } || die "у боксу $BOX нема публічного IP."
echo "box=$BOX  ip=$IP"

# --- підтвердження (це прод: recreate контейнерів) ---
if [ "$ASSUME_YES" -eq 0 ]; then
  printf 'Оновити секрети з S3 і перезапустити застосунок на %s (%s)? [y/N] ' "$BOX" "$IP"
  read -r ans
  case "$ans" in y|Y|yes|YES|Yes) ;; *) echo "скасовано."; exit 0 ;; esac
fi

# --- виконання (REMOTE — літерал; BUCKET передається як $1) ---
# shellcheck disable=SC2086
printf '%s' "$REMOTE" | ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$IP" bash -s -- "$BUCKET"

echo "✅ Секрети оновлено й застосунок перезапущено: $BOX ($IP)"
