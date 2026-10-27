# PROJECT MAP — tg1 (Telegram mailer bot)

> Карта проекта для быстрого входа в контекст. **Читать первой** при старте сессии, чтобы не перечитывать код с нуля.
> Правится по мере изменений. Внизу — журнал решений/фишек/ошибок.

Дата последнего обновления карты: 2026-10-27 (кастом-эмодзи iOS-иконки на кнопках).

---

## 1. Что это
Telegram-бот рассылки писем (aiogram 3) + FastAPI-трекер/логи (`api/`). Рассылка: шаблон (Jinja2) → сокращение ссылки (n9) → отправка через один из бэкендов → лог в БД.

**Три бэкенда отправки:**
1. **Local SMTP** — отправка с хоста бота через SMTP-аккаунт.
2. **Node (SSH)** — отправка выполняется НА удалённом сервере-ноде (её IP) по SSH: бот шлёт на ноду self-contained python-скрипт через stdin.
3. **AWS SES (API)** — через boto3 с хоста бота, либо (если AWS-аккаунт привязан к ноде) SES-запрос выполняется на ноде (stdlib SigV4).

## 2. Как запускать / важные команды
- **БД — PostgreSQL** (не sqlite!). Строка в `.env`: `postgresql+psycopg://greedy@localhost:5432/mailer_app`. (В `.env.example` другая — ориентируйся на `.env`.)
- Запуск бота: `.venv/bin/python -m bot.main`
- Миграции: `.venv/bin/python -m alembic upgrade head`
  - ⚠️ **Папка проекта переехала** (`/Users/greedy/Downloads/ALL/mailer/tg1`), поэтому у console-script'ов (`alembic`, `pip`) **битый shebang** на старый путь. Запускать только через `.venv/bin/python -m alembic` / `-m pip`.
- Venv: `.venv/` в корне. Python 3.12, aiogram 3.31.
- Тестов проекта для нового кода нет; pytest в venv не установлен. Проверка — через sqlite smoke-скрипты (`cdb._engine = create_engine("sqlite://")`, переопределять `_engine` в модулях).

## 3. Структура
```
bot/
  app.py            — build_dispatcher(): регистрация роутеров + ErrorReportMiddleware
  main.py           — старт поллинга + SQLModel.metadata.create_all(engine)
  fsm/admin.py      — все FSM-состояния (SmtpAdd, SmtpAutoAdd, NodeAdd, TemplateUpload,
                      RubricAdd, RubricEdit, MailboxAdd, AwsAdd, SmtpLimit)
  keyboards/
    main_menu.py    — reply-меню (📤 Отправить письмо / 📋 Меню / 🛠 Админка)
    admin_menu.py   — ADMIN_BUTTONS (reply-клавиатура админки, persistent)
    short_menu.py   — инлайн-клавиатуры SHORT (переписаны)
    icons.py        — кастом-эмодзи iOS-иконки: EMOJI_ICON (пак tgiosicons) +
                      iconize_markup() + IconRequestMiddleware (меняет ведущий
                      эмодзи кнопки на icon_custom_emoji_id, убирает его из текста)
    (smtp_menu.py УДАЛЁН — был мёртвый/битый)
  handlers/
    start.py        — /start, кнопка «📋 Меню» (кнопку отправки обрабатывает flow_send)
    flow_send.py    — ВЕСЬ флоу отправки письма (FSM SendFlow)
    admin/
      index.py      — вход в админку, роутинг reply-кнопок разделов, back_to_admin()
      smtp.py       — SMTP: add/clear/settings(bindings) (большой файл ~820 строк)
      smtp_limits.py— SMTP лимиты/автосмена/принудит.смена (callback-префикс slim:)
      short.py      — SHORT (сокращатель, callback-префикс short:)
      nodes.py      — NODES (SSH-серверы, node:)
      aws.py        — AWS SES (aws:)
      tpl.py        — TEMPLATES (загрузка шаблонов по имени файла, tpl:)
      rubrics.py    — РУБРИКИ (под кнопкой 🧩 SRVS, rub:)
      mailtypes.py  — ТИПЫ ПОЧТ (gmail/main/кастом, mb:)
      srvs/stats/users/api/test/alert.py — заглушки (кроме srvs→rubrics)
  services/
    mailer.py       — probe_smtp (тест/self-test), send_via_account (local SMTP, HTML)
    smtp_pool.py    — set_binding/get_binding/pick_smtp (выбор SMTP + автосмена по лимитам)
    node_runner.py  — SSH: send_via_node (SMTP на ноде), send_ses_via_node (SES на ноде),
                      check_node, ses_sigv4 (stdlib-подпись, сверена с botocore)
    aws_ses.py      — boto3: send_via_ses, check_aws, parse_aws_line
    short_client.py — shorten_url (n9, деградирует до исходной ссылки при ошибке)
    short_policy.py — ShortBinding resolve/set/clear (приоритет service>country>target>global)
    templates.py    — render_template (Jinja2), default_template, resolve_template_html
    mailboxes.py    — управляемые типы почт (сид дефолтов + CRUD + карты)
    n9_adapter.py   — httpx к локальному n9-сервису
  middlewares/
    error_report.py — ErrorReportMiddleware (ловит исключения хендлеров, логирует, не роняет)
    acl.py, context.py — ЗАГЛУШКИ (TODO)
common/
  config.py         — Settings (pydantic-settings), settings.admin_ids_set
  db.py             — get_engine()/engine (Postgres/sqlite), create_all через metadata
  security.py       — ⚠️ encrypt_password/decrypt_password — ЗАГЛУШКИ (пароли в открытом виде!)
  constants.py      — ALL_SERVICES, COUNTRIES(список), COUNTRY_FLAGS, COUNTRY_SERVICES,
                      MAILBOXES=[(code,name)] (сид для типов почт)
  models/           — SQLModel-таблицы (см. раздел 4)
migrations/         — alembic (см. раздел 5)
```

