import logging
from typing import List, Tuple

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document
from aiogram.fsm.context import FSMContext
from sqlmodel import Session, select

from common.db import get_engine
from common.models.aws import AwsAccount
from common.security import encrypt_password
from common.config import settings
from bot.fsm.admin import AwsAdd
from bot.services.aws_ses import parse_aws_line

router = Router(name="aws-admin")
log = logging.getLogger("bot")
_engine = get_engine()


def kb_aws_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="+ Add", callback_data="aws:add"),
            InlineKeyboardButton(text="📋 Список", callback_data="aws:list"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="aws:back")],
    ])


def _kb_add_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="aws:root")]])


def _kb_list() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    with Session(_engine) as db:
        accs = db.exec(select(AwsAccount).order_by(AwsAccount.id)).all()
    if not accs:
        rows.append([InlineKeyboardButton(text="Пусто — добавьте SES-аккаунты", callback_data="aws:add")])
    else:
        for a in accs:
            mark = "🟢" if a.is_active else "🔴"
            node_tag = " 🖥" if a.node_id else ""
            rows.append([
                InlineKeyboardButton(text=f"{mark} {a.email_from} ({a.region}){node_tag}", callback_data=f"aws:open:{a.id}"),
                InlineKeyboardButton(text="🗑", callback_data=f"aws:del:{a.id}"),
            ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="aws:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _aws_detail(aid: int):
    from common.models.node import Node
    with Session(_engine) as db:
        a = db.get(AwsAccount, aid)
        if not a:
            return None, None
        node = db.get(Node, a.node_id) if a.node_id else None
    node_txt = f"{node.login}@{node.ip}" if node else "— (бот-side)"
    text = (
        f"☁️ <b>{a.email_from}</b>\n\n"
        f"• Region: {a.region}\n"
        f"• Access key: <code>{a.access_key[:6]}…</code>\n"
        f"• Нода (IP отправки): {node_txt}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Тест квоты", callback_data=f"aws:test:{aid}")],
        [InlineKeyboardButton(text="🖥 Привязать ноду", callback_data=f"aws:node:{aid}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"aws:del:{aid}")],
        [InlineKeyboardButton(text="⬅️ Список", callback_data="aws:list")],
    ])
    return text, kb


def _kb_node_pick(aid: int) -> InlineKeyboardMarkup:
    from common.models.node import Node
    rows = [[InlineKeyboardButton(text="— Без ноды (бот-side)", callback_data=f"aws:nodeset:{aid}:none")]]
    with Session(_engine) as db:
        nodes = db.exec(select(Node).where(Node.is_active == True).order_by(Node.id)).all()  # noqa: E712
    for n in nodes:
        rows.append([InlineKeyboardButton(text=f"🖥 {n.login}@{n.ip}", callback_data=f"aws:nodeset:{aid}:{n.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"aws:open:{aid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_aws_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("☁️ AWS SES — аккаунты отправки через API:", reply_markup=kb_aws_root())


@router.callback_query(F.data == "aws:root")
async def aws_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("☁️ AWS SES — аккаунты отправки через API:", reply_markup=kb_aws_root())
    except Exception:
        await cq.message.answer("☁️ AWS SES:", reply_markup=kb_aws_root())
    await cq.answer()


@router.callback_query(F.data == "aws:back")
async def aws_back(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    from bot.handlers.admin.index import back_to_admin
    await back_to_admin(cq)


@router.callback_query(F.data == "aws:add")
async def aws_add(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.message.answer(
        "Добавление AWS SES (одна строка — один аккаунт):\n"
        "<code>region:accesskey:secretkey:emailfrom</code>\n\n"
        "Напр.: <code>us-east-1:AKIA...:wJalr...:noreply@site.com</code>\n"
        "Либо пришлите <b>.txt</b> файл.",
        reply_markup=_kb_add_cancel(),
    )
    await state.set_state(AwsAdd.waiting)
    await cq.answer()


@router.callback_query(F.data == "aws:list")
async def aws_list(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        await cq.message.edit_text("☁️ AWS SES → список (тап — тест квоты, 🗑 — удалить):", reply_markup=_kb_list())
    except Exception:
        await cq.message.answer("☁️ AWS SES → список:", reply_markup=_kb_list())
    await cq.answer()


@router.callback_query(F.data.startswith("aws:open:"))
async def aws_open(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    aid = int(cq.data.split(":")[-1])
    text, kb = _aws_detail(aid)
    if text is None:
        await cq.answer("Не найден", show_alert=True)
        return
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("aws:node:"))
async def aws_node_pick(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    aid = int(cq.data.split(":")[-1])
    await cq.message.edit_text("🖥 С какой ноды слать этот SES-аккаунт?", reply_markup=_kb_node_pick(aid))
    await cq.answer()


@router.callback_query(F.data.startswith("aws:nodeset:"))
async def aws_node_set(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, aid, val = cq.data.split(":", 3)
    aid = int(aid)
    node_id = None if val == "none" else int(val)
    with Session(_engine) as db:
        a = db.get(AwsAccount, aid)
        if a:
            a.node_id = node_id
            db.add(a)
            db.commit()
    text, kb = _aws_detail(aid)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer("Привязка обновлена")


@router.callback_query(F.data.startswith("aws:test:"))
async def aws_test(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        aid = int(cq.data.split(":")[-1])
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return
    with Session(_engine) as db:
        aws = db.get(AwsAccount, aid)
    if not aws:
        await cq.answer("Не найден", show_alert=True)
        return
    await cq.answer("Проверяю SES…")
    from bot.services.aws_ses import check_aws
    ok, info = await check_aws(aws)
    status = "🟢 OK" if ok else "🔴 FAIL"
    await cq.message.answer(f"{status} — <code>{aws.email_from}</code>\n<code>{info[:300]}</code>", reply_markup=kb_aws_root())


@router.callback_query(F.data.startswith("aws:del:"))
async def aws_del(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        aid = int(cq.data.split(":")[-1])
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return
    with Session(_engine) as db:
        obj = db.get(AwsAccount, aid)
        if not obj:
            await cq.answer("Уже удалён", show_alert=True)
        else:
            db.delete(obj)
            db.commit()
            await cq.answer("Удалён")
    await cq.message.edit_text("☁️ AWS SES → список (тап — тест квоты, 🗑 — удалить):", reply_markup=_kb_list())


@router.message(AwsAdd.waiting, F.text)
async def aws_add_text(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    lines = [ln for ln in (m.text or "").splitlines() if ln.strip()]
    ok, err, errors = _ingest(lines)
    await m.answer(_report(ok, err, errors, total=len(lines)), reply_markup=kb_aws_root())
    await state.clear()


@router.message(AwsAdd.waiting, F.document)
async def aws_add_file(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    doc: Document = m.document
    if not (doc.file_name and doc.file_name.lower().endswith(".txt")):
        await m.answer("Нужен .txt файл.", reply_markup=_kb_add_cancel())
        return
    file = await m.bot.get_file(doc.file_id)
    content = await m.bot.download_file(file.file_path)
    text = content.read().decode("utf-8", errors="ignore")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    ok, err, errors = _ingest(lines)
    await m.answer(_report(ok, err, errors, total=len(lines)), reply_markup=kb_aws_root())
    await state.clear()


def _ingest(lines: List[str]) -> Tuple[int, int, List[str]]:
    ok = err = 0
    errors: List[str] = []
    with Session(_engine) as db:
        existing = {(a.region, a.email_from) for a in db.exec(select(AwsAccount)).all()}
        for ln in lines:
            try:
                region, access, secret, email = parse_aws_line(ln)
                if (region, email) in existing:
                    errors.append(f"{email} ({region}) -> дубликат")
                    err += 1
                    continue
                db.add(AwsAccount(
                    region=region, access_key=access,
                    secret_key_enc=encrypt_password(secret), email_from=email, is_active=True,
                ))
                existing.add((region, email))
                ok += 1
            except Exception as e:
                errors.append(f"{ln[:24]}… -> {e}")
                err += 1
        db.commit()
    return ok, err, errors


def _report(ok: int, err: int, errors: List[str], total: int) -> str:
    if total == 1:
        if ok == 1:
            return "✅ <b>SES-аккаунт добавлен</b>"
        return "❌ <b>Не добавлено</b>\n\n<code>" + (errors[0] if errors else "ошибка") + "</code>"
    out = ["Готово.", f"Добавлено: <b>{ok}</b>", f"Ошибок: <b>{err}</b>"]
    if errors:
        out.append("\nПервые ошибки:")
        out.extend(f"• <code>{e}</code>" for e in errors[:5])
    return "\n".join(out)
