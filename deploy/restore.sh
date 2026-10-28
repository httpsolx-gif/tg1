#!/usr/bin/env bash
# Восстановить БД из дампа (для переноса бота на новый сервер).
# Запуск: bash deploy/restore.sh deploy/backups/db_YYYYmmdd_HHMMSS.sql.gz
# ВНИМАНИЕ: перезальёт содержимое целевой БД из DATABASE_URL (.env).
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DUMP="${1:-}"; [[ -n "$DUMP" && -f "$DUMP" ]] || { echo "Укажи файл дампа .sql.gz"; exit 1; }

URL="$(grep -E '^DATABASE_URL=' "$PROJECT_DIR/.env" | head -1 | cut -d= -f2-)"
[[ "$URL" == sqlite* ]] && { echo "Для sqlite просто распакуй .gz на место файла БД."; exit 0; }

rest="${URL#*://}"; creds="${rest%%@*}"; hostpart="${rest#*@}"
user="${creds%%:*}"; pass="${creds#*:}"; [[ "$pass" == "$creds" ]] && pass=""
hostport="${hostpart%%/*}"; db="${hostpart#*/}"; db="${db%%\?*}"
host="${hostport%%:*}"; port="${hostport#*:}"; [[ "$port" == "$hostport" ]] && port=5432

read -r -p "Залить дамп в БД '$db' на $host:$port (перезапишет данные)? [y/N] " a
[[ "$a" == "y" || "$a" == "Y" ]] || { echo "Отмена."; exit 1; }

echo "==> останавливаю mailerbot (если есть)"; systemctl stop mailerbot 2>/dev/null || true
echo "==> restore"
gunzip -c "$DUMP" | PGPASSWORD="$pass" psql -h "$host" -p "$port" -U "$user" -d "$db"
echo "==> запускаю mailerbot (если есть)"; systemctl start mailerbot 2>/dev/null || true
echo "OK"
