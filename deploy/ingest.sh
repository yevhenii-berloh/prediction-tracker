#!/usr/bin/env bash
#
# ingest.sh — запустити один цикл інжесту на живому AWS-боксі однією командою.
#
# Резолвить бокс (tag Name=prophet-checker, running) → SSH → curl -X POST
# localhost:8000/ingest/run на боксі (порт 8000 лише на localhost боксу, як
# health-loop у deploy.sh) → чекає CycleReport і друкує підсумок.
#
# МУТУЄ ПРОД: пише передбачення в БД, рухає курсори каналів, палить LLM-гроші.
# Тому за замовчуванням питає підтвердження (як deploy.sh); -y щоб пропустити.
# Синхронний: HTTP-запит блокується до кінця циклу — тримай -m/TIMEOUT з запасом.
#
# Приклади:
#   ./deploy/ingest.sh                 # запустити цикл, з підтвердженням
#   ./deploy/ingest.sh -y              # без підтвердження
#   ./deploy/ingest.sh --timeout 1800  # довший ліміт на цикл (сек)
#   ./deploy/ingest.sh --dry-run       # надрукувати план, нічого не робити
#
# Конфіг через env (є дефолти): REGION, SSH_KEY, SSH_USER, BOX_TAG, SSH_OPTS, TIMEOUT.

set -euo pipefail

REGION="${REGION:-eu-central-1}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/prophet-checker-key.pem}"
SSH_USER="${SSH_USER:-ec2-user}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
# ServerAliveInterval: цикл може мовчати хвилинами — keepalive, щоб NAT/firewall не рвав SSH.
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8 -o ServerAliveInterval=30}"
TIMEOUT="${TIMEOUT:-900}"

ASSUME_YES=0
DRY_RUN=0

usage() { sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 2; }

# --- аргументи ---
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)     ASSUME_YES=1 ;;
    -n|--dry-run) DRY_RUN=1 ;;
    --timeout)    shift; TIMEOUT="${1:-}" ;;
    --timeout=*)  TIMEOUT="${1#*=}" ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

printf '%s' "$TIMEOUT" | grep -Eq '^[0-9]+$' || die "TIMEOUT має бути цілим числом секунд: '$TIMEOUT'"

# Віддалена команда: curl на боксі. -w кладе HTTP-код окремим рядком-маркером після тіла;
# 2>&1 — щоб помилка з'єднання curl (app лежить) теж долетіла назад для діагностики.
REMOTE_CMD="curl -sS -m $TIMEOUT -w '\n__HTTP__ %{http_code}\n' -X POST localhost:8000/ingest/run 2>&1"

# --- dry-run: надрукувати й вийти (без AWS/SSH, працює будь-де) ---
if [ "$DRY_RUN" -eq 1 ]; then
  echo "# DRY RUN — нічого не виконується"
  echo "# region=$REGION  box_tag=$BOX_TAG  ssh_user=$SSH_USER  timeout=${TIMEOUT}s"
  echo "# резолв боксу: aws ec2 describe-instances (Name=$BOX_TAG, state=running) → IP"
  echo "# потім на боксі: $REMOTE_CMD"
  exit 0
fi

# --- preflight ---
command -v aws >/dev/null || die "нема aws CLI"
command -v ssh >/dev/null || die "нема ssh"
[ -f "$SSH_KEY" ] || die "нема SSH-ключа: $SSH_KEY (задай через SSH_KEY=...)"

# --- резолв боксу (як deploy.sh/logs.sh: describe-instances → id → IP) ---
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

# --- підтвердження (інжест мутує прод) ---
if [ "$ASSUME_YES" -eq 0 ]; then
  printf 'Запустити цикл інжесту на боксі %s (%s)? Пише в прод-БД і палить LLM-гроші. [y/N] ' "$BOX" "$IP"
  read -r ans
  case "$ans" in y|Y|yes|YES|Yes) ;; *) echo "скасовано."; exit 0 ;; esac
fi

# --- виконання ---
echo "→ інжест-цикл на боксі (таймаут ${TIMEOUT}с, цикл може тривати хвилини)..." >&2
# shellcheck disable=SC2086
raw="$(ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$IP" "$REMOTE_CMD" 2>&1 || true)"

code="$(printf '%s\n' "$raw" | sed -n 's/^__HTTP__ //p' | tail -1)"
body="$(printf '%s\n' "$raw" | sed '/^__HTTP__ /d')"

# нема маркера → відповідь не долетіла (SSH недоступний / бокс не той)
[ -n "$code" ] || {
  echo "нема відповіді від app по SSH — бокс живий і застосунок піднятий? Глянь status.sh / logs.sh:" >&2
  printf '%s\n' "$raw" >&2
  exit 1
}

case "$code" in
  200) ;;  # нижче — успішний друк
  503) die "app віддав 503 — оркестратор ще не готовий (бокс щойно піднявся?). Спробуй за мить: status.sh" ;;
  500) echo "app віддав 500 — катастрофічний збій циклу. Логи: ./deploy/logs.sh" >&2
       printf '%s\n' "$body" >&2; exit 1 ;;
  000) echo "curl не достукався до app на боксі (лежить / не на 8000?). Глянь status.sh / logs.sh:" >&2
       printf '%s\n' "$body" >&2; exit 1 ;;
  *)   echo "app віддав HTTP $code:" >&2; printf '%s\n' "$body" >&2; exit 1 ;;
esac

# --- успіх: читабельний підсумок (jq best-effort) ---
if command -v jq >/dev/null 2>&1; then
  printf '%s' "$body" | jq .
  echo
  printf '%s' "$body" | jq -r '
    "Підсумок: \(.channels_processed | length) канал(ів), " +
    "\([.channels_processed[].posts_seen] | add // 0) постів, " +
    "\([.channels_processed[].posts_with_predictions] | add // 0) з передбаченнями, " +
    "\([.channels_processed[].predictions_extracted] | add // 0) витягнуто"'
  errs="$(printf '%s' "$body" | jq -r '.channels_processed[] | select(.error != null) | "  ⚠ \(.person_source_id): \(.error)"')"
  [ -n "$errs" ] && { echo "Канали з помилками:"; printf '%s\n' "$errs"; }
else
  printf '%s\n' "$body"
  echo "(встанови jq для читабельного підсумку)" >&2
fi

echo "✅ Інжест-цикл завершено на боксі $BOX ($IP)"
