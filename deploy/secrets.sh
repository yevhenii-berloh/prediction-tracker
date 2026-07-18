#!/usr/bin/env bash
#
# secrets.sh — керування секрет-бакетом S3 (.env + tg_session) для prophet-checker.
#
# Безпечно пише конфіги у приватний S3-бакет, звідки бокс тягне їх на bootstrap.
# Дефолтний примітив — точкова правка ОДНОГО ключа (round-trip), щоб не затерти
# прод-only ключі (DATABASE_URL на RDS, токени), яких нема в локальному .env.
# Значення секретів НІКОЛИ не друкуються в термінал.
#
# Використання: ./deploy/secrets.sh [-y] [-n] <команда> [аргументи]
#   (глобальні прапорці йдуть ПЕРЕД командою)
#
# Команди:
#   set KEY VALUE     точково записати один ключ у .env (round-trip, решта ключів ціла)
#   unset KEY         прибрати ключ із .env
#   list              обʼєкти бакета + імена ключів у .env (без значень)
#   pull [dest]       завантажити .env у файл (⚠️ секрети; дефолт ./env.from-s3)
#   push <file>       ПОВНА заміна .env вмістом файлу (diff ключів + підтвердження)
#   put-file <l> [r]  залити довільний файл (напр. новий tg_session.session)
#
# Прапорці: -y/--yes (без підтверджень), -n/--dry-run (показати, не міняти), -h
# Env: SECRETS_BUCKET (обхід резолву), SECRETS_STACK (дефолт prophet-secrets), REGION
#
# Приклади:
#   ./deploy/secrets.sh set TELEGRAM_SOURCE_ENABLED false
#   ./deploy/secrets.sh list
#   ./deploy/secrets.sh -y put-file ./tg_session.session

set -euo pipefail

REGION="${REGION:-eu-central-1}"
SECRETS_STACK="${SECRETS_STACK:-prophet-secrets}"
ENV_KEY="${ENV_KEY:-.env}"        # імʼя обʼєкта env-файлу в бакеті
ASSUME_YES=0
DRY_RUN=0

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

die() { echo "ERROR: $*" >&2; exit 1; }

