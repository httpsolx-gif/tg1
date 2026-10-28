"""TEST — короткие проверки сокращателей / SMTP / нод + удаление нерабочих (п.42).

Каждый тест прогоняет все записи своего типа параллельно, показывает 🟢/🔴 с
кратким итогом и, если есть нерабочие, кнопку «🗑 Удалить нерабочие (N)».
Нерабочие id запоминаются в памяти до удаления/перепроверки.

- Сокращатели: n9 (shorten_url, не удаляется) + личные short-домены (регистрируем
  проверочный код на ноде и дёргаем публичный 301 через CF).
- SMTP: probe_smtp (self-test на сам аккаунт).
- Ноды: check_node (SSH + python3).
"""
from __future__ import annotations

import asyncio
import logging
from html import escape as _esc

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlmodel import Session, select

from common.db import get_engine
from common.config import settings
from common.security import decrypt_password
from common.models.smtp import SmtpAccount
from common.models.node import Node
from common.models.short_domain import ShortDomain
from common.models.scope import ScopeCfg, ScopeSmtp

router = Router(name="test-admin")
log = logging.getLogger("bot")
_engine = get_engine()

# нерабочие id с последнего прогона: {(uid, kind): [id, ...]}
_FAILED: dict = {}


def _admin(uid: int) -> bool:
    return uid in settings.admin_ids_set


def _btn(t: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t, callback_data=cb)


def kb_test_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🔗 Тест сокращателей", "test:short")],
        [_btn("📧 Тест SMTP", "test:smtp")],
        [_btn("🖥 Тест нод", "test:nodes")],
        [_btn("⬅️ Назад", "admin:back")],
    ])


async def show_test_root(m: Message):
    if not _admin(m.from_user.id):
        return
    await m.answer("🧪 <b>TEST</b> — проверки и удаление нерабочих:", reply_markup=kb_test_root())


