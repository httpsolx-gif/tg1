"""🕵️ SPAM-check — админ-раздел (п.55).

Компактно: список сид-ящиков, загрузка пачкой (`email app_password [сервис] [страна]`),
вкл/выкл/удаление ящика, «▶ Проверить сейчас», «📊 Последние результаты».
IMAP-хост/подпись определяются по домену автоматически (см. services/spamcheck.py).
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import List, Tuple

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document
from aiogram.fsm.context import FSMContext
from sqlmodel import Session, select, desc

from common.db import get_engine
from common.config import settings
from common import catalog
from common.models.spamcheck import SpamSeed, SpamCheckResult
from bot.fsm.admin import SpamSeedAdd
from bot.services.spamcheck import guess_imap, run_and_report, format_card, imap_login_test, _PLACE

router = Router(name="spamcheck-admin")
log = logging.getLogger("bot")
_engine = get_engine()

_SEP_RE = re.compile(r"[\s:;|]+")


def _default_cs() -> Tuple[str, str]:
    """Первая активная пара страна+сервис из каталога (дефолт, когда не указано)."""
    for country, svcs in catalog.country_services(only_with_services=True).items():
        return country, svcs[0]
    return "", ""


def _country_for_service(service: str) -> str:
    """Первая страна, у которой есть такой сервис; иначе дефолтная."""
    for country, svcs in catalog.country_services(only_with_services=True).items():
        if service in svcs:
            return country
    return _default_cs()[0]


# ---------- keyboards ----------
def kb_spamcheck_root() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    with Session(_engine) as db:
        seeds = db.exec(select(SpamSeed).order_by(SpamSeed.id)).all()
    for s in seeds:
        mark = "🟢" if s.active else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {s.email} · {s.service}", callback_data=f"spam:open:{s.id}")])
    if not seeds:
        rows.append([InlineKeyboardButton(text="Пусто — добавьте ящики", callback_data="spam:add")])
    rows.append([InlineKeyboardButton(text="+ Добавить ящики", callback_data="spam:add")])
    rows.append([
        InlineKeyboardButton(text="▶️ Проверить сейчас", callback_data="spam:run"),
        InlineKeyboardButton(text="📊 Последние", callback_data="spam:last"),
    ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="spam:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_add_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="spam:root")],
    ])


def _seed_view(sid: int):
    with Session(_engine) as db:
        s = db.get(SpamSeed, sid)
    if not s:
        return None, None
    host, port, ssl, label = guess_imap(s.email)
    host = s.imap_host or host
    mark = "🟢 включён" if s.active else "🔴 выключен"
    text = (
        f"📬 <b>{s.email}</b> ({s.label or label})\n\n"
        f"• Проба: <b>{s.country} · {s.service}</b>\n"
        f"• IMAP: <code>{host}:{s.imap_port or port}</code> {'SSL' if (s.imap_ssl if s.imap_host else ssl) else '—'}\n"
        f"• Статус: {mark}"
    )
    toggle = "🔴 Выключить" if s.active else "🟢 Включить"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle, callback_data=f"spam:toggle:{sid}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"spam:del:{sid}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="spam:root")],
    ])
    return text, kb


# ---------- entry ----------
async def show_spamcheck_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("🕵️ SPAM-check — сид-ящики и проверка размещения:", reply_markup=kb_spamcheck_root())


@router.callback_query(F.data == "spam:root")
async def spam_root_cb(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("🕵️ SPAM-check — сид-ящики и проверка размещения:",
                                   reply_markup=kb_spamcheck_root())
    except Exception:
        await cq.message.answer("🕵️ SPAM-check — сид-ящики и проверка размещения:",
                                reply_markup=kb_spamcheck_root())
    await cq.answer()


@router.callback_query(F.data == "spam:back")
async def spam_back(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    from bot.handlers.admin.index import back_to_admin
    await back_to_admin(cq)


# ---------- add ----------
@router.callback_query(F.data == "spam:add")
async def spam_add(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    dc, ds = _default_cs()
    await cq.message.answer(
        "Добавление сид-ящиков (одна строка — один ящик):\n"
        "<code>email app_password [сервис] [страна]</code>\n\n"
        "Разделители: пробел / <code>:</code> / <code>;</code> / <code>|</code>.\n"
        "• <b>app_password</b> — пароль приложения (IMAP при 2FA).\n"
        "• <b>сервис/страна</b> не указаны → "
        f"<b>{dc} · {ds}</b> (первый активный).\n"
        "IMAP-хост и подпись провайдера — автоматом по домену.\n"
        "🔌 При загрузке сразу проверю вход по IMAP: с битым паролем/без связи "
        "ящик добавится <b>выключенным</b>.\n\n"
        "Либо пришлите <b>.txt</b> файл.",
        reply_markup=_kb_add_cancel(),
    )
    await state.set_state(SpamSeedAdd.waiting)
    await cq.answer()


def _parse_seed_line(line: str) -> Tuple[str, str, str, str]:
    """`email pass [service] [country]` → (email, pass, country, service). Бросает ValueError."""
    parts = _SEP_RE.split(line.strip())
    if len(parts) < 2:
        raise ValueError("нужно минимум `email app_password`")
    email_addr, pw = parts[0].strip().lower(), parts[1].strip()
    if "@" not in email_addr or not pw:
        raise ValueError("подозрительный email/пароль")
    service = parts[2].strip() if len(parts) >= 3 else ""
    country = parts[3].strip().upper() if len(parts) >= 4 else ""
    if not service:
        country, service = _default_cs()
    elif not country:
        country = _country_for_service(service)
    return email_addr, pw, country, service


async def _add_and_validate(lines: List[str]) -> str:
    """Распарсить, проверить IMAP-подключение каждого ящика и добавить.
    Прошедшие вход — active=True, с битым логином/связью — добавляются выключенными."""
    parsed: List[Tuple[str, str, str, str]] = []
    errors: List[str] = []
    for ln in lines:
        try:
            parsed.append(_parse_seed_line(ln))
        except Exception as e:
            errors.append(f"{ln} -> {e}")

    with Session(_engine) as db:
        existing = {s.email for s in db.exec(select(SpamSeed)).all()}
    uniq: List[Tuple[str, str, str, str]] = []
    for email_addr, pw, country, service in parsed:
        if email_addr in existing:
            errors.append(f"{email_addr} -> дубликат")
            continue
        existing.add(email_addr)
        uniq.append((email_addr, pw, country, service))

    # IMAP-логин проверяем параллельно (imaplib синхронный → в executor)
    loop = asyncio.get_event_loop()
    tests = await asyncio.gather(*[
        loop.run_in_executor(None, imap_login_test, e, p) for (e, p, _c, _s) in uniq
    ]) if uniq else []

    now = datetime.utcnow().isoformat()
    ok_lines: List[str] = []
    bad_lines: List[str] = []
    with Session(_engine) as db:
        for (email_addr, pw, country, service), (ok, info) in zip(uniq, tests):
            host, port, ssl, label = guess_imap(email_addr)
            db.add(SpamSeed(
                email=email_addr, imap_host=host, imap_port=port, imap_ssl=ssl,
                imap_pass=pw, country=country, service=service, label=label,
                active=ok, created_at=now,
            ))
            if ok:
                ok_lines.append(f"✅ {email_addr} · {service} · {info}")
            else:
                bad_lines.append(f"⛔ {email_addr} · {info} — добавлен выключенным")
        db.commit()

    if not (ok_lines or bad_lines or errors):
        return "Нечего добавлять."
    out = [f"Загрузка: ✅ <b>{len(ok_lines)}</b> · ⛔ <b>{len(bad_lines)}</b> · строк с ошибкой <b>{len(errors)}</b>", ""]
    out += ok_lines + bad_lines
    if errors:
        out.append("\nСтроки с ошибкой:")
        out += [f"• <code>{e}</code>" for e in errors[:5]]
    return "\n".join(out)


@router.message(SpamSeedAdd.waiting, F.text)
async def spam_add_text(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    lines = [ln for ln in (m.text or "").splitlines() if ln.strip()]
    await state.clear()
    await m.answer("⏳ Проверяю IMAP-подключения…")
    report = await _add_and_validate(lines)
    await m.answer(report, reply_markup=kb_spamcheck_root())


@router.message(SpamSeedAdd.waiting, F.document)
async def spam_add_file(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    doc: Document = m.document
    if not (doc.file_name and doc.file_name.lower().endswith(".txt")):
        await m.answer("Нужен .txt файл. Каждая строка: <code>email app_password [сервис] [страна]</code>",
                       reply_markup=_kb_add_cancel())
        return
    file = await m.bot.get_file(doc.file_id)
    content = await m.bot.download_file(file.file_path)
    text = content.read().decode("utf-8", errors="ignore")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    await state.clear()
    await m.answer("⏳ Проверяю IMAP-подключения…")
    report = await _add_and_validate(lines)
    await m.answer(report, reply_markup=kb_spamcheck_root())


# ---------- open / toggle / delete ----------
@router.callback_query(F.data.startswith("spam:open:"))
async def spam_open(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    sid = int(cq.data.split(":")[-1])
    text, kb = _seed_view(sid)
    if not text:
        await cq.answer("Ящик не найден", show_alert=True)
        return
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("spam:toggle:"))
async def spam_toggle(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = int(cq.data.split(":")[-1])
    with Session(_engine) as db:
        s = db.get(SpamSeed, sid)
        if not s:
            await cq.answer("Ящик не найден", show_alert=True)
            return
        s.active = not s.active
        db.add(s)
        db.commit()
        state_txt = "включён" if s.active else "выключен"
    text, kb = _seed_view(sid)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await cq.answer(f"Ящик {state_txt}")


@router.callback_query(F.data.startswith("spam:del:"))
async def spam_delete(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = int(cq.data.split(":")[-1])
    with Session(_engine) as db:
        obj = db.get(SpamSeed, sid)
        if not obj:
            await cq.answer("Уже удалён", show_alert=True)
        else:
            db.delete(obj)
            db.commit()
            await cq.answer("Удалён")
    try:
        await cq.message.edit_text("🕵️ SPAM-check — сид-ящики и проверка размещения:",
                                   reply_markup=kb_spamcheck_root())
    except Exception:
        pass


# ---------- run now / last ----------
async def _run_now(bot, chat_id: int):
    """Фоновый прогон: карточка уходит в канал (если настроен) и ответом админу."""
    try:
        results, posted = await run_and_report(bot, post_empty=True)
        note = "" if posted else "\n\n<i>(SPAMCHECK_CHANNEL_ID не задан — карточка только здесь)</i>"
        await bot.send_message(chat_id, format_card(results) + note)
    except Exception as e:
        log.warning("spamcheck run_now: %s", e)
        try:
            await bot.send_message(chat_id, f"🔴 Проверка не удалась: <code>{e}</code>")
        except Exception:
            pass


@router.callback_query(F.data == "spam:run")
async def spam_run(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    with Session(_engine) as db:
        n = len(db.exec(select(SpamSeed).where(SpamSeed.active == True)).all())  # noqa: E712
    if not n:
        await cq.answer("Нет активных ящиков", show_alert=True)
        return
    wait = int(settings.SPAMCHECK_WAIT_SEC or 90)
    await cq.answer(f"Запустил проверку {n} ящ. — ~{wait}с на поиск…", show_alert=True)
    asyncio.create_task(_run_now(cq.message.bot, cq.message.chat.id))


@router.callback_query(F.data == "spam:last")
async def spam_last(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    with Session(_engine) as db:
        rows = db.exec(select(SpamCheckResult).order_by(desc(SpamCheckResult.id)).limit(15)).all()
        emails = {s.id: s.email for s in db.exec(select(SpamSeed)).all()}
    if not rows:
        await cq.answer("Результатов пока нет", show_alert=True)
        return
    lines = ["📊 <b>Последние проверки</b>", ""]
    for r in rows:
        when = (r.ts or "")[5:16].replace("T", " ")
        place = _PLACE.get(r.placement, r.placement)
        em = emails.get(r.seed_id, f"#{r.seed_id}")
        lines.append(f"{when} · {em} · {place}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="spam:root")]])
    try:
        await cq.message.edit_text("\n".join(lines), reply_markup=kb)
    except Exception:
        await cq.message.answer("\n".join(lines), reply_markup=kb)
    await cq.answer()