usage() { sed -n '3,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

confirm() {  # $1 = запит; поважає -y
  [ "$ASSUME_YES" = 1 ] && return 0
  printf '%s [y/N] ' "$1"
  local a; read -r a
  case "$a" in y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}

s3cp() { aws s3 cp --region "$REGION" --only-show-errors "$@"; }
s3uri() { echo "s3://$BUCKET/$1"; }
valid_key() { printf '%s' "$1" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*$'; }
key_names() { grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$1" 2>/dev/null | tr -d '=' | sort || true; }

fetch_env() {  # $1 = куди; 0 якщо є, 1 якщо обʼєкта нема
  s3cp "$(s3uri "$ENV_KEY")" "$1" 2>/dev/null
}

print_version() {  # новий version-id (versioning увімкнено на бакеті)
  local key="${1:-$ENV_KEY}" v
  v="$(aws s3api head-object --region "$REGION" --bucket "$BUCKET" --key "$key" \
        --query 'VersionId' --output text 2>/dev/null || true)"
  { [ -n "$v" ] && [ "$v" != "None" ]; } && echo "   S3 version: $v"
  return 0
}

resolve_bucket() {
  if [ -n "${SECRETS_BUCKET:-}" ]; then echo "$SECRETS_BUCKET"; return; fi
  local b
  b="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$SECRETS_STACK" \
        --query "Stacks[0].Outputs[?OutputKey=='SecretsBucketName'].OutputValue" \
        --output text 2>/dev/null || true)"
  { [ -n "$b" ] && [ "$b" != "None" ]; } || \
    die "не резолвиться бакет — задай SECRETS_BUCKET=... або перевір стек $SECRETS_STACK"
  echo "$b"
}

cmd_set() {  # $1=KEY $2=VALUE
  local key="$1" val="$2"
  valid_key "$key" || die "недопустимий ключ: $key"
  local cur="$TMP/cur" new="$TMP/new"
  if ! fetch_env "$cur"; then echo "note: $ENV_KEY ще нема — створюю новий"; : > "$cur"; fi
  grep -vE "^${key}=" "$cur" > "$new" || true    # прибрати старий рядок ключа
  printf '%s=%s\n' "$key" "$val" >> "$new"       # додати новий (значення — літерал)
  if [ "$DRY_RUN" = 1 ]; then
    echo "DRY: set $key (значення приховано) → $(s3uri "$ENV_KEY")"
    echo "DRY: ключі після = $(key_names "$new" | tr '\n' ' ')"
    return 0
  fi
  confirm "Записати ключ '$key' у $(s3uri "$ENV_KEY")?" || { echo "скасовано"; return 0; }
  s3cp "$new" "$(s3uri "$ENV_KEY")"
  echo "✅ set $key (значення приховано)"; print_version
}

cmd_unset() {  # $1=KEY
  local key="$1"
  valid_key "$key" || die "недопустимий ключ: $key"
  local cur="$TMP/cur" new="$TMP/new"
  fetch_env "$cur" || die "$ENV_KEY нема в бакеті"
  grep -qE "^${key}=" "$cur" || { echo "ключа '$key' і так нема — no-op"; return 0; }
  grep -vE "^${key}=" "$cur" > "$new" || true
  [ "$DRY_RUN" = 1 ] && { echo "DRY: прибрати $key з $(s3uri "$ENV_KEY")"; return 0; }
  confirm "Прибрати ключ '$key' з $(s3uri "$ENV_KEY")?" || { echo "скасовано"; return 0; }
  s3cp "$new" "$(s3uri "$ENV_KEY")"
  echo "✅ unset $key"; print_version
}

cmd_list() {
  echo "== обʼєкти бакета $BUCKET =="
  aws s3 ls --region "$REGION" "s3://$BUCKET/" || true
  echo ""
  echo "== ключі в $ENV_KEY (лише імена) =="
  local cur="$TMP/cur"
  if fetch_env "$cur"; then key_names "$cur"; else echo "($ENV_KEY нема)"; fi
}

cmd_pull() {  # $1=dest (опц.)
  local dest="${1:-./env.from-s3}"
  [ "$DRY_RUN" = 1 ] && { echo "DRY: завантажити $(s3uri "$ENV_KEY") → $dest"; return 0; }
  if [ -e "$dest" ]; then confirm "$dest існує — перезаписати?" || { echo "скасовано"; return 0; }; fi
  fetch_env "$dest" || die "не вдалося завантажити $ENV_KEY"
  echo "✅ завантажено → $dest"
  echo "⚠️  містить СЕКРЕТИ — не комітити; прибери після редагування."
}

cmd_push() {  # $1=file
  local file="$1"
  [ -f "$file" ] || die "нема файлу: $file"
  local cur="$TMP/cur"
  echo "== diff ключів (лише імена) =="
  if fetch_env "$cur"; then
    echo "додаються:            $(comm -13 <(key_names "$cur") <(key_names "$file") | tr '\n' ' ')"
    echo "ПРИБИРАЮТЬСЯ (прод!):  $(comm -23 <(key_names "$cur") <(key_names "$file") | tr '\n' ' ')"
  else
    echo "(поточного $ENV_KEY нема — це перший заллив)"
  fi
  [ "$DRY_RUN" = 1 ] && { echo "DRY: замінити $(s3uri "$ENV_KEY") вмістом $file"; return 0; }
  echo "⚠️  ПОВНА заміна прод-$ENV_KEY: ключі, яких нема у файлі, ЗНИКНУТЬ."
  confirm "Замінити весь $ENV_KEY вмістом $file?" || { echo "скасовано"; return 0; }
  s3cp "$file" "$(s3uri "$ENV_KEY")"
  echo "✅ залито $file → $(s3uri "$ENV_KEY")"; print_version
}

cmd_put_file() {  # $1=local $2=remote(опц.)
  local lf="$1" remote="${2:-}"
  [ -f "$lf" ] || die "нема файлу: $lf"
  [ -n "$remote" ] || remote="$(basename "$lf")"
  [ "$DRY_RUN" = 1 ] && { echo "DRY: залити $lf → $(s3uri "$remote")"; return 0; }
  confirm "Залити $lf → $(s3uri "$remote")?" || { echo "скасовано"; return 0; }
  s3cp "$lf" "$(s3uri "$remote")"
  echo "✅ залито → $(s3uri "$remote")"; print_version "$remote"
}

# --- глобальні прапорці (ПЕРЕД командою) ---
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)     ASSUME_YES=1; shift ;;
    -n|--dry-run) DRY_RUN=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    -*)           die "невідомий прапорець: $1 (глобальні прапорці йдуть перед командою)" ;;
    *)            break ;;
  esac
done

CMD="${1:-}"; [ $# -gt 0 ] && shift || true

case "$CMD" in
  ""|help) usage; [ -z "$CMD" ] && exit 1 || exit 0 ;;
esac

BUCKET="$(resolve_bucket)"
echo "bucket: $BUCKET" >&2

# точні лічильники аргументів → трейлінг-прапорець/зайвий арг дає зрозумілу помилку, не тихо ігнорується
H="прапорці (-y/-n) ставляться ПЕРЕД командою"
case "$CMD" in
  set)      [ $# -eq 2 ] || die "usage: set KEY VALUE ($H)";               cmd_set "$1" "$2" ;;
  unset)    [ $# -eq 1 ] || die "usage: unset KEY ($H)";                   cmd_unset "$1" ;;
  list)     [ $# -eq 0 ] || die "usage: list ($H)";                        cmd_list ;;
  pull)     [ $# -le 1 ] || die "usage: pull [dest] ($H)";                 cmd_pull "${1:-}" ;;
  push)     [ $# -eq 1 ] || die "usage: push <file> ($H)";                 cmd_push "$1" ;;
  put-file) { [ $# -ge 1 ] && [ $# -le 2 ]; } || die "usage: put-file <local> [remote] ($H)"; cmd_put_file "$1" "${2:-}" ;;
  *)        die "невідома команда: $CMD (див. -h)" ;;
esac