@router.callback_query(F.data == "test:root")
async def test_root(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        await cq.message.edit_text("🧪 <b>TEST</b> — проверки и удаление нерабочих:", reply_markup=kb_test_root())
    except Exception:
        await cq.message.answer("🧪 <b>TEST</b> — проверки и удаление нерабочих:", reply_markup=kb_test_root())
    await cq.answer()


def _result_kb(kind: str, n_fail: int) -> InlineKeyboardMarkup:
    rows = []
    if n_fail:
        rows.append([_btn(f"🗑 Удалить нерабочие ({n_fail})", f"test:del:{kind}")])
    rows.append([_btn("🔄 Перепроверить", f"test:{kind}")])
    rows.append([_btn("⬅️ Назад", "test:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================ Сокращатели ============================
async def _curl_head(url: str, timeout: int = 15) -> str:
    """HEAD/GET заголовки публичного URL (возвращает сырой текст, пусто при ошибке)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-sI", "--max-time", str(timeout), url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
        return (out or b"").decode("utf-8", "ignore")
    except Exception:
        return ""


async def _check_domain(d_id: int, domain: str, node) -> tuple[bool, str]:
    """Личный short-домен рабочий ⇔ регистрируется код и публичный 301 на target."""
    from bot.services.short_domain import register_link
    code = await register_link(node, domain, "https://example.org/probe")
    if not code:
        return False, "регистрация кода не удалась (нода недоступна?)"
    head = await _curl_head(f"https://{domain}/{code}")
    first = head.splitlines()[0] if head else ""
    loc = next((l for l in head.splitlines() if l.lower().startswith("location:")), "")
    ok = "301" in first and "example.org/probe" in loc.lower()
    return ok, (first.strip() or "нет ответа")


@router.callback_query(F.data == "test:short")
async def test_short(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.answer("Проверяю сокращатели…")
    await cq.message.edit_text("⏳ Проверяю сокращатели…")
    # n9 (не удаляется)
    from bot.services.short_client import shorten_url
    n9 = await shorten_url("https://example.org/ping")
    n9_ok = bool(n9) and n9.startswith("http") and n9 != "https://example.org/ping"
    # личные домены
    with Session(_engine) as db:
        domains = db.exec(select(ShortDomain).where(ShortDomain.is_active == True)).all()  # noqa: E712
        items = []
        for d in domains:
            node = db.get(Node, d.node_id) if d.node_id else None
            if node is not None:
                db.expunge(node)
            items.append((d.id, d.domain, d.status, node))
    results = await asyncio.gather(*[
        _check_domain(did, dom, node) if (status == "ready" and node) else _noready(status, node)
        for (did, dom, status, node) in items
    ]) if items else []
    lines = ["🔗 <b>Тест сокращателей</b>\n",
             f"{'🟢' if n9_ok else '🔴'} n9.cl — {'ок' if n9_ok else 'недоступен'} <i>(не удаляется)</i>"]
    failed = []
    if items:
        lines.append("\n<b>Личные домены:</b>")
        for (did, dom, status, _node), (ok, info) in zip(items, results):
            lines.append(f"{'🟢' if ok else '🔴'} <b>{_esc(dom)}</b> — {_esc(info[:80])}")
            if not ok:
                failed.append(did)
    else:
        lines.append("\n<i>Личных доменов нет.</i>")
    _FAILED[(cq.from_user.id, "short")] = failed
    await cq.message.edit_text("\n".join(lines), reply_markup=_result_kb("short", len(failed)))


async def _noready(status: str, node) -> tuple[bool, str]:
    return False, f"не готов (status={status}{', нет ноды' if not node else ''})"


# ============================ SMTP ============================
async def _check_smtp(host, port, login, email, pw) -> tuple[bool, str]:
    from bot.services.mailer import probe_smtp
    try:
        res = await probe_smtp(host, int(port), login, email, pw,
                               recipient_override=email, timeout=25)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return bool(res.ok), (res.smtp_response or res.error or ("ок" if res.ok else "ошибка"))


@router.callback_query(F.data == "test:smtp")
async def test_smtp(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.answer("Проверяю SMTP…")
    await cq.message.edit_text("⏳ Проверяю SMTP-аккаунты…")
    with Session(_engine) as db:
        accs = db.exec(select(SmtpAccount).order_by(SmtpAccount.id)).all()
        items = [(a.id, a.host, a.port, a.login, a.email, decrypt_password(a.password_enc or "")) for a in accs]
    if not items:
        await cq.message.edit_text("📧 <b>Тест SMTP</b>\n\n<i>Аккаунтов нет.</i>",
                                   reply_markup=_result_kb("smtp", 0))
        return
    results = await asyncio.gather(*[_check_smtp(h, p, lg, em, pw) for (_i, h, p, lg, em, pw) in items])
    lines = ["📧 <b>Тест SMTP</b>  (self-test)\n"]
    failed = []
    for (sid, host, port, _lg, email, _pw), (ok, info) in zip(items, results):
        lines.append(f"{'🟢' if ok else '🔴'} <b>{_esc(email)}</b> ({_esc(host)}:{port}) — {_esc(str(info)[:70])}")
        if not ok:
            failed.append(sid)
    _FAILED[(cq.from_user.id, "smtp")] = failed
    await cq.message.edit_text("\n".join(lines), reply_markup=_result_kb("smtp", len(failed)))


# ============================ Ноды ============================
async def _check_one_node(node) -> tuple[bool, str]:
    from bot.services.node_runner import check_node
    try:
        return await check_node(node)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


@router.callback_query(F.data == "test:nodes")
async def test_nodes(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.answer("Проверяю ноды…")
    await cq.message.edit_text("⏳ Проверяю ноды (SSH)…")
    with Session(_engine) as db:
        nodes = db.exec(select(Node).order_by(Node.id)).all()
        items = []
        for n in nodes:
            db.expunge(n)
            items.append(n)
    if not items:
        await cq.message.edit_text("🖥 <b>Тест нод</b>\n\n<i>Нод нет.</i>", reply_markup=_result_kb("nodes", 0))
        return
    results = await asyncio.gather(*[_check_one_node(n) for n in items])
    lines = ["🖥 <b>Тест нод</b>  (SSH + python3)\n"]
    failed = []
    for n, (ok, info) in zip(items, results):
        lines.append(f"{'🟢' if ok else '🔴'} <b>{_esc(n.ip)}</b> ({_esc(n.login)}) — {_esc(str(info)[:70])}")
        if not ok:
            failed.append(n.id)
    _FAILED[(cq.from_user.id, "nodes")] = failed
    await cq.message.edit_text("\n".join(lines), reply_markup=_result_kb("nodes", len(failed)))


# ============================ Удаление нерабочих ============================
def _delete_smtp(db: Session, sid: int) -> bool:
    acc = db.get(SmtpAccount, sid)
    if not acc:
        return False
    db.delete(acc)
    for l in db.exec(select(ScopeSmtp).where(ScopeSmtp.smtp_id == sid)).all():
        db.delete(l)
    try:
        from common.models.autoswitch import SmtpBinding
        for b in db.exec(select(SmtpBinding).where(SmtpBinding.smtp_id == sid)).all():
            db.delete(b)
    except Exception:
        pass
    for cfg in db.exec(select(ScopeCfg).where(ScopeCfg.current_key == f"smtp:{sid}")).all():
        cfg.current_key = None
        db.add(cfg)
    return True


def _delete_domain(db: Session, did: int) -> bool:
    d = db.get(ShortDomain, did)
    if not d:
        return False
    db.delete(d)
    for cfg in db.exec(select(ScopeCfg).where(ScopeCfg.short == f"dom:{did}")).all():
        cfg.short, cfg.short_at = None, None
        db.add(cfg)
    return True


def _delete_node(db: Session, nid: int) -> bool:
    n = db.get(Node, nid)
    if not n:
        return False
    db.delete(n)
    return True


@router.callback_query(F.data.startswith("test:del:"))
async def test_delete(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    kind = cq.data.split(":")[2]
    ids = list(_FAILED.get((cq.from_user.id, kind), []))
    if not ids:
        await cq.answer("Нечего удалять", show_alert=True)
        return
    fn = {"smtp": _delete_smtp, "nodes": _delete_node, "short": _delete_domain}.get(kind)
    if fn is None:
        await cq.answer("Неизвестный тип", show_alert=True)
        return
    n = 0
    with Session(_engine) as db:
        for i in ids:
            if fn(db, i):
                n += 1
        db.commit()
    _FAILED[(cq.from_user.id, kind)] = []
    await cq.answer(f"Удалено: {n}")
    await cq.message.edit_text(
        f"🗑 Удалено нерабочих: <b>{n}</b>.\nЗапустите тест заново для актуального списка.",
        reply_markup=_result_kb(kind, 0))
