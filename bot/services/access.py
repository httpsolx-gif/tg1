"""Классификация отправителей (ban/white/black) + CRUD списков доступа.

Идентификация по числовому id ИЛИ @username (сверяем оба при отправке).
Правило: админ → white; в ban → 'ban' (блок); в white → 'white' (обычная);
иначе (явный black или нигде не числится) → 'black'.

Профиль black (шаблон/тайтл/имя/SMTP/сокра) живёт в рубрике «black» и
настраивается в 🧩 Сервисы по этой рубрике (bot/handlers/admin/rubrics).
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from sqlmodel import Session, select

from common.db import get_engine
from common.models.access import AccessEntry, SeenUser

_engine = get_engine()

LISTS = ("ban", "white", "black")
LIST_RU = {"ban": "🚫 BAN", "white": "✅ White", "black": "⚫️ Black"}

_SPLIT = re.compile(r"[\s,;]+")


def _norm(raw: str):
    """'@User' → ('user', False); '12345' → ('12345', True); пусто → None."""
    s = (raw or "").strip().lstrip("@").lower()
    if not s:
        return None
    return (s, True) if s.isdigit() else (s, False)


def add_entries(lst: str, text: str) -> tuple[int, int]:
    """Добавить пачку (id/@username через пробел/запятую/перенос). → (добавлено, пропущено)."""
    added = skipped = 0
    with Session(_engine) as db:
        for p in (x for x in _SPLIT.split(text or "") if x.strip()):
            n = _norm(p)
            if not n:
                continue
            val, is_id = n
            if db.exec(select(AccessEntry).where(
                    (AccessEntry.lst == lst) & (AccessEntry.value == val))).first():
                skipped += 1
                continue
            db.add(AccessEntry(lst=lst, value=val, is_id=is_id))
            added += 1
        db.commit()
    return added, skipped


def remove_entry(entry_id: int) -> bool:
    with Session(_engine) as db:
        e = db.get(AccessEntry, entry_id)
        if not e:
            return False
        db.delete(e)
        db.commit()
        return True


def list_entries(lst: str) -> list[AccessEntry]:
    with Session(_engine) as db:
        return db.exec(select(AccessEntry).where(AccessEntry.lst == lst).order_by(AccessEntry.id)).all()


def classify(uid: int, username: str | None, admin_ids: set) -> str:
    """'ban' | 'white' | 'black'. Админы — всегда white (обычная отправка)."""
    if uid in (admin_ids or set()):
        return "white"
    keys = {str(uid)}
    if username:
        keys.add(username.strip().lstrip("@").lower())
    with Session(_engine) as db:
        lists = {r.lst for r in db.exec(select(AccessEntry).where(AccessEntry.value.in_(keys))).all()}
    if "ban" in lists:
        return "ban"
    if "white" in lists:
        return "white"
    return "black"  # не в white и не в ban → black (даже если явно в black или нигде)


# ----------------------------- перевод между списками -----------------------------
def _purge_keys(db, keys: set) -> None:
    if not keys:
        return
    for e in db.exec(select(AccessEntry).where(AccessEntry.value.in_(keys))).all():
        db.delete(e)


def set_class(value: str, lst: str) -> tuple[bool, str]:
    """Перевести один id/@username в список lst (убрав из остальных). → (ok, показ-значение)."""
    n = _norm(value)
    if not n:
        return False, ""
    val, is_id = n
    with Session(_engine) as db:
        _purge_keys(db, {val})
        db.add(AccessEntry(lst=lst, value=val, is_id=is_id))
        db.commit()
    return True, ("" if is_id else "@") + val


def set_class_bulk(text: str, lst: str) -> int:
    """Пачкой (id/@username через пробел/запятую/перенос) → в список lst. → сколько применено."""
    n = 0
    for p in (x for x in _SPLIT.split(text or "") if x.strip()):
        ok, _ = set_class(p, lst)
        if ok:
            n += 1
    return n


def unban(value: str) -> bool:
    """Убрать один id/@username из ban (→ по умолчанию black, если не в white)."""
    nm = _norm(value)
    if not nm:
        return False
    val, _ = nm
    with Session(_engine) as db:
        e = db.exec(select(AccessEntry).where(
            (AccessEntry.lst == "ban") & (AccessEntry.value == val))).first()
        if not e:
            return False
        db.delete(e)
        db.commit()
    return True


def unban_bulk(text: str) -> int:
    n = 0
    for p in (x for x in _SPLIT.split(text or "") if x.strip()):
        if unban(p):
            n += 1
    return n


def _uid_keys(uid: int | None, username: str | None) -> set:
    keys: set = set()
    if uid is not None:
        keys.add(str(uid))
    if username:
        u = username.strip().lstrip("@").lower()
        if u:
            keys.add(u)
    return keys


def set_class_user(uid: int | None, username: str | None, lst: str) -> None:
    """Перевести юзера (по id И @username — обе записи) в список lst. Добавляем по id, если он есть."""
    keys = _uid_keys(uid, username)
    with Session(_engine) as db:
        _purge_keys(db, keys)
        if uid is not None:
            db.add(AccessEntry(lst=lst, value=str(uid), is_id=True))
        elif username:
            db.add(AccessEntry(lst=lst, value=username.strip().lstrip("@").lower(), is_id=False))
        db.commit()


def unban_user(uid: int | None, username: str | None) -> None:
    keys = _uid_keys(uid, username)
    if not keys:
        return
    with Session(_engine) as db:
        for e in db.exec(select(AccessEntry).where(
                (AccessEntry.lst == "ban") & (AccessEntry.value.in_(keys)))).all():
            db.delete(e)
        db.commit()


# ----------------------------- виденные юзеры (id → username) -----------------------------
def remember_user(uid: int, username: str | None) -> None:
    """Запомнить id→username отправителя (апсерт). Вызывается на входе во флоу отправки."""
    if not uid:
        return
    uname = (username or "").strip().lstrip("@").lower() or None
    now = datetime.utcnow().isoformat()
    try:
        with Session(_engine) as db:
            u = db.get(SeenUser, uid)
            if not u:
                db.add(SeenUser(user_id=uid, username=uname, first_seen=now, last_seen=now))
            else:
                if uname:
                    u.username = uname
                u.last_seen = now
                db.add(u)
            db.commit()
    except Exception:
        pass  # лог отправки важнее — не роняем флоу из-за seen_user


def get_username(uid: int) -> str | None:
    with Session(_engine) as db:
        u = db.get(SeenUser, uid)
        return u.username if u else None


def resolve_uid(text: str) -> int | None:
    """id (число) или @username → user_id. username ищется по таблице seen_user."""
    nm = _norm(text)
    if not nm:
        return None
    val, is_id = nm
    if is_id:
        return int(val)
    with Session(_engine) as db:
        u = db.exec(select(SeenUser).where(SeenUser.username == val)).first()
        return u.user_id if u else None


# ----------------------------- аналитика по юзеру -----------------------------
async def user_report(uid: int) -> dict:
    """Сводка по отправителю: отправки, переходы, дубли адресов, разбивка по почтам (как в STATS)."""
    from bot.handlers.admin import stats as S
    from common.models.send_log import SendLog
    from common.models.mailbox import Mailbox
    from common.models.rubric import Rubric, RubricMailbox

    clicked = await S._clicked_tracks()
    with Session(_engine) as db:
        rows = db.exec(select(SendLog).where(SendLog.user_id == uid)).all()
        providers = [(m.name, m.domains) for m in db.exec(select(Mailbox)).all()]
        rub_names = {r.id: (r.name or "").lower() for r in db.exec(select(Rubric)).all()}
        rubric_tokens = [rm.mailbox for rm in db.exec(select(RubricMailbox)).all()
                         if rm.mailbox and rub_names.get(rm.rubric_id) not in ("other", "black")]
    prov_map = S._build_providers(providers)
    toks = " ".join(rubric_tokens)
    tracked = S._tracked_providers(prov_map, toks)

    ok_rows = [r for r in rows if r.status == "ok"]
    email_counts = Counter((r.email or "").strip().lower() for r in ok_rows if r.email)
    dup_addrs = sum(1 for c in email_counts.values() if c >= 2)
    dup_extra = sum(c - 1 for c in email_counts.values() if c >= 2)
    clicks = len({r.track for r in ok_rows if r.track and r.track in clicked})
    buckets = Counter(S._mailbox_bucket(r.email, prov_map, tracked, toks) for r in ok_rows)
    return {
        "sends_total": len(rows),
        "sends_ok": len(ok_rows),
        "uniq_addrs": len(email_counts),
        "dup_addrs": dup_addrs,
        "dup_extra": dup_extra,
        "clicks": clicks,
        "buckets": buckets,
    }