## 4. Модели БД (common/models/)
| Таблица | Файл | Ключевые поля | Как создаётся |
|---|---|---|---|
| `smtp` (SmtpAccount) | smtp.py | kind(gmail/main/...), host,port,login,email,password_enc,pool,is_active,error_count,**send_limit,sent_count** | migration + create_all |
| `SmtpBinding` | **autoswitch.py** (НЕ smtp.py!) | group,scope,country,service_id,smtp_id,active | migration |
| `AutoSwitch` | autoswitch.py | kind,scope,country,service_id,enabled,limit_per_account | migration |
| `sendlog` (SendLog) | send_log.py | user_id,service_id,smtp_id,email,status,error,created_at(str ISO) | migration |
| `ShortBinding` | short.py | scope(enum),country,service,target(enum),**enabled**,created_at,updated_at | migration |
| `Service` | service.py | name,slug,country,active | migration |
| `node` (Node) | node.py | login,ip,password_enc,port,**region**,is_active,error_count | **create_all** (+ALTER region) |
| `aws_account` (AwsAccount) | aws.py | region,access_key,secret_key_enc,email_from,**node_id**(FK→node),is_active | **create_all** |
| `email_template` (EmailTemplate) | template.py | service,mailbox,html,filename; uniq(service,mailbox) | **create_all** |
| `rubric` (Rubric) | rubric.py | name,sender_name,title,smtp_group,shortener | **create_all** |
| `rubric_mailbox` (RubricMailbox) | rubric.py | rubric_id(FK),mailbox; uniq(rubric_id,mailbox) | **create_all** |
| `mailbox` (Mailbox) | mailbox.py | code,name,is_builtin | **create_all** |
| `apitoken`, `click` | api_token.py, click.py | — | migration |

⚠️ **Дивергенция**: новые таблицы (node/aws/template/rubric/mailbox) создаются только `create_all` при старте бота, их НЕТ в alembic. Старые — в миграциях. Колонки SMTP-лимитов — в миграции `a1b2c3d4e5f6`.

## 5. Миграции (alembic)
Цепочка (одна голова):
```
<base> → 59cd39a4252c (init) → 1bfcd5b33afd (short enums) → a1b2c3d4e5f6 (smtp send limits)
```
`a1b2c3d4e5f6` добавляет `smtp.send_limit`, `smtp.sent_count` (server_default 0, not null).

