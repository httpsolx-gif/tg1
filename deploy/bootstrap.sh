#!/usr/bin/env bash
# Поднять БОТА на чистом сервере (Debian/Ubuntu) одной командой.
#   sudo DB_PASS='...' BOT_TOKEN='...' ADMIN_IDS='...' bash deploy/bootstrap.sh
# Резервный сервер (общая база основного):
#   sudo DATABASE_URL='postgresql+psycopg://mailer:PASS@MAIN_IP:5432/mailer_app' BOT_TOKEN='...' ADMIN_IDS='...' bash deploy/bootstrap.sh
# Юниты: mailerbot + mailer-api + mailer-userbot (INSTALL_API=0 / INSTALL_USERBOT=0 — не ставить).
# API на своём домене (CF + nginx, deploy/api_domain.py): API_DOMAIN='grzl.cc' CF_TOKEN='…' NAMESILO_KEY='…'
#   (токены можно не передавать, если база уже с short-доменами — возьмутся оттуда)
# Идемпотентно: можно запускать повторно. Роль «сокра» ставится провижном домена
# из бота; роль «отправка» — просто добавить ноду в NODES (ничего ставить не надо).
set -euo pipefail

# --- параметры (env или дефолты) ---
DB_NAME="${DB_NAME:-mailer_app}"
DB_USER="${DB_USER:-mailer}"
DB_PASS="${DB_PASS:-}"                 # пусто → сгенерим (только если .env ещё нет)
# РЕЗЕРВНЫЙ сервер: передай DATABASE_URL общей базы основного сервера — тогда локальный
# Postgres не ставится, а бот/userbot сразу встают в резерв (лидер-лок, п.57).
# Без этого резерв поднял бы СВОЮ пустую базу, стал бы вторым лидером и дрался за токен.
EXT_DATABASE_URL="${DATABASE_URL:-}"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(whoami)}}"
# интерпретатор для venv: проекту нужен Python ≥3.11 (на Ubuntu 22.04 — PYTHON=python3.11 из deadsnakes)
PYTHON="${PYTHON:-python3}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

if [[ -n "$EXT_DATABASE_URL" ]]; then
  echo "==> Проект: $PROJECT_DIR ; запуск под: $RUN_USER ; БД: внешняя (DATABASE_URL)"
else
  echo "==> Проект: $PROJECT_DIR ; запуск под: $RUN_USER ; БД: $DB_NAME/$DB_USER (локальная)"
fi

if [[ $EUID -ne 0 ]]; then echo "Запускай через sudo (нужен apt/systemctl)."; exit 1; fi

# --- системные пакеты ---
echo "==> apt deps"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip postgresql-client \
                   nftables openssl curl ca-certificates

# --- БД и роль (только локальная база) ---
if [[ -z "$EXT_DATABASE_URL" ]]; then
  apt-get install -y postgresql
  systemctl enable --now postgresql
  # Пароль роли меняем, только если его передали явно или .env ещё нет (первый запуск).
  # Иначе повторный запуск сгенерил бы новый пароль, а .env остался бы со старым → все сервисы без БД.
  SET_PASS=0
  if [[ -n "$DB_PASS" ]]; then
    SET_PASS=1
  elif [[ ! -f "$ENV_FILE" ]]; then
    DB_PASS="$(openssl rand -hex 16)"; SET_PASS=1; echo "==> сгенерирован DB_PASS"
  fi
  echo "==> postgres: роль и база (идемпотентно)"
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
    [[ -n "$DB_PASS" ]] || DB_PASS="$(openssl rand -hex 16)"
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';" >/dev/null
    SET_PASS=1
  fi
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
  fi
  if [[ "$SET_PASS" == "1" ]]; then
    sudo -u postgres psql -c "ALTER ROLE ${DB_USER} PASSWORD '${DB_PASS}';" >/dev/null
  fi
fi

