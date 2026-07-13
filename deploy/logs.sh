#!/usr/bin/env bash
#
# logs.sh — подивитись логи застосунку на живому AWS-боксі однією командою.
#
# Резолвить бокс (tag Name=prophet-checker, running) → SSH → `docker compose logs`.
# Бокс лише-SSH (публічного HTTP/лог-агрегатора нема), IP міняється при кожному
# старті — тому резолвимо динамічно, як deploy.sh. Строго read-only (лише читає логи).
#
# Приклади:
#   ./deploy/logs.sh                   # останні 100 рядків app (API + бот)
#   ./deploy/logs.sh -f                # стрім (follow), Ctrl-C щоб вийти
#   ./deploy/logs.sh --tail 500        # більше історії
#   ./deploy/logs.sh --since 30m       # за останні 30 хвилин
#   ./deploy/logs.sh --migrate         # логи one-shot міграцій (дебаг деплою)
#
# Конфіг через env (є дефолти): REGION, SSH_KEY, SSH_USER, BOX_TAG, SSH_OPTS.

set -euo pipefail

REGION="${REGION:-eu-central-1}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/prophet-checker-key.pem}"
SSH_USER="${SSH_USER:-ec2-user}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8}"

SERVICE="app"
TAIL="100"
SINCE=""
FOLLOW=0

usage() { sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 2; }

# --- аргументи ---
while [ $# -gt 0 ]; do
  case "$1" in
    -f|--follow)  FOLLOW=1 ;;
    --migrate)    SERVICE="migrate" ;;
    -n|--tail)    shift; TAIL="${1:-}" ;;
    --tail=*)     TAIL="${1#*=}" ;;
    --since)      shift; SINCE="${1:-}" ;;
    --since=*)    SINCE="${1#*=}" ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# --- preflight ---
command -v aws >/dev/null || die "нема aws CLI"
command -v ssh >/dev/null || die "нема ssh"
[ -f "$SSH_KEY" ] || die "нема SSH-ключа: $SSH_KEY (задай через SSH_KEY=...)"

# --- резолв боксу (як deploy.sh: describe-instances → id → IP) ---
BOX="$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$BOX_TAG" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
{ [ -n "$BOX" ] && [ "$BOX" != "None" ]; } || \
  die "нема живого боксу '$BOX_TAG'. Env на паузі? Див. runbook/stop-env.md «Підйом»."
[ "$(printf '%s' "$BOX" | wc -w)" -eq 1 ] || die "кілька живих боксів: $BOX — не вгадую."

IP="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$BOX" \
  --query 'Reservations[].Instances[].PublicIpAddress' --output text)"
{ [ -n "$IP" ] && [ "$IP" != "None" ]; } || die "у боксу $BOX нема публічного IP."

# --- віддалена команда (read-only: лише читає логи) ---
REMOTE_CMD="cd /opt/app && sudo docker compose -f docker-compose.yml logs --tail=$TAIL"
[ -n "$SINCE" ] && REMOTE_CMD="$REMOTE_CMD --since $SINCE"
[ "$FOLLOW" = "1" ] && REMOTE_CMD="$REMOTE_CMD -f"
REMOTE_CMD="$REMOTE_CMD $SERVICE"

info="→ $BOX ($IP) — logs $SERVICE"
[ "$FOLLOW" = "1" ] && info="$info (follow, Ctrl-C щоб вийти)"
echo "$info" >&2

# follow → форсимо tty (-tt), щоб Ctrl-C коректно долітав до віддаленого docker compose.
# Дві гілки, а не порожній масив: bash 3.2 (дефолт macOS) падає на "${arr[@]}" під set -u.
# shellcheck disable=SC2086
if [ "$FOLLOW" = "1" ]; then
  exec ssh $SSH_OPTS -tt -i "$SSH_KEY" "$SSH_USER@$IP" "$REMOTE_CMD"
else
  exec ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$IP" "$REMOTE_CMD"
fi
