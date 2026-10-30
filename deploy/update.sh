#!/usr/bin/env bash
# Обновить прод на ЭТОМ сервере без остановки: бэкап → код → зависимости → схема БД →
# рестарт по одному юниту с проверкой → при ошибке откат кода и рестарт на старом.
#
#   sudo bash deploy/update.sh            # подтянуть текущую ветку с origin
#   sudo bash deploy/update.sh <ref>      # выкатить конкретный коммит/тег/ветку (в т.ч. откат назад)
#
# Два сервера (основной + резерв на общей базе, лидер-лок п.57): СНАЧАЛА на резерве, ПОТОМ на основном.
# Рестарт основного отпускает лок → резерв (уже на новом коде) забирает работу за секунды.
# Скрипт сам показывает, кто сейчас лидер, и предупредит, если резерва нет (будет пауза в пару секунд).
#
# env: YES=1 — не спрашивать подтверждений; SKIP_BACKUP=1 — без бэкапа; FORCE=1 — рестарт, даже если
#      код не изменился; FORCE_PIP=1 — pip install, даже если pyproject.toml не менялся;
#      HEALTH_SEC (20) — сколько секунд юнит должен прожить без падений; LEADER_WAIT (60).
# Откат БД не автоматический: схема меняется только добавлением (старый код с ней работает),
# а заливать дамп = потерять данные после бэкапа. Если очень надо — deploy/restore.sh <файл бэкапа>.
set -euo pipefail
# Всё тело в { … } — bash прочитает скрипт целиком до запуска: git reset ниже перезапишет этот файл.
{

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$PROJECT_DIR/.venv/bin/python"
HEALTH_SEC="${HEALTH_SEC:-20}"
LEADER_WAIT="${LEADER_WAIT:-60}"
ALL_UNITS=(mailerbot mailer-api mailer-userbot)

if [[ $EUID -ne 0 ]]; then echo "Запускай через sudo (нужен systemctl)."; exit 1; fi
cd "$PROJECT_DIR"
# git/pip — от владельца проекта (под ним работают юниты и лежит .venv)
RUN_USER="$(stat -c %U "$PROJECT_DIR")"
as_user() { sudo -u "$RUN_USER" -H -- "$@"; }

# один запуск за раз
exec 9>"$PROJECT_DIR/.update.lock"
flock -n 9 || { echo "update.sh уже запущен."; exit 1; }

mkdir -p "$PROJECT_DIR/logs"
LOG="$PROJECT_DIR/logs/update_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

step() { echo; echo "==> $*"; }
die()  { echo "❌ $*"; exit 1; }
confirm() {
  [[ "${YES:-0}" == "1" ]] && return 0
  read -r -p "$1 [y/N] " a </dev/tty
  [[ "$a" == "y" || "$a" == "Y" ]]
}

# Кто держит лидер-лок: строки «L host» (лидер) / «S host» (резерв). Пусто — нет ни одного.
leaders() {
  as_user env PYTHONPATH="$PROJECT_DIR" "$PY" - "$1" <<'PY'
import sys
from sqlalchemy import text
from common.db import get_engine
e = get_engine()
if e.dialect.name != "postgresql":
    sys.exit(0)
with e.connect() as c:
    rows = c.execute(text(
        "SELECT a.application_name, bool_or(l.pid IS NOT NULL) FROM pg_stat_activity a "
        "LEFT JOIN pg_locks l ON l.pid = a.pid AND l.locktype = 'advisory' AND l.granted "
        "WHERE a.application_name LIKE :p GROUP BY a.pid, a.application_name"),
        {"p": f"leader:{sys.argv[1]}@%"}).all()
for app, held in rows:
    print("L" if held else "S", app.split("@", 1)[1])
PY
}

show_leaders() {
  local out; out="$(leaders "$1" 2>/dev/null || true)"
  if [[ -z "$out" ]]; then echo "   $1: никого (sqlite / LEADER_LOCK=0 / не запущен)"; return; fi
  while read -r role host; do
    [[ "$role" == L ]] && echo "   $1: 🟢 лидер  $host" || echo "   $1: 🟡 резерв $host"
  done <<<"$out"
}

# Юнит прожил HEALTH_SEC: active и тот же MainPID (не падал и не перезапускался systemd).
check_unit() {
  local u="$1" pid0 pid i
  pid0="$(systemctl show -p MainPID --value "$u")"
  for ((i = 0; i < HEALTH_SEC; i++)); do
    sleep 1
    [[ "$(systemctl is-active "$u")" == active ]] || { echo "   $u: не active"; return 1; }
    pid="$(systemctl show -p MainPID --value "$u")"
    [[ "$pid" == "$pid0" && "$pid" != 0 ]] || { echo "   $u: перезапускался (PID $pid0 → $pid)"; return 1; }
  done
  case "$u" in
    mailer-api) check_api || return 1 ;;
    mailerbot) check_leader tg1:bot || return 1 ;;
    mailer-userbot) check_leader tg1:userbot || return 1 ;;
  esac
}

