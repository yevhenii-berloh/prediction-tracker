#!/usr/bin/env bash
#
# verify.sh — запустити один цикл верифікації на живому AWS-боксі однією командою.
#
# Резолвить бокс (tag Name=prophet-checker, running) → SSH → curl -X POST
# localhost:8000/verify/run на боксі (порт 8000 лише на localhost боксу, як
# health-loop у deploy.sh) → чекає VerificationCycleReport і друкує підсумок.
#
# МУТУЄ ПРОД: пише статуси/confidence/evidence прогнозів у БД, палить LLM-гроші
# (Verifier робить 2 виклики на прогноз). Тому за замовчуванням питає підтвердження
# (як deploy.sh); -y щоб пропустити. Синхронний: HTTP-запит блокується до кінця циклу.
#
# Приклади:
#   ./deploy/verify.sh                 # верифікувати весь бэклог unverified, з підтвердженням
#   ./deploy/verify.sh --limit 5       # лише перші 5 (спершу мала партія — розумно)
#   ./deploy/verify.sh -y              # без підтвердження
#   ./deploy/verify.sh --timeout 1800  # довший ліміт на цикл (сек)
#   ./deploy/verify.sh --dry-run       # надрукувати план, нічого не робити
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
LIMIT=""

usage() { sed -n '3,21p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 2; }

# --- аргументи ---
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)     ASSUME_YES=1 ;;
    -n|--dry-run) DRY_RUN=1 ;;
    --timeout)    shift; TIMEOUT="${1:-}" ;;
    --timeout=*)  TIMEOUT="${1#*=}" ;;
    --limit)      shift; LIMIT="${1:-}" ;;
    --limit=*)    LIMIT="${1#*=}" ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

printf '%s' "$TIMEOUT" | grep -Eq '^[0-9]+$' || die "TIMEOUT має бути цілим числом секунд: '$TIMEOUT'"
if [ -n "$LIMIT" ]; then
  printf '%s' "$LIMIT" | grep -Eq '^[1-9][0-9]*$' || die "--limit має бути додатнім цілим: '$LIMIT'"
fi

# URL з опційним ?limit=N (ендпоінт приймає limit як query-параметр).
URL="localhost:8000/verify/run"
[ -n "$LIMIT" ] && URL="$URL?limit=$LIMIT"

# Віддалена команда: curl на боксі. -w кладе HTTP-код окремим рядком-маркером після тіла;
# 2>&1 — щоб помилка з'єднання curl (app лежить) теж долетіла назад для діагностики.
REMOTE_CMD="curl -sS -m $TIMEOUT -w '\n__HTTP__ %{http_code}\n' -X POST $URL 2>&1"

# --- dry-run: надрукувати й вийти (без AWS/SSH, працює будь-де) ---
if [ "$DRY_RUN" -eq 1 ]; then
  echo "# DRY RUN — нічого не виконується"
  echo "# region=$REGION  box_tag=$BOX_TAG  ssh_user=$SSH_USER  timeout=${TIMEOUT}s  limit=${LIMIT:-<весь бэклог>}"
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

# --- підтвердження (верифікація мутує прод) ---
if [ "$ASSUME_YES" -eq 0 ]; then
  printf 'Запустити цикл верифікації на боксі %s (%s)? Пише статуси в прод-БД і палить LLM-гроші. [y/N] ' "$BOX" "$IP"
  read -r ans
  case "$ans" in y|Y|yes|YES|Yes) ;; *) echo "скасовано."; exit 0 ;; esac
fi

# --- виконання ---
echo "→ цикл верифікації на боксі (таймаут ${TIMEOUT}с, цикл може тривати хвилини)..." >&2
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
  printf '%s' "$body" | jq -r '"Підсумок: \(.verified) верифіковано, \(.failed) провалено, \(.skipped) пропущено"'
  errs="$(printf '%s' "$body" | jq -r '.entries[] | select(.error != null) | "  ⚠ \(.prediction_id): \(.error)"')"
  [ -n "$errs" ] && { echo "Прогнози з помилками:"; printf '%s\n' "$errs"; }
else
  printf '%s\n' "$body"
  echo "(встанови jq для читабельного підсумку)" >&2
fi

echo "✅ Цикл верифікації завершено на боксі $BOX ($IP)"
