#!/usr/bin/env bash
#
# psql.sh — psql-сесія до прод-RDS з локалі, через SSH-тунель на боксі.
#
# RDS приватний (PubliclyAccessible=false, інгрес 5432 лише з app-SG), тож напряму
# з локалі до нього не достукатись. Скрипт робить це за один крок: тягне .env із
# секрет-бакета (там канонічний DATABASE_URL) → резолвить живий бокс → піднімає
# ssh -L тунель → запускає psql. Тунель гаситься на виході, навіть по Ctrl-C.
#
# Секрети НЕ друкуються і НЕ потрапляють в argv: psql отримує їх лише через
# PG*-змінні оточення, а завантажений .env лежить у mktemp-каталозі до виходу.
#
# Використання: ./deploy/psql.sh [--stats] [psql-аргументи...]
#
# Приклади:
#   ./deploy/psql.sh                                    # інтерактивна сесія
#   ./deploy/psql.sh -c 'select count(*) from predictions'
#   ./deploy/psql.sh -f scripts/data/report.sql
#   ./deploy/psql.sh --stats                            # зріз: автори / курсор інжесту / доки / прогнози
#
# Конфіг через env (є дефолти): REGION, SSH_KEY, SSH_USER, BOX_TAG, SSH_OPTS,
#   SECRETS_STACK, SECRETS_BUCKET, ENV_KEY, LOCAL_PORT.

set -euo pipefail

REGION="${REGION:-eu-central-1}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/prophet-checker-key.pem}"
SSH_USER="${SSH_USER:-ec2-user}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8}"
SECRETS_STACK="${SECRETS_STACK:-prophet-secrets}"
ENV_KEY="${ENV_KEY:-.env}"          # імʼя обʼєкта env-файлу в бакеті
LOCAL_PORT="${LOCAL_PORT:-15432}"   # локальний кінець тунелю

TMP="$(mktemp -d)"
CTL="$TMP/ssh-ctl"                  # ControlMaster-сокет: дає чим убити тунель на виході
BOX_IP=""                           # заповнює resolve_box; cleanup читає

# Тунель живе у власному ssh-процесі (-f), тож гасимо його через control-сокет.
cleanup() {
  if [ -S "$CTL" ] && [ -n "$BOX_IP" ]; then
    ssh -S "$CTL" -O exit "$SSH_USER@$BOX_IP" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

usage() { sed -n '3,22p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 2; }

STATS_SQL="
-- count(distinct …): два left join (docs + predictions) множать рядки (фан-аут),
-- тож без distinct кожна метрика роздувається на кардинальність іншої гілки.
select p.name,
       count(distinct d.id)  as docs,
       count(distinct pr.id) as predictions,
       count(distinct pr.id) filter (where pr.verified_at is not null) as verified
from persons p
left join raw_documents d on d.person_id = p.id
left join predictions  pr on pr.person_id = p.id
group by p.name
order by predictions desc;

-- Курсор інжесту (person_sources.last_collected_at): часовий фронтир, до якого
-- зібрано й опрацьовано пости кожного джерела. lag = наскільки курсор відстає
-- від «тепер» — великий lag означає великий беклог непідтягнутих постів. Джерела
-- з найстарішим курсором ідуть першими. docs/processed рахуються на автора (не на
-- джерело — у авторів зазвичай одне джерело).
with doc_counts as (
  select person_id,
         count(*)                          as docs,
         count(*) filter (where processed) as processed
  from raw_documents
  group by person_id
)
select p.name                                             as author,
       ps.source_identifier                               as channel,
       ps.enabled,
       ps.last_collected_at                               as ingest_cursor,
       date_trunc('second', now() - ps.last_collected_at) as lag,
       coalesce(c.docs, 0)                                as docs,
       coalesce(c.processed, 0)                           as processed
from person_sources ps
join persons p         on p.id = ps.person_id
left join doc_counts c on c.person_id = ps.person_id
order by ps.last_collected_at asc, author;

select status, count(*) from predictions group by status order by 2 desc;

select count(*) as docs,
       count(*) filter (where processed) as processed
from raw_documents;
"

preflight() {
  command -v aws  >/dev/null || die "нема aws CLI"
  command -v ssh  >/dev/null || die "нема ssh"
  command -v psql >/dev/null || die "нема psql (brew install libpq)"
  [ -f "$SSH_KEY" ] || die "нема SSH-ключа: $SSH_KEY (задай через SSH_KEY=...)"
  port_free || die "локальний порт $LOCAL_PORT зайнятий — задай інший: LOCAL_PORT=15433 $0"
}

port_free() {
  ! (exec 3<>"/dev/tcp/127.0.0.1/$LOCAL_PORT") 2>/dev/null
}

# Бакет: явний SECRETS_BUCKET або Output secrets-стека (як у secrets.sh).
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

# Бокс: як у connect.sh/logs.sh — тег + running, IP міняється при кожному старті.
resolve_box() {
  local id
  id="$(aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Name,Values=$BOX_TAG" "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[].InstanceId' --output text)"
  { [ -n "$id" ] && [ "$id" != "None" ]; } || \
    die "нема живого боксу '$BOX_TAG'. Env на паузі? Див. runbook/stop-env.md «Підйом»."
  [ "$(printf '%s' "$id" | wc -w)" -eq 1 ] || die "кілька живих боксів: $id — не вгадую."

  BOX_IP="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$id" \
    --query 'Reservations[].Instances[].PublicIpAddress' --output text)"
  { [ -n "$BOX_IP" ] && [ "$BOX_IP" != "None" ]; } || die "у боксу $id нема публічного IP."
  echo "→ бокс $id ($BOX_IP)" >&2
}

# DATABASE_URL → PG*-змінні. Форма: postgresql+asyncpg://user:pass@host:port/db
# Пароль підставляється як є: runbook забороняє в ньому # % / @ " і пробіл, тож
# percent-декодування не потрібне.
parse_db_url() {
  local file="$1" raw rest creds hostport
  raw="$(grep -m1 '^DATABASE_URL=' "$file" | cut -d= -f2-)"
  [ -n "$raw" ] || die "у $ENV_KEY нема DATABASE_URL"

  rest="${raw#*://}"                    # user:pass@host:port/db
  creds="${rest%%@*}"
  rest="${rest#*@}"                     # host:port/db
  hostport="${rest%%/*}"

  DB_USER="${creds%%:*}"
  DB_PASS=""
  [ "$creds" != "$DB_USER" ] && DB_PASS="${creds#*:}"

  DB_HOST="${hostport%%:*}"
  DB_PORT="${hostport##*:}"
  [ "$DB_PORT" = "$DB_HOST" ] && DB_PORT=5432   # порт у URL опційний

  DB_NAME="${rest#*/}"
  DB_NAME="${DB_NAME%%\?*}"             # відкинути ?query, якщо є

  case "$DB_HOST" in
    localhost|127.0.0.1|db|postgres)
      die "DATABASE_URL у $ENV_KEY вказує на $DB_HOST — це не RDS. Тунель не потрібен." ;;
  esac
}

