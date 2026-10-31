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


# ======================= карточка домена =======================
def _card(did: int):
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        node = db.get(Node, d.node_id) if d and d.node_id else None
    if not d:
        return None, None
    lines = [
        f"🌐 <b>{_esc(d.domain)}</b>",
        f"• Зона: <code>{_esc(d.zone_name)}</code>",
        f"• Статус: <b>{_STATUS_RU.get(d.status, d.status)}</b>",
    ]
    if node:
        lines.append(f"• Origin-нода: <code>{_esc(node.login)}@{_esc(node.ip)}</code>")
    if d.ns1 or d.ns2:
        lines.append(f"• NS CF (пропишите у регистратора):\n   <code>{_esc(d.ns1 or '')}</code>\n   <code>{_esc(d.ns2 or '')}</code>")
    if d.last_error:
        lines.append(f"• Последняя ошибка: <code>{_esc(d.last_error[:200])}</code>")

    rows = []
    if d.status in ("zone", "pending_ns"):
        if d.registrar and _registrar_key(d):
            rows.append([_btn(f"⚡ Авто-NS ({_REG_RU.get(d.registrar, d.registrar)})", f"sdom:autons:{did}")])
        rows.append([_btn("🔄 Проверить NS / активацию", f"sdom:checkns:{did}")])
    if d.status in ("active", "error", "provisioning"):
        rows.append([_btn("⚙️ Настроить сервер (origin)", f"sdom:provision:{did}")])
    if d.status == "ready":
        rows.append([_btn("🧪 Тест редиректа", f"sdom:test:{did}"),
                     _btn("📊 Статистика", f"sdom:stats:{did}")])
    if d.status in ("ready", "active", "error"):
        extra = [_btn("🔀 Сменить ноду", f"sdom:movenode:{did}")]
        if d.status == "ready":
            extra.append(_btn("♻️ Переразвернуть", f"sdom:provision:{did}"))
        rows.append(extra)
    rows.append([_btn("🔁 Статус/обновить", f"sdom:open:{did}"), _btn("🗑 Удалить", f"sdom:del:{did}")])
    rows.append([_btn("⬅️ К списку доменов", "sdom:list")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_card(cq: CallbackQuery, did: int, note: str | None = None):
    text, kb = _card(did)
    if text is None:
        await cq.answer("Домен не найден", show_alert=True)
        return
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer(note or None)


# ======================= список доменов =======================
# откуда открыли список: id области (⚙️ SHORT на карточке) → «Назад» на карточку; 0 = из раздела SHORT
_ret_scope: dict[int, int] = {}


def _list_kb(uid: int = 0) -> InlineKeyboardMarkup:
    with Session(_engine) as db:
        doms = db.exec(select(ShortDomain).order_by(ShortDomain.id)).all()
    rows = []
    for d in doms:
        mark = "✅" if d.status == "ready" else ("⚠️" if d.status == "error" else "⏳")
        rows.append([_btn(f"{mark} {d.domain} — {_STATUS_RU.get(d.status, d.status)}", f"sdom:open:{d.id}")])
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
        await _ask_node(m, state, f"Зона CF будет: <code>{_esc(_apex(dom))}</code>\n\n")
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
        n = short_nodes[0]
        await m.answer(head + f"🖥 Origin-нода: <code>{_esc(n.login)}@{_esc(n.ip)}</code> (единственная с ролью short)")
        await _create_domain(m, state, n.id, m.from_user.id)
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

    wait = await msg.answer(f"⏳ Создаю зону <code>{_esc(zone)}</code> в Cloudflare…")
    try:
        zone_id, ns = await cf.create_zone(token, zone)
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
        did, ns1, ns2 = d.id, d.ns1, d.ns2  # до commit ниже: после него объект истекает
        # привязка к области, из которой пришли (ссылки не укоротятся, пока статус != ready)
        if bind_sid:
            cfg = db.get(ScopeCfg, bind_sid)
            if cfg:
                cfg.short, cfg.short_at = f"dom:{did}", sc.now_iso()
                db.add(cfg)
                db.commit()

    bind_note = "\n\n🔗 Домен уже выбран как SHORT для области (заработает после готовности)." if bind_sid else ""
    ns_note = (f"Нажмите «⚡ Авто-NS ({_REG_RU[registrar]})» ниже — пропишу сам."
               if registrar else "Ключа регистратора нет (OTHER → 🔑 Ключи) — смените NS у регистратора вручную.")
    await wait.edit_text(
        "✅ Зона создана. Нужно прописать домену NS Cloudflare:\n"
        f"   <code>{_esc(ns1 or '')}</code>\n   <code>{_esc(ns2 or '')}</code>\n\n"
        + ns_note + " Как пропишется (минуты–часы) — жмите «Проверить NS»." + bind_note,
    )
    text, kb = _card(did)
    await msg.answer(text, reply_markup=kb)


# ======================= авто-NS у регистратора (NameSilo) =======================
@router.callback_query(F.data.startswith("sdom:autons:"))
async def sdom_autons(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        reg_tok = _registrar_key(d)
        reg = d.registrar or "namesilo"
        domain, ns1, ns2 = (d.zone_name or d.domain), d.ns1, d.ns2
    name = _REG_RU.get(reg, reg)
    if not reg_tok:
        await cq.answer(f"Нет ключа {name} (OTHER → 🔑 Ключи)", show_alert=True)
        return
    await cq.answer(f"Меняю NS в {name}…")
    if reg == "dynadot":
        from bot.services import dynadot as api
    else:
        from bot.services import namesilo as api
    try:
        detail = await api.change_nameservers(reg_tok, domain, [ns1, ns2])
    except Exception as e:
        await cq.message.answer(f"🔴 {name}: <code>{_esc(str(e)[:200])}</code>\n"
                                "Пропишите NS вручную у регистратора.")
        return
    await cq.message.answer(
        f"✅ NS отправлены в {name}: <code>{_esc(str(detail)[:120])}</code>\n"
        "Распространение — минуты-часы. Потом жмите «Проверить NS»."
    )
    await _show_card(cq, did)


# ======================= проверка NS / активация (фаза A финал) =======================
@router.callback_query(F.data.startswith("sdom:checkns:"))
async def sdom_checkns(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        token = _cf_token(d)
        zone_id, origin_ip, domain = d.cf_zone_id, d.origin_ip, d.domain
    await cq.answer("Проверяю…")
    try:
        await cf.activation_check(token, zone_id)
        st = await cf.zone_status(token, zone_id)
    except Exception as e:
        await cq.message.answer(f"🔴 CF: <code>{_esc(str(e)[:200])}</code>")
        return
    if st != "active":
        await cq.message.answer(
            f"⏳ Зона ещё <b>{_esc(st)}</b> — NS не прописались. "
            "Проверьте у регистратора NS и попробуйте позже."
        )
        return
    # active → SSL Full + Always HTTPS + ПРОКСИРОВАННАЯ A-запись (оранжевое облако)
    try:
        await cf.set_ssl(token, zone_id, "full")
        await cf.always_use_https(token, zone_id, True)
        rid = await cf.upsert_a_record(token, zone_id, domain, origin_ip, proxied=True)
        leaks = await cf.hide_origin_ip(token, zone_id, origin_ip)
    except Exception as e:
        with Session(_engine) as db:
            d = db.get(ShortDomain, did)
            d.status, d.last_error = "error", str(e)[:250]
            db.add(d); db.commit()
        await _show_card(cq, did, "Ошибка CF")
        return
    with Session(_engine) as db:
        d = db.get(ShortDomain, did)
        d.cf_record_id = rid
        d.status = "active"
        d.last_error = None
        d.activated_at = sc.now_iso()
        db.add(d); db.commit()
    leak_note = ("\n\n⚠️ В зоне были записи, светившие IP origin — исправлено:\n"
                 + "\n".join(f"• {_esc(x)}" for x in leaks)) if leaks else ""
    await cq.message.answer(
        "✅ Зона <b>active</b>, SSL=Full, A-запись создана <b>проксированной</b> (IP origin скрыт). "
        "Теперь можно настроить сервер." + leak_note
    )
    await _show_card(cq, did)


# ======================= провижн origin (фаза B) =======================
async def _do_provision(did: int) -> tuple[bool, list[str], str]:
    """Загрузить домен+ноду, выполнить provision, записать статус/cert. → (ok, logs, err)."""
    with Session(_engine) as db:
        drow = db.get(ShortDomain, did)
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
    return ok, logs, err


@router.callback_query(F.data.startswith("sdom:provision:"))
async def sdom_provision(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    did = int(cq.data.split(":")[2])
    await cq.answer("Настраиваю сервер…")
    await cq.message.answer("⏳ Настраиваю origin по SSH (ключ+CSR → origin-cert → сервис → фаервол → проверка)…")
    ok, logs, err = await _do_provision(did)
    report = "\n".join(logs) if logs else "(пусто)"
    head = "✅ Готово — домен поднят." if ok else f"🔴 Провижн прервался: <code>{_esc(err[:200])}</code>"
    await cq.message.answer(f"{head}\n\n<b>Шаги:</b>\n{_esc(report)}")
    await _show_card(cq, did)


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
    await cq.message.answer(f"⏳ A-запись → {newip}. Разворачиваю сервис на новой ноде…")
    # 2) провижн новой ноды
    ok, logs, err = await _do_provision(did)
    report = "\n".join(logs) if logs else "(пусто)"
    head = "✅ Перенесено — домен на новой ноде." if ok else f"🔴 Провижн прервался: <code>{_esc(err[:200])}</code>"
    await cq.message.answer(f"{head}\n\n<b>Шаги:</b>\n{_esc(report)}")
    await _show_card(cq, did)


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
