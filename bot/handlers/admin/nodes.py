import logging
import re
import time
from typing import List, Tuple

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document
from aiogram.fsm.context import FSMContext
from sqlmodel import Session, select

from common.db import get_engine
from common.models.node import Node
from common.security import encrypt_password
from common.config import settings
from bot.fsm.admin import NodeAdd, NodeEdit
from bot.services import node_pool

router = Router(name="nodes-admin")
log = logging.getLogger("bot")
_engine = get_engine()


# ---------- keyboards ----------
def _mark(n: Node) -> str:
    """🟢 работает / 🟠 включена, но не отвечает (переключились на другую) / 🔴 выключена."""
    if not n.is_active:
        return "🔴"
    return "🟠" if node_pool.is_down(n.id) else "🟢"


def _tags(n: Node) -> str:
    return (f" · {n.region}" if n.region else "") + (" · 🛟 резерв" if n.is_backup else "")


def kb_nodes_root() -> InlineKeyboardMarkup:
    """Корень NODES = сразу список нод (в названии — IP), снизу Add/Назад."""
    rows: List[List[InlineKeyboardButton]] = []
    with Session(_engine) as db:
        nodes = db.exec(select(Node).order_by(Node.id)).all()
    for n in nodes:
        rows.append([InlineKeyboardButton(text=f"{_mark(n)} {n.ip}{_tags(n)}", callback_data=f"node:open:{n.id}")])
    rows.append([InlineKeyboardButton(text="+ Добавить ноду", callback_data="node:add")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="node:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_add_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="node:root")],
    ])


def _kb_list() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    with Session(_engine) as db:
        nodes = db.exec(select(Node).order_by(Node.id)).all()
    if not nodes:
        rows.append([InlineKeyboardButton(text="Пусто — добавьте ноды", callback_data="node:add")])
    else:
        for n in nodes:
            rows.append([
                InlineKeyboardButton(text=f"{_mark(n)} {n.login}@{n.ip}{_tags(n)}", callback_data=f"node:open:{n.id}"),
            ])
    rows.append([InlineKeyboardButton(text="+ Добавить ноду", callback_data="node:add")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="node:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _node_view(nid: int):
    """Карточка ноды: регион, статус, кнопки управления."""
    with Session(_engine) as db:
        n = db.get(Node, nid)
    if not n:
        return None, None
    mark = "🟢 включена" if n.is_active else "🔴 выключена"
    since = node_pool.down_since(n.id) if n.is_active else None
    if since:
        mark = f"🟠 не отвечает с {time.strftime('%H:%M:%S', time.localtime(since))} — письма идут через другую ноду"
    _ROLE_RU = {"send": "отправка", "short": "сокращалка", "both": "отправка+сокращалка"}
    purpose = n.purpose or "send"
    text = (
        f"🖥 <b>{n.login}@{n.ip}</b>\n\n"
        f"• Роль: <b>{_ROLE_RU.get(purpose, purpose)}</b>\n"
        f"• Регион (гео AWS): <b>{n.region or '—'}</b>\n"
        f"• Тип: <b>{'🛟 резервная' if n.is_backup else 'основная'}</b>\n"
        f"• Статус: {mark}\n\n"
        "<i>Резервная берётся для отправки, только если основные не отвечают по SSH. "
        "Основная поднялась — письма сами возвращаются на неё.</i>"
    )
    toggle = "🔴 Выключить" if n.is_active else "🟢 Включить"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎛 Роль: {_ROLE_RU.get(purpose, purpose)}", callback_data=f"node:role:{nid}")],
        [InlineKeyboardButton(text=f"🌍 Регион: {n.region or '—'}", callback_data=f"node:region:{nid}")],
        [InlineKeyboardButton(text=("🛟 Резервная → сделать основной" if n.is_backup else "🛟 Сделать резервной"),
                              callback_data=f"node:backup:{nid}")],
        [
            InlineKeyboardButton(text=toggle, callback_data=f"node:toggle:{nid}"),
            InlineKeyboardButton(text="🧪 Тест SSH", callback_data=f"node:test:{nid}"),
        ],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"node:del:{nid}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="node:root")],
    ])
    return text, kb


# ---------- entry (из админ-меню) ----------
async def show_nodes_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("🖥 NODES — выберите ноду или добавьте:", reply_markup=kb_nodes_root())


