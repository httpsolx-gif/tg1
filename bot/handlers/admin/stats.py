"""STATS — статистика отправок и переходов с конверсией (п.37).

Отправки берутся из `SendLog` (разрезы country/service/mailbox пишутся на отправке).
Переходы считаются только по личным short-доменам: трек-id зашит в ссылку, origin
логирует его на ноде (дедуп 1 slug=1 переход), здесь трек-id джойнится с SendLog.
Поэтому конверсия осмысленна для отправок через личный домен; n9/без сокращения —
переходы неизвестны (track пуст → в клики не попадают).

Разделы: Общая / По странам / По почтам / По сервисам / 🔁 Сброс. Колбэки `st:`.
"""
from __future__ import annotations

import logging
from collections import Counter
from html import escape as _esc

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlmodel import Session, select

from common.db import get_engine
from common.config import settings
from common.domains import domain_in, parse_tokens
from common.models.send_log import SendLog
from common.models.short_domain import ShortDomain
from common.models.node import Node
from common.models.mailbox import Mailbox
from common.models.rubric import Rubric, RubricMailbox

router = Router(name="stats-admin")
log = logging.getLogger("bot")
_engine = get_engine()


def _admin(uid: int) -> bool:
    return uid in settings.admin_ids_set


def _btn(t: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t, callback_data=cb)


def kb_stats_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("📈 Общая", "st:overall")],
        [_btn("🌍 По странам", "st:dim:country"), _btn("📮 По почтам", "st:dim:mailbox")],
        [_btn("🛠 По сервисам", "st:dim:service")],
        [_btn("🔁 Сброс статистики", "st:reset")],
        [_btn("⬅️ Назад", "admin:back")],
    ])


async def show_stats_root(m: Message):
    if not _admin(m.from_user.id):
        return
    await m.answer("📊 <b>STATS</b> — отправки / переходы / конверсия:", reply_markup=kb_stats_root())