## 6. Админ-меню (reply, ADMIN_BUTTONS)
```
📧 SMTP   🔗 SHORT   🧩 SRVS(=рубрики)   📊 STATS
👥 USERS  🛡 API     🧪 TEST             🚨 ALERT
🖥 NODES  ☁️ AWS
⬅️ Назад
```
Меню ИНЛАЙНОВОЕ (admin_menu_inline, callback `adm:<key>`); вход «🛠 Админка» (reply).
Заглушки (только текст): STATS, USERS, API, TEST, ALERT.
**Убраны из меню:** 📮 MAIL (типы почт) и 📑 TEMPL (шаблоны) — консолидация в рубрики; код tpl.py/mailtypes.py остался (дроп шаблонов `.html` и подстановка работают, но без кнопок-разделов).

**Callback-префиксы инлайн-разделов:** `smtp:`, `slim:` (лимиты), `short:`, `node:`, `aws:`, `tpl:`, `rub:`, `mb:`, `send:` (флоу), `admin:back`.

**Админ-меню — ИНЛАЙН** (`admin_menu_inline()` в admin_menu.py): чтобы на кнопках рендерились кастом-иконки (на reply-кнопках нельзя). Вход «🛠 Админка» (reply) → инлайн-меню 12 разделов + Назад (callback `adm:<key>`). Диспетчер `adm:` в index.py (`admin_section`) редактирует сообщение в клавиатуру раздела (переиспользует `kb_*_root()`), `adm:back` → главное меню. Reply-бар остаётся только для входа (📤/📋/🛠, юникод). back-кнопки разделов → `back_to_admin` → редактирует обратно в инлайн-админку.

**Навигация «Назад»:** `back_to_admin(cq)` в index.py (теперь редактирует сообщение в инлайн-админку) — удаляет инлайн-сообщение раздела и шлёт одно «Админка:» с reply-меню (чтобы не плодить дубли). Внутри разделов «Назад» редактирует сообщение на уровень выше. «🏠 В корень SMTP» = `smtp:root` (не `smtp:back`!).

## 7a. Флоу отправки — ДОМЕН-ОРИЕНТИРОВАННЫЙ (актуальный, flow_send.py переписан)
Шаги FSM: **service → link → email → backend → send**.
1. «📤 Отправить письмо» → выбрать/ввести **сервис** (olx.pl; кнопки из шаблонов + текст).
2. → **ссылка**.
3. → **e-mail получателя**.
4. На e-mail: `rubric_for_email(email)` по домену → **рубрика**. Нет рубрики → стоп с подсказкой. Есть → показывает рубрику, **почту (`mailbox_for_email`, по домену)**, шаблон/тайтл/отправителя (с учётом оверрайда сервиса, если есть — помечено 🔧).
5. **backend**: Локально / нода / AWS SES.
6. Отправка (`take_backend`): почта определяется по домену `mailbox_for_email(email)`; **оверрайд (сервис×рубрика×почта)** `service_rubric_cfg(service, rubric_id, mailbox)` (точная почта → иначе `mailbox=''`-generic) переопределяет **имя отправителя / тайтл / шаблон**. Если оверрайда нет — берётся из рубрики: SMTP-группа, тайтл, имя, сокращатель; **шаблон = `cfg.template_html` ИЛИ `resolve_template_html(service, рубрика.name[0])`** (код = ПЕРВАЯ БУКВА имени рубрики: «slayer»→`olx.pl-s.html`; нет → стандартный). `_do_send` шлёт выбранным бэкендом, пишет SendLog, инкрементит sent_count.
- Шаблоны хранятся по (service, **код-буква**) — `parse_template_filename('olx.pl-s.html')→('olx.pl','s')` (больше НЕ мапится на тип почты).
- Проверено на sqlite (sim_resolve): точный оверрайд (olx.pl,slayer,main)→CfgSender/CfgTitle/cfg-html; без оверрайда (allegro.pl)→имя/тайтл из рубрики + шаблон по букве `allegro.pl-s`; generic `mailbox=''` ловит gmail; нет рубрики для домена→стоп. SMTP-группа=slay, сокращатель уважается.

## 7. (устар.) старый флоу — режимы рубрика/вручную — переписан на §7a
Кнопка «📤 Отправить письмо» → выбор режима:
- **✍️ Вручную**: group(gmail/main) → backend(node/local/SES) → sender → subject → template(файл/текст/📄станд/📚библиотека) → link(авто-сокр) → email → отправка.
- **🗂 По рубрике**: rubric → service(из библиотеки шаблонов) → mailbox(почты рубрики) → **шаблон подтягивается авто** по (service,mailbox) → backend → link → email. sender/subject/smtp-группа/сокращатель берутся ИЗ рубрики; сокращение уважает `rubric.shortener`.

