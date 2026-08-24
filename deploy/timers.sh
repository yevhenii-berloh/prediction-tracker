#!/usr/bin/env bash
#
# timers.sh — розклад інжесту/верифікації на живому AWS-боксі: стан, установка, зняття.
#
# Резолвить бокс (tag Name=prophet-checker, running) → SSH → systemd. За замовчуванням
# READ-ONLY: показує, чи заряджені таймери, коли наступний запуск і чим закінчились
# останні. Мутуючі режими (--install/--uninstall) питають підтвердження, -y пропускає.
# Побратими: status.sh, logs.sh, refresh.sh.
#
# Приклади:
#   ./deploy/timers.sh                 # стан + останні результати (read-only)
#   ./deploy/timers.sh --tail 25       # більше історії на таймер
#   ./deploy/timers.sh --install       # залити юніти з deploy/box/ і зарядити таймери
#   ./deploy/timers.sh --uninstall     # зняти таймери й прибрати юніти
#   ./deploy/timers.sh --run ingest    # прогнати один тік просто зараз (МУТУЄ ПРОД)
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
ASSUME_YES=0
RUN_NAME=""

usage() { sed -n '3,18p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 2; }

# --- аргументи ---
while [ $# -gt 0 ]; do
  case "$1" in
    --tail)       shift; TAIL="${1:-}" ;;
    --tail=*)     TAIL="${1#*=}" ;;
    --install)    MODE="install" ;;
    --uninstall)  MODE="uninstall" ;;
    --run)        MODE="run"; shift; RUN_NAME="${1:-}" ;;
    --run=*)      MODE="run"; RUN_NAME="${1#*=}" ;;
    -y|--yes)     ASSUME_YES=1 ;;
    -n|--dry-run) DRY_RUN=1 ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

printf '%s' "$TAIL" | grep -Eq '^[1-9][0-9]*$' || die "--tail має бути додатнім цілим: '$TAIL'"

if [ "$MODE" = "run" ]; then
  case "$RUN_NAME" in
    ingest|verify) ;;
    *) die "--run приймає лише 'ingest' або 'verify' (дано: '${RUN_NAME:-<порожньо>}')" ;;
  esac
fi

# --- віддалений блок: лише читання (list-timers + journalctl) ---
# Одинарні лапки — нічого не розкривається локально; $TAIL вставляємо конкатенацією.
REMOTE_STATUS='echo "== timers =="
systemctl list-timers --all --no-pager "prophet-*.timer" || true
for u in prophet-ingest prophet-verify; do
  echo
  echo "== $u ($(systemctl is-enabled "$u.timer" 2>/dev/null || echo not-installed)) =="
  sudo journalctl -u "$u.service" --no-pager -o short-iso -n '"$TAIL"' 2>/dev/null || echo "(нема записів)"
done'

# Юніти їдуть таром у stdin SSH-зʼєднання: текст юнітів лишається в одному місці
# (deploy/box/) і не може розійтись із тим, що перевіряють тести.
REMOTE_INSTALL='set -euo pipefail
tmp="$(mktemp -d)"
trap "rm -rf $tmp" EXIT
tar -C "$tmp" -xf -
sudo install -m 755 "$tmp/prophet-tick.sh" /usr/local/bin/prophet-tick.sh
sudo install -m 644 "$tmp/prophet-ingest.service" "$tmp/prophet-ingest.timer" \
  "$tmp/prophet-verify.service" "$tmp/prophet-verify.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prophet-ingest.timer prophet-verify.timer
systemctl list-timers --all --no-pager "prophet-*.timer"'

REMOTE_UNINSTALL='set -uo pipefail
sudo systemctl disable --now prophet-ingest.timer prophet-verify.timer || true
sudo rm -f /etc/systemd/system/prophet-ingest.service /etc/systemd/system/prophet-ingest.timer \
  /etc/systemd/system/prophet-verify.service /etc/systemd/system/prophet-verify.timer \
  /usr/local/bin/prophet-tick.sh
sudo systemctl daemon-reload
echo "юніти прибрано"'

# systemctl start на Type=oneshot блокується до кінця циклу, тож журнал після нього
# уже містить рядок підсумку. Код юніта пробрасуємо назад.
REMOTE_RUN='set -uo pipefail
sudo systemctl start prophet-'"$RUN_NAME"'.service; rc=$?
sudo journalctl -u prophet-'"$RUN_NAME"'.service --no-pager -o short-iso -n 20
exit $rc'

case "$MODE" in
  status)    REMOTE="$REMOTE_STATUS" ;;
  install)   REMOTE="$REMOTE_INSTALL" ;;
  uninstall) REMOTE="$REMOTE_UNINSTALL" ;;
  run)       REMOTE="$REMOTE_RUN" ;;
esac

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

if [ "$MODE" = "install" ]; then
  [ -d "$BOX_DIR" ] || die "нема каталогу з юнітами: $BOX_DIR (задай через BOX_DIR=...)"
  [ -f "$BOX_DIR/prophet-tick.sh" ] || die "у $BOX_DIR нема prophet-tick.sh"
fi

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

# --- підтвердження для мутуючих режимів ---
if [ "$MODE" != "status" ] && [ "$ASSUME_YES" -eq 0 ]; then
  if [ "$MODE" = "run" ]; then
    printf 'Прогнати тік %s на боксі %s (%s)? Пише в прод-БД і палить LLM-гроші. [y/N] ' \
      "$RUN_NAME" "$BOX" "$IP"
  else
    printf 'Режим %s на боксі %s (%s)? [y/N] ' "$MODE" "$BOX" "$IP"
  fi
  read -r ans || ans=""
  case "$ans" in y|Y|yes|YES|Yes) ;; *) echo "скасовано."; exit 0 ;; esac
fi

# --- виконання ---
# shellcheck disable=SC2086
case "$MODE" in
  install)
    tar -C "$BOX_DIR" -cf - . | ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$IP" "$REMOTE"
    echo "✅ таймери встановлено й заряджено на боксі $BOX ($IP)"
    ;;
  *)
    ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$IP" "$REMOTE"
    ;;
esac
