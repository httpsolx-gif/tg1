"""Личные short-домены: флоу добавления (Cloudflare → NS → active → A-запись)
и управление/провижн origin-ноды. Колбэки `sdom:*`.

Порядок без утечки IP:
  A. CF: создать зону → показать NS (регистратор вручную) → ждать active →
     SSL=Full + Always HTTPS → ПРОКСИРОВАННАЯ A-запись (оранжевое облако).
  B. SSH-провижн ноды: origin-cert, мини-сервис, фаервол только для IP CF → strict.
"""
from __future__ import annotations

import logging
from html import escape as _esc

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext
from sqlmodel import Session, select

from common.db import get_engine
from common.config import settings
from common.security import encrypt_password, decrypt_password
from common.models.node import Node
from common.models.short_domain import ShortDomain
from common.models.scope import ScopeCfg
from bot.fsm.admin import ShortDomainAdd
from bot.services import cloudflare as cf
from bot.services import scope as sc
from bot.services import keys
from bot.services.short_domain import gen_secret

router = Router(name="short-domains")
log = logging.getLogger("bot")
_engine = get_engine()

_MULTI_SUFFIX = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "net.au", "org.au",
    "co.nz", "com.br", "co.jp", "com.tr", "co.za", "com.mx",
}


def _admin(uid: int) -> bool:
    return uid in settings.admin_ids_set


def _btn(t: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t, callback_data=cb)


def _cf_token(d: ShortDomain) -> str:
    """Общий токен CF (OTHER → 🔑 Ключи); старый, сохранённый в домене, — запасной."""
    return keys.get("cf") or decrypt_password(d.cf_token_enc or "")


def _registrar_key(d: ShortDomain) -> str:
    return keys.get(d.registrar or "") or decrypt_password(d.registrar_token_enc or "")


_REG_RU = {"namesilo": "NameSilo", "dynadot": "Dynadot"}


async def _detect_registrar(zone: str) -> str | None:
    """Чей домен: NameSilo (если ключ есть и домен в аккаунте), иначе Dynadot (если ключ есть)."""
    ns_key, dd_key = keys.get("namesilo"), keys.get("dynadot")
    if ns_key:
        from bot.services import namesilo
        try:
            await namesilo.get_nameservers(ns_key, zone)
            return "namesilo"
        except Exception:
            pass
    if dd_key:
        return "dynadot"
    return None


def _apex(domain: str) -> str:
    """Грубая оценка apex-зоны (без PSL): учитывает частые многосоставные суффиксы."""
    labels = domain.lower().strip(".").split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_SUFFIX:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else domain


_STATUS_RU = {
    "new": "создан", "zone": "зона CF создана", "pending_ns": "ждём NS у регистратора",
    "active": "CF active, A-запись есть", "provisioning": "идёт настройка сервера",
    "ready": "✅ готов", "error": "⚠️ ошибка",
}


def _short_status(status: str) -> str:
    return "Work" if status == "ready" else "Pending"