**Backend-шаг** (`_kb_backends`): «🖥 Локально», ноды (`send:be:node:{id}`, с гео), AWS-аккаунты (`send:be:aws:{id}`, подпись `({region}→нода|bot)`). Роутинг в `_do_send`:
- aws_id → **нода по РЕГИОНУ**: `_node_for_aws(aws)` берёт активную ноду с `region == aws.region` (гео AWS → нода с тем же гео), иначе ручная `aws.node_id`, иначе None;
- нода найдена → SES на ноде (`send_ses_via_node`); нет → SES bot-side (boto3, `send_via_ses`);
- node_id → SMTP на ноде (`send_via_node`);
- иначе → local SMTP (`mailer.send_via_account`).
При успешной SMTP-отправке `sent_count += 1`. Пишется SendLog. Кнопки результата: 🔁 Переотправить (in-memory `_LAST_SEND[user]`), ✉️ Новое письмо.

## 8. Разделы — детали
- **SMTP** (smtp.py): Add (формат `host:port:login:emailfrom:password`, при добавлении делается probe-отправка self-test на сам аккаунт), Clear (выборочно/неиспользуемые), Settings (привязка SMTP к global/страна/сервис через SmtpBinding), Add/Clear auto. Кнопки «Auto/Лимиты», «Limit», «Forced change» ведут в `slim:` (smtp_limits.py).
- **SMTP лимиты** (smtp_limits.py): по аккаунту — лимит/сброс счётчика/исчерпать/вкл-выкл; лимит на группу; сброс всех; принудительная смена (исчерпать текущий аккаунт группы → уйдёт следующий). **Автосмена** работает сама: `pick_smtp` пропускает `sent_count ≥ send_limit` (lim>0) и неактивные/исчерпанные привязки.
- **SHORT** (short.py + short_menu.py): выбор сокращателя (n9) по scope target(gmail/main)→global/страны→сервисы. Данные стран берутся из constants.
- **NODES** (nodes.py): добавление `login ip pass` (разделители ` :;|`, пачкой/.txt). **При добавлении спрашивает гео/регион AWS** (`NodeAdd.waiting_region`; `-`/кнопка «Без региона» = None) — применяется ко всей пачке. **Список = карточки нод** (`node:open:{id}`): 🌍 регион (сменить, `node:region:`→`NodeEdit.region`, `-`=убрать), 🟢/🔴 вкл-выкл (`node:toggle:`), 🧪 тест SSH, 🗑 удалить, снизу «+ Добавить». **Регион рулит маршрутом SES**: AWS-аккаунт (гео X) шлётся через ноду с `region==X` ⚠️ **Регион — ТОЧНЫЙ код AWS** (`us-east-1`, `eu-central-1` — как в AWS-аккаунте), матчинг строгий (`lower()==lower()`); «us» и прочие сокращения не сматчатся. Вводится **вручную** (по решению юзера — пикер не нужен), код региона печатать точно. — см. `_node_for_aws` в §7 Backend. Формат AWS: `region:accesskey:secretkey:emailfrom`. Нет ноды с этим гео → bot-side.
- **AWS** (aws.py): ВХОД из раздела 🖥 NODES (кнопка «☁️ AWS SES» → callback `aws:root`), отдельной кнопки в админ-меню нет. Добавление `region:accesskey:secretkey:emailfrom`, список→детали (🧪 тест квоты, 🖥 привязать ноду, 🗑).
- **TEMPLATES** (tpl.py): просто кидаешь `.html`; имя файла `service-код.html` решает → (service, mailbox). Коды из типов почт (m=main,g=gmail,h=hotmail,o=outlook,y=yahoo,+кастом). Upsert по (service,mailbox). Список/удаление.
- **SRVS (🧩) = рубрики + сервисы** (rubrics.py). Корень `kb_srvs_root()`: кнопки **Рубрики** (`rub:root`) и **Сервисы** (`svc:root`).
  - **РУБРИКИ** (`rub:`) = центральная сущность. **Корень `kb_rubrics_root()` = СРАЗУ список рубрик** (`rub:open:{id}`) + снизу «+ Добавить рубрику» (`rub:add`), «Назад»→`srv:root`. Клик по рубрике → карточка: **✏️ Название**, **🌐 Домены получателей** (короткие токены: gmx web gmail — см. §9 матчинг), **✉️ Почты** (добавить/убрать), SMTP-группа (gmail/main), сокращатель, тайтл, имя отправителя.
  - **Авто-выбор рубрики по домену получателя** при отправке: `rubric_for_email(email)` → рубрика, чьи `domains` содержат домен (gmx.de→main, gmail.com→gmail; нет → стоп). Колонка `rubric.domains` добавлена ALTER.
  - **СЕРВИСЫ** (`svc:`) = пер-сервисные оверрайды: **страна (`svc:c:{code}`) → сервис (`svc:s:{code}:{service}`) → рубрика (`svc:r:…:{rid}`) → почта (`svc:m:…:{mailbox}`) → детали**. На экране детали — **✏️ Имя отправителя / Тайтл / Шаблон** (`svc:e:{sn|st|tpl}:…`, FSM `ServiceCfgEdit`, шаблон = приём документа). Хранится в `ServiceRubricConfig` (`service_rubric_config`, uniq `service+rubric_id+mailbox`): поля `sender_name/title/template_html`, `mailbox=''`=любая. Резолв при отправке — `service_rubric_cfg()` (см. §7a.6).
