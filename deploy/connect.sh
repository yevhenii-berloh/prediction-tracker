#!/usr/bin/env bash
#
# connect.sh — відкрити інтерактивний shell у docker-сервісі на живому боксі.
#
# Резолвить бокс (tag Name=prophet-checker, running) → SSH з tty → `docker compose
# exec <сервіс>`. Тобто ec2 → docker → сервіс однією командою. Бокс лише-SSH, IP
# міняється при кожному старті — тому резолвимо динамічно, як logs.sh/deploy.sh.
#
# Приклади:
#   ./deploy/connect.sh                # bash усередині контейнера app
#   ./deploy/connect.sh python         # запустити python у app (замість shell)
#   ./deploy/connect.sh -s migrate     # зайти в інший compose-сервіс
#   ./deploy/connect.sh --box          # shell на самому боксі (не в контейнері)
#   ./deploy/connect.sh -- psql "$DATABASE_URL"   # усе після -- = команда в контейнері
#
# Конфіг через env (є дефолти): REGION, SSH_KEY, SSH_USER, BOX_TAG, SSH_OPTS.

set -euo pipefail

REGION="${REGION:-eu-central-1}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/prophet-checker-key.pem}"
SSH_USER="${SSH_USER:-ec2-user}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8}"

BOX_SHELL=0
SVC="app"

usage() { sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 2; }

# --- аргументи (прапорці → перший не-прапорець = початок команди) ---
while [ $# -gt 0 ]; do
  case "$1" in
    --box)        BOX_SHELL=1 ;;
    -s|--service) shift; SVC="${1:-}" ;;
    --service=*)  SVC="${1#*=}" ;;
    -h|--help)    usage; exit 0 ;;
    --)           shift; break ;;
    -*)           echo "unknown arg: $1" >&2; usage; exit 2 ;;
    *)            break ;;
  esac
  shift
done
[ -n "$SVC" ] || die "порожній сервіс (-s <name>)"
CMD="$*"   # усе, що лишилось = команда; порожньо → інтерактивний shell

# --- який віддалений рядок виконати ---
# host-режим: shell/команда на самому боксі. container-режим: те саме, але через `compose exec`.
if [ "$BOX_SHELL" = "1" ]; then
  REMOTE_CMD="$CMD"
else
  INNER="${CMD:-bash}"   # дефолт-shell контейнера (образ python:slim має bash)
  REMOTE_CMD="cd /opt/app && sudo docker compose -f docker-compose.yml exec $SVC $INNER"
fi

# --- preflight ---
command -v aws >/dev/null || die "нема aws CLI"
command -v ssh >/dev/null || die "нема ssh"
[ -f "$SSH_KEY" ] || die "нема SSH-ключа: $SSH_KEY (задай через SSH_KEY=...)"

# --- резолв боксу (як logs.sh: describe-instances → id → IP) ---
BOX="$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$BOX_TAG" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
{ [ -n "$BOX" ] && [ "$BOX" != "None" ]; } || \
  die "нема живого боксу '$BOX_TAG'. Env на паузі? Див. runbook/stop-env.md «Підйом»."
[ "$(printf '%s' "$BOX" | wc -w)" -eq 1 ] || die "кілька живих боксів: $BOX — не вгадую."

IP="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$BOX" \
  --query 'Reservations[].Instances[].PublicIpAddress' --output text)"
{ [ -n "$IP" ] && [ "$IP" != "None" ]; } || die "у боксу $BOX нема публічного IP."

target="контейнер $SVC"; [ "$BOX_SHELL" = "1" ] && target="хост"
echo "→ $BOX ($IP) — $target${CMD:+ :: $CMD}" >&2

# -tt форсить tty, щоб інтерактивний shell / Ctrl-C коректно працювали через ssh.
# Дві гілки, а не порожній хвіст: порожній REMOTE_CMD у host-режимі → чистий login-shell.
# shellcheck disable=SC2086
if [ -n "$REMOTE_CMD" ]; then
  exec ssh $SSH_OPTS -tt -i "$SSH_KEY" "$SSH_USER@$IP" "$REMOTE_CMD"
else
  exec ssh $SSH_OPTS -tt -i "$SSH_KEY" "$SSH_USER@$IP"
fi
