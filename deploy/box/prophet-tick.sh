#!/usr/bin/env bash
#
# prophet-tick.sh — один запланований цикл на боксі (ingest або verify).
#
# Живе на боксі як /usr/local/bin/prophet-tick.sh; запускається юнітами
# prophet-ingest.service / prophet-verify.service. Бере СПІЛЬНИЙ flock (обидва
# таймери — один лок-файл), б'є POST у localhost:8000 і пише рівно один рядок
# підсумку в stdout (systemd → journald).
#
# Usage: prophet-tick.sh <name> <url> <timeout-seconds>
#   name    — ingest | verify (йде в рядок логу)
#   url     — повний localhost-URL разом із query (?limit=50)
#   timeout — секунди, передається в curl -m
#
# Exit: 0 — цикл пройшов (HTTP 200) АБО тік пропущено (лок зайнятий — це норма).
#       1 — цикл не пройшов (не-200 або curl не достукався).
#       2 — погані аргументи.
#
# Конфіг через env: LOCK_FILE (дефолт /var/lock/prophet-tick.lock).

set -uo pipefail

LOCK_FILE="${LOCK_FILE:-/var/lock/prophet-tick.lock}"
# flock -E: окремий код на «лок зайнятий», інакше не відрізнити від exit 1 команди.
CONFLICT_CODE=99

name="${1:-}"
url="${2:-}"
timeout="${3:-}"

if [ -z "$name" ] || [ -z "$url" ] || [ -z "$timeout" ]; then
  echo "usage: prophet-tick.sh <name> <url> <timeout-seconds>" >&2
  exit 2
fi

# Перший захід перезапускає себе під flock; другий (PROPHET_TICK_LOCKED=1) уже з локом.
if [ "${PROPHET_TICK_LOCKED:-0}" != "1" ]; then
  export PROPHET_TICK_LOCKED=1
  flock -n -E "$CONFLICT_CODE" "$LOCK_FILE" "$0" "$name" "$url" "$timeout"
  rc=$?
  if [ "$rc" = "$CONFLICT_CODE" ]; then
    echo "tick=$name skipped: previous tick still running"
    exit 0
  fi
  exit "$rc"
fi

# --- звідси лок наш ---

# Сума одного числового поля по всьому тілу: "posts_seen":40 + "posts_seen":2 → 42.
# Без jq/python — на боксі гарантовані лише coreutils.
sum_field() {
  printf '%s' "$2" | grep -o "\"$1\":[0-9]*" | cut -d: -f2 | awk '{ s += $1 } END { print s + 0 }'
}

summarize() {
  local body="$2"
  if [ "$1" = "verify" ]; then
    printf 'verified=%s failed=%s skipped=%s' \
      "$(sum_field verified "$body")" \
      "$(sum_field failed "$body")" \
      "$(sum_field skipped "$body")"
    return
  fi
  local channels
  # grep -c рахує РЯДКИ, а тіло приходить одним рядком → рахуємо входження.
  channels="$(printf '%s' "$body" | grep -o '"person_source_id"' | wc -l | tr -d ' ')"
  printf 'channels=%s posts=%s predictions=%s post_failures=%s' \
    "$channels" \
    "$(sum_field posts_seen "$body")" \
    "$(sum_field predictions_extracted "$body")" \
    "$(sum_field posts_failed "$body")"
}

raw="$(curl -sS -m "$timeout" -w '\n__HTTP__ %{http_code}\n' -X POST "$url" 2>&1)"
code="$(printf '%s\n' "$raw" | sed -n 's/^__HTTP__ //p' | tail -1)"
body="$(printf '%s\n' "$raw" | sed '/^__HTTP__ /d')"

if [ "$code" = "200" ]; then
  echo "tick=$name ok http=200 $(summarize "$name" "$body")"
  exit 0
fi

# Не-200: тіло в один рядок і обрізане — journald не місце для повного дампу.
echo "tick=$name FAILED http=${code:-none} body=$(printf '%s' "$body" | tr '\n' ' ' | cut -c1-300)" >&2
exit 1