- **MAIL / типы почт** (mailtypes.py): РАЗДЕЛ УБРАН из меню (дублировал рубрики — домены/почты теперь в рубриках). Код/таблица `mailbox` остались (коды m/g/h/o/y для имён шаблонов, почты рубрик), но админ-кнопки MAIL нет. `mailbox.domains` — устаревшая колонка (домены переехали на рубрику).

## 8a. Кастом-эмодзи iOS-иконки (как в proxy-проекте)
Пак: `t.me/addemoji/tgiosicons`. Механика повторяет рабочий проект `/Users/greedy/Downloads/ALL/proxy`:
- **Текст сообщений** → `richify()` оборачивает юникод-эмодзи в `<tg-emoji emoji-id=...>` (parse_mode=HTML). Карта эмодзи→id — `tgiosicons_ids.json` (генерится из `getStickerSet(name="tgiosicons")`). Middleware `RichifyRequestMiddleware`.
- **Инлайн-кнопки** → `icon_custom_emoji_id` (ведущий эмодзи, текст обрезается). Middleware `IconRequestMiddleware`, `iconize_markup`.
- **Reply-кнопки** → кастом-эмодзи НЕ рендерятся → остаются юникодом (proxy вообще без reply-меню). 
- Оба middleware регистрируются в main.py, гейтятся флагом **`USE_CUSTOM_EMOJI`** (config, env). На неподходящем боте = 0 → везде юникод.

