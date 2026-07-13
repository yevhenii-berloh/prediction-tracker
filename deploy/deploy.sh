#!/usr/bin/env bash
#
# deploy.sh — залити нову версію коду на живий AWS-бокс однією командою.
#
# Драйвить бокс по SSH: резолвить instance → git pull (або checkout --ref) →
# docker compose up -d --build → перевіряє exit-code migrate → health-loop.
# Це виконувана версія runbook/deploy.md (там — «чому» і кроки-судження).
#
# СВІДОМО НЕ робить: не пушить/не мерджить (Крок 0 — рука), не відкочує міграції,
# не чіпає CloudFormation. Пуш нової версії у ПУБЛІЧНИЙ репо — за тобою.
#
# Приклади:
#   ./deploy/deploy.sh                 # деплой latest main, з підтвердженням
#   ./deploy/deploy.sh -y              # без підтвердження
#   ./deploy/deploy.sh --ref v1.2.0    # конкретний коміт/тег/гілка
#   ./deploy/deploy.sh --dry-run       # надрукувати віддалений блок, нічого не робити
#
# Конфіг через env (є дефолти): REGION, SSH_KEY, SSH_USER, BOX_TAG, SSH_OPTS.

set -euo pipefail

REGION="${REGION:-eu-central-1}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/prophet-checker-key.pem}"
SSH_USER="${SSH_USER:-ec2-user}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
SECRETS_STACK="${SECRETS_STACK:-prophet-secrets}"
# IP боксу змінюється при кожному stop/start → не тримаємо known_hosts (інакше warning на зміну ключа).
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ASSUME_YES=0
DRY_RUN=0
REF=""

usage() {
  sed -n '3,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

die() { echo "ERROR: $*" >&2; exit 1; }

# --- аргументи ---
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)     ASSUME_YES=1 ;;
    -n|--dry-run) DRY_RUN=1 ;;
    --ref)        shift; REF="${1:-}" ;;
    --ref=*)      REF="${1#*=}" ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

if [ -n "$REF" ] && ! printf '%s' "$REF" | grep -Eq '^[A-Za-z0-9._/-]+$'; then
  die "--ref має неприпустимі символи: '$REF' (дозволено A-Z a-z 0-9 . _ / -)"
fi

# --- віддалений блок (виконується на боксі; $1 = REF, $2 = BUCKET) ---
# Лапкований heredoc: нічого не розкривається локально, усе — на боксі.
read -r -d '' REMOTE <<'REMOTE_EOF' || true
set -euo pipefail
REF="${1:-}"
BUCKET="${2:-}"
[ -n "$BUCKET" ] || { echo "нема BUCKET у віддаленому блоці" >&2; exit 1; }
compose() { sudo docker compose -f docker-compose.yml "$@"; }

cd /opt/app

echo "== репо, яке тягне бокс (звірка з Кроком 0) =="
sudo git -C /opt/app remote -v

echo "== нова версія =="
if [ -n "$REF" ]; then
  sudo git fetch --all --prune
  sudo git checkout "$REF"
else
  sudo git pull --ff-only
fi
sudo git log --oneline -1

echo "== свіжі секрети з S3 (роль інстансу читає бакет) =="
sudo aws s3 cp "s3://$BUCKET/.env" /opt/app/.env

echo "== build & up (на t3.small небистро; --force-recreate → перечитати env_file) =="
if ! compose up -d --build --force-recreate; then
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

echo "== compose ps =="
compose ps
echo "OK: деплой завершено на боксі"
REMOTE_EOF

# --- dry-run: надрукувати й вийти (без AWS/SSH, працює будь-де) ---
if [ "$DRY_RUN" -eq 1 ]; then
  echo "# DRY RUN — нічого не виконується"
  echo "# region=$REGION  box_tag=$BOX_TAG  ssh_user=$SSH_USER  ref=${REF:-<latest main>}  secrets_stack=$SECRETS_STACK"
  echo "# резолв бакета: aws cloudformation describe-stacks (SecretsBucketName) або SECRETS_BUCKET=..."
  echo "# резолв боксу: aws ec2 describe-instances (Name=$BOX_TAG, state=running) → IP"
  echo "# потім: printf '%s' \"\$REMOTE\" | ssh $SSH_OPTS -i $SSH_KEY $SSH_USER@<ip> bash -s -- '${REF:-}' '<bucket>'"
  echo "# --- віддалений блок ---"
  printf '%s\n' "$REMOTE"
  exit 0
fi

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

# --- preflight ---
command -v aws >/dev/null || die "нема aws CLI"
command -v ssh >/dev/null || die "нема ssh"
[ -f "$SSH_KEY" ] || die "нема SSH-ключа: $SSH_KEY (задай через SSH_KEY=...)"

# локальна git-гігієна (best-effort, не фатально): бокс тягне ПУБЛІЧНИЙ репо — пуш за тобою
if git -C "$SCRIPT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  branch="$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  [ "$branch" = "main" ] || echo "WARN: локальна гілка '$branch' — бокс тягне дефолтну (main)."
  [ -z "$(git -C "$SCRIPT_DIR" status --porcelain 2>/dev/null)" ] || echo "WARN: є незакомічені зміни — вони не поїдуть."
  if git -C "$SCRIPT_DIR" rev-parse --verify -q origin/main >/dev/null 2>&1; then
    ahead="$(git -C "$SCRIPT_DIR" rev-list --count origin/main..HEAD 2>/dev/null || echo 0)"
    [ "${ahead:-0}" = "0" ] || echo "WARN: локальний HEAD на $ahead коміт(ів) попереду origin/main — запушив?"
  fi
  echo "NOTE: бокс тягне ПУБЛІЧНИЙ репо; переконайся, що версія там (deploy.md Крок 0)."
fi

# --- резолв боксу ---
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

BUCKET="$(resolve_bucket)"
echo "bucket=$BUCKET"

# --- підтвердження ---
if [ "$ASSUME_YES" -eq 0 ]; then
  printf 'Деплой %s на бокс %s (%s)? [y/N] ' "${REF:-latest main}" "$BOX" "$IP"
  read -r ans
  case "$ans" in y|Y|yes|YES|Yes) ;; *) echo "скасовано."; exit 0 ;; esac
fi

# --- виконання (REMOTE — літерал; REF=$1, BUCKET=$2) ---
# shellcheck disable=SC2086
printf '%s' "$REMOTE" | ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$IP" bash -s -- "$REF" "$BUCKET"

echo "✅ Деплой OK: $BOX ($IP), версія ${REF:-latest main}"
