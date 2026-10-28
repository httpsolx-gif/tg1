#!/usr/bin/env bash
# Поднять БОТА на чистом сервере (Debian/Ubuntu) одной командой.
#   sudo DB_PASS='...' BOT_TOKEN='...' ADMIN_IDS='...' bash deploy/bootstrap.sh
# Идемпотентно: можно запускать повторно. Роль «сокра» ставится провижном домена
# из бота; роль «отправка» — просто добавить ноду в NODES (ничего ставить не надо).
set -euo pipefail

# --- параметры (env или дефолты) ---
DB_NAME="${DB_NAME:-mailer_app}"
DB_USER="${DB_USER:-mailer}"
DB_PASS="${DB_PASS:-}"                 # пусто → сгенерим
RUN_USER="${RUN_USER:-${SUDO_USER:-$(whoami)}}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Проект: $PROJECT_DIR ; запуск под: $RUN_USER ; БД: $DB_NAME/$DB_USER"

if [[ $EUID -ne 0 ]]; then echo "Запускай через sudo (нужен apt/systemctl)."; exit 1; fi

# --- системные пакеты ---
echo "==> apt deps"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip postgresql postgresql-client \
                   nftables openssl curl ca-certificates

systemctl enable --now postgresql

# --- БД и роль ---
if [[ -z "$DB_PASS" ]]; then DB_PASS="$(openssl rand -hex 16)"; echo "==> сгенерирован DB_PASS"; fi
echo "==> postgres: роль и база (идемпотентно)"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  END IF;
END \$\$;
SELECT 'db exists' FROM pg_database WHERE datname='${DB_NAME}' \gset
SQL
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
fi
sudo -u postgres psql -c "ALTER ROLE ${DB_USER} PASSWORD '${DB_PASS}';" >/dev/null

# --- venv + зависимости ---
echo "==> venv + pip install -e ."
sudo -u "$RUN_USER" python3 -m venv "$PROJECT_DIR/.venv"
sudo -u "$RUN_USER" "$PROJECT_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
sudo -u "$RUN_USER" "$PROJECT_DIR/.venv/bin/pip" install -e "$PROJECT_DIR"

# --- .env ---
ENV_FILE="$PROJECT_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "==> создаю .env"
  DBURL="postgresql+psycopg://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}"
  cat > "$ENV_FILE" <<ENV
BOT_TOKEN=${BOT_TOKEN:-PUT_YOUR_TOKEN}
ADMIN_IDS=${ADMIN_IDS:-}
DATABASE_URL=${DBURL}
ERR_CHANNEL_ID=${ERR_CHANNEL_ID:-}
SEND_CHANNEL_ID=${SEND_CHANNEL_ID:-}
BACKUP_CHANNEL_ID=${BACKUP_CHANNEL_ID:-}
BACKUP_EVERY_HOURS=${BACKUP_EVERY_HOURS:-12}
LOG_LEVEL=INFO
ENV
  chown "$RUN_USER":"$RUN_USER" "$ENV_FILE"; chmod 600 "$ENV_FILE"
  echo "   !!! впиши BOT_TOKEN/ADMIN_IDS в $ENV_FILE, если не передал их в env"
else
  echo "==> .env уже есть — не трогаю"
fi

# --- systemd ---
echo "==> systemd mailerbot.service"
sed -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" -e "s#__RUN_USER__#$RUN_USER#g" \
    "$PROJECT_DIR/deploy/mailerbot.service" > /etc/systemd/system/mailerbot.service
systemctl daemon-reload
systemctl enable mailerbot
systemctl restart mailerbot
sleep 2
systemctl --no-pager --full status mailerbot | head -12 || true

echo "==> Готово. Логи: journalctl -u mailerbot -f"
echo "    Таблицы создаются ботом при старте (create_all + ensure-колонки)."
