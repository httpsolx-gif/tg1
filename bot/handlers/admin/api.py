"""🛡 API — выдача и управление токенами публичного API отправки (api/README.md).

Только админ, только в Telegram. Через сам API управлять токенами нельзя.
Полное значение токена показываем ОДИН раз при создании (в БД — только хэш).

Корень: ➕ Создать · 📋 Список. Карточка токена: правка полей (сервисы, домены
ссылок, квота/сутки, рейт/мин, IP, срок, scopes, метка), ⛔️ Отозвать / ♻️ Включить,
🗑 Удалить (с подтверждением). Колбэки `api:`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from html import escape as _esc

from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext

from common.config import settings
from bot.fsm.admin import ApiTokenFlow
from bot.services import api_tokens as T

router = Router(name="api-admin")
log = logging.getLogger("bot")


def _admin(uid: int) -> bool:
    return uid in settings.admin_ids_set


def _btn(t: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t, callback_data=cb)


# ----------------------------- корень -----------------------------
def kb_api_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("➕ Создать токен", "api:new")],
        [_btn("📋 Список токенов", "api:list")],
        [_btn("🚫 Suppression", "api:supp"), _btn("🧾 Аудит", "api:audit")],
        [_btn("⬅️ Назад", "admin:back")],
    ])


_ROOT_TEXT = (
    "🛡 <b>API</b> — токены публичного API отправки.\n\n"
    "• Клиент по токену может <b>запускать отправку</b> (scope <code>send</code>) — "
    "задаёт только <i>сервис, email, ссылку</i>. Шаблон/SMTP/сокра ставит система.\n"
    "• Токен ограничен: сервисы, домены ссылок, квота/сутки, рейт, срок, IP.\n"
    "• Значение токена показывается <b>один раз</b> при создании (в БД — только хэш).\n"
    "<i>Управление токенами — только здесь, через API нельзя.</i>"
)


async def show_api_root(m: Message):
    if not _admin(m.from_user.id):
        return
    await m.answer(_ROOT_TEXT, reply_markup=kb_api_root())


@router.callback_query(F.data == "api:root")
async def api_root(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text(_ROOT_TEXT, reply_markup=kb_api_root())
    except Exception:
        await cq.message.answer(_ROOT_TEXT, reply_markup=kb_api_root())
    await cq.answer()


# ----------------------------- создание -----------------------------
@router.callback_query(F.data == "api:new")
async def api_new(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(ApiTokenFlow.label)
    await cq.message.answer(
        "Создание токена. Пришлите <b>метку</b> (кому выдан, напр. «клиент X»).\n"
        "Или «-» — без метки. Остальное (сервисы, квоту, срок) настроите в карточке.")
    await cq.answer()


@router.message(ApiTokenFlow.label, F.text)
async def api_new_label(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    await state.clear()
    label = (m.text or "").strip()
    if label in ("-", "—"):
        label = ""
    tok, raw = T.create(label=label)
    await m.answer(
        "✅ Токен создан. <b>Скопируйте сейчас — больше он не покажется:</b>\n\n"
        f"<code>{_esc(raw)}</code>\n\n"
        "⚠️ В БД хранится только хэш. Потерян — создайте новый и отзовите старый.\n\n"
        "<b>По умолчанию:</b> сервисы — <b>все</b>, домены ссылок — <b>любые</b>, "
        "квота и срок — <b>без лимита</b>. Ограничить что-либо можно в карточке.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [_btn("⚙️ Открыть карточку", f"api:card:{tok.id}")],
            [_btn("⬅️ В меню API", "api:root")],
        ]))


# ----------------------------- список -----------------------------
def _status(t) -> str:
    if not t.active:
        return "🔴 отозван"
    if t.expires_at:
        try:
            if datetime.utcnow() >= datetime.fromisoformat(t.expires_at):
                return "🟠 истёк"
        except ValueError:
            pass
    return "🟢 активен"


@router.callback_query(F.data == "api:list")
async def api_list(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    toks = T.list_tokens()
    rows = []
    for t in toks:
        lbl = f" · {t.label}" if t.label else ""
        rows.append([_btn(f"{_status(t)} {t.prefix}…{lbl}", f"api:card:{t.id}")])
    rows.append([_btn("➕ Создать", "api:new"), _btn("⬅️ Назад", "api:root")])
    text = f"📋 Токены API ({len(toks)}):" if toks else "📋 Токенов пока нет."
    try:
        await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        await cq.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


# ----------------------------- карточка -----------------------------
def _fmt(v, empty="—") -> str:
    return _esc(str(v)) if v not in (None, "", 0) else empty


def _fmt_list(v, empty="—", star="все") -> str:
    """csv-поле: «*» → «все», пусто → empty, иначе значение."""
    if v in (None, ""):
        return empty
    return star if "*" in str(v).split(",") else _esc(str(v))


def _kb_card(t) -> InlineKeyboardMarkup:
    toggle = (_btn("♻️ Включить", f"api:enable:{t.id}") if not t.active
              else _btn("⛔️ Отозвать", f"api:revoke:{t.id}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("✏️ Сервисы", f"api:edit:allowed_services:{t.id}"),
         _btn("✏️ Домены ссылок", f"api:edit:allowed_link_domains:{t.id}")],
        [_btn("✏️ Квота/сут", f"api:edit:daily_quota:{t.id}"),
         _btn("✏️ Рейт/мин", f"api:edit:rate_per_min:{t.id}")],
        [_btn("✏️ IP", f"api:edit:ip_whitelist:{t.id}"),
         _btn("✏️ Срок", f"api:edit:expires_days:{t.id}")],
        [_btn("✏️ Метка", f"api:edit:label:{t.id}"),
         _btn("✏️ Scopes", f"api:edit:scopes:{t.id}")],
        [_btn("📊 Статистика", f"api:stats:{t.id}")],
        [toggle, _btn("🗑 Удалить", f"api:del:{t.id}")],
        [_btn("🔄 Обновить", f"api:card:{t.id}"), _btn("⬅️ Список", "api:list")],
    ])


def _card_text(t) -> str:
    quota = f"{t.sent_today}/{t.daily_quota}" if t.daily_quota else f"{t.sent_today} (без лимита)"
    return (
        f"🛡 Токен <code>{_esc(t.prefix)}…</code> — {_status(t)}\n"
        f"Метка: <b>{_fmt(t.label)}</b>\n\n"
        f"• Scopes: <code>{_esc(t.scopes)}</code>\n"
        f"• Сервисы: {_fmt_list(t.allowed_services, empty='ни одного')}\n"
        f"• Домены ссылок: {_fmt_list(t.allowed_link_domains, empty='ни одного')}\n"
        f"• Квота/сутки: <b>{quota}</b> · рейт/мин: <b>{_fmt(t.rate_per_min, '∞')}</b>\n"
        f"• IP-whitelist: {_fmt(t.ip_whitelist, 'любой')}\n"
        f"• Срок: {_fmt(t.expires_at, 'бессрочно')}\n"
        f"• Создан: {_fmt(t.created_at)}\n"
        f"• Последний вызов: {_fmt(t.last_used_at, 'не было')}"
        + (f" · IP {_esc(t.last_ip)}" if t.last_ip else "") + "\n\n"
        "<i>Для отправки нужны заданные «Сервисы» и «Домены ссылок» (default-deny).</i>"
    )


async def _show_card(target, tid: int, *, edit: bool):
    t = T.get(tid)
    if not t:
        txt = "Токен не найден (удалён?)."
        kb = InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Список", "api:list")]])
    else:
        txt, kb = _card_text(t), _kb_card(t)
    if edit:
        try:
            await target.edit_text(txt, reply_markup=kb)
            return
        except Exception:
            pass
    await target.answer(txt, reply_markup=kb)


@router.callback_query(F.data.startswith("api:card:"))
async def api_card(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    tid = int(cq.data.split(":")[2])
    await _show_card(cq.message, tid, edit=True)
    await cq.answer()


# ----------------------------- статистика по токену -----------------------------
async def _token_report(tid: int) -> dict:
    """Сводка отправок токена: ok/fail/всего, переходы (трек-id), по сервисам, по дням."""
    from collections import Counter
    from sqlmodel import Session, select
    from common.db import get_engine
    from common.models.send_log import SendLog
    from bot.handlers.admin import stats as S

    clicked = await S._clicked_tracks()
    with Session(get_engine()) as db:
        rows = db.exec(select(SendLog).where(SendLog.api_token_id == tid)).all()
    ok = [r for r in rows if r.status == "ok"]
    clicks = len({r.track for r in ok if r.track and r.track in clicked})
    by_svc = Counter((r.service or "—") for r in ok)
    by_day = Counter((r.created_at or "")[:10] for r in ok if r.created_at)
    return {"total": len(rows), "ok": len(ok), "fail": len(rows) - len(ok),
            "clicks": clicks, "by_svc": by_svc, "by_day": by_day}


@router.callback_query(F.data.startswith("api:stats:"))
async def api_stats(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    tid = int(cq.data.split(":")[2])
    t = T.get(tid)
    if not t:
        await cq.answer("Токен не найден", show_alert=True)
        return
    await cq.answer("Считаю…")
    rep = await _token_report(tid)
    conv = f"{(100.0 * rep['clicks'] / rep['ok']):.1f}%" if rep["ok"] else "—"
    quota = f"{t.sent_today}/{t.daily_quota}" if t.daily_quota else f"{t.sent_today} (без лимита)"
    svc = "\n".join(f"   • {_esc(k)} — {v}" for k, v in rep["by_svc"].most_common(10)) or "   —"
    days = "\n".join(f"   • {_esc(k)} — {v}" for k, v in sorted(rep["by_day"].items(), reverse=True)[:7]) or "   —"
    text = (
        f"📊 Токен <code>{_esc(t.prefix)}…</code>{(' · ' + _esc(t.label)) if t.label else ''}\n\n"
        f"• Отправок: <b>{rep['ok']}</b> ok из {rep['total']} (ошибок {rep['fail']})\n"
        f"• Переходов: <b>{rep['clicks']}</b> (конв {conv})\n"
        f"• Квота сегодня: <b>{quota}</b>\n"
        f"• Последний вызов: {_fmt(t.last_used_at, 'не было')}\n\n"
        f"<b>По сервисам:</b>\n{svc}\n\n"
        f"<b>По дням (7):</b>\n{days}\n\n"
        "<i>Переходы считаются по личным short-доменам (трек-id).</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🔄 Обновить", f"api:stats:{tid}"), _btn("⬅️ Карточка", f"api:card:{tid}")],
    ])
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)


# ----------------------------- правка полей -----------------------------
_PROMPTS = {
    "label": "метку (кому выдан). «-» — очистить",
    "allowed_services": "разрешённые <b>сервисы</b> через пробел/запятую (напр. <code>olx.pl allegro.pl</code>). <code>*</code> — все. «-» — очистить (тогда ни один)",
    "allowed_link_domains": "<b>домены ссылок</b>, на которые можно слать (напр. <code>go.example.com</code>). <code>*</code> — любые. «-» — очистить (тогда ни один)",
    "ip_whitelist": "<b>IP/CIDR</b> через пробел (напр. <code>1.2.3.4 10.0.0.0/24</code>). «-» — без ограничения",
    "daily_quota": "лимит <b>писем в сутки</b> (число; 0 = без лимита)",
    "rate_per_min": "лимит <b>запросов в минуту</b> (число; 0 = без лимита)",
    "expires_days": "<b>срок</b> в днях от сейчас (число; 0 = бессрочно)",
    "scopes": "<b>scopes</b> через пробел: <code>send</code> и/или <code>logs:read</code>",
}


@router.callback_query(F.data.startswith("api:edit:"))
async def api_edit(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, field, tid_s = cq.data.split(":", 3)
    if field not in _PROMPTS:
        await cq.answer("Неизвестное поле", show_alert=True)
        return
    await state.set_state(ApiTokenFlow.edit)
    await state.update_data(field=field, tid=int(tid_s))
    await cq.message.answer(f"Пришлите {_PROMPTS[field]}:")
    await cq.answer()


@router.message(ApiTokenFlow.edit, F.text)
async def api_edit_text(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    data = await state.get_data()
    field, tid = data.get("field"), data.get("tid")
    await state.clear()
    if not field or tid is None:
        await m.answer("Сессия сброшена, откройте карточку заново.")
        return
    ok = T.set_field(tid, field, m.text or "")
    await m.answer("✅ Сохранено." if ok else "⚠️ Не понял значение — попробуйте ещё раз из карточки.")
    await _show_card(m, tid, edit=False)


# ----------------------------- отзыв / включение -----------------------------
@router.callback_query(F.data.startswith("api:revoke:"))
async def api_revoke(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    tid = int(cq.data.split(":")[2])
    T.revoke(tid)
    await _show_card(cq.message, tid, edit=True)
    await cq.answer("Отозван")


@router.callback_query(F.data.startswith("api:enable:"))
async def api_enable(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    tid = int(cq.data.split(":")[2])
    T.enable(tid)
    await _show_card(cq.message, tid, edit=True)
    await cq.answer("Включён")


# ----------------------------- удаление (с подтверждением) -----------------------------
@router.callback_query(F.data.startswith("api:del:"))
async def api_del(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    tid = int(cq.data.split(":")[2])
    t = T.get(tid)
    name = f"{t.prefix}…" if t else str(tid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🗑 Да, удалить", f"api:delok:{tid}")],
        [_btn("↩️ Отмена", f"api:card:{tid}")],
    ])
    await cq.message.edit_text(
        f"Удалить токен <code>{_esc(name)}</code> безвозвратно?\n"
        "<i>История отправок останется, но привязка к токену потеряется. "
        "Чтобы просто выключить и сохранить аудит — используйте «Отозвать».</i>",
        reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("api:delok:"))
async def api_delok(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    tid = int(cq.data.split(":")[2])
    T.delete(tid)
    await cq.answer("Удалён")
    # вернуться к списку
    toks = T.list_tokens()
    rows = [[_btn(f"{_status(t)} {t.prefix}…" + (f" · {t.label}" if t.label else ""),
                  f"api:card:{t.id}")] for t in toks]
    rows.append([_btn("➕ Создать", "api:new"), _btn("⬅️ Назад", "api:root")])
    text = f"📋 Токены API ({len(toks)}):" if toks else "📋 Токенов пока нет."
    try:
        await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        await cq.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# ----------------------------- suppression-лист -----------------------------
def _kb_supp() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("➕ Добавить", "api:suppadd"), _btn("🗑 Удалить", "api:suppdel")],
        [_btn("🔄 Обновить", "api:supp"), _btn("⬅️ Назад", "api:root")],
    ])


async def _show_supp(target, *, edit: bool):
    from bot.services import suppression as SUP
    rows = SUP.list_all(limit=30)
    total = SUP.count()
    body = "\n".join(f"• <code>{_esc(r.email)}</code>"
                     + (f" <i>({_esc(r.reason)})</i>" if r.reason else "") for r in rows) or "<i>пусто</i>"
    text = (
        f"🚫 <b>Suppression-лист</b> ({total}) — адреса, на которые НЕ слать "
        "(отписки/жалобы/баунсы). Проверяется и в боте, и в API.\n\n"
        f"{body}" + ("\n\n<i>показаны последние 30</i>" if total > 30 else "")
    )
    if edit:
        try:
            await target.edit_text(text, reply_markup=_kb_supp())
            return
        except Exception:
            pass
    await target.answer(text, reply_markup=_kb_supp())


@router.callback_query(F.data == "api:supp")
async def api_supp(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await _show_supp(cq.message, edit=True)
    await cq.answer()


@router.callback_query(F.data.in_({"api:suppadd", "api:suppdel"}))
async def api_supp_io(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    mode = "add" if cq.data == "api:suppadd" else "del"
    await state.set_state(ApiTokenFlow.supp)
    await state.update_data(supp_mode=mode)
    verb = "добавить в" if mode == "add" else "убрать из"
    await cq.message.answer(f"Пришлите e-mail(ы), чтобы {verb} suppression-лист "
                            "(можно пачкой через пробел/запятую/перенос):")
    await cq.answer()


@router.message(ApiTokenFlow.supp, F.text)
async def api_supp_text(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    from bot.services import suppression as SUP
    mode = (await state.get_data()).get("supp_mode", "add")
    await state.clear()
    if mode == "add":
        n = SUP.add_bulk(m.text or "", reason="manual")
        await m.answer(f"✅ Добавлено в suppression: {n}.")
    else:
        import re as _re
        n = sum(1 for p in _re.split(r"[\s,;]+", m.text or "") if p.strip() and SUP.remove(p.strip()))
        await m.answer(f"🗑 Убрано из suppression: {n}.")
    await _show_supp(m, edit=False)


# ----------------------------- аудит вызовов -----------------------------
@router.callback_query(F.data == "api:audit")
async def api_audit_view(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    from bot.services import api_audit as AUD
    rows = AUD.recent(limit=20)
    if rows:
        lines = []
        for r in rows:
            who = f"#{r.token_id}" if r.token_id else (f"{r.token_prefix}…" if r.token_prefix else "—")
            ts = (r.created_at or "")[11:19]
            lines.append(f"<code>{ts}</code> {r.status} {_esc(r.method or '')} {_esc(r.path or '')} · {who} · {_esc(r.ip or '')}")
        body = "\n".join(lines)
    else:
        body = "<i>пока нет вызовов</i>"
    text = f"🧾 <b>Аудит API</b> — последние {len(rows)} вызовов:\n\n{body}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🔄 Обновить", "api:audit"), _btn("⬅️ Назад", "api:root")],
    ])
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
