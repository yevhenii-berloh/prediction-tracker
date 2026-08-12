#!/usr/bin/env bash
#
# stop.sh — пауза prod-середовища: зупинити EC2-бокс і RDS, щоб не палити кредити.
#
# Це ПАУЗА, не знесення: stop ≠ terminate. EBS боксу (код, tg_session) і storage
# RDS лишаються на місці, підйом — ./deploy/start.sh за хвилини. Знесення стеків
# (delete-stack) цей скрипт не робить і робити не вміє.
#
# Id-и боксу й RDS резолвляться динамічно (міняються при пересозданні стеків),
# тому нічого не хардкодиться. Вже зупинені ресурси пропускаються як no-op.
#
# Приклади:
#   ./deploy/stop.sh              # пауза, з підтвердженням
#   ./deploy/stop.sh -y           # без підтвердження
#   ./deploy/stop.sh --dry-run    # показати, що виконає, нічого не роблячи
#
# Конфіг через env (є дефолти): REGION, BOX_TAG, DATA_STACK.
#
# Застереження: RDS у stopped живе максимум 7 днів — далі AWS підніме її сама.
# Тримати довше — просто зупинити знову.

set -euo pipefail

REGION="${REGION:-eu-central-1}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
DATA_STACK="${DATA_STACK:-prophet-data}"

ASSUME_YES=0
DRY_RUN=0

usage() { sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 1; }
run() { if [ "$DRY_RUN" = "1" ]; then echo "DRY: $*"; else "$@"; fi; }

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)     ASSUME_YES=1 ;;
    -n|--dry-run) DRY_RUN=1 ;;
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

stop_box() {
  local box="$1" state
  state="$(box_state "$box")"
  if [ "$state" = "stopped" ] || [ "$state" = "stopping" ]; then
    echo "EC2 $box: вже $state — пропускаю"
    return
  fi
  echo "== зупиняю EC2 $box (був $state) =="
  run aws ec2 stop-instances --region "$REGION" --instance-ids "$box" --output text >/dev/null
}

stop_rds() {
  local rds="$1" state
  state="$(rds_state "$rds")"
  if [ "$state" = "stopped" ] || [ "$state" = "stopping" ]; then
    echo "RDS $rds: вже $state — пропускаю"
    return
  fi
  if [ "$state" != "available" ]; then
    # stop-db-instance приймає лише available; у backing-up/modifying команда впаде
    echo "RDS $rds: стан '$state', зупинка можлива лише з 'available' — пропускаю" >&2
    return
  fi
  echo "== зупиняю RDS $rds =="
  run aws rds stop-db-instance --region "$REGION" --db-instance-identifier "$rds" --output text >/dev/null
}

confirm() {
  [ "$ASSUME_YES" = "1" ] && return 0
  [ "$DRY_RUN" = "1" ] && return 0
  printf 'Зупинити середовище (пауза, дані зберігаються)? [y/N] '
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

  echo "prophet-checker — пауза середовища ($REGION)"
  echo "  box = ${box:-—}"
  echo "  rds = ${rds:-—}"
  confirm

  [ -n "$box" ] && stop_box "$box"
  [ -n "$rds" ] && stop_rds "$rds"

  echo
  echo "зупинка запущена (перехід у stopped триває хвилини)."
  echo "перевірити: ./deploy/status.sh --no-ssh   (очікуєш Verdict: PAUSED)"
  echo "підняти:    ./deploy/start.sh"
  echo "нагадування: RDS у stopped — максимум 7 днів, далі AWS підніме її сама."
}

main