# ======================= карточка домена =======================
def _card(did: int, note: str | None = None):
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        node = db.get(Node, d.node_id) if d and d.node_id else None
    if not d:
        return None, None
    # коротко (п.92): домен · Pending/Work, нода, NS (пока не готов), ошибка — если есть
    lines = [f"🌐 <b>{_esc(d.domain)}</b> · <b>{_short_status(d.status)}</b>"]
    if node:
        lines.append(f"• Origin-нода: <code>{_esc(node.ip)}</code>")
    if d.status != "ready" and (d.ns1 or d.ns2):
        lines.append(f"\n   <code>{_esc(d.ns1 or '')}</code>\n   <code>{_esc(d.ns2 or '')}</code>")
    if d.last_error and d.status != "ready":
        lines.append(f"\n⚠️ <code>{_esc(d.last_error[:160])}</code>")

    rows = []
    if d.status in ("zone", "pending_ns"):
        if d.registrar and _registrar_key(d):
            rows.append([_btn(f"⚡ Авто-NS ({_REG_RU.get(d.registrar, d.registrar)})", f"sdom:autons:{did}")])
        rows.append([_btn("🔄 Проверить NS", f"sdom:checkns:{did}")])
    if d.status in ("active", "error", "provisioning"):
        rows.append([_btn("⚙️ Настроить сервер", f"sdom:provision:{did}")])
    if d.status == "ready":
        rows.append([_btn("🧪 Тест редиректа", f"sdom:test:{did}"),
                     _btn("📊 Статистика", f"sdom:stats:{did}")])
    if d.status in ("ready", "active", "error"):
        extra = [_btn("🔀 Сменить ноду", f"sdom:movenode:{did}")]
        if d.status == "ready":
            extra.append(_btn("♻️ Переразвернуть", f"sdom:provision:{did}"))
        rows.append(extra)
    rows.append([_btn("🔁 Обновить", f"sdom:open:{did}"), _btn("🗑 Удалить", f"sdom:del:{did}")])
    rows.append([_btn("⬅️ Назад", "sdom:list")])
    if note:
        lines.append("\n" + note)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_card(cq: CallbackQuery, did: int, note: str | None = None):
    """Перерисовать карточку НА МЕСТЕ (результат действия — строкой note внизу), без новых сообщений."""
    text, kb = _card(did, note)
    if text is None:
        await _answer(cq, "Домен не найден", alert=True)
        return
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e):  # то же самое — ничего не шлём (раньше тут дублилась карточка)
            await cq.message.answer(text, reply_markup=kb)
    await _answer(cq)


async def _answer(cq: CallbackQuery, text: str | None = None, alert: bool = False) -> None:
    try:
        await cq.answer(text, show_alert=alert)
    except Exception:
        pass  # уже отвечен — не страшно


# ======================= список доменов =======================
# откуда открыли список: id области (⚙️ SHORT на карточке) → «Назад» на карточку; 0 = из раздела SHORT
_ret_scope: dict[int, int] = {}