# API слушает порт: любой HTTP-ответ (хоть 404 на «/») — живой.
check_api() {
  local host port code
  host="$(grep -E '^API_HOST=' .env | tail -1 | cut -d= -f2- || true)"; host="${host:-127.0.0.1}"
  port="$(grep -E '^API_PORT=' .env | tail -1 | cut -d= -f2- || true)"; port="${port:-8080}"
  [[ "$host" == 0.0.0.0 ]] && host=127.0.0.1
  code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://$host:$port/" || true)"
  [[ "$code" != 000 && -n "$code" ]] || { echo "   mailer-api: не отвечает на $host:$port"; return 1; }
  echo "   mailer-api: отвечает ($host:$port → HTTP $code)"
}

# После рестарта кто-то (этот сервер или резерв) должен держать лок.
LOCK_SEEN=" "   # локи, в которых до обновления кто-то стоял (через пробел)
check_leader() {
  local name="$1" i
  [[ "$LOCK_SEEN" == *" $name "* ]] || return 0   # лок не используется (sqlite / LEADER_LOCK=0)
  for ((i = 0; i < LEADER_WAIT; i++)); do
    if leaders "$name" 2>/dev/null | grep -q '^L '; then show_leaders "$name"; return 0; fi
    sleep 1
  done
  echo "   $name: за ${LEADER_WAIT}с никто не стал лидером"; return 1
}

pip_install() { as_user "$PY" -m pip install -q -e "$PROJECT_DIR"; }

# ---------- 1. проверки ----------
step "Проверки"
[[ -x "$PY" ]] || die "нет $PY — сначала deploy/bootstrap.sh"
[[ -f .env ]] || die "нет .env"
as_user git diff --quiet && as_user git diff --cached --quiet \
  || die "на сервере есть незакоммиченные правки в tracked-файлах (git status) — правь локально, не на проде"

OLD="$(as_user git rev-parse HEAD)"
as_user git fetch --quiet --tags origin
if [[ -n "${1:-}" ]]; then
  NEW="$(as_user git rev-parse --verify "$1^{commit}")" || die "не найден ref: $1"
else
  NEW="$(as_user git rev-parse '@{u}')" || die "у ветки нет upstream — укажи ref аргументом"
  as_user git merge-base --is-ancestor "$OLD" "$NEW" \
    || die "origin не fast-forward от текущего HEAD — укажи ref явно, если так и задумано"
fi

UNITS=()
for u in "${ALL_UNITS[@]}"; do
  [[ "$(systemctl is-active "$u" 2>/dev/null || true)" == active ]] && UNITS+=("$u")
