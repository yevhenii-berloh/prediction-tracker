#!/usr/bin/env bash
#
# start.sh — підйом паузнутого prod-середовища: RDS, потім бокс.
#
# Порядок НЕ довільний: застосунок на старті чекає БД, тож RDS піднімається
# першою і скрипт дочікується 'available' перед стартом боксу. Застосунок
# підіймається сам (docker увімкнено на буті, app має restart: unless-stopped).
#
# Публічний IP боксу змінюється при кожному старті (Elastic IP нема) — скрипт
# друкує новий у кінці. Якщо змінився ТВІЙ IP, SSH не пустить, доки не оновиш
# ingress app-SG (див. runbook/stop-env.md, «Застереження»).
#
# Приклади:
#   ./deploy/start.sh              # підйом, з підтвердженням
#   ./deploy/start.sh -y           # без підтвердження
#   ./deploy/start.sh --dry-run    # показати, що виконає, нічого не роблячи
#   ./deploy/start.sh --no-wait    # не чекати available/running (повернутись одразу)
#
# Конфіг через env (є дефолти): REGION, BOX_TAG, DATA_STACK.

set -euo pipefail

REGION="${REGION:-eu-central-1}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
DATA_STACK="${DATA_STACK:-prophet-data}"

ASSUME_YES=0
DRY_RUN=0
NO_WAIT=0

usage() { sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 1; }
run() { if [ "$DRY_RUN" = "1" ]; then echo "DRY: $*"; else "$@"; fi; }

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)     ASSUME_YES=1 ;;
    -n|--dry-run) DRY_RUN=1 ;;
    --no-wait)    NO_WAIT=1 ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

preflight() {
  command -v aws >/dev/null || die "нема aws CLI"
  aws sts get-caller-identity --query Account --output text >/dev/null 2>&1 \
    || die "нема валідних AWS-креденшелів (aws sts get-caller-identity впав)"
}

resolve_box() {
  aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Name,Values=$BOX_TAG" \
              "Name=instance-state-name,Values=running,stopped,pending,stopping" \
    --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null | head -1
}

resolve_rds() {
  aws rds describe-db-instances --region "$REGION" \
    --query "DBInstances[?starts_with(DBInstanceIdentifier,'$DATA_STACK')].DBInstanceIdentifier" \
    --output text 2>/dev/null | head -1
}

box_state() {
  aws ec2 describe-instances --region "$REGION" --instance-ids "$1" \
    --query 'Reservations[].Instances[].State.Name' --output text 2>/dev/null
}

rds_state() {
  aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$1" \
    --query 'DBInstances[].DBInstanceStatus' --output text 2>/dev/null
}

start_rds() {
  local rds="$1" state
  state="$(rds_state "$rds")"
  if [ "$state" = "available" ]; then
    echo "RDS $rds: вже available — пропускаю"
    return
  fi
  if [ "$state" = "stopped" ]; then
    echo "== піднімаю RDS $rds =="
    run aws rds start-db-instance --region "$REGION" --db-instance-identifier "$rds" --output text >/dev/null
  else
    echo "RDS $rds: стан '$state' — старт не потрібен, чекаю"
  fi
  [ "$NO_WAIT" = "1" ] && return
  echo "чекаю db-instance-available (кілька хвилин)…"
  run aws rds wait db-instance-available --region "$REGION" --db-instance-identifier "$rds"
}

start_box() {
  local box="$1" state
  state="$(box_state "$box")"
  if [ "$state" = "running" ]; then
    echo "EC2 $box: вже running — пропускаю"
  else
    echo "== піднімаю EC2 $box (був $state) =="
    run aws ec2 start-instances --region "$REGION" --instance-ids "$box" --output text >/dev/null
    [ "$NO_WAIT" = "1" ] || run aws ec2 wait instance-running --region "$REGION" --instance-ids "$box"
  fi
}

report_ip() {
  [ "$DRY_RUN" = "1" ] && return
  local ip
  ip="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$1" \
        --query 'Reservations[].Instances[].PublicIpAddress' --output text 2>/dev/null)"
  [ "$ip" = "None" ] && ip=""
  echo "новий публічний IP: ${ip:-—}"
}

confirm() {
  [ "$ASSUME_YES" = "1" ] && return 0
  [ "$DRY_RUN" = "1" ] && return 0
  printf 'Підняти середовище (RDS → бокс)? [y/N] '
  local answer
  read -r answer
  case "$answer" in
    y|Y|yes) return 0 ;;
    *) echo "скасовано"; exit 0 ;;
  esac
}

main() {
  preflight
  local box rds
  box="$(resolve_box)"
  rds="$(resolve_rds)"
  [ -n "$box" ] || [ -n "$rds" ] || die "не знайдено ні боксу з тегом $BOX_TAG, ні RDS $DATA_STACK*"

  echo "prophet-checker — підйом середовища ($REGION)"
  echo "  rds = ${rds:-—}  (перший: застосунок чекає БД)"
  echo "  box = ${box:-—}"
  confirm

  [ -n "$rds" ] && start_rds "$rds"
  [ -n "$box" ] && start_box "$box"
  [ -n "$box" ] && report_ip "$box"

  echo
  echo "застосунок підіймається сам (restart: unless-stopped)."
  echo "перевірити: ./deploy/status.sh   (очікуєш Verdict: UP (healthy))"
}

main
