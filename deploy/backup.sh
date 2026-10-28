#!/usr/bin/env bash
# Локальный бэкап БД (pg_dump|gzip) в deploy/backups с ротацией.
# Запуск: bash deploy/backup.sh   (читает DATABASE_URL из .env)
# Для «всегда» можно повесить в cron:  0 */12 * * * cd /path && bash deploy/backup.sh
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$PROJECT_DIR/deploy/backups"; mkdir -p "$OUT_DIR"
KEEP="${KEEP:-14}"

# DATABASE_URL из .env
URL="$(grep -E '^DATABASE_URL=' "$PROJECT_DIR/.env" | head -1 | cut -d= -f2-)"
[[ -n "$URL" ]] || { echo "DATABASE_URL не найден в .env"; exit 1; }

TS="$(date +%Y%m%d_%H%M%S)"
if [[ "$URL" == sqlite* ]]; then
  SRC="${URL##*///}"; OUT="$OUT_DIR/db_${TS}.sqlite.gz"
  gzip -9 -c "$SRC" > "$OUT"
else
  # postgresql+psycopg://user:pass@host:port/dbname
  rest="${URL#*://}"; creds="${rest%%@*}"; hostpart="${rest#*@}"
  user="${creds%%:*}"; pass="${creds#*:}"; [[ "$pass" == "$creds" ]] && pass=""
  hostport="${hostpart%%/*}"; db="${hostpart#*/}"; db="${db%%\?*}"
  host="${hostport%%:*}"; port="${hostport#*:}"; [[ "$port" == "$hostport" ]] && port=5432
  OUT="$OUT_DIR/db_${TS}.sql.gz"
  PGPASSWORD="$pass" pg_dump -h "$host" -p "$port" -U "$user" -d "$db" \
      --no-owner --no-privileges | gzip -9 > "$OUT"
fi
echo "OK: $OUT ($(du -h "$OUT" | cut -f1))"

# ротация
ls -1t "$OUT_DIR"/db_*.gz 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
