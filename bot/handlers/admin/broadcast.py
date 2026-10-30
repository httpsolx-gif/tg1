"""📣 BROADCAST — авторассылка по группам от имени аккаунта (userbot, п.56).

Админка пишет конфиг/цели в общую БД; постит отдельный процесс `userbot/main.py`
(Telethon, под аккаунтом пользователя). Здесь: группы, текст, фото, интервал, лимит,
вкл/выкл, «отправить сейчас», статус.
"""
import logging
import os
from html import escape as _esc
from pathlib import Path
from typing import List

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from common.config import settings
from common import broadcast_store as store
from bot.fsm.admin import BroadcastFlow

router = Router(name="broadcast-admin")
log = logging.getLogger("bot")

ROOT = Path(__file__).resolve().parents[3]
MEDIA_DIR = ROOT / "userbot" / "media"


# ------------------------------- клавиатуры / экран -------------------------------
def _root_text() -> str:
    st = store.get_state()
    n_all = len(store.list_targets())
    n_act = len(store.list_targets(active_only=True))
    on = "🟢 ВКЛ" if st.enabled else "🔴 ВЫКЛ"
    has_photo = "есть" if (st.media_path and os.path.exists(st.media_path)) else "—"
    cap = f"{st.daily_cap}/сут" if st.daily_cap else "без лимита"
    txt = (st.caption or "").strip()
    # текст хранится как HTML (m.html_text) — в превью экранируем, режем ДО экранирования,
    # иначе тег/«&» из подписи ломает parse_mode=HTML и экран раздела перестаёт открываться
    preview = _esc((txt[:120] + "…") if len(txt) > 120 else (txt or "—"))
    return (
        f"📣 <b>BROADCAST</b> (userbot) · {on}\n\n"
        f"• Группы: <b>{n_act}</b> активных из {n_all}\n"
        f"• Фото: <b>{has_photo}</b>\n"
        f"• Текст: {preview}\n"
        f"• Интервал: <b>{st.interval_min} мин</b> · пауза {st.delay_min}–{st.delay_max}с · {cap}\n"
        f"• Отправлено сегодня: <b>{st.sent_today}</b>\n"
        f"• Последний проход: {_esc(st.status or '—')}"
    )


def kb_broadcast_root() -> InlineKeyboardMarkup:
    st = store.get_state()
    toggle = "🔴 Выключить" if st.enabled else "🟢 Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle, callback_data="bc:toggle")],
        [InlineKeyboardButton(text=f"👥 Группы ({len(store.list_targets())})", callback_data="bc:targets")],
        [
            InlineKeyboardButton(text="✏️ Текст", callback_data="bc:text"),
            InlineKeyboardButton(text="🖼 Фото", callback_data="bc:photo"),
        ],
        [
            InlineKeyboardButton(text=f"⏱ Интервал: {st.interval_min}м", callback_data="bc:interval"),
            InlineKeyboardButton(text=f"🚦 Лимит: {st.daily_cap or '∞'}", callback_data="bc:cap"),
        ],
        [InlineKeyboardButton(text="🐢 Паузы между группами", callback_data="bc:delays")],
        [InlineKeyboardButton(text="▶️ Отправить сейчас", callback_data="bc:run")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bc:back")],
    ])


def _kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="bc:root")]])


async def _show_root(msg: Message, edit: bool = False):
    store.ensure_state()
    if edit:
        try:
            await msg.edit_text(_root_text(), reply_markup=kb_broadcast_root())
            return
        except Exception:
            pass
    await msg.answer(_root_text(), reply_markup=kb_broadcast_root())


async def show_broadcast_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await _show_root(m)


@router.callback_query(F.data == "bc:root")
async def bc_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await _show_root(cq.message, edit=True)
    await cq.answer()