def _list_kb(uid: int = 0) -> InlineKeyboardMarkup:
    with Session(_engine) as db:
        doms = db.exec(select(ShortDomain).order_by(ShortDomain.id)).all()
    rows = []
    for d in doms:
        mark = "✅" if d.status == "ready" else ("⚠️" if d.status == "error" else "⏳")
        rows.append([_btn(f"{mark} {d.domain} · {_short_status(d.status)}", f"sdom:open:{d.id}")])
    sid = _ret_scope.get(uid, 0)
    rows.append([_btn("➕ Добавить свой домен", "sdom:add:0")])
    rows.append([_btn("⬅️ Назад", f"sco:card:{sid}" if sid else "sdom:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "sdom:list")
@router.callback_query(F.data.startswith("sdom:list:"))
async def sdom_list(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    uid = cq.from_user.id
    if cq.data.startswith("sdom:list:"):  # явный вход: с карточки (id области) или из раздела SHORT (0)
        _ret_scope[uid] = int(cq.data.split(":")[2])
    try:
        await cq.message.edit_text("🌐 Личные short-домены:", reply_markup=_list_kb(uid))
    except Exception:
        await cq.message.answer("🌐 Личные short-домены:", reply_markup=_list_kb(uid))
    await cq.answer()


@router.callback_query(F.data == "sdom:back")
async def sdom_back(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    from bot.handlers.admin.index import back_to_admin
    await back_to_admin(cq)


@router.callback_query(F.data.startswith("sdom:open:"))
async def sdom_open(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await _show_card(cq, int(cq.data.split(":")[2]))


# ======================= добавление: сбор данных =======================
def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Отмена", "sdom:list")]])


@router.callback_query(F.data.startswith("sdom:add:"))
async def sdom_add(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    bind_sid = int(cq.data.split(":")[2])  # область, к которой привязать (0 = ниоткуда)
    if bind_sid:  # с карточки области → «Отмена»/«Назад» из списка вернут на неё
        _ret_scope[cq.from_user.id] = bind_sid
    await state.clear()
    await state.set_state(ShortDomainAdd.domain)
    await state.update_data(bind_sid=bind_sid)
    await cq.message.answer(
        "🌐 Введите <b>short-домен</b> (хост в ссылках), напр. <code>go.example.com</code> "
        "или <code>example.com</code>.\n\n"
        "⚠️ Домен должен быть твой, и ты сможешь сменить у него NS у регистратора.",
        reply_markup=_cancel_kb(),
    )
    await cq.answer()


@router.message(ShortDomainAdd.domain, F.text)
async def sdom_in_domain(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    dom = (m.text or "").strip().lower()
    for pre in ("https://", "http://"):
        if dom.startswith(pre):
            dom = dom[len(pre):]
    dom = dom.strip("/").split("/")[0]
    if "." not in dom or " " in dom:
        await m.answer("Нужен корректный домен, напр. <code>go.example.com</code>. Ещё раз:")
        return
    await state.update_data(domain=dom, zone_name=_apex(dom))
    if keys.get("cf"):  # токен уже задан в OTHER → 🔑 Ключи — сразу к ноде
        await _ask_node(m, state)
        return
    await state.set_state(ShortDomainAdd.token)
    await m.answer(
        f"Зона CF будет: <code>{_esc(_apex(dom))}</code>\n\n"
        "🔑 Токен Cloudflare не задан (OTHER → 🔑 Ключи). Пришлите <b>Cloudflare API Token</b> "
        "(scoped: Zone·DNS·Edit + SSL and Certificates·Edit) — сохраню его в ключи:",
        reply_markup=_cancel_kb(),
    )


async def _ask_node(m: Message, state: FSMContext, head: str = "") -> None:
    with Session(_engine) as db:
        nodes = db.exec(select(Node).where(Node.is_active == True)).all()  # noqa: E712
    short_nodes = [n for n in nodes if n.has_role("short")]
    if len(short_nodes) == 1:  # единственная short-нода — выбираем сами, без вопроса
        await _create_domain(m, state, short_nodes[0].id, m.from_user.id)
        return
    await state.set_state(ShortDomainAdd.node)
    note, kb = _nodes_kb()
    await m.answer(head + "🖥 Выберите <b>origin-ноду</b> (где встанет редирект за CF):" + note, reply_markup=kb)


@router.message(ShortDomainAdd.token, F.text)
async def sdom_in_token(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    token = (m.text or "").strip()
    try:
        await m.delete()  # не держим токен в истории чата
    except Exception:
        pass
    ok, info = await cf.verify_token(token)
    if not ok:
        await m.answer(f"🔴 Токен не прошёл проверку: <code>{_esc(info[:200])}</code>\nПришлите другой:",
                       reply_markup=_cancel_kb())
        return
    keys.set("cf", token)
    await _ask_node(m, state, "✅ Токен валиден и сохранён в OTHER → 🔑 Ключи.\n\n")


def _nodes_kb() -> tuple[str, InlineKeyboardMarkup]:
    with Session(_engine) as db:
        nodes = db.exec(select(Node).where(Node.is_active == True).order_by(Node.id)).all()  # noqa: E712
    short_nodes = [n for n in nodes if n.has_role("short")]
    pool = short_nodes or nodes
    note = "" if short_nodes else "\n<i>Нет нод с ролью «сокращалка» — показаны все активные. Роль можно задать в NODES.</i>"
    rows = [[_btn(f"🖥 {n.login}@{n.ip} [{n.purpose}]", f"sdom:pick:{n.id}")] for n in pool]
    if not pool:
        rows = [[_btn("Нет активных нод — добавьте в NODES", "sdom:list")]]
    rows.append([_btn("⬅️ Отмена", "sdom:list")])
    return note, InlineKeyboardMarkup(inline_keyboard=rows)


# ======================= добавление: создать зону CF =======================
@router.callback_query(ShortDomainAdd.node, F.data.startswith("sdom:pick:"))
async def sdom_pick_node(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.answer()
    try:
        await cq.message.delete()  # убрать список нод
    except Exception:
        pass
    await _create_domain(cq.message, state, int(cq.data.split(":")[2]), cq.from_user.id)


async def _create_domain(msg: Message, state: FSMContext, node_id: int, uid: int) -> None:
    """Создать зону CF и запись домена на выбранной ноде; ответы — новыми сообщениями в чат msg."""
    data = await state.get_data()
    await state.clear()
    domain, zone = data["domain"], data["zone_name"]
    token, email, bind_sid = keys.get("cf"), None, int(data.get("bind_sid") or 0)
    if not token:
        await msg.answer("🔴 Нет токена Cloudflare — задайте в OTHER → 🔑 Ключи.")
        return
    registrar = await _detect_registrar(zone)

    # зона уже есть в CF → пересоздаём (чистая, без старых записей); но не если в ней живут
    # другие наши short-домены (поддомены той же apex-зоны) — их бы снесло
    with Session(_engine) as db:
        shared = db.exec(select(ShortDomain).where(ShortDomain.zone_name == zone,
                                                   ShortDomain.domain != domain)).first()
    wait = await msg.answer(f"⏳ {_esc(domain)}…")
    try:
        zone_id, ns = await cf.create_zone(token, zone, recreate=not shared)
    except Exception as e:
        await wait.edit_text(f"🔴 Не удалось создать зону: <code>{_esc(str(e)[:250])}</code>",
                             reply_markup=_list_kb(uid))
        return

    with Session(_engine) as db:
        d = db.exec(select(ShortDomain).where(ShortDomain.domain == domain)).first()
        if d is None:
            d = ShortDomain(domain=domain)
        d.zone_name = zone
        # токены — общие (keys); в домене только чей регистратор
        d.cf_account_email = email
        d.ssl_email = email
        d.registrar = registrar
        d.cf_zone_id = zone_id
        d.ns1 = ns[0] if len(ns) > 0 else None
        d.ns2 = ns[1] if len(ns) > 1 else None
        d.node_id = node_id
        d.origin_ip = db.get(Node, node_id).ip if db.get(Node, node_id) else None
        d.secret_enc = encrypt_password(gen_secret())
        d.status = "pending_ns"
        d.created_at = sc.now_iso()
        db.add(d)
        db.commit()
        db.refresh(d)
        did = d.id
        # привязка к области, из которой пришли (ссылки не укоротятся, пока статус != ready)
        if bind_sid:
            cfg = db.get(ScopeCfg, bind_sid)
            if cfg:
                cfg.short, cfg.short_at = f"dom:{did}", sc.now_iso()
                db.add(cfg)
                db.commit()

    note = None
    if registrar:  # ключ регистратора есть — NS прописываем сразу, дальше фон доведёт до Work
        err = await _autons(did)
        note = f"🔴 <code>{_esc(err)}</code>" if err else "⏳ NS прописаны — дальше само, пришлю «work ✅»."
    text, kb = _card(did, note)
    await wait.edit_text(text, reply_markup=kb)


# ======================= авто-NS у регистратора (NameSilo) =======================
async def _autons(did: int) -> str | None:
    """Прописать NS CF у регистратора. → None (ок) | текст ошибки."""
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        reg_tok = _registrar_key(d)
        reg = d.registrar or "namesilo"
        zone, ns1, ns2 = (d.zone_name or d.domain), d.ns1, d.ns2
    name = _REG_RU.get(reg, reg)
    if not reg_tok:
        return f"нет ключа {name} (OTHER → 🔑 Ключи)"
    if reg == "dynadot":
        from bot.services import dynadot as api
    else:
        from bot.services import namesilo as api
    try:
        await api.change_nameservers(reg_tok, zone, [ns1, ns2])
    except Exception as e:
        return f"{name}: {str(e)[:160]} — пропишите NS вручную"
    return None


async def _activate(did: int) -> tuple[str, str | None]:
    """NS прописались? → SSL Full + Always HTTPS + ПРОКСИРОВАННАЯ A-запись + чистка записей с IP.
    → ("active"|"pending"|"error", ошибка/статус зоны)."""
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        token = _cf_token(d)
        zone_id, origin_ip, domain = d.cf_zone_id, d.origin_ip, d.domain
    try:
        await cf.activation_check(token, zone_id)
        st = await cf.zone_status(token, zone_id)
    except Exception as e:
        return "pending", f"CF: {str(e)[:160]}"  # сеть/CF — попробуем позже
    if st != "active":
        return "pending", st
    try:
        await cf.set_ssl(token, zone_id, "full")
        await cf.always_use_https(token, zone_id, True)
        try:  # анти-флуд на краю CF; не встало (нет прав WAF у токена) — домен всё равно поднимаем
            await cf.set_rate_limit(token, zone_id)
        except Exception as e:
            log.warning("short %s: rate limit CF не поставлен: %s", domain, e)
        rid = await cf.upsert_a_record(token, zone_id, domain, origin_ip, proxied=True)
        leaks = await cf.hide_origin_ip(token, zone_id, origin_ip)
        if leaks:
            log.warning("short %s: исправлены записи со светившимся IP: %s", domain, leaks)
    except Exception as e:
        with Session(_engine) as db:
            d = db.get(ShortDomain, did)
            d.status, d.last_error = "error", str(e)[:250]
            db.add(d); db.commit()
        return "error", str(e)[:160]
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        d.cf_record_id, d.status, d.last_error, d.activated_at = rid, "active", None, sc.now_iso()
        db.add(d); db.commit()
    return "active", None


_busy: set[int] = set()  # домены, которые прямо сейчас поднимаются (кнопка или фон)


async def _advance(did: int, bot) -> tuple[str, str | None]:
    """Довести домен как можно дальше: NS → active → сервер → Work. Ошибка → уведомление админам.
    → (состояние, пояснение)."""
    if did in _busy:
        return "busy", None
    _busy.add(did)
    try:
        with Session(_engine) as db:
            d = db.get(ShortDomain, did)
            status, domain = (d.status, d.domain) if d else (None, "")
        if status in ("zone", "pending_ns"):
            state, info = await _activate(did)
            if state == "error":
                await _notify_admins(bot, f"🔴 Домен <b>{_esc(domain)}</b>: Cloudflare — <code>{_esc(info or '')}</code>")
                return state, info
            if state != "active":
                return state, info
            status = "active"
        if status in ("active", "provisioning"):
            ok, _logs, err = await _do_provision(did, bot)  # успех → «Домен X work ✅» (п.93)
            if not ok:
                await _notify_admins(bot, f"🔴 Домен <b>{_esc(domain)}</b>: сервер не настроен — <code>{_esc(err[:160])}</code>")
                return "error", err
            return "ready", None
        return status or "missing", None
    finally:
        _busy.discard(did)


async def short_autoloop(bot) -> None:
    """Фон: домены в ожидании NS / с зоной active сами доходят до Work (раз в минуту)."""
    import asyncio
    while True:
        await asyncio.sleep(60)
        try:
            with Session(_engine) as db:
                ids = [d.id for d in db.exec(select(ShortDomain).where(
                    ShortDomain.status.in_(("zone", "pending_ns", "active", "provisioning")))).all()]
            for did in ids:
                await _advance(did, bot)
        except Exception as e:
            log.warning("short autoloop: %s", e)


@router.callback_query(F.data.startswith("sdom:autons:"))
async def sdom_autons(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    await _show_card(cq, did, "⏳ Прописываю NS…")
    err = await _autons(did)
    await _show_card(cq, did, f"🔴 <code>{_esc(err)}</code>" if err else "⏳ NS прописаны — дальше само, пришлю «work ✅».")


# ======================= проверка NS / активация → сразу сервер =======================
@router.callback_query(F.data.startswith("sdom:checkns:"))
async def sdom_checkns(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    await _show_card(cq, did, "⏳ Проверяю…")
    state, info = await _advance(did, cq.bot)
    note = {
        "ready": None,
        "busy": "⏳ Уже поднимается — пришлю «work ✅».",
        "pending": f"⏳ NS ещё не прописались ({_esc(info or '')}) — проверяю сам раз в минуту.",
    }.get(state, f"🔴 <code>{_esc(info or state)}</code>")
    await _show_card(cq, did, note)


# ======================= провижн origin (фаза B) =======================
async def _do_provision(did: int, bot=None) -> tuple[bool, list[str], str]:
    """Загрузить домен+ноду, выполнить provision, записать статус/cert. → (ok, logs, err).
    bot — чтобы прислать админам «Домен X work ✅», когда домен впервые стал Work."""
    with Session(_engine) as db:
        drow = db.get(ShortDomain, did)
        was_ready = drow.status == "ready"
        node = db.get(Node, drow.node_id) if drow and drow.node_id else None
        token = _cf_token(drow)
        secret = decrypt_password(drow.secret_enc or "")
        drow.status = "provisioning"; drow.last_error = None
        db.add(drow); db.commit(); db.refresh(drow)
        db.expunge(drow)
        if node is not None:
            db.refresh(node)  # commit истёк объект — перечитываем перед detach
            db.expunge(node)
    if node is None:
        with Session(_engine) as db:
            d = db.get(ShortDomain, did); d.status = "error"; d.last_error = "нет origin-ноды"; db.add(d); db.commit()
        return False, [], "нет origin-ноды"
    from bot.services.short_provision import provision
    ok, logs, err = await provision(node, drow, token, secret)
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        d.cf_cert_id = drow.cf_cert_id
        if ok:
            d.status = "ready"; d.ready_at = sc.now_iso(); d.last_error = None
        else:
            d.status = "error"; d.last_error = err[:250]
        db.add(d); db.commit()
        domain = d.domain
    if ok and not was_ready and bot is not None:
        await _notify_admins(bot, f"Домен <b>{_esc(domain)}</b> work ✅")
    return ok, logs, err


async def _notify_admins(bot, text: str) -> None:
    for uid in settings.admin_ids_set:
        try:
            await bot.send_message(uid, text)
        except Exception as e:
            log.warning("notify admin %s: %s", uid, e)


@router.callback_query(F.data.startswith("sdom:provision:"))
async def sdom_provision(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    if did in _busy:
        await _show_card(cq, did, "⏳ Уже поднимается — пришлю «work ✅».")
        return
    await _show_card(cq, did, "⏳ Настраиваю сервер…")
    _busy.add(did)
    try:
        ok, logs, err = await _do_provision(did, cq.bot)
    finally:
        _busy.discard(did)
    await _show_card(cq, did, None if ok else f"🔴 <code>{_esc(err[:160])}</code>")


# ======================= перенос на другую ноду / переразвёртывание =======================
@router.callback_query(F.data.startswith("sdom:movenode:"))
async def sdom_movenode(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        cur = d.node_id if d else None
        nodes = db.exec(select(Node).where(Node.is_active == True).order_by(Node.id)).all()  # noqa: E712
    short_nodes = [n for n in nodes if n.has_role("short")] or nodes
    rows = [[_btn(f"🖥 {n.login}@{n.ip} [{n.purpose}]" + (" ⬅️ текущая" if n.id == cur else ""),
                  f"sdom:setnode:{did}:{n.id}")] for n in short_nodes]
    rows = rows or [[_btn("Нет активных нод", f"sdom:open:{did}")]]
    rows.append([_btn("⬅️ Отмена", f"sdom:open:{did}")])
    await cq.message.edit_text("🔀 Выберите новую origin-ноду (A-запись переедет, сервис развернётся):",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


@router.callback_query(F.data.startswith("sdom:setnode:"))
async def sdom_setnode(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, did, nid = cq.data.split(":", 3)
    did, nid = int(did), int(nid)
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        node = db.get(Node, nid)
        token = _cf_token(d)
        zone_id, domain = d.cf_zone_id, d.domain
        newip = node.ip if node else None
    if not newip:
        await cq.answer("Нода не найдена", show_alert=True)
        return
    await cq.answer("Переношу…")
    # 1) перекинуть проксированную A-запись на новый IP
    try:
        rid = await cf.upsert_a_record(token, zone_id, domain, newip, proxied=True)
        await cf.hide_origin_ip(token, zone_id, newip)
    except Exception as e:
        await cq.message.answer(f"🔴 CF A-запись: <code>{_esc(str(e)[:200])}</code>")
        return
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        d.node_id = nid; d.origin_ip = newip; d.cf_record_id = rid
        db.add(d); db.commit()
    await _show_card(cq, did, "⏳ Разворачиваю на новой ноде…")
    # 2) провижн новой ноды
    ok, logs, err = await _do_provision(did, cq.bot)
    await _show_card(cq, did, "✅ Перенесено." if ok else f"🔴 <code>{_esc(err[:160])}</code>")


# ======================= тест редиректа через публичный CF =======================
@router.callback_query(F.data.startswith("sdom:test:"))
async def sdom_test(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        ready = bool(d and d.is_active and d.status == "ready")
        domain = d.domain if d else None
        node = db.get(Node, d.node_id) if d and d.node_id else None
        if node is not None:
            db.expunge(node)
    await cq.answer()
    if not (ready and domain and node):
        await cq.message.answer("⚠️ Домен не готов или нет ноды — тест недоступен.")
        return
    from bot.services.short_domain import register_link
    code = await register_link(node, domain, "https://example.org/probe?src=shortcf")
    if not code:
        await cq.message.answer("🔴 Не удалось зарегистрировать код на ноде (нода недоступна?).")
        return
    url = f"https://{domain}/{code}"
    await cq.message.answer(
        "🧪 Тестовая ссылка (должна 301 на example.org):\n"
        f"<code>{_esc(url)}</code>\n\nОткройте в браузере — проверка идёт через публичный CF."
    )


# ======================= статистика переходов =======================
@router.callback_query(F.data.startswith("sdom:stats:"))
async def sdom_stats(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        node = db.get(Node, d.node_id) if d and d.node_id else None
        domain = d.domain if d else None
    if node is None:
        await cq.answer("Нет origin-ноды", show_alert=True)
        return
    await cq.answer("Читаю статистику…")
    from bot.services.short_stats import click_stats
    st = await click_stats(node, domain)
    if "error" in st:
        await cq.message.answer(f"🔴 Статистика недоступна: <code>{_esc(st['error'])}</code>")
        return
    lines = [f"📊 <b>{_esc(domain)}</b>", f"• Уникальных переходов: <b>{st.get('unique', 0)}</b> <i>(1 slug = 1 переход)</i>"]
    recent = st.get("recent") or []
    if recent:
        lines.append("\nПоследние:")
        for ts, target in recent:
            lines.append(f"• <code>{_esc((ts or '')[:19])}</code> → {_esc((target or '')[:60])}")
    await cq.message.answer("\n".join(lines))


# ======================= удаление =======================
def delete_domains(db: Session, ids: list[int]) -> int:
    """Удалить домены из бота (CF-зона и сервис на ноде остаются) и снять ссылки областей на них."""
    n = 0
    for did in ids:
        d = db.get(ShortDomain, did)
        if d:
            db.delete(d)
            n += 1
        for cfg in db.exec(select(ScopeCfg).where(ScopeCfg.short == f"dom:{did}")).all():
            cfg.short, cfg.short_at = None, None
            db.add(cfg)
    db.commit()
    return n


@router.callback_query(F.data.startswith("sdom:del:"))
async def sdom_del(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        _btn("✅ Да, удалить", f"sdom:delyes:{did}"), _btn("❌ Нет", f"sdom:open:{did}"),
    ]])
    await cq.message.edit_text(
        "🗑 Удалить домен из бота?\n<i>Зона и записи в Cloudflare и сервис на ноде останутся — "
        "удалите их вручную при необходимости.</i>", reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("sdom:delyes:"))
async def sdom_del_yes(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    with Session(_engine) as db:
        delete_domains(db, [did])
    await cq.message.edit_text("🌐 Личные short-домены:", reply_markup=_list_kb(cq.from_user.id))
    await cq.answer("Удалён")