open_tunnel() {
  echo "→ тунель localhost:$LOCAL_PORT → $DB_HOST:$DB_PORT" >&2
  # -n: stdin тунелю → /dev/null. Без цього фоновий ssh ковтає stdin скрипта,
  # і `./deploy/psql.sh < report.sql` (чи pipe) віддає psql порожній вхід.
  # shellcheck disable=SC2086
  ssh $SSH_OPTS -i "$SSH_KEY" -M -S "$CTL" -f -N -n \
      -L "$LOCAL_PORT:$DB_HOST:$DB_PORT" "$SSH_USER@$BOX_IP" \
    || die "не піднявся SSH-тунель до $BOX_IP"

  local i
  for i in $(seq 1 20); do
    port_free || return 0
    sleep 0.25
  done
  die "тунель не почав слухати localhost:$LOCAL_PORT за 5с"
}

main() {
  local stats=0
  case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --stats)   stats=1; shift ;;
  esac

  preflight
  local bucket env_file="$TMP/env"
  bucket="$(resolve_bucket)"
  echo "→ секрети з s3://$bucket/$ENV_KEY" >&2
  aws s3 cp --region "$REGION" --only-show-errors "s3://$bucket/$ENV_KEY" "$env_file" \
    || die "не вдалося завантажити $ENV_KEY з бакета $bucket"

  parse_db_url "$env_file"
  resolve_box
  open_tunnel

  # Креденшели йдуть у psql лише оточенням — ні в argv, ні в термінал.
  export PGHOST=localhost PGPORT="$LOCAL_PORT" PGUSER="$DB_USER" PGPASSWORD="$DB_PASS"
  export PGDATABASE="$DB_NAME" PGSSLMODE="${DB_SSL_MODE:-require}"  # на RDS rds.force_ssl=1

  echo "→ psql $DB_USER@$DB_NAME (RDS $DB_HOST)" >&2
  # Без exec: тунель має померти в trap-і після виходу з psql.
  # STATS_SQL іде stdin-ом (-f -), а не -c: так psql друкує результат КОЖНОГО
  # з трьох запитів, а не лише останнього.
  if [ "$stats" = "1" ]; then
    psql -v ON_ERROR_STOP=1 "$@" -f - <<<"$STATS_SQL"
  else
    psql "$@"
  fi
}

main "$@"