done
[[ ${#UNITS[@]} -gt 0 ]] || die "ни один из юнитов (${ALL_UNITS[*]}) не запущен — это не прод? используй bootstrap.sh"

echo "   сервер: $(hostname) ; проект: $PROJECT_DIR ; пользователь: $RUN_USER"
echo "   юниты к рестарту: ${UNITS[*]}"
echo "   код: ${OLD:0:8} → ${NEW:0:8}"
if [[ "$OLD" == "$NEW" ]]; then
  [[ "${FORCE:-0}" == "1" ]] || { echo "✅ Уже актуально, ничего не делаю (FORCE=1 — рестарт всё равно)."; exit 0; }
else
  as_user git --no-pager log --oneline "$OLD..$NEW" | head -30 | sed 's/^/     /'
  as_user git merge-base --is-ancestor "$NEW" "$OLD" && echo "   ⚠️ это ОТКАТ на более старый коммит"
fi

echo "   лидер-лок сейчас:"
for name in tg1:bot tg1:userbot; do show_leaders "$name"; done
me="$(hostname)"
for name in tg1:bot tg1:userbot; do
  st="$(leaders "$name" 2>/dev/null || true)"
  [[ -n "$st" ]] && LOCK_SEEN+="$name "
  if grep -q "^L $me\$" <<<"$st" && ! grep -q '^S ' <<<"$st"; then
    echo "   ⚠️ $name: лидер — этот сервер, резерва нет → при рестарте пауза в несколько секунд"
    echo "      (сообщения боту не теряются: Telegram держит их в очереди до подъёма)"
  fi
done
confirm "Обновить?" || die "отменено"

# ---------- 2. бэкап ----------
BACKUP="-"
if [[ "${SKIP_BACKUP:-0}" != "1" ]]; then
  step "Бэкап БД"
  out="$(as_user bash deploy/backup.sh)" || die "бэкап не удался — обновление не начинал"
  echo "$out"
  BACKUP="$(sed -n 's/^OK: \([^ ]*\).*/\1/p' <<<"$out")"
fi

# ---------- откат ----------
RESTARTED=()
PIP_CHANGED=0
rollback() {
  echo; echo "⏪ ОТКАТ на ${OLD:0:8}"
  as_user git reset --quiet --hard "$OLD" || echo "   ⚠️ git reset не удался — проверь руками"
  if [[ "$PIP_CHANGED" == 1 ]]; then pip_install || echo "   ⚠️ pip install старой версии не удался"; fi
  local u ok=1
  for u in ${RESTARTED[@]+"${RESTARTED[@]}"}; do
    echo "   рестарт $u (старый код)"
    systemctl restart "$u"
    check_unit "$u" || { ok=0; echo "   ⚠️ $u не поднялся и на старом коде — смотри journalctl -u $u"; }
  done
  ((ok)) && echo "   откат завершён, всё на ${OLD:0:8}"
  echo "   схема БД не откатывалась (только добавления). Бэкап до обновления: $BACKUP"
  echo "   лог: $LOG"
  exit 1
}
fail() { echo "❌ $*"; rollback; }

# ---------- 3. код ----------
step "Код → ${NEW:0:8}"
as_user git reset --quiet --hard "$NEW"

# ---------- 4. зависимости ----------
if [[ "${FORCE_PIP:-0}" == "1" ]] || ! as_user git diff --quiet "$OLD" "$NEW" -- pyproject.toml; then
  step "Зависимости (pip install -e .)"
  PIP_CHANGED=1
  pip_install || fail "pip install"
else
  step "Зависимости: pyproject.toml не менялся — пропускаю"
fi

# ---------- 5. проверка кода до рестарта ----------
step "Проверка: компиляция и импорты"
as_user "$PY" -m compileall -q bot common api userbot || fail "синтаксическая ошибка (compileall)"
imports="import bot.app, api.app"
[[ " ${UNITS[*]} " == *" mailer-userbot "* ]] && imports+=", userbot.main"
as_user env PYTHONPATH="$PROJECT_DIR" "$PY" -c "$imports" || fail "ошибка импорта нового кода"

# ---------- 6. миграции ----------
# Схема живёт в create_all + _ensure_columns (bot/main.py:init_db), прогоняем новым кодом до рестарта.
step "Миграции: схема БД (init_db)"
as_user env PYTHONPATH="$PROJECT_DIR" "$PY" -c "from bot.main import init_db; init_db()" \
  || fail "init_db (схема БД)"
has_alembic="$(as_user env PYTHONPATH="$PROJECT_DIR" "$PY" -c \
  "from sqlalchemy import inspect; from common.db import get_engine; print(int(inspect(get_engine()).has_table('alembic_version')))" \
  2>/dev/null || echo 0)"
if [[ "$has_alembic" == 1 ]]; then
  step "Миграции: alembic upgrade head"
  as_user env PYTHONPATH="$PROJECT_DIR" "$PY" -m alembic upgrade head || fail "alembic upgrade head"
else
  echo "   alembic_version в БД нет (база из create_all) — alembic пропускаю"
fi

# ---------- 7. рестарт по одному ----------
for u in "${UNITS[@]}"; do
  step "Рестарт $u (проверка ${HEALTH_SEC}с)"
  RESTARTED+=("$u")
  systemctl restart "$u" || fail "systemctl restart $u"
  check_unit "$u" || { journalctl -u "$u" -n 30 --no-pager || true; fail "$u не прошёл проверку"; }
  echo "   ✅ $u"
done

step "Готово: ${OLD:0:8} → ${NEW:0:8}"
for name in tg1:bot tg1:userbot; do show_leaders "$name"; done
echo "   бэкап: $BACKUP ; лог: $LOG"
echo "   второй сервер ещё не обновлён? запусти там: sudo bash deploy/update.sh ${NEW:0:8}"
exit 0
}
