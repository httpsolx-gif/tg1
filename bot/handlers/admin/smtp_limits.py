"""SMTP: лимиты отправок, автосмена, принудительная смена.

Автосмена работает автоматически: pick_smtp пропускает исчерпанные аккаунты
(sent_count >= send_limit). Здесь — управление лимитами, сброс счётчиков и
принудительная смена (исчерпать текущий аккаунт группы, чтобы ушёл следующий).
Префикс callback: slim: (отдельно от smtp:, чтобы не ловился smtp_nav).
"""
import logging
from typing import List

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlmodel import Session, select
from sqlalchemy import or_

from common.db import get_engine
from common.models.smtp import SmtpAccount
from common.config import settings
from bot.fsm.admin import SmtpLimit
from bot.services.smtp_pool import pick_smtp

router = Router(name="smtp-limits")
log = logging.getLogger("bot")
_engine = get_engine()


def _exhausted(a: SmtpAccount) -> bool:
    return (a.send_limit or 0) > 0 and (a.sent_count or 0) >= a.send_limit


# ---------- keyboards ----------
def kb_limits_root() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    with Session(_engine) as db:
        accs = db.exec(select(SmtpAccount).order_by(SmtpAccount.kind, SmtpAccount.id)).all()
    if not accs:
        rows.append([InlineKeyboardButton(text="Нет SMTP-аккаунтов", callback_data="slim:root")])
    else:
        for a in accs:
            lim = a.send_limit or 0
            limtxt = f"{a.sent_count}/{lim}" if lim else f"{a.sent_count}/∞"
            mark = "🔴" if (not a.is_active or _exhausted(a)) else "🟢"
            rows.append([InlineKeyboardButton(
                text=f"{mark} {a.email} [{a.kind}] {limtxt}",
                callback_data=f"slim:acc:{a.id}",
            )])
    rows.append([
        InlineKeyboardButton(text="📈 Лимит группе", callback_data="slim:grp"),
        InlineKeyboardButton(text="♻️ Сброс счётчиков", callback_data="slim:resetall"),
    ])
    rows.append([InlineKeyboardButton(text="⏭ Принудит. смена", callback_data="slim:forced")])
    rows.append([InlineKeyboardButton(text="🏠 В корень SMTP", callback_data="smtp:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_account(a: SmtpAccount) -> InlineKeyboardMarkup:
    toggle = "🔴 Выключить" if a.is_active else "🟢 Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Лимит", callback_data=f"slim:setlim:{a.id}")],
        [
            InlineKeyboardButton(text="♻️ Сброс счётчика", callback_data=f"slim:reset:{a.id}"),
            InlineKeyboardButton(text="⏭ Исчерпать", callback_data=f"slim:exhaust:{a.id}"),
        ],
        [InlineKeyboardButton(text=toggle, callback_data=f"slim:toggle:{a.id}")],
        [InlineKeyboardButton(text="⬅️ Список", callback_data="slim:root")],
    ])


def _kb_group_pick(action: str) -> InlineKeyboardMarkup:
    with Session(_engine) as db:
        kinds = sorted({k for (k,) in db.exec(select(SmtpAccount.kind).distinct()).all()}) or ["gmail", "main"]
    rows = [[InlineKeyboardButton(text=f"📮 {k}", callback_data=f"slim:{action}:{k}")] for k in kinds]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="slim:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _acc_text(a: SmtpAccount) -> str:
    lim = a.send_limit or 0
    return (
        f"📧 <b>{a.email}</b>\n\n"
        f"• Категория: {a.kind}\n"
        f"• Лимит: {lim if lim else '∞'}\n"
        f"• Отправлено: {a.sent_count}\n"
        f"• Статус: {'🟢 активен' if (a.is_active and not _exhausted(a)) else '🔴 недоступен'}"
    )


# ---------- entry ----------
async def show_limits_root(cq_or_msg) -> None:
    text = "📈 SMTP → Лимиты и автосмена\n(тап по аккаунту — настройки):"
    if isinstance(cq_or_msg, CallbackQuery):
        try:
            await cq_or_msg.message.edit_text(text, reply_markup=kb_limits_root())
        except Exception:
            await cq_or_msg.message.answer(text, reply_markup=kb_limits_root())
        await cq_or_msg.answer()
    else:
        await cq_or_msg.answer(text, reply_markup=kb_limits_root())


@router.callback_query(F.data == "slim:root")
async def slim_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await show_limits_root(cq)


