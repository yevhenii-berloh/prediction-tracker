#!/usr/bin/env bash
#
# status.sh — read-only статус prod-середовища prophet-checker на AWS.
#
# Відповідає на одне питання: «що зараз живе?» — по шарах, кожен терпить
# відсутність нижчого (стеки → EC2 → RDS → застосунок по SSH). Env часто
# на паузі (EC2+RDS stopped) заради кредитів, а бокс лише-SSH (нема публічного
# HTTP, IP міняється при кожному старті) — тож глибокий шар best-effort.
#
# СВІДОМО read-only: лише describe-* / sts get-caller-identity / read-only SSH.
# НІКОЛИ не start/stop/create/update/delete — пауза й підйом живуть у runbook/stop-env.md.
#
# Приклади:
#   ./deploy/status.sh            # повний статус + спроба SSH-health
#   ./deploy/status.sh --no-ssh   # лише AWS-рівень (працює будь-де, без ключа)
#
# Конфіг через env (є дефолти): REGION, BOX_TAG, SSH_KEY, SSH_USER, SSH_OPTS,
#   SECRETS_STACK, DATA_STACK, COMPUTE_STACK.

# НЕ -e: статус має дійти до кінця й відрепортити, навіть коли шар нижче відсутній.
set -uo pipefail

REGION="${REGION:-eu-central-1}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/prophet-checker-key.pem}"
SSH_USER="${SSH_USER:-ec2-user}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8}"
SECRETS_STACK="${SECRETS_STACK:-prophet-secrets}"
DATA_STACK="${DATA_STACK:-prophet-data}"
COMPUTE_STACK="${COMPUTE_STACK:-prophet-compute}"

NO_SSH=0

# --- глобали статусу (заповнюють check_*; verdict читає) ---
ACCOUNT=""
STACKS_PRESENT=0
EC2_STATE="absent"
EC2_IP=""
RDS_STATUS="absent"
APP="unknown"

usage() { sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 2; }
row() { printf '  %-18s %s\n' "$1" "$2"; }

# --- аргументи ---
while [ $# -gt 0 ]; do
  case "$1" in
    --no-ssh)  NO_SSH=1 ;;
    -h|--help) usage; exit 0 ;;
    *)         echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# --- віддалений read-only блок (виконується на боксі; лапкований — нічого не розкривається локально) ---
read -r -d '' REMOTE <<'REMOTE_EOF' || true
set -uo pipefail
compose() { sudo docker compose -f docker-compose.yml "$@"; }
cd /opt/app 2>/dev/null || { echo "HEALTH=000"; echo "MIGRATE=NA"; echo "APPUP=0"; exit 0; }
health="$(curl -s -o /dev/null -w '%{http_code}' localhost:8000/health 2>/dev/null || echo 000)"
mig_cid="$(compose ps -aq migrate 2>/dev/null || true)"
if [ -n "$mig_cid" ]; then
  mig="$(sudo docker inspect -f '{{.State.ExitCode}}' "$mig_cid" 2>/dev/null || echo NA)"
else
  mig="NA"
fi
up="$(compose ps --status running -q 2>/dev/null | wc -l | tr -d ' ')"
echo "HEALTH=$health"
echo "MIGRATE=$mig"
echo "APPUP=${up:-0}"
REMOTE_EOF

preflight() {
  command -v aws >/dev/null || die "нема aws CLI"
  if ! ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"; then
    die "нема валідних AWS-креденшелів (aws sts get-caller-identity впав)"
  fi
}

check_stacks() {
  echo "CloudFormation"
  local s st
  for s in "$SECRETS_STACK" "$DATA_STACK" "$COMPUTE_STACK"; do
    if st="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$s" \
             --query 'Stacks[0].StackStatus' --output text 2>/dev/null)"; then
      STACKS_PRESENT=$((STACKS_PRESENT + 1))
    else
      st="ABSENT"
    fi
    row "$s" "$st"
  done
}