⚠️ **ГЛАВНОЕ — eligibility БОТА:** кастом-эмодзи рендерятся только если **владелец бота Premium** (для личных чатов) ИЛИ у бота **Fragment-юзернейм**. Иначе Telegram молча схлопывает их в обычные эмодзи (в тексте `entities: None`, на кнопке иконки нет). Проверка eligibility: `sendMessage` с `<tg-emoji>` и parse_mode=HTML → в ответе `result.entities` должен содержать `custom_emoji`. 
- @testikgrbot (8075615020) — НЕ eligible. @mortyteam_bot (8681841649) — eligible. Текущий токен в .env — mortyteam.
- Это НЕ про код: на eligible-боте всё работает, на не-eligible — нет.
- ID иконок достаются ботом через **`getStickerSet(name="tgiosicons")`** (кастом-эмодзи-пак = стикерсет; у каждого стикера есть `custom_emoji_id` и базовый `emoji`).
- Применяется централизованно: `IconRequestMiddleware` (bot.session.middleware, подключён в main.py) → `iconize_markup()` у любой исходящей клавиатуры меняет ведущий эмодзи кнопки на кастом-иконку (по совпадению базового эмодзи, с нормализацией FE0F) и убирает эмодзи из текста.
- ⚠️ **`➕` УБРАН** из `EMOJI_ICON` и `tgiosicons_ids.json` — кастом-иконка пака выглядела как «⊙» (троеточие в круге) и путала. Все кнопки-добавления теперь используют ASCII-«+» (не иконизируется, не richify-ится). Менял через `perl -CSD -pe 's/\x{2795}/+/g'` по хендлерам.
- Карта `EMOJI_ICON` (46 эмодзи): 30 прямых + семантические замены (⏭→➡️, ♻️→🔄, ✍️→✏️, ✖️→❌, 🌍→🌐, 📋→📝, 📑→🔖, 📚→📖, 📧/📨/📮/✉️→📥, 🚨→🔔, 🛠→🔧, 🧪→🔎, 🧹→🧽, ⛔️→🚫). Юникодом остаются: 🔴 🟢 (статус), 🔹 (буллет), 🚀 (лог), и кнопки-«только эмодзи» (напр. одиночный 🗑 — текст не может стать пустым).
- **Иконизируются и инлайн-, и reply-меню.** У reply-кнопок текст обрезается до безэмодзийного ('SMTP', 'Админка', 'Назад'), поэтому хендлеры reply-команд матчат ОБА варианта: `F.text.in_({"🛠 Админка", "Админка"})` (трансформированы в index.py/start.py). `MENU_TEXTS`/`_MENU_TEXTS` тоже содержат стрип-варианты (через `strip_lead_emoji`). При добавлении новой reply-кнопки — её хендлер тоже матчить через `.in_({эмодзи-вариант, стрип})`.
- Чтобы поменять иконку: правь `EMOJI_ICON` в [bot/keyboards/icons.py](bot/keyboards/icons.py).

## 9. Ключевые решения, фишки и грабли (ПОМНИТЬ)
- ⚠️ **ТРИ разных «main/gmail» — не путать** (частая путаница юзера):
  1. **SMTP-группа** (`smtp_pool.SMTP_GROUPS = ("gmail","main")`, Literal) — ровно 2, захардкожены; к ним привязаны SMTP-аккаунты (SMTP→Настройки→Gmail/Main). Это `rubric.smtp_group`.
  2. **Почта / mailbox** (таблица `mailbox`, 5 шт: main/gmail/hotmail/outlook/yahoo) — тип по домену, засеиваются `ensure_seeded()`. Для `mailbox_for_email` и буквы шаблона.
  3. **Рубрика** (таблица `rubric`) — юзер создаёт сам (имя произвольное), вписывает домены, привязывает к SMTP-группе. **Без рубрик отправка стоит** (`rubric_for_email`→None).
  - Фикс бага: кнопка «SMTP-группа» у рубрики (`rub_smtp`) раньше предлагала ИМЕНА ПОЧТ (`mb.names()`, 5 шт, включая несуществующие как группы hotmail/outlook/yahoo) → теперь `SMTP_GROUPS` (gmail/main). Создание рубрики не завершалось если юзер жал /start вместо ввода имени (FSM `RubricAdd.name` сбрасывался) — рубрик было 0.
  - Засеяны 2 стартовые рубрики: main(group=main), gmail(group=gmail), домены пустые (юзер вписывает сам).