@router.callback_query(F.data == "st:root")
async def st_root(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("📊 <b>STATS</b> — отправки / переходы / конверсия:", reply_markup=kb_stats_root())
    except Exception:
        await cq.message.answer("📊 <b>STATS</b> — отправки / переходы / конверсия:", reply_markup=kb_stats_root())
    await cq.answer()


# ---------- сбор данных ----------
async def _clicked_tracks() -> set:
    """Все трек-id переходов со всех нод готовых доменов (объединение)."""
    from bot.services.short_stats import clicked_tracks
    with Session(_engine) as db:
        node_ids = {d.node_id for d in db.exec(
            select(ShortDomain).where(ShortDomain.status == "ready")).all() if d.node_id}
        nodes = [db.get(Node, nid) for nid in node_ids]
        for n in nodes:
            if n:
                db.expunge(n)
    tracks: set = set()
    for n in nodes:
        if n:
            tracks |= await clicked_tracks(n)
    return tracks


# --- «По почтам»: группировка по ПРОВАЙДЕРУ домена получателя -----------------
# Статистика по почтам пишется только для доменов, ДОБАВЛЕННЫХ в рубрики (их
# «Почты»). Домены одного сервиса сливаются в одну почту (Gmail = gmail.com +
# googlemail.com; Hotmail = hotmail/outlook/live/msn — Microsoft — вместе).
# Домены, не добавленные ни в одну рубрику, идут одной кучей «other».
OTHER_BUCKET = "other"
_PROVIDER_MERGE = {"outlook": "hotmail"}  # Microsoft: hotmail+outlook → hotmail


def _domain_of(email: str) -> str:
    return (email or "").rsplit("@", 1)[-1].strip().lower()


def _merged_name(name: str) -> str:
    nm = (name or "").lower()
    return _PROVIDER_MERGE.get(nm, nm)


def _build_providers(mailboxes: list) -> dict:
    """merged_name(lower) → строка доменов сервиса (домены одного сервиса объединены)."""
    prov: dict = {}
    for name, domains in mailboxes:
        key = _merged_name(name)
        prov[key] = (prov.get(key, "") + " " + (domains or "")).strip()
    return prov


def _provider_of_dom(dom: str, providers: dict) -> str | None:
    for key, domains in providers.items():
        if domains and domain_in(dom, domains):
            return key
    return None


def _tracked_providers(providers: dict, rubric_tokens_str: str) -> set:
    """merged-провайдеры, у которых ХОТЯ БЫ ОДИН домен добавлен в рубрику
    (тогда вся почта сервиса, а не только добавленный домен, идёт под её именем)."""
    tracked = set()
    for key, domains in providers.items():
        if any(domain_in(d, rubric_tokens_str) for d in parse_tokens(domains)):
            tracked.add(key)
    return tracked


def _mailbox_bucket(email: str, providers: dict, tracked: set, rubric_tokens_str: str) -> str:
    """Почта для статистики: имя сервиса (слитые домены), если он добавлен в рубрику;
    добавленный домен без известного сервиса → сам домен; иначе 'other'."""
    dom = _domain_of(email)
    if not dom:
        return OTHER_BUCKET
    prov = _provider_of_dom(dom, providers)
    if prov and prov in tracked:
        return prov.title()
    if domain_in(dom, rubric_tokens_str):  # добавлен индивидуально, но сервис неизвестен
        return dom
    return OTHER_BUCKET


async def _compute():
    """→ (total_sends, total_clicks, sends_by{dim->{val:n}}, clicks_by{dim->{val:n}})."""
    clicked = await _clicked_tracks()
    dims = ("country", "service", "mailbox")
    sends_by = {d: Counter() for d in dims}
    clicks_by = {d: Counter() for d in dims}
    total_sends = 0
    clicked_tracks_seen: set = set()
    with Session(_engine) as db:
        rows = db.exec(select(SendLog).where(SendLog.status == "ok")).all()
        # таксономия провайдеров (домены) + домены, добавленные в рубрики (кроме рубрики 'other')
        providers = [(m.name, m.domains) for m in db.exec(select(Mailbox)).all()]
        rub_names = {r.id: (r.name or "").lower() for r in db.exec(select(Rubric)).all()}
        rubric_tokens = [rm.mailbox for rm in db.exec(select(RubricMailbox)).all()
                         if rm.mailbox and rub_names.get(rm.rubric_id) != "other"]
    prov_map = _build_providers(providers)
    rubric_tokens_str = " ".join(rubric_tokens)
    tracked = _tracked_providers(prov_map, rubric_tokens_str)
    for r in rows:
        total_sends += 1
        vals = {
            "country": r.country or "—",
            "service": r.service or "—",
            "mailbox": _mailbox_bucket(r.email, prov_map, tracked, rubric_tokens_str),
        }
        for d in dims:
            sends_by[d][vals[d]] += 1
        if r.track and r.track in clicked:
            clicked_tracks_seen.add(r.track)
            for d in dims:
                clicks_by[d][vals[d]] += 1
    total_clicks = len(clicked_tracks_seen)
    return total_sends, total_clicks, sends_by, clicks_by


def _pct(clicks: int, sends: int) -> str:
    return f"{(100.0 * clicks / sends):.1f}%" if sends else "—"


@router.callback_query(F.data == "st:overall")
async def st_overall(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.answer("Считаю…")
    sends, clicks, _, _ = await _compute()
    text = (
        "📈 <b>Общая статистика</b>\n\n"
        f"• Отправок (успешных): <b>{sends}</b>\n"
        f"• Переходов (уникальных): <b>{clicks}</b>\n"
        f"• Конверсия: <b>{_pct(clicks, sends)}</b>\n\n"
        "<i>Переходы считаются только по личным short-доменам.</i>"
    )
    await cq.message.edit_text(text, reply_markup=_back_kb())


_DIM_RU = {"country": "странам", "service": "сервисам", "mailbox": "почтам"}


@router.callback_query(F.data.startswith("st:dim:"))
async def st_dim(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    dim = cq.data.split(":")[2]
    await cq.answer("Считаю…")
    _, _, sends_by, clicks_by = await _compute()
    sb, cb = sends_by.get(dim, {}), clicks_by.get(dim, {})
    rows = sorted(sb.items(), key=lambda kv: kv[1], reverse=True)
    lines = [f"📊 <b>По {_DIM_RU.get(dim, dim)}</b>  (отпр / перех / %)\n"]
    if not rows:
        lines.append("<i>нет данных</i>")
    for val, s in rows[:40]:
        c = cb.get(val, 0)
        lines.append(f"• <b>{_esc(str(val))}</b> — {s} / {c} / {_pct(c, s)}")
    if dim == "mailbox":
        lines.append("\n<i>По почтам считаются только домены, добавленные в «Почты» рубрик "
                     "(домены одного сервиса — одной строкой). Остальные — «other».</i>")
    await cq.message.edit_text("\n".join(lines), reply_markup=_back_kb())


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Назад", "st:root")]])


# ---------- сброс ----------
@router.callback_query(F.data == "st:reset")
async def st_reset(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        _btn("✅ Да, сбросить всё", "st:resetyes"), _btn("❌ Нет", "st:root"),
    ]])
    await cq.message.edit_text(
        "🔁 Сбросить ВСЮ статистику?\n<i>Очистятся лог отправок (SendLog) и переходы на нодах.</i>",
        reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data == "st:resetyes")
async def st_reset_yes(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.answer("Сбрасываю…")
    from bot.services.short_stats import reset_clicks
    # отправки
    with Session(_engine) as db:
        for r in db.exec(select(SendLog)).all():
            db.delete(r)
        db.commit()
        node_ids = {d.node_id for d in db.exec(
            select(ShortDomain).where(ShortDomain.status == "ready")).all() if d.node_id}
        nodes = [db.get(Node, nid) for nid in node_ids]
        for n in nodes:
            if n:
                db.expunge(n)
    # переходы на нодах
    real = [n for n in nodes if n]
    okn = 0
    for n in real:
        if await reset_clicks(n):
            okn += 1
    await cq.message.edit_text(
        f"✅ Статистика сброшена (SendLog очищен, ноды: {okn}/{len(real)}).",
        reply_markup=kb_stats_root())
