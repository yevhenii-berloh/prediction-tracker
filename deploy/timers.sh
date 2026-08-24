#!/usr/bin/env bash
#
# timers.sh — стан розкладу інжесту/верифікації на живому AWS-боксі.
#
# Резолвить бокс (tag Name=prophet-checker, running) → SSH → systemd. За замовчуванням
# READ-ONLY: показує, чи заряджені таймери, коли наступний запуск і чим закінчились
# останні. Побратими: status.sh, logs.sh, refresh.sh.
#
# Приклади:
#   ./deploy/timers.sh                 # стан + останні результати (read-only)
#   ./deploy/timers.sh --tail 25       # більше історії на таймер
#   ./deploy/timers.sh --dry-run       # надрукувати план, нічого не робити
#
# Конфіг через env (є дефолти): REGION, SSH_KEY, SSH_USER, BOX_TAG, SSH_OPTS, BOX_DIR.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGION="${REGION:-eu-central-1}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/prophet-checker-key.pem}"
SSH_USER="${SSH_USER:-ec2-user}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
# ServerAliveInterval: тік мовчить хвилинами — keepalive, щоб NAT/firewall не рвав SSH.
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8 -o ServerAliveInterval=30}"
BOX_DIR="${BOX_DIR:-$HERE/box}"

MODE="status"
TAIL="10"
DRY_RUN=0

usage() { sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 2; }

# --- аргументи ---
while [ $# -gt 0 ]; do
  case "$1" in
    --tail)       shift; TAIL="${1:-}" ;;
    --tail=*)     TAIL="${1#*=}" ;;
    -n|--dry-run) DRY_RUN=1 ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

printf '%s' "$TAIL" | grep -Eq '^[1-9][0-9]*$' || die "--tail має бути додатнім цілим: '$TAIL'"

# --- віддалений блок: лише читання (list-timers + journalctl) ---
# Одинарні лапки — нічого не розкривається локально; $TAIL вставляємо конкатенацією.
REMOTE_STATUS='echo "== timers =="
systemctl list-timers --all --no-pager "prophet-*.timer" || true
for u in prophet-ingest prophet-verify; do
  echo
  echo "== $u ($(systemctl is-enabled "$u.timer" 2>/dev/null || echo not-installed)) =="
  sudo journalctl -u "$u.service" --no-pager -o short-iso -n '"$TAIL"' 2>/dev/null || echo "(нема записів)"
done'

REMOTE="$REMOTE_STATUS"

# --- dry-run: надрукувати й вийти (без AWS/SSH, працює будь-де) ---
if [ "$DRY_RUN" -eq 1 ]; then
  echo "# DRY RUN — нічого не виконується"
  echo "# region=$REGION  box_tag=$BOX_TAG  ssh_user=$SSH_USER  mode=$MODE  tail=$TAIL"
  echo "# резолв боксу: aws ec2 describe-instances (Name=$BOX_TAG, state=running) → IP"
  echo "# --- віддалений блок ---"
  printf '%s\n' "$REMOTE"
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

# --- виконання ---
# shellcheck disable=SC2086
ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$IP" "$REMOTE"