- **Гибкий матчинг доменов** (`common/domains.py`, `domain_in`/`token_matches`): в поле доменов рубрики/почты можно писать КОРОТКИЕ токены вперемешку с полными: `gmx`→gmx.de/net/com/*.gmx.de, `web`→web.de, `gmail`→gmail.com (НЕ googlemail — другая метка), полный `gmx.de`→gmx.de и поддомены. Короткий токен = любая метка домена (`token in dom.split('.')`), с точкой = домен/суффикс (`endswith`). Ведущий `@` игнорируется. Подключён в `rubric_for_email` и `mailbox_for_email` (раньше был строгий `dom in set` — только точное совпадение).
- ⚠️ **venv shebang битый** (папка переехала) → `alembic`/`pip` только через `.venv/bin/python -m ...`.
- ⚠️ **Пароли в открытом виде**: `common/security.py` encrypt/decrypt — заглушки (return as-is). `FERNET` генерит новый ключ на каждый старт (но не используется). На ноды/в SES пароли уходят plaintext внутри задания. НЕ продакшн.
- ⚠️ **SES и «разные IP»**: исходящий IP писем у SES — инфраструктура AWS, НЕ IP вызывающего. Запуск SES с ноды меняет IP API-запроса, не IP доставки. Для реально разных IP нужны dedicated IP pools AWS.
- **SigV4 для SES на ноде** (node_runner.ses_sigv4) — stdlib, **сверена с botocore побайтово**. В ноду-скрипт встраивается через `inspect.getsource(ses_sigv4)` — тестируется ровно то, что исполняется.
- **boto3 на нодах НЕ нужен** — SES на ноде через stdlib (urllib + ручной SigV4). На ноде нужен только `python3`.
- `SmtpBinding` импортировать из `common.models.autoswitch`, НЕ из `common.models.smtp` (там его нет — была ошибка ImportError в «Неиспользуемые»).
- `short_policy` работает с полем `enabled` (НЕ `is_active`), адаптера в модели нет — он всегда n9.
- Привязки SMTP хранятся составным scope-строкой («country:PL», «service:PL:olx.pl», «global»); `pick_smtp` читает тем же ключом (раньше был рассинхрон → привязки не влияли на отправку).
- `flow_send` раньше НЕ был зарегистрирован в диспетчере (кнопка отправки была мёртвой) — теперь регистрируется.
- Выход из FSM по нажатию нижнего меню: хендлеры `cancel_*_on_menu` (StateFilter + текст из MENU_TEXTS) — иначе нажатие «съедается» как ввод. Добавлять сюда каждое новое waiting-состояние.
- `db.exec(select(Model.col))` в SQLModel может вернуть скаляры — unpacking `(x,)` ломается. В flow_send есть `_scalars()`-хелпер.
- ⚠️ **Ошибка моя**: сначала сказал, что кастом-эмодзи на кнопках нельзя — НЕВЕРНО. Bot API 9.4 добавил `icon_custom_emoji_id` у кнопок; с Premium-владельцем работает. Проверять факты по актуальной версии, а не по памяти.
- ⚠️ **Грабля: убийство процесса бота.** `pkill -f "python -m bot.main"` ловит только zsh-обёртку (там lowercase `python`), а реальный процесс — `.../Python -m bot.main` (capital P) — остаётся сиротой и продолжает поллинг. Накопились 5 инстансов → `TelegramConflictError` → UI «мелькает». Убивать: `pkill -9 -f "bot.main"` (ловит и обёртку, и Python), проверять `ps ax | grep -- "-m bot.main" | grep -i python`.
- ⚠️ **Грабля: iconize reply-кнопок ломает навигацию, если не поправить хендлеры.** У reply-кнопок текст = команда. Решение: iconize обрезает эмодзи у reply-кнопок ТОЖЕ, а хендлеры матчат `F.text.in_({эмодзи, стрип})` + MENU_TEXTS содержит стрип-варианты. Новую reply-кнопку добавляешь — не забудь оба варианта в хендлере.
- Зависимости, добавленные в сессии: `asyncssh>=2.14,<2.18` (2.24 требует cryptography≥46, конфликт с пином `<44` → пинится 2.17), `boto3`, `aiohttp` (был undeclared), `jinja2` (был). cryptography держать `>=43,<44`.

## 10. Не сделано / TODO
- SMTP: присвоение аккаунту кастомной категории (kind) — сейчас kind инферится из email (gmail/main). Рубрика может выбрать кастомную SMTP-категорию, но реальных аккаунтов под неё не будет без ручного назначения kind → нужен экран редактирования SMTP (назначить kind).
- Шаблоны — глобальные по (service,mailbox); per-rubric override (rubric_id в шаблоне) не сделан.
- Массовая рассылка по списку (решили: пока один получатель).
- Заглушки разделов: STATS/USERS/API/TEST/ALERT, middlewares acl/context, services/templates — часть TODO.
- Новые таблицы вне alembic (см. §4) — при желании завести миграции.

## 12. Тестирование
Тестов в репо нет; pytest не установлен. Проверка — **offline smoke на in-memory sqlite** + статический аудит, Postgres/Telegram не трогаем.
- **Аудит кнопок**: собрать все `callback_data` (вкл. `_btn(...)`) и сверить с `F.data ==`/`startswith` хендлерами → 0 мёртвых кнопок. (f-string с переменным префиксом вроде `f"{prefix}:{x}"` даёт ложное «X:X:X» — проверять конкретные рантайм-варианты.)
- **Интеграция**: переопределить `common.db._engine` + `_engine` в КАЖДОМ модуле (список в §3/grep `_engine = get_engine()`), `create_all`, засидить, гонять сервисы и `_do_send` по всем бэкендам с замоканной сетью.
  - ⚠️ Грабля моков: `flow_send` импортит `send_via_node` на уровне модуля (`from ... import send_via_node`) → мокать `fs.send_via_node`, а не `node_runner.send_via_node`. А `send_ses_via_node`/`send_via_ses`/`mailer_svc.*` берутся при вызове (local import / attr) → мокаются на своих модулях.
- Последний полный прогон: 19/19 green (mailboxes, templates, rubrics, short_policy, smtp_pool+лимиты/автосмена, _do_send все 4 бэкенда + sent_count + shorten on/off, SendLog, сборка всех клавиатур) + аудит кнопок 0 dead.

## 11. Журнал сессии (что делали)
1. Починка кнопок «Назад» везде (SMTP home/root, SHORT, admin:back callback, Add-flow cancel, дубли «Админка:»).
2. Аудит кода → фиксы: SHORT bindings (schema mismatch), SmtpBinding import, flow_send не зарегистрирован, mailer без send, pick_smtp last_used_at (нет поля), probe на чужой gmail, ErrorReportMiddleware не подключён, dead smtp_menu.py.
3. Флоу отправки (один получатель): sender/subject/template/link/email + лог.
4. NODES (SSH-серверы) + отправка SMTP с ноды.
5. TEMPLATES (загрузка по имени файла) + RUBRICS + привязки рубрики + автоподстановка шаблона.
6. MAIL (управляемые типы почт, кастомные).
7. AWS SES (bot-side boto3 + node-side stdlib SigV4) + привязка к ноде.
8. SMTP лимиты/автосмена/принудительная смена (+ миграция).
9. Загрузка шаблона «кидком» без кнопок (глобальный document-хендлер при StateFilter(None)).
10. Кастом-эмодзи iOS-иконки на кнопках (пак tgiosicons, middleware) — см. §8a.
11. Консолидация меню в SRVS: MAIL/TEMPL/AWS убраны из топ-меню (AWS → под NODES); админ-меню стало ИНЛАЙН (для рендера иконок).
12. Авто-рубрика по домену получателя (`rubric_for_email`), почта по домену (`mailbox_for_email`). Шаблон = первая буква рубрики.
13. SRVS = Рубрики + Сервисы: пер-сервисные оверрайды (сервис×рубрика×почта) имени/тайтла/шаблона — `ServiceRubricConfig`, навигация `svc:*`. Резолв вшит в `flow_send.take_backend`. Проверено sim_resolve (5 кейсов) + чистый рестарт (@mortyteam_bot, 1 поллер, таблица создана).
14. Разбор путаницы «почему нет рубрик»: у юзера main/gmail жили как SMTP-группы и почты, но таблица `rubric` пустая (создание не доводилось — /start вместо ввода имени). Вынес `SMTP_GROUPS` в `smtp_pool`, починил баг выбора SMTP-группы рубрики (отдавал имена почт → теперь gmail/main), засеял рубрики main+gmail (домены пусто). См. §9 «ТРИ разных main/gmail».
15. Гибкий матчинг доменов (`common/domains.py`): короткие токены `gmx web gmail` ловят gmx.de/net, web.de, gmail.com (метка домена), полный `gmx.de` — домен+поддомены. Подключён в `rubric_for_email`+`mailbox_for_email`. 17/17 проверок.
16. Ноды: поле `region` (гео AWS) + ALTER. Добавление в 2 шага (ноды → вопрос про регион, `-`/кнопка = без). Список нод = карточки: сменить регион, вкл/выкл, тест, удалить, «+ Добавить». Сквозной тест ОК. Путь SES пока не трогал.
17. Рубрики: корень = сразу список + «+ Добавить» снизу, клик → карточка с правкой почт. Заменил иконку-троеточие `➕`→ASCII «+» везде (убрал из обеих карт). Рестарт чистый, проверки зелёные.
18. SES-маршрут по региону: `_node_for_aws(aws)` — AWS-аккаунт (гео X) шлётся через ноду с `region==X` (иначе ручная привязка `aws.node_id`, иначе bot-side). Picker/лог показывают маршрут. Симуляция 4/4 (match/активная/нет-ноды/ручная).
