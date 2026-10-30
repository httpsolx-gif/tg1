# deploy — быстрый подъём и бэкапы

Топология: **бот** на одном сервере, **сокра** (short-домены) на другом, **отправка** с нод.

## Роли — как поднять

- **Отправка (sender-нода):** ничего ставить не надо. Добавь ноду в боте: `🖥 NODES → + Добавить ноду` (`login ip pass`). Бот шлёт на неё self-contained python по SSH (нужен только python3). Роль в карточке ноды — `🎛 Роль`.
- **Сокра (shorter-нода):** добавь ноду (как выше), помечай роль `short`/`both`, затем в боте `🔗 SHORT → Личные домены → ➕ Свой домен`. Провижн (origin-cert, сервис, фаервол CF-only) бот делает сам по SSH. Перенос домена на другую ноду — на карточке домена.
- **Бот (на чистом сервере):** скрипт ниже.

## Бот на чистом сервере (Debian/Ubuntu)

```bash
# скопировать проект на сервер (git clone / scp), затем:
sudo DB_PASS='сгенерится-если-пусто' \
     BOT_TOKEN='123:ABC' ADMIN_IDS='1424975206' \
     BACKUP_CHANNEL_ID='-100...' BACKUP_EVERY_HOURS='12' \
     bash deploy/bootstrap.sh
```
Скрипт идемпотентный: ставит системные пакеты, Postgres (роль+база), venv + `pip install -e .`,
создаёт `.env` (если нет), systemd-юниты и запускает их. Таблицы бот создаёт сам
на старте (`create_all` + ensure-колонки).

| Юнит | Что | Автоподъём |
|---|---|---|
| `mailerbot` | бот (polling + бэкапы + спам-проверка), под лидер-локом | `Restart=always` + `enable` |
| `mailer-api` | FastAPI (`API_HOST`/`API_PORT` из `.env`, по умолчанию 127.0.0.1:8080) | `Restart=always` + `enable` |
| `mailer-userbot` | авторассылка по группам, под лидер-локом; стартует только когда есть `userbot/account.session` | `Restart=always` + `enable` |

Не ставить API/userbot: `INSTALL_API=0` / `INSTALL_USERBOT=0`. Userbot после установки ждёт входа:
`sudo -u <user> .venv/bin/python -m userbot.login`, затем `systemctl start mailer-userbot`
(нужен `telethon`: `.venv/bin/python -m pip install telethon`).
Логи: `journalctl -u mailerbot -f` (`-u mailer-api`, `-u mailer-userbot`).

**Резерв на втором сервере:** тот же `bootstrap.sh` с `DATABASE_URL` общей базы —
`sudo DATABASE_URL='postgresql+psycopg://mailer:PASS@MAIN_IP:5432/mailer_app' BOT_TOKEN='…' ADMIN_IDS='…' bash deploy/bootstrap.sh`
(локальный Postgres не ставится). На основном сервере Postgres должен принимать подключения с IP резерва
(`listen_addresses` в postgresql.conf + строка в `pg_hba.conf`, лучше через приватную сеть/VPN). Только прямое
подключение, не через pgbouncer.
Бот и userbot там встанут в резерв (лидер-лок, п.57 PROJECT_MAP) и перехватят работу, если основной умрёт.

## Текущий прод

- **Сервер бота:** `23.27.22.210`, проект `/opt/mailer` (юзер `mailer`), Python 3.11, бот @grzlmails_bot,
  API `127.0.0.1:8090` → `https://grzl.cc`. На сервере же — `tgshop` (Morty Shop, :8080), не трогать.
- **Код** едет через git по SSH в bare-репо на сервере (он же `origin` рабочей копии):
  ```bash
  git push root@23.27.22.210:/opt/mailer.git HEAD:main
  ssh root@23.27.22.210 'cd /opt/mailer && bash deploy/update.sh'
  ```

## API на своём домене (Cloudflare + nginx)

```bash
sudo .venv/bin/python deploy/api_domain.py grzl.cc      # на сервере бота, после bootstrap
```
Или сразу в bootstrap: `API_DOMAIN='grzl.cc' CF_TOKEN='…' NAMESILO_KEY='…'` (токены можно не передавать —
возьмутся из short-доменов в базе бота). Идемпотентно: зона в CF → NS у NameSilo → SSL Full(strict) +
Always HTTPS → проксируемая A-запись на IP сервера → origin-сертификат CF (ключ не покидает сервер,
`/etc/ssl/mailer-api/`) → `/etc/nginx/conf.d/mailer-api.conf` (:443 → `127.0.0.1:API_PORT`, :80 → https).
nginx пускает **только IP Cloudflare** (остальным 403) — API доверяет `CF-Connecting-IP`.
Только DNS с любой машины: `--ip <IP сервера> --dns-only`. Swagger для клиента — `API_DOCS=1` в `.env`
→ `https://<домен>/docs`; основная дока клиенту — `api/CLIENT.md`.

## Обновление прода без остановки

```bash
sudo bash deploy/update.sh            # подтянуть текущую ветку с origin
sudo bash deploy/update.sh <коммит>   # выкатить конкретный коммит (в т.ч. откатиться назад)
```
Делает: бэкап БД → `git` → `pip install -e .` (если менялся `pyproject.toml`) → проверка компиляции и
импортов → схема БД (`bot.main.init_db`, `alembic upgrade head` — только если база под alembic) →
рестарт запущенных юнитов **по одному** с проверкой (живёт `HEALTH_SEC`=20с без падений, API отвечает,
лидер-лок кем-то взят). Любая ошибка → откат кода на прежний коммит и рестарт на нём. БД не откатывается
(схема только добавляется) — путь к бэкапу печатается, при нужде `restore.sh`. Лог — `logs/update_*.log`.

**Два сервера:** сначала на резерве, потом на основном. Рестарт основного отпускает лидер-лок, и резерв
(уже на новом коде) забирает работу за секунды. На одном сервере — пауза в пару секунд; сообщения боту не
теряются (Telegram держит их в очереди). Скрипт показывает, кто сейчас лидер, и предупреждает, если резерва нет.

Правила: правки — локально (свой тестовый `BOT_TOKEN` и база, не прод-токен), на сервере только `git`;
изменения схемы — только добавлением (новые колонки с `DEFAULT`/`NULL`), чтобы старый код на резерве
не ломался. env: `YES=1` без вопросов, `SKIP_BACKUP=1`, `FORCE=1` рестарт без изменений, `FORCE_PIP=1`.

## Бэкапы

- **Всегда (оффсайт):** задай в `.env` `BACKUP_CHANNEL_ID` (сделай бота админом канала) и
  `BACKUP_EVERY_HOURS` (напр. 12). Бот сам шлёт дамп БД в канал по расписанию.
- **По кнопке:** `💾 BACKUP` в админке → «Бэкап в канал сейчас» или «Выгрузить все БД в чат»
  (дамп Postgres + `clicks.db` со всех shorter-нод — файлами в чат).
- **Локально/cron:** `bash deploy/backup.sh` (дамп в `deploy/backups/`, ротация KEEP=14).
  Для «всегда» локально: `0 */12 * * * cd /path/to/tg1 && bash deploy/backup.sh`.

## Перенос / восстановление бота

```bash
# на новом сервере: bootstrap (см. выше), затем залить последний дамп:
bash deploy/restore.sh deploy/backups/db_YYYYmmdd_HHMMSS.sql.gz
```
⚠️ Один `BOT_TOKEN` = один поллер. Два экземпляра на ОДНОЙ базе безопасны — работает только
лидер (лидер-лок). Перенос на новую базу из дампа: останови старого бота, подними нового.