check_ec2() {
  echo "EC2"
  local out
  out="$(aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Name,Values=$BOX_TAG" \
              "Name=instance-state-name,Values=running,stopped,pending,stopping" \
    --query 'Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress]' \
    --output text 2>/dev/null)"
  if [ -z "$out" ]; then
    EC2_STATE="absent"
    row "(none)" "—"
    return
  fi
  local line
  line="$(printf '%s\n' "$out" | head -1)"
  local id state ip
  id="$(printf '%s' "$line" | awk '{print $1}')"
  state="$(printf '%s' "$line" | awk '{print $2}')"
  ip="$(printf '%s' "$line" | awk '{print $3}')"
  [ "$ip" = "None" ] && ip=""
  EC2_STATE="$state"
  EC2_IP="$ip"
  row "$id" "$state   ip ${ip:-—}"
}

check_rds() {
  echo "RDS"
  local out
  out="$(aws rds describe-db-instances --region "$REGION" \
    --query "DBInstances[?starts_with(DBInstanceIdentifier,'$DATA_STACK')].[DBInstanceIdentifier,DBInstanceStatus]" \
    --output text 2>/dev/null)"
  if [ -z "$out" ]; then
    RDS_STATUS="absent"
    row "(none)" "—"
    return
  fi
  local line id status
  line="$(printf '%s\n' "$out" | head -1)"
  id="$(printf '%s' "$line" | awk '{print $1}')"
  status="$(printf '%s' "$line" | awk '{print $2}')"
  RDS_STATUS="$status"
  row "$id" "$status"
}

# app-шар best-effort: тільки коли бокс running, є IP і є ключ — інакше skip з причиною.
check_app() {
  echo "App (SSH)"
  if [ "$NO_SSH" = "1" ]; then
    APP="skip:no-ssh-flag"
    row "skipped" "--no-ssh (app health unchecked)"
    return
  fi
  if [ "$EC2_STATE" != "running" ]; then
    local why="box not running"
    [ "$EC2_STATE" = "stopped" ] && why="box stopped"
    [ "$EC2_STATE" = "absent" ] && why="no box"
    APP="skip:not-running"
    row "skipped" "$why"
    return
  fi
  if [ -z "$EC2_IP" ]; then
    APP="skip:no-ip"
    row "skipped" "no public IP"
    return
  fi
  if [ ! -f "$SSH_KEY" ]; then
    APP="skip:no-key"
    row "skipped" "no key at $SSH_KEY"
    return
  fi

  local out
  # shellcheck disable=SC2086
  if out="$(printf '%s' "$REMOTE" | ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$EC2_IP" bash -s 2>/dev/null)"; then
    local health mig up
    health="$(printf '%s\n' "$out" | sed -n 's/^HEALTH=//p' | head -1)"
    mig="$(printf '%s\n' "$out" | sed -n 's/^MIGRATE=//p' | head -1)"
    up="$(printf '%s\n' "$out" | sed -n 's/^APPUP=//p' | head -1)"
    row "/health" "${health:-?}"
    row "migrate exit" "${mig:-?}"
    row "running services" "${up:-?}"
    if [ "${health:-}" = "200" ]; then APP="ok"; else APP="degraded"; fi
  else
    APP="unreachable"
    row "skipped" "SSH unreachable (check SG ingress / your IP)"
  fi
}

verdict() {
  echo
  if [ "$STACKS_PRESENT" -eq 0 ] && [ "$EC2_STATE" = "absent" ] && [ "$RDS_STATUS" = "absent" ]; then
    echo "Verdict: NOT DEPLOYED"
    return
  fi
  if [ "$EC2_STATE" = "stopped" ]; then
    echo "Verdict: PAUSED"
    return
  fi
  if [ "$EC2_STATE" = "running" ]; then
    case "$APP" in
      ok)               echo "Verdict: UP (healthy)" ;;
      skip:no-ssh-flag) echo "Verdict: RUNNING (app health unchecked)" ;;
      *)                echo "Verdict: PARTIAL" ;;
    esac
    return
  fi
  echo "Verdict: PARTIAL"
}

main() {
  preflight
  echo "prophet-checker — AWS status ($REGION, acct $ACCOUNT)"
  echo
  check_stacks
  check_ec2
  check_rds
  check_app
  verdict
}

main