@router.callback_query(F.data == "node:root")
async def nodes_root_cb(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("🖥 NODES — выберите ноду или добавьте:", reply_markup=kb_nodes_root())
    except Exception:
        await cq.message.answer("🖥 NODES — выберите ноду или добавьте:", reply_markup=kb_nodes_root())
    await cq.answer()


@router.callback_query(F.data == "node:back")
async def nodes_back(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    # возврат в админку — через общий хелпер
    from bot.handlers.admin.index import back_to_admin
    await back_to_admin(cq)


# ---------- add ----------
@router.callback_query(F.data == "node:add")
async def node_add(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.message.answer(
        "Добавление нод.\n\n"
        "Формат (одна строка — одна нода):\n"
        "<code>login ip pass</code>\n\n"
        "Разделители: пробел / <code>:</code> / <code>;</code> / <code>|</code>.\n"
        "Либо пришлите <b>.txt</b> файл.",
        reply_markup=_kb_add_cancel(),
    )
    await state.set_state(NodeAdd.waiting_input)
    await cq.answer()


@router.callback_query(F.data == "node:list")
async def node_list(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        await cq.message.edit_text("🖥 NODES → список (нажмите для удаления):", reply_markup=_kb_list())
    except Exception:
        await cq.message.answer("🖥 NODES → список (нажмите для удаления):", reply_markup=_kb_list())
    await cq.answer()


@router.callback_query(F.data.startswith("node:open:"))
async def node_open(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    nid = int(cq.data.split(":")[-1])
    text, kb = _node_view(nid)
    if not text:
        await cq.answer("Нода не найдена", show_alert=True)
        return
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("node:toggle:"))
async def node_toggle(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    nid = int(cq.data.split(":")[-1])
    with Session(_engine) as db:
        n = db.get(Node, nid)
        if not n:
            await cq.answer("Нода не найдена", show_alert=True)
            return
        n.is_active = not n.is_active
        db.add(n)
        db.commit()
        state_txt = "включена" if n.is_active else "выключена"
    text, kb = _node_view(nid)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await cq.answer(f"Нода {state_txt}")


@router.callback_query(F.data.startswith("node:backup:"))
async def node_backup(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    nid = int(cq.data.split(":")[-1])
    with Session(_engine) as db:
        n = db.get(Node, nid)
        if not n:
            await cq.answer("Нода не найдена", show_alert=True)
            return
        n.is_backup = not n.is_backup
        db.add(n)
        db.commit()
        backup = n.is_backup
    text, kb = _node_view(nid)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await cq.answer("Нода теперь резервная" if backup else "Нода теперь основная")


_ROLE_CYCLE = {"send": "short", "short": "both", "both": "send"}


@router.callback_query(F.data.startswith("node:role:"))
async def node_role(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    nid = int(cq.data.split(":")[-1])
    with Session(_engine) as db:
        n = db.get(Node, nid)
        if not n:
            await cq.answer("Нода не найдена", show_alert=True)
            return
        n.purpose = _ROLE_CYCLE.get(n.purpose or "send", "short")
        db.add(n)
        db.commit()
        new = n.purpose
    text, kb = _node_view(nid)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await cq.answer(f"Роль: {new}")


@router.callback_query(F.data.startswith("node:region:"))
async def node_region_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    nid = int(cq.data.split(":")[-1])
    await state.update_data(edit_nid=nid)
    await state.set_state(NodeEdit.region)
    await cq.message.answer(
        "🌍 Новый <b>регион (гео AWS)</b> для ноды — например <code>us-east-1</code>.\n"
        "Чтобы убрать регион — отправьте <code>-</code> (минус).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"node:open:{nid}")],
        ]),
    )
    await cq.answer()


@router.message(NodeEdit.region, F.text)
async def node_region_set(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    data = await state.get_data()
    nid = int(data.get("edit_nid", 0))
    region = _norm_region(m.text)
    with Session(_engine) as db:
        n = db.get(Node, nid)
        if not n:
            await state.clear()
            await m.answer("Нода не найдена.", reply_markup=kb_nodes_root())
            return
        n.region = region
        db.add(n)
        db.commit()
    await state.clear()
    text, kb = _node_view(nid)
    await m.answer(("✅ Регион обновлён.\n\n" + text) if text else "Готово.", reply_markup=kb)


@router.callback_query(F.data.startswith("node:test:"))
async def node_test(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        nid = int(cq.data.split(":")[-1])
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return
    with Session(_engine) as db:
        node = db.get(Node, nid)
    if not node:
        await cq.answer("Нода не найдена", show_alert=True)
        return
    await cq.answer("Проверяю SSH…")
    from bot.services.node_runner import check_node
    ok, info = await check_node(node)
    status = "🟢 OK" if ok else "🔴 FAIL"
    await cq.message.answer(
        f"{status} — <code>{node.login}@{node.ip}</code>\n<code>{info[:300]}</code>",
        reply_markup=kb_nodes_root(),
    )


@router.callback_query(F.data.startswith("node:del:"))
async def node_delete(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        nid = int(cq.data.split(":")[-1])
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return
    with Session(_engine) as db:
        obj = db.get(Node, nid)
        if not obj:
            await cq.answer("Уже удалена", show_alert=True)
        else:
            db.delete(obj)
            db.commit()
            await cq.answer("Удалена")
    await cq.message.edit_text("🖥 NODES → список (нажмите для удаления):", reply_markup=_kb_list())


def _kb_region_skip() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➖ Без региона", callback_data="node:noregion")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="node:root")],
    ])


async def _ask_region(m: Message, state: FSMContext, parsed, perrors, total):
    """Сохранить распарсенные ноды в FSM и спросить гео/регион AWS."""
    if not parsed:
        # нечего добавлять — сразу отчёт об ошибках
        await m.answer(_report(0, len(perrors), perrors, total=total), reply_markup=kb_nodes_root())
        await state.clear()
        return
    await state.update_data(parsed=parsed, perrors=perrors, total=total)
    await state.set_state(NodeAdd.waiting_region)
    await m.answer(
        f"Нод к добавлению: <b>{len(parsed)}</b>.\n\n"
        "🌍 Укажите <b>гео (регион AWS)</b> для них — например <code>us-east-1</code>, <code>eu-central-1</code>.\n"
        "Если без региона — отправьте <code>-</code> (минус) или нажмите кнопку.",
        reply_markup=_kb_region_skip(),
    )


@router.message(NodeAdd.waiting_input, F.text)
async def node_add_text(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    lines = [ln for ln in (m.text or "").splitlines() if ln.strip()]
    parsed, perrors = _parse_lines(lines)
    await _ask_region(m, state, parsed, perrors, total=len(lines))


@router.message(NodeAdd.waiting_input, F.document)
async def node_add_file(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    doc: Document = m.document
    if not (doc.file_name and doc.file_name.lower().endswith(".txt")):
        await m.answer("Нужен .txt файл. Каждая строка: <code>login ip pass</code>", reply_markup=_kb_add_cancel())
        return
    file = await m.bot.get_file(doc.file_id)
    content = await m.bot.download_file(file.file_path)
    text = content.read().decode("utf-8", errors="ignore")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    parsed, perrors = _parse_lines(lines)
    await _ask_region(m, state, parsed, perrors, total=len(lines))


def _norm_region(raw: str | None) -> str | None:
    r = (raw or "").strip().lower()
    if not r or r in {"-", "—", "–"}:
        return None
    return r


async def _finish_add(m: Message, state: FSMContext, region: str | None):
    data = await state.get_data()
    parsed = [tuple(x) for x in data.get("parsed", [])]
    perrors = list(data.get("perrors", []))
    total = int(data.get("total", len(parsed)))
    ok, err, ierrors = _insert_nodes(parsed, region)
    note = f"\n🌍 Регион: <b>{region}</b>" if region else "\n🌍 Регион: — (без региона)"
    await m.answer(_report(ok, err + len(perrors), perrors + ierrors, total=total) + note,
                   reply_markup=kb_nodes_root())
    await state.clear()


@router.message(NodeAdd.waiting_region, F.text)
async def node_add_region(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await _finish_add(m, state, _norm_region(m.text))


@router.callback_query(NodeAdd.waiting_region, F.data == "node:noregion")
async def node_add_noregion(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await _finish_add(cq.message, state, None)
    await cq.answer("Без региона")


# ---------- parsing / ingest ----------
_SEP_RE = re.compile(r"[\s:;|]+")


def _parse_node_line(line: str) -> Tuple[str, str, str]:
    # Делим максимум на 3 поля: login, ip, остаток = пароль
    # (пароль может содержать пробелы/разделители).
    parts = _SEP_RE.split(line.strip(), maxsplit=2)
    if len(parts) < 3:
        raise ValueError("ожидается `login ip pass`")
    login, ip, pw = parts[0].strip(), parts[1].strip(), parts[2].strip()
    # ip/host должен выглядеть как адрес или домен
    if not (("." in ip) or (":" in ip)):
        raise ValueError(f"подозрительный ip/host: {ip}")
    if not login or not pw:
        raise ValueError("пустой login/pass")
    return login, ip, pw


def _parse_lines(lines: List[str]) -> Tuple[List[Tuple[str, str, str]], List[str]]:
    """Распарсить строки в (login, ip, pw); вернуть (валидные, ошибки_парсинга)."""
    parsed: List[Tuple[str, str, str]] = []
    errors: List[str] = []
    for ln in lines:
        try:
            parsed.append(_parse_node_line(ln))
        except Exception as e:
            errors.append(f"{ln} -> {e}")
    return parsed, errors


def _insert_nodes(parsed: List[Tuple[str, str, str]], region: str | None) -> Tuple[int, int, List[str]]:
    """Вставить распарсенные ноды с заданным регионом; дедуп по (login, ip)."""
    ok = err = 0
    errors: List[str] = []
    with Session(_engine) as db:
        existing = {(n.login, n.ip) for n in db.exec(select(Node)).all()}
        for login, ip, pw in parsed:
            if (login, ip) in existing:
                errors.append(f"{login}@{ip} -> дубликат")
                err += 1
                continue
            db.add(Node(login=login, ip=ip, password_enc=encrypt_password(pw),
                        region=region, is_active=True))
            existing.add((login, ip))
            ok += 1
        db.commit()
    return ok, err, errors


def _report(ok: int, err: int, errors: List[str], total: int) -> str:
    if total == 1:
        if ok == 1:
            return "✅ <b>Нода добавлена</b>"
        first = errors[0] if errors else "Неизвестная ошибка"
        return "❌ <b>Не добавлено</b>\n\n<code>" + first + "</code>"
    out = ["Готово.", f"Добавлено: <b>{ok}</b>", f"Ошибок: <b>{err}</b>"]
    if errors:
        out.append("\nПервые ошибки:")
        out.extend(f"• <code>{e}</code>" for e in errors[:5])
    return "\n".join(out)