@router.callback_query(F.data.startswith("slim:acc:"))
async def slim_acc(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    aid = int(cq.data.split(":")[-1])
    with Session(_engine) as db:
        a = db.get(SmtpAccount, aid)
    if not a:
        await cq.answer("Не найден", show_alert=True)
        return
    try:
        await cq.message.edit_text(_acc_text(a), reply_markup=_kb_account(a))
    except Exception:
        await cq.message.answer(_acc_text(a), reply_markup=_kb_account(a))
    await cq.answer()


@router.callback_query(F.data.startswith("slim:reset:"))
async def slim_reset(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    aid = int(cq.data.split(":")[-1])
    with Session(_engine) as db:
        a = db.get(SmtpAccount, aid)
        if a:
            a.sent_count = 0
            db.add(a)
            db.commit()
            db.refresh(a)
            await cq.message.edit_text(_acc_text(a), reply_markup=_kb_account(a))
    await cq.answer("Счётчик сброшен")


@router.callback_query(F.data.startswith("slim:exhaust:"))
async def slim_exhaust(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    aid = int(cq.data.split(":")[-1])
    with Session(_engine) as db:
        a = db.get(SmtpAccount, aid)
        if a:
            # принудительно «исчерпать»: если есть лимит — добить счётчик, иначе выключить
            if (a.send_limit or 0) > 0:
                a.sent_count = a.send_limit
            else:
                a.is_active = False
            db.add(a)
            db.commit()
            db.refresh(a)
            await cq.message.edit_text(_acc_text(a), reply_markup=_kb_account(a))
    await cq.answer("Аккаунт исчерпан")


@router.callback_query(F.data.startswith("slim:toggle:"))
async def slim_toggle(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    aid = int(cq.data.split(":")[-1])
    with Session(_engine) as db:
        a = db.get(SmtpAccount, aid)
        if a:
            a.is_active = not a.is_active
            db.add(a)
            db.commit()
            db.refresh(a)
            await cq.message.edit_text(_acc_text(a), reply_markup=_kb_account(a))
    await cq.answer("Готово")


@router.callback_query(F.data == "slim:resetall")
async def slim_resetall(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    with Session(_engine) as db:
        n = 0
        for a in db.exec(select(SmtpAccount)).all():
            a.sent_count = 0
            db.add(a)
            n += 1
        db.commit()
    await cq.answer(f"Сброшено: {n}")
    await show_limits_root(cq)


# ---------- forced change ----------
@router.callback_query(F.data == "slim:forced")
async def slim_forced(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.message.edit_text("⏭ Принудительная смена — выберите группу:", reply_markup=_kb_group_pick("forcedo"))
    await cq.answer()


@router.callback_query(F.data.startswith("slim:forcedo:"))
async def slim_forced_do(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    group = cq.data.split(":")[-1]
    current = await pick_smtp(group=group)
    if current is None:
        await cq.answer("Нет доступного SMTP в группе", show_alert=True)
        return
    with Session(_engine) as db:
        a = db.get(SmtpAccount, current.id)
        if a:
            if (a.send_limit or 0) > 0:
                a.sent_count = a.send_limit
            else:
                a.is_active = False
            db.add(a)
            db.commit()
    nxt = await pick_smtp(group=group)
    nxt_txt = nxt.email if nxt else "— (больше нет доступных)"
    await cq.answer("Сменили")
    await cq.message.edit_text(
        f"⏭ Группа <b>{group}</b>: исчерпан <code>{current.email}</code>\nСледующий: <code>{nxt_txt}</code>",
        reply_markup=kb_limits_root(),
    )


# ---------- set limit (account / group) ----------
@router.callback_query(F.data.startswith("slim:setlim:"))
async def slim_setlim(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    aid = int(cq.data.split(":")[-1])
    await state.set_state(SmtpLimit.value)
    await state.update_data(target="acc", acc_id=aid)
    await cq.message.answer("Введите лимит отправок (число, 0 = без лимита):")
    await cq.answer()


@router.callback_query(F.data == "slim:grp")
async def slim_grp(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.message.edit_text("📈 Лимит на группу — выберите группу:", reply_markup=_kb_group_pick("grpset"))
    await cq.answer()


@router.callback_query(F.data.startswith("slim:grpset:"))
async def slim_grpset(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    group = cq.data.split(":")[-1]
    await state.set_state(SmtpLimit.value)
    await state.update_data(target="group", group=group)
    await cq.message.answer(f"Введите лимит для всех SMTP группы <b>{group}</b> (0 = без лимита):")
    await cq.answer()


@router.message(SmtpLimit.value, F.text)
async def slim_value(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    raw = (m.text or "").strip()
    if not raw.isdigit():
        await m.answer("Нужно число (0 = без лимита). Введите ещё раз:")
        return
    val = int(raw)
    data = await state.get_data()
    await state.clear()
    with Session(_engine) as db:
        if data.get("target") == "acc":
            a = db.get(SmtpAccount, data.get("acc_id"))
            if a:
                a.send_limit = val
                db.add(a)
                db.commit()
                db.refresh(a)
                await m.answer(f"✅ Лимит {val or '∞'} для <b>{a.email}</b>", reply_markup=_kb_account(a))
                return
        elif data.get("target") == "group":
            group = data.get("group")
            n = 0
            for a in db.exec(select(SmtpAccount).where(SmtpAccount.kind == group)).all():
                a.send_limit = val
                db.add(a)
                n += 1
            db.commit()
            await m.answer(f"✅ Лимит {val or '∞'} установлен для {n} аккаунтов группы <b>{group}</b>", reply_markup=kb_limits_root())
            return
    await m.answer("Не удалось применить лимит.", reply_markup=kb_limits_root())