# --- venv + зависимости ---
echo "==> venv + pip install -e ."
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || { echo "Нужен Python ≥3.11: $($PYTHON --version 2>&1). Передай PYTHON=python3.11"; exit 1; }
sudo -u "$RUN_USER" "$PYTHON" -m venv "$PROJECT_DIR/.venv"
sudo -u "$RUN_USER" "$PROJECT_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
sudo -u "$RUN_USER" "$PROJECT_DIR/.venv/bin/pip" install -e "$PROJECT_DIR"

# --- .env ---
if [[ ! -f "$ENV_FILE" ]]; then
  echo "==> создаю .env"
  if [[ -n "$EXT_DATABASE_URL" ]]; then
    DBURL="$EXT_DATABASE_URL"
  else
    DBURL="postgresql+psycopg://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}"
  fi
  cat > "$ENV_FILE" <<ENV
BOT_TOKEN=${BOT_TOKEN:-PUT_YOUR_TOKEN}
ADMIN_IDS=${ADMIN_IDS:-}
DATABASE_URL=${DBURL}
ERR_CHANNEL_ID=${ERR_CHANNEL_ID:-}
SEND_CHANNEL_ID=${SEND_CHANNEL_ID:-}
BACKUP_CHANNEL_ID=${BACKUP_CHANNEL_ID:-}
BACKUP_EVERY_HOURS=${BACKUP_EVERY_HOURS:-12}
API_PORT=${API_PORT:-8080}
LOG_LEVEL=INFO
ENV
  chown "$RUN_USER":"$RUN_USER" "$ENV_FILE"; chmod 600 "$ENV_FILE"
  echo "   !!! впиши BOT_TOKEN/ADMIN_IDS в $ENV_FILE, если не передал их в env"
else
  echo "==> .env уже есть — не трогаю"
  if [[ -n "$EXT_DATABASE_URL" ]] && ! grep -qF "DATABASE_URL=${EXT_DATABASE_URL}" "$ENV_FILE"; then
    echo "   ⚠️ DATABASE_URL в $ENV_FILE отличается от переданного — проверь вручную"
  fi
fi

# --- systemd ---
# Все юниты: Restart=always (упал → поднимется) + enable (ребут сервера → поднимется).
# Бот и userbot под лидер-локом (п.57) — на втором сервере тот же bootstrap даёт резерв.
UNITS=(mailerbot)
[[ "${INSTALL_API:-1}" == "1" ]] && UNITS+=(mailer-api)
[[ "${INSTALL_USERBOT:-1}" == "1" ]] && UNITS+=(mailer-userbot)

for u in "${UNITS[@]}"; do
  echo "==> systemd $u.service"
  sed -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" -e "s#__RUN_USER__#$RUN_USER#g" \
      "$PROJECT_DIR/deploy/$u.service" > "/etc/systemd/system/$u.service"
done
systemctl daemon-reload
for u in "${UNITS[@]}"; do
  systemctl enable "$u"
  systemctl restart "$u"
done
sleep 2
for u in "${UNITS[@]}"; do
  systemctl --no-pager --full status "$u" | head -8 || true
done

# --- API на своём домене ---
if [[ -n "${API_DOMAIN:-}" && " ${UNITS[*]} " == *" mailer-api "* ]]; then
  echo "==> API на домене $API_DOMAIN (Cloudflare + nginx)"
  "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/deploy/api_domain.py" "$API_DOMAIN" \
    || echo "   ⚠️ домен API не настроен — повтори: sudo $PROJECT_DIR/.venv/bin/python deploy/api_domain.py $API_DOMAIN"
fi

echo "==> Готово. Логи: journalctl -u mailerbot -f  (и -u mailer-api / -u mailer-userbot)"
echo "    Таблицы создаются ботом при старте (create_all + ensure-колонки)."
if [[ " ${UNITS[*]} " == *" mailer-userbot "* && ! -f "$PROJECT_DIR/userbot/account.session" ]]; then
  echo "    userbot включён, но ждёт входа: sudo -u $RUN_USER $PROJECT_DIR/.venv/bin/python -m userbot.login"
  echo "    затем: systemctl start mailer-userbot"
fi