@router.callback_query(F.data == "bc:back")
async def bc_back(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    # BC живёт внутри ALERT — назад туда, а не в админку
    from bot.handlers.admin.alert import kb_alert_root, ALERT_TITLE
    try:
        await cq.message.edit_text(ALERT_TITLE, reply_markup=kb_alert_root())
    except TelegramBadRequest:
        await cq.message.answer(ALERT_TITLE, reply_markup=kb_alert_root())
    await cq.answer()


# ------------------------------- вкл/выкл, запуск -------------------------------
@router.callback_query(F.data == "bc:toggle")
async def bc_toggle(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    st = store.get_state()
    store.update_state(enabled=not st.enabled)
    await _show_root(cq.message, edit=True)
    await cq.answer("Включено" if not st.enabled else "Выключено")


@router.callback_query(F.data == "bc:run")
async def bc_run(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    if not store.list_targets(active_only=True):
        await cq.answer("Нет активных групп", show_alert=True)
        return
    store.update_state(run_now=True)
    await cq.answer("Запрошен разовый проход — userbot выполнит в течение ~15с", show_alert=True)
    await _show_root(cq.message, edit=True)


# ------------------------------- текст -------------------------------
@router.callback_query(F.data == "bc:text")
async def bc_text_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(BroadcastFlow.caption)
    await cq.message.answer("✏️ Пришлите <b>текст</b> сообщения (подпись к фото). Форматирование (жирный, курсив, ссылки) сохраняется.\n"
                            "Лимит: 1024 символа с фото, 4096 без фото.",
                            reply_markup=_kb_cancel())
    await cq.answer()


@router.message(BroadcastFlow.caption, F.text)
async def bc_text_set(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    st = store.get_state()
    has_photo = bool(st.media_path and os.path.exists(st.media_path))
    limit = 1024 if has_photo else 4096  # лимиты Telegram: подпись к фото / сообщение
    if len(m.text) > limit:
        await m.answer(f"Слишком длинно: {len(m.text)} симв., максимум {limit}"
                       + (" (подпись к фото)" if has_photo else "") + ".", reply_markup=_kb_cancel())
        return
    # html_text — сохраняем форматирование; userbot шлёт с parse_mode=html
    store.update_state(caption=m.html_text)
    await state.clear()
    await m.answer("✅ Текст сохранён.")
    await _show_root(m)


# ------------------------------- фото -------------------------------
@router.callback_query(F.data == "bc:photo")
async def bc_photo_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(BroadcastFlow.photo)
    await cq.message.answer("🖼 Пришлите <b>фото</b> для рассылки (как фото или файлом-картинкой).\n"
                            "Чтобы убрать фото — отправьте <code>-</code>.",
                            reply_markup=_kb_cancel())
    await cq.answer()


@router.message(BroadcastFlow.photo, F.text.in_({"-", "—"}))
async def bc_photo_clear(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    store.update_state(media_path=None)
    await state.clear()
    await m.answer("✅ Фото убрано (будет только текст).")
    await _show_root(m)


@router.message(BroadcastFlow.photo, F.photo | F.document)
async def bc_photo_set(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    if m.photo:
        file_id = m.photo[-1].file_id
        ext = ".jpg"
    else:
        doc = m.document
        if not (doc.mime_type or "").startswith("image/"):
            await m.answer("Нужно изображение. Пришлите фото или картинку-файл.", reply_markup=_kb_cancel())
            return
        file_id = doc.file_id
        ext = os.path.splitext(doc.file_name or "")[1] or ".jpg"
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    dest = MEDIA_DIR / f"broadcast{ext}"
    tmp = MEDIA_DIR / f".upload{ext}"
    # сначала качаем во временный файл: упала загрузка (>20 МБ, сеть) → старое фото цело
    try:
        file = await m.bot.get_file(file_id)
        await m.bot.download_file(file.file_path, destination=str(tmp))
    except Exception as e:
        tmp.unlink(missing_ok=True)
        log.warning("bc photo download: %s", e)
        await m.answer(f"❌ Не удалось скачать фото: <code>{_esc(str(e)[:200])}</code>\n"
                       "Telegram отдаёт ботам файлы до 20 МБ.", reply_markup=_kb_cancel())
        return
    os.replace(tmp, dest)
    # убрать прежние файлы рассылки с другим расширением (только после успешной загрузки)
    for old in MEDIA_DIR.glob("broadcast.*"):
        if old != dest:
            old.unlink(missing_ok=True)
    store.update_state(media_path=str(dest))
    await state.clear()
    await m.answer(f"✅ Фото сохранено: <code>{dest.name}</code>")
    await _show_root(m)


@router.message(BroadcastFlow.photo)
async def bc_photo_wrong(m: Message):
    """Любой другой ввод на шаге фото — подсказать, а не молча уйти в другие роутеры."""
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("Жду фото (или <code>-</code>, чтобы убрать фото).", reply_markup=_kb_cancel())


# ------------------------------- интервал / лимит / паузы -------------------------------
@router.callback_query(F.data == "bc:interval")
async def bc_interval_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(BroadcastFlow.interval)
    await cq.message.answer("⏱ <b>Интервал</b> между полными проходами, в минутах (например <code>60</code>).\n"
                            "Чем больше — тем безопаснее для аккаунта.", reply_markup=_kb_cancel())
    await cq.answer()


@router.message(BroadcastFlow.interval, F.text)
async def bc_interval_set(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    try:
        v = max(1, int(m.text.strip()))
    except ValueError:
        await m.answer("Нужно число (минуты).", reply_markup=_kb_cancel())
        return
    store.update_state(interval_min=v)
    await state.clear()
    await m.answer(f"✅ Интервал: {v} мин.")
    await _show_root(m)


@router.callback_query(F.data == "bc:cap")
async def bc_cap_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(BroadcastFlow.cap)
    await cq.message.answer("🚦 <b>Лимит сообщений в сутки</b> (0 = без лимита). Рекомендую держать небольшим.",
                            reply_markup=_kb_cancel())
    await cq.answer()


@router.message(BroadcastFlow.cap, F.text)
async def bc_cap_set(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    try:
        v = max(0, int(m.text.strip()))
    except ValueError:
        await m.answer("Нужно число.", reply_markup=_kb_cancel())
        return
    store.update_state(daily_cap=v)
    await state.clear()
    await m.answer(f"✅ Лимит: {v or '∞'} в сутки.")
    await _show_root(m)


@router.callback_query(F.data == "bc:delays")
async def bc_delays_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(BroadcastFlow.delays)
    await cq.message.answer("🐢 <b>Пауза между группами</b> — два числа «мин макс» в секундах "
                            "(например <code>8 25</code>). Рандом в этом диапазоне между отправками.",
                            reply_markup=_kb_cancel())
    await cq.answer()


@router.message(BroadcastFlow.delays, F.text)
async def bc_delays_set(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    parts = m.text.replace(",", " ").split()
    try:
        lo, hi = int(parts[0]), int(parts[1])
        if lo < 0 or hi < lo:
            raise ValueError
    except (ValueError, IndexError):
        await m.answer("Нужно два числа: «мин макс», мин ≤ макс (например <code>8 25</code>).",
                       reply_markup=_kb_cancel())
        return
    store.update_state(delay_min=lo, delay_max=hi)
    await state.clear()
    await m.answer(f"✅ Паузы: {lo}–{hi}с.")
    await _show_root(m)


# ------------------------------- группы -------------------------------
def kb_targets() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for t in store.list_targets():
        mark = "🟢" if t.active else "🔴"
        name = t.title or t.chat_ref
        warn = " ⚠️" if t.last_error else ""
        rows.append([
            InlineKeyboardButton(text=f"{mark} {name}{warn}", callback_data=f"bc:topen:{t.id}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить группы", callback_data="bc:tadd")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bc:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "bc:targets")
async def bc_targets(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text(
            "👥 <b>Группы рассылки</b> (нажми, чтобы вкл/выкл/удалить):", reply_markup=kb_targets())
    except Exception:
        await cq.message.answer("👥 <b>Группы рассылки</b>:", reply_markup=kb_targets())
    await cq.answer()


@router.callback_query(F.data == "bc:tadd")
async def bc_tadd(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(BroadcastFlow.targets)
    await cq.message.answer(
        "Пришлите группы — по одной в строке:\n"
        "<code>@username</code> · <code>https://t.me/username</code> · числовой <code>-100…</code>\n\n"
        "ℹ️ Для приватных групп берите <b>числовой id</b> (получить: "
        "<code>.venv/bin/python -m userbot.list_dialogs</code>). Аккаунт должен уже состоять в группе.",
        reply_markup=_kb_cancel())
    await cq.answer()


@router.message(BroadcastFlow.targets, F.text)
async def bc_tadd_set(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    refs = [ln.strip() for ln in (m.text or "").splitlines() if ln.strip()]
    added, dup = store.add_targets(refs)
    await state.clear()
    await m.answer(f"Добавлено: <b>{added}</b>, дубликатов: <b>{dup}</b>.")
    await m.answer("👥 <b>Группы рассылки</b>:", reply_markup=kb_targets())


def _target_view(tid: int):
    t = next((x for x in store.list_targets() if x.id == tid), None)
    if not t:
        return None, None
    mark = "🟢 активна" if t.active else "🔴 выключена"
    text = (
        f"👥 <b>{_esc(t.title or t.chat_ref)}</b>\n\n"
        f"• Ссылка/id: <code>{_esc(t.chat_ref)}</code>\n"
        f"• Статус: {mark}\n"
        f"• Последняя ок: {_esc(t.last_ok or '—')}\n"
        f"• Ошибка: {_esc((t.last_error or '—')[:300])}"
    )
    toggle = "🔴 Выключить" if t.active else "🟢 Включить"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle, callback_data=f"bc:ttoggle:{tid}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"bc:tdel:{tid}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bc:targets")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("bc:topen:"))
async def bc_topen(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    tid = int(cq.data.split(":")[-1])
    text, kb = _target_view(tid)
    if not text:
        await cq.answer("Не найдено", show_alert=True)
        return
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("bc:ttoggle:"))
async def bc_ttoggle(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    tid = int(cq.data.split(":")[-1])
    res = store.toggle_target(tid)
    if res is None:
        await cq.answer("Не найдено", show_alert=True)
        return
    text, kb = _target_view(tid)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await cq.answer("Включена" if res else "Выключена")


@router.callback_query(F.data.startswith("bc:tdel:"))
async def bc_tdel(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    tid = int(cq.data.split(":")[-1])
    store.delete_target(tid)
    await cq.answer("Удалена")
    try:
        await cq.message.edit_text("👥 <b>Группы рассылки</b>:", reply_markup=kb_targets())
    except Exception:
        pass
