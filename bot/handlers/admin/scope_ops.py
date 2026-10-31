"""SRVS → Сервисы: SMTP/SHORT на область (страна × сервис × рубрика, ALL).

Колбэки `sco:*`. Открыть область: `sco:o:{country|*}:{service|*}:{rid|0}`,
дальше всё по id области: `sco:{act}:{scope_id}[:arg]`.
"""
import logging
import re
from html import escape as _esc
from typing import List

from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from sqlmodel import Session, select

from common.db import get_engine
from common.config import settings
from common.models.rubric import Rubric
from common.models.aws import AwsAccount
from common.models.scope import ScopeCfg, ScopeSmtp, ScopeAws
from common.models.smtp import SmtpAccount
from common.security import encrypt_password, decrypt_password
from bot.fsm.admin import ScopeInput
from bot.services import scope as sc

router = Router(name="scope-ops")
log = logging.getLogger("bot")
_engine = get_engine()

SHORT_PROVIDERS = ["n9.cl"]


def _admin(uid: int) -> bool:
    return uid in settings.admin_ids_set


def _btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)


def _back_cb(cfg: ScopeCfg) -> str:
    if cfg.country == "*":
        return "svc:root"
    if cfg.service == "*":
        return f"svc:c:{cfg.country}"
    return f"svc:s:{cfg.country}:{cfg.service}"


def _limtxt(sid: int, kind: str, a, cfg: ScopeCfg | None = None) -> str:
    """Счётчик/лимит аккаунта в ЭТОЙ области (per-scope счётчик, не глобальный)."""
    return f"{sc.scope_count(sid, kind, a.id)}/{sc.limit_for(a, cfg) or '∞'}"


def _auto_txt(cfg: ScopeCfg) -> str:
    lim = f"каждые {cfg.switch_limit}" if cfg.switch_limit else "по лимитам аккаунтов"
    base = f"{'вкл' if cfg.autoswitch else 'выкл'} · {lim}"
    return base + (" · по кругу" if cfg.wrap else "")


# ---------- карточка ----------
def ops_text(scope_id: int) -> str:
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, scope_id)
        accs = sc.base_accounts(db, scope_id)
        aws = sc.base_aws(db, scope_id)
        cur = next(((k, a) for k, a in sc.entries(db, scope_id) if sc.key(k, a) == cfg.current_key), None)
    if cfg.short == "off":
        short = "выкл"
    elif cfg.short and cfg.short.startswith("dom:"):
        from common.models.short_domain import ShortDomain
        with Session(_engine) as db:
            dd = db.get(ShortDomain, int(cfg.short.split(":", 1)[1]))
        short = f"🌐 {dd.domain}" + ("" if (dd and dd.status == "ready") else " (не готов)") if dd else "🌐 (удалён)"
    else:
        short = cfg.short or "не задан (как у ALL / выше)"
    base = f"{len(accs)} SMTP, {len(aws)} AWS" if (accs or aws) else "пусто (как у ALL / выше)"
    return (
        f"• База: {base}\n"
        + (f"• Текущий: <code>{_esc(sc.acc_email(*cur))}</code> ({'AWS' if cur[0] == 'aws' else 'SMTP'})\n" if cur else "")
        + f"• Автосмена: {_auto_txt(cfg)}\n"
        f"• SHORT: {_esc(short)}\n"
        "<i>Приоритет — у настроенного последним (ALL или конкретное).</i>"
    )


def ops_rows(scope_id: int) -> List[List[InlineKeyboardButton]]:
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, scope_id)
    i = scope_id
    # без дублей (п.63): «Загрузить» = «+ SMTP», «Очистка» = «🗑 SMTP», «+ SHORT» = «⚙️ SHORT»
    # вели на те же экраны; принуд. смена — внутри «Автосмена»
    return [
        [_btn(f"🔁 Автосмена: {_auto_txt(cfg)}", f"sco:auto:{i}")],
        # слева SMTP, справа SHORT; выгрузка — в «Автосмене»;
        # ➕ SHORT = сразу ввод нового домена (привяжется к области), ⚙️ SHORT = какую сокращалку ставить
        [_btn("➕ SMTP", f"sco:up:{i}"), _btn("➕ SHORT", f"sdom:add:{i}")],
        [_btn("⚙️ SMTP", f"sco:accs:{i}"), _btn("⚙️ SHORT", f"sco:sh:{i}")],
        [_btn("🗑 SMTP", f"sco:clr:{i}"), _btn("🗑 SHORT", f"sco:shdel:{i}")],
    ]


def render(scope_id: int):
    """Карточка области: конкретный сервис (любая рубрика или ALL рубрик) → карточка сервиса
    (имя/тайтл/шаблон + ops), иначе (страна/ALL стран) только ops."""
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, scope_id)
        rname = None
        if cfg.rubric_id:
            r = db.get(Rubric, cfg.rubric_id)
            rname = r.name if r else None
    if cfg.country != "*" and cfg.service != "*":
        from bot.handlers.admin.rubrics import _svc_cfg_view
        return _svc_cfg_view(cfg.country, cfg.service, cfg.rubric_id, "")
    text = f"🧩 <b>{_esc(sc.label(cfg, rname))}</b>\n\n" + ops_text(scope_id)
    rows = ops_rows(scope_id)
    rows.append([_btn("⬅️ Назад", _back_cb(cfg))])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _show(cq: CallbackQuery, scope_id: int, note: str | None = None) -> None:
    text, kb = render(scope_id)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer(note or None)


def _scope_id(cq: CallbackQuery) -> int:
    return int(cq.data.split(":")[2])


@router.callback_query(F.data.startswith("sco:o:"))
async def sco_open(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    _, _, country, service, rid = cq.data.split(":", 4)
    with Session(_engine) as db:
        cfg = sc.get_or_create(db, country, service, int(rid))
    await _show(cq, cfg.id)


@router.callback_query(F.data.startswith("sco:card:"))
async def sco_card(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await _show(cq, _scope_id(cq))


# ---------- автосмена / принудительная смена ----------
def _auto_view(sid: int):
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, sid)
        cur = next(((k, a) for k, a in sc.entries(db, sid) if sc.key(k, a) == cfg.current_key), None)
    cur_txt = (f"\nТекущий: <code>{_esc(sc.acc_email(*cur))}</code> ({'AWS API' if cur[0] == 'aws' else 'SMTP'})"
               if cur else "")
    text = f"🔁 Автосмена: <b>{_auto_txt(cfg)}</b>{cur_txt}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🔴 Выключить" if cfg.autoswitch else "🟢 Включить", f"sco:autotog:{sid}")],
        [_btn(f"📈 Лимит автосмены: {cfg.switch_limit or '—'}", f"sco:autolim:{sid}")],
        [_btn(f"🔄 По кругу: {'вкл 🟢' if cfg.wrap else 'выкл 🔴'}", f"sco:wraptog:{sid}")],
        [_btn("⏭ Принуд. смена", f"sco:forced:{sid}")],
        [_btn("📤 Выгрузка SMTP", f"sco:dl:{sid}")],
        [_btn("⬅️ Назад", f"sco:card:{sid}")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("sco:auto:"))
async def sco_auto(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    text, kb = _auto_view(_scope_id(cq))
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("sco:autotog:"))
async def sco_auto_toggle(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, sid)
        cfg.autoswitch = not cfg.autoswitch
        db.add(cfg)
        db.commit()
        on = cfg.autoswitch
    text, kb = _auto_view(sid)
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer(f"Автосмена {'вкл' if on else 'выкл'}")


@router.callback_query(F.data.startswith("sco:wraptog:"))
async def sco_wrap_toggle(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, sid)
        cfg.wrap = not cfg.wrap
        db.add(cfg)
        db.commit()
        on = cfg.wrap
    text, kb = _auto_view(sid)
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer(f"По кругу {'вкл' if on else 'выкл'}")


@router.callback_query(F.data.startswith("sco:autolim:"))
async def sco_auto_limit(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    await state.set_state(ScopeInput.limit)
    await state.update_data(scope_id=sid, acc_id=-1)
    await cq.message.answer("📈 Лимит автосмены — сколько писем с одного аккаунта до смены (0 = по лимитам аккаунтов):")
    await cq.answer()


@router.callback_query(F.data.startswith("sco:forced:"))
async def sco_forced(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, sid)
        if not sc.entries(db, sid):
            await cq.answer("База пуста", show_alert=True)
            return
        nxt = sc.current_entry(db, cfg, advance=True)
    if nxt is None:
        await cq.answer("Нет другого доступного аккаунта в базе", show_alert=True)
        return
    # кнопка теперь на экране «Автосмена» — остаёмся на нём (виден новый текущий)
    text, kb = _auto_view(sid)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await cq.answer(f"Сменили на {sc.acc_email(*nxt)}")


# ---------- SHORT ----------
@router.callback_query(F.data.startswith("sco:sh:"))
async def sco_short(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    from common.models.short_domain import ShortDomain
    with Session(_engine) as db:
        cur = db.get(ScopeCfg, sid).short
        # все личные домены, и поднимающиеся тоже (п.94): пока не Work, ссылки идут полными
        doms = db.exec(select(ShortDomain).order_by(ShortDomain.id)).all()
    # только ✅ рабочий / ❌ ещё не поднят (п.98); текущий выбор — в заголовке
    rows = [[_btn(f"✅ {p}", f"sco:shset:{sid}:{p}")] for p in SHORT_PROVIDERS]
    names = {p: p for p in SHORT_PROVIDERS} | {"off": "не сокращать"}
    for d in doms:
        rows.append([_btn(f"{'✅' if d.status == 'ready' else '❌'} {d.domain}", f"sco:shdom:{sid}:{d.id}")])
        names[f"dom:{d.id}"] = d.domain
    rows.append([_btn("Не сокращать", f"sco:shset:{sid}:off")])
    rows.append([_btn("⬅️ Назад", f"sco:card:{sid}")])
    now = f"Сейчас: <b>{_esc(names.get(cur, cur))}</b>" if cur else "Сейчас: как у ALL / выше"
    await cq.message.edit_text(f"⚙️ SHORT для области\n{now}\n"
                               "<i>❌ — домен ещё поднимается, до ✅ ссылки уходят полными.</i>",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


@router.callback_query(F.data.startswith("sco:shdom:"))
async def sco_short_domain(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, sid, did = cq.data.split(":", 3)
    sid = int(sid)
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, sid)
        cfg.short, cfg.short_at = f"dom:{int(did)}", sc.now_iso()
        db.add(cfg)
        db.commit()
    await _show(cq, sid, "SHORT (личный домен) сохранён")


@router.callback_query(F.data.startswith("sco:shset:"))
async def sco_short_set(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, sid, val = cq.data.split(":", 3)
    sid = int(sid)
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, sid)
        cfg.short, cfg.short_at = val, sc.now_iso()
        db.add(cfg)
        db.commit()
    await _show(cq, sid, "SHORT сохранён")


# ---------- 🗑 SHORT: удаление личных short-доменов (как 🗑 SMTP) ----------
_SHD_NOTE = "<i>Удаляются из бота; зона в Cloudflare и сервис на ноде остаются.</i>"


@router.callback_query(F.data.startswith("sco:shdel:"))
async def sco_short_del(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🧽 Выборочно", f"sco:shdpick:{sid}"), _btn("🗑 Неиспользуемые", f"sco:shdunused:{sid}")],
        [_btn("💥 Все", f"sco:shdall:{sid}")],
        [_btn("↩️ Сбросить SHORT области", f"sco:shreset:{sid}")],
        [_btn("⬅️ Назад", f"sco:card:{sid}")],
    ])
    await cq.message.edit_text(
        "🗑 Удалить short-домены:\n<i>Неиспользуемые = не выбраны SHORT ни в одной области.</i>\n"
        "<i>«Сбросить» — только убрать SHORT у этой области (будет как у ALL / выше).</i>", reply_markup=kb)
    await cq.answer()


def _kb_shd_pick(sid: int) -> InlineKeyboardMarkup:
    from common.models.short_domain import ShortDomain
    with Session(_engine) as db:
        doms = db.exec(select(ShortDomain).order_by(ShortDomain.id)).all()
    rows = [[_btn(f"🗑 {d.domain} ({d.status})", f"sco:shdx:{sid}:{d.id}")] for d in doms]
    rows = rows or [[_btn("Доменов нет", f"sco:card:{sid}")]]
    rows.append([_btn("⬅️ Назад", f"sco:shdel:{sid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("sco:shdpick:"))
async def sco_short_del_pick(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    await cq.message.edit_text(f"🧽 Нажмите на домен, чтобы удалить:\n{_SHD_NOTE}", reply_markup=_kb_shd_pick(sid))
    await cq.answer()


@router.callback_query(F.data.startswith("sco:shdx:"))
async def sco_short_del_one(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    from bot.handlers.admin.short_domains import delete_domains
    _, _, sid, did = cq.data.split(":", 3)
    sid = int(sid)
    with Session(_engine) as db:
        delete_domains(db, [int(did)])
    await cq.message.edit_text(f"🧽 Нажмите на домен, чтобы удалить:\n{_SHD_NOTE}", reply_markup=_kb_shd_pick(sid))
    await cq.answer("Удалён")


@router.callback_query(F.data.startswith("sco:shdunused:"))
async def sco_short_del_unused(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    from common.models.short_domain import ShortDomain
    from bot.handlers.admin.short_domains import delete_domains
    sid = _scope_id(cq)
    with Session(_engine) as db:
        used = {c.short for c in db.exec(select(ScopeCfg).where(ScopeCfg.short.is_not(None))).all()}
        ids = [d.id for d in db.exec(select(ShortDomain)).all() if f"dom:{d.id}" not in used]
        n = delete_domains(db, ids)
    await _show(cq, sid, f"Удалено неиспользуемых: {n}")


@router.callback_query(F.data.startswith("sco:shdall:"))
async def sco_short_del_all(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("✅ Да, удалить всё", f"sco:shdallyes:{sid}"), _btn("❌ Нет", f"sco:shdel:{sid}")],
    ])
    await cq.message.edit_text(f"💥 Удалить ВСЕ личные short-домены из бота?\n{_SHD_NOTE}", reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("sco:shdallyes:"))
async def sco_short_del_all_yes(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    from common.models.short_domain import ShortDomain
    from bot.handlers.admin.short_domains import delete_domains
    sid = _scope_id(cq)
    with Session(_engine) as db:
        n = delete_domains(db, [d.id for d in db.exec(select(ShortDomain)).all()])
    await _show(cq, sid, f"Удалено: {n}")


@router.callback_query(F.data.startswith("sco:shreset:"))
async def sco_short_reset(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, sid)
        cfg.short, cfg.short_at = None, None
        db.add(cfg)
        db.commit()
    await _show(cq, sid, "SHORT области сброшен")


# ---------- SMTP-база: загрузка ----------
@router.callback_query(F.data.startswith("sco:up:"))
async def sco_upload(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    await state.set_state(ScopeInput.smtp)
    await state.update_data(scope_id=sid)
    await cq.message.answer(
        "📥 Пришлите строки или .txt (по одной на строку, можно вперемешку):\n"
        "• SMTP: <code>host:port:user:pass</code> (user = email) или <code>host:port:user:email:pass</code>\n"
        "• AWS SES: <code>region:accesskey:secretkey:emailfrom</code> (напр. eu-west-1:…)\n"
        "Новые SMTP проверяются тестовой отправкой, AWS — запросом квоты; уже известные просто добавляются в базу.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Отмена", f"sco:card:{sid}")]]),
    )
    await cq.answer()


_REGION = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d+$")


def _mask(ln: str) -> str:
    """Строка для отчёта без секретов: первые 2 поля + …"""
    parts = ln.split(":")
    return ":".join(parts[:2]) + (":…" if len(parts) > 2 else "")


def parse_smtp_line(ln: str) -> tuple[str, int, str, str, str]:
    """host:port:user:email:pass (и прочие формы раздела SMTP) или host:port:user:pass (email = user)."""
    from bot.handlers.admin.smtp import _parse_smtp_line_flexible
    try:
        return _parse_smtp_line_flexible(ln)
    except ValueError:
        parts = ln.strip().split(":")
        if len(parts) >= 4 and parts[1].strip().isdigit():
            host, port, user = parts[0].strip(), int(parts[1]), parts[2].strip()
            pw = ":".join(parts[3:]).strip()
            if "@" not in user:
                raise ValueError("в host:port:user:pass логин должен быть email (иначе host:port:user:email:pass)")
            return host, port, user, user, pw
        raise


async def _ingest_aws(db: Session, scope_id: int, ln: str) -> bool:
    from bot.services.aws_ses import parse_aws_line, check_aws
    region, access, secret, email = parse_aws_line(ln)
    acc = db.exec(
        select(AwsAccount).where(
            (AwsAccount.region == region) & (AwsAccount.access_key == access) & (AwsAccount.email_from == email)
        )
    ).first()
    if acc is None:
        acc = AwsAccount(region=region, access_key=access, secret_key_enc=encrypt_password(secret), email_from=email)
        ok, info = await check_aws(acc)
        if not ok:
            raise ValueError(f"SES: {info[:150]}")
        db.add(acc)
        db.commit()
        db.refresh(acc)
    if db.exec(select(ScopeAws).where((ScopeAws.scope_id == scope_id) & (ScopeAws.aws_id == acc.id))).first():
        return False
    db.add(ScopeAws(scope_id=scope_id, aws_id=acc.id))
    return True


async def _ingest_smtp(db: Session, scope_id: int, ln: str) -> bool:
    from bot.services.mailer import probe_smtp
    host, port, user, email, password = parse_smtp_line(ln)
    acc = db.exec(
        select(SmtpAccount).where(
            (SmtpAccount.host == host) & (SmtpAccount.port == port)
            & (SmtpAccount.login == user) & (SmtpAccount.email == email)
        )
    ).first()
    if acc is None:
        probe = await probe_smtp(host=host, port=port, login=user, sender_email=email, password=password)
        if not probe.ok:
            raise ValueError(probe.error or probe.smtp_response or "probe failed")
        # kind='scope': такие SMTP не попадают в общий фолбэк групп gmail/main
        acc = SmtpAccount(kind="scope", host=host, port=port, login=user, email=email,
                          password_enc=encrypt_password(password), pool="scope")
        db.add(acc)
        db.commit()
        db.refresh(acc)
    if db.exec(select(ScopeSmtp).where((ScopeSmtp.scope_id == scope_id) & (ScopeSmtp.smtp_id == acc.id))).first():
        return False
    db.add(ScopeSmtp(scope_id=scope_id, smtp_id=acc.id))
    return True


async def ingest(scope_id: int, lines: list[str]) -> tuple[int, list[str]]:
    """Строки SMTP и AWS вперемешку → в базу области. AWS узнаётся по региону в начале (eu-west-1:…)."""
    added, errors = 0, []
    with Session(_engine) as db:
        for ln in lines:
            is_aws = bool(_REGION.match(ln.split(":", 1)[0].strip()))
            try:
                if await (_ingest_aws if is_aws else _ingest_smtp)(db, scope_id, ln):
                    db.commit()  # сразу: rollback на следующей битой строке не должен её откатить
                    added += 1
            except Exception as e:
                db.rollback()
                errors.append(f"{_mask(ln)} -> {e}")
        if added:
            cfg = db.get(ScopeCfg, scope_id)
            cfg.smtp_at = sc.now_iso()
            db.add(cfg)
        db.commit()
    return added, errors


async def _after_ingest(m: Message, state: FSMContext, lines: list[str]) -> None:
    sid = (await state.get_data()).get("scope_id")
    await state.clear()
    if not sid:
        await m.answer("Что-то пошло не так, откройте область заново.")
        return
    await m.answer(f"⏳ Проверяю {len(lines)} шт…")
    added, errors = await ingest(sid, lines)
    msg = f"✅ Добавлено в базу: {added} из {len(lines)}"
    if errors:
        msg += "\n\n❌ Ошибки:\n" + "\n".join(f"<code>{_esc(e)}</code>" for e in errors[:15])
    text, kb = render(sid)
    await m.answer(msg)
    await m.answer(text, reply_markup=kb)


@router.message(ScopeInput.smtp, F.text)
async def sco_upload_text(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    lines = [ln.strip() for ln in (m.text or "").splitlines() if ln.strip()]
    await _after_ingest(m, state, lines)


@router.message(ScopeInput.smtp, F.document)
async def sco_upload_file(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    if not (m.document.file_name or "").lower().endswith(".txt"):
        await m.answer("Нужен .txt файл.")
        return
    file = await m.bot.get_file(m.document.file_id)
    content = await m.bot.download_file(file.file_path)
    text = content.read().decode("utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    await _after_ingest(m, state, lines)


# ---------- SMTP-база: скачать ----------
@router.callback_query(F.data.startswith("sco:dl:"))
async def sco_download(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    with Session(_engine) as db:
        accs = sc.base_accounts(db, sid)
        aws = sc.base_aws(db, sid)
    if not accs and not aws:
        await cq.answer("База пуста", show_alert=True)
        return
    body = "\n".join(
        [f"{a.host}:{a.port}:{a.login}:{a.email}:{decrypt_password(a.password_enc or '')}" for a in accs]
        + [f"{a.region}:{a.access_key}:{decrypt_password(a.secret_key_enc or '')}:{a.email_from}" for a in aws]
    ) + "\n"
    await cq.message.answer_document(BufferedInputFile(body.encode(), filename=f"base_scope_{sid}.txt"),
                                     caption=f"База области: {len(accs)} SMTP, {len(aws)} AWS")
    await cq.answer()


# ---------- SMTP-база: очистка ----------
def _unlink(db: Session, scope_id: int, smtp_ids: list[int], aws_ids: list[int] = ()) -> int:
    n = 0
    for aws_id in aws_ids:
        link = db.exec(select(ScopeAws).where((ScopeAws.scope_id == scope_id) & (ScopeAws.aws_id == aws_id))).first()
        if not link:
            continue
        db.delete(link)
        n += 1
        db.flush()
        # AWS живёт только в базах областей — больше нигде не нужен → удаляем
        acc = db.get(AwsAccount, aws_id)
        if acc and not db.exec(select(ScopeAws.id).where(ScopeAws.aws_id == aws_id)).first():
            db.delete(acc)
    for smtp_id in smtp_ids:
        link = db.exec(
            select(ScopeSmtp).where((ScopeSmtp.scope_id == scope_id) & (ScopeSmtp.smtp_id == smtp_id))
        ).first()
        if not link:
            continue
        db.delete(link)
        n += 1
        db.flush()
        # SMTP, заведённый только под области и больше нигде не нужный — удаляем целиком
        acc = db.get(SmtpAccount, smtp_id)
        still = db.exec(select(ScopeSmtp.id).where(ScopeSmtp.smtp_id == smtp_id)).first()
        if acc and acc.pool == "scope" and not still:
            db.delete(acc)
    cfg = db.get(ScopeCfg, scope_id)
    if n:
        left = db.exec(select(ScopeSmtp.id).where(ScopeSmtp.scope_id == scope_id)).first() \
            or db.exec(select(ScopeAws.id).where(ScopeAws.scope_id == scope_id)).first()
        # Очистка/удаление аккаунта — не переконфигурация: НЕ обновляем smtp_at
        # (иначе maintenance делал бы эту область победителем над более специфичными).
        # Базу опустошили — снимаем smtp_at, чтобы она перестала быть победителем.
        if not left:
            cfg.smtp_at = None
        removed = {f"smtp:{i}" for i in smtp_ids} | {f"aws:{i}" for i in aws_ids}
        if cfg.current_key in removed:
            cfg.current_key = None
        db.add(cfg)
    db.commit()
    return n


@router.callback_query(F.data.startswith("sco:clr:"))
async def sco_clear(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🧽 Выборочно", f"sco:clrpick:{sid}"), _btn("🗑 Неиспользуемые", f"sco:clrunused:{sid}")],
        [_btn("💥 Все", f"sco:clrall:{sid}")],
        [_btn("⬅️ Назад", f"sco:card:{sid}")],
    ])
    await cq.message.edit_text("🧹 Очистить базу области (SMTP + AWS):\n<i>Неиспользуемые = 0 отправок.</i>", reply_markup=kb)
    await cq.answer()


def _kb_pick(sid: int) -> InlineKeyboardMarkup:
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, sid)
        accs = sc.base_accounts(db, sid)
        aws = sc.base_aws(db, sid)
    rows = [[_btn(f"🗑 {a.email} — {_limtxt(sid, 'smtp', a, cfg)}", f"sco:clrdel:{sid}:{a.id}")] for a in accs]
    rows += [[_btn(f"🗑 ☁️ {a.email_from} ({a.region})", f"sco:clrdelaws:{sid}:{a.id}")] for a in aws]
    rows = rows or [[_btn("База пуста", f"sco:card:{sid}")]]
    rows.append([_btn("⬅️ Назад", f"sco:clr:{sid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("sco:clrpick:"))
async def sco_clear_pick(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    await cq.message.edit_text("🧽 Нажмите на SMTP/AWS, чтобы убрать из базы:", reply_markup=_kb_pick(sid))
    await cq.answer()


@router.callback_query(F.data.startswith("sco:clrdel:") | F.data.startswith("sco:clrdelaws:"))
async def sco_clear_del(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, kind, sid, acc_id = cq.data.split(":", 3)
    sid = int(sid)
    with Session(_engine) as db:
        if kind == "clrdelaws":
            _unlink(db, sid, [], [int(acc_id)])
        else:
            _unlink(db, sid, [int(acc_id)])
    await cq.message.edit_text("🧽 Нажмите на SMTP/AWS, чтобы убрать из базы:", reply_markup=_kb_pick(sid))
    await cq.answer("Убран")


@router.callback_query(F.data.startswith("sco:clrunused:"))
async def sco_clear_unused(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    with Session(_engine) as db:
        ids = [a.id for a in sc.base_accounts(db, sid) if not sc.scope_count(sid, "smtp", a.id)]
        aws_ids = [a.id for a in sc.base_aws(db, sid) if not sc.scope_count(sid, "aws", a.id)]
        n = _unlink(db, sid, ids, aws_ids)
    await _show(cq, sid, f"Убрано неиспользуемых: {n}")


@router.callback_query(F.data.startswith("sco:clrall:"))
async def sco_clear_all(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("✅ Да, очистить всё", f"sco:clrallyes:{sid}"), _btn("❌ Нет", f"sco:clr:{sid}")],
    ])
    await cq.message.edit_text("💥 Убрать ВСЕ аккаунты (SMTP и AWS) из базы этой области?", reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("sco:clrallyes:"))
async def sco_clear_all_yes(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid = _scope_id(cq)
    with Session(_engine) as db:
        n = _unlink(db, sid, [a.id for a in sc.base_accounts(db, sid)], [a.id for a in sc.base_aws(db, sid)])
    await _show(cq, sid, f"Очищено: {n}")


# ---------- SMTP: настроить (аккаунты базы) ----------
def _kb_accs(sid: int) -> InlineKeyboardMarkup:
    with Session(_engine) as db:
        cfg = db.get(ScopeCfg, sid)
        ents = sc.entries(db, sid)
    rows = []
    for k, a in ents:
        exh = sc._exhausted(sc.scope_count(sid, k, a.id), a, cfg)
        mark = "🔴" if (not a.is_active or exh) else "🟢"
        cur = " ⬅️" if sc.key(k, a) == cfg.current_key else ""
        name = a.email if k == "smtp" else f"☁️ {a.email_from} ({a.region})"
        cb = f"sco:acc:{sid}:{a.id}" if k == "smtp" else f"sco:aws:{sid}:{a.id}"
        rows.append([_btn(f"{mark} {name} {_limtxt(sid, k, a, cfg)}{cur}", cb)])
    if not ents:
        rows.append([_btn("➕ База пуста — загрузить SMTP", f"sco:up:{sid}")])
    rows.append([_btn(f"🔁 Автосмена: {_auto_txt(cfg)}", f"sco:auto:{sid}")])
    rows.append([_btn("⬅️ Назад", f"sco:card:{sid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("sco:accs:"))
async def sco_accounts(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    sid = _scope_id(cq)
    await cq.message.edit_text("⚙️ База — SMTP и AWS (⬅️ = текущий):", reply_markup=_kb_accs(sid))
    await cq.answer()


def _acc_view(sid: int, acc_id: int):
    with Session(_engine) as db:
        a = db.get(SmtpAccount, acc_id)
        cfg = db.get(ScopeCfg, sid)
    if a is None:
        return None, None
    note = " <i>(лимит автосмены области)</i>" if cfg and cfg.switch_limit else ""
    text = (
        f"📮 <code>{_esc(a.email)}</code>\n"
        f"• {_esc(a.host)}:{a.port}\n"
        f"• Отправлено/лимит: {_limtxt(sid, 'smtp', a, cfg)}{note}\n"
        f"• Статус: {'🟢 вкл' if a.is_active else '🔴 выкл'}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("📈 Лимит", f"sco:lim:{sid}:{a.id}"), _btn("♻️ Сброс счётчика", f"sco:reset:{sid}:{a.id}")],
        [_btn("🔴 Выключить" if a.is_active else "🟢 Включить", f"sco:tog:{sid}:{a.id}")],
        [_btn("🗑 Убрать из базы", f"sco:accdel:{sid}:{a.id}")],
        [_btn("⬅️ Список", f"sco:accs:{sid}")],
    ])
    return text, kb


def _ids(cq: CallbackQuery) -> tuple[int, int]:
    _, _, sid, acc_id = cq.data.split(":", 3)
    return int(sid), int(acc_id)


async def _show_acc(cq: CallbackQuery, sid: int, acc_id: int, note: str | None = None):
    text, kb = _acc_view(sid, acc_id)
    if text is None:
        await cq.answer("SMTP не найден", show_alert=True)
        return
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer(note or None)


@router.callback_query(F.data.startswith("sco:acc:"))
async def sco_account(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await _show_acc(cq, *_ids(cq))


@router.callback_query(F.data.startswith("sco:reset:") | F.data.startswith("sco:tog:"))
async def sco_account_flip(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid, acc_id = _ids(cq)
    if cq.data.startswith("sco:reset:"):
        sc.reset_scope_count(sid, "smtp", acc_id)  # сброс счётчика именно этой области
        await _show_acc(cq, sid, acc_id, "Счётчик сброшен")
        return
    with Session(_engine) as db:
        a = db.get(SmtpAccount, acc_id)
        if a:
            a.is_active = not a.is_active
            db.add(a)
            db.commit()
    await _show_acc(cq, sid, acc_id, "Готово")


@router.callback_query(F.data.startswith("sco:accdel:"))
async def sco_account_del(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid, acc_id = _ids(cq)
    with Session(_engine) as db:
        _unlink(db, sid, [acc_id])
    await cq.message.edit_text("⚙️ База — SMTP и AWS (⬅️ = текущий):", reply_markup=_kb_accs(sid))
    await cq.answer("Убран")


@router.callback_query(F.data.startswith("sco:lim:") | F.data.startswith("sco:awslim:"))
async def sco_limit(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid, acc_id = _ids(cq)
    kind = "aws" if cq.data.startswith("sco:awslim:") else "smtp"
    await state.set_state(ScopeInput.limit)
    await state.update_data(scope_id=sid, acc_id=acc_id, kind=kind)
    await cq.message.answer("📈 Введите лимит отправок этому аккаунту (0 = без лимита):")
    await cq.answer()


@router.message(ScopeInput.limit, F.text)
async def sco_limit_value(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    val = (m.text or "").strip()
    if not val.isdigit():
        await m.answer("Нужно целое число ≥ 0. Ещё раз:")
        return
    data = await state.get_data()
    await state.clear()
    sid, acc_id = data.get("scope_id"), data.get("acc_id")
    if acc_id == -1:
        with Session(_engine) as db:
            cfg = db.get(ScopeCfg, sid)
            cfg.switch_limit = int(val)
            db.add(cfg)
            db.commit()
        text, kb = _auto_view(sid)
        await m.answer(f"✅ Лимит автосмены: {val if int(val) else 'по лимитам аккаунтов'}\n\n{text}", reply_markup=kb)
        return
    with Session(_engine) as db:
        a = db.get(AwsAccount if data.get("kind") == "aws" else SmtpAccount, acc_id)
        if a:
            a.send_limit = int(val)
            db.add(a)
            db.commit()
    await m.answer(f"✅ Лимит {val} сохранён.\n\n⚙️ База — SMTP и AWS (⬅️ = текущий):", reply_markup=_kb_accs(sid))


# ---------- AWS SES в базе ----------
def _aws_view(sid: int, aws_id: int):
    with Session(_engine) as db:
        a = db.get(AwsAccount, aws_id)
        cfg = db.get(ScopeCfg, sid)
    if a is None:
        return None, None
    note = " <i>(лимит автосмены области)</i>" if cfg and cfg.switch_limit else ""
    text = (
        f"☁️ AWS SES <code>{_esc(a.email_from)}</code>\n"
        f"• Регион: {_esc(a.region)} (шлётся через ноду с этим регионом, иначе с бота)\n"
        f"• Отправлено/лимит: {_limtxt(sid, 'aws', a, cfg)}{note}\n"
        f"• Статус: {'🟢 вкл' if a.is_active else '🔴 выкл'}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("📈 Лимит", f"sco:awslim:{sid}:{a.id}"), _btn("♻️ Сброс счётчика", f"sco:awsreset:{sid}:{a.id}")],
        [_btn("🧪 Тест квоты", f"sco:awstest:{sid}:{a.id}"),
         _btn("🔴 Выключить" if a.is_active else "🟢 Включить", f"sco:awstog:{sid}:{a.id}")],
        [_btn("🗑 Убрать из базы", f"sco:awsdel:{sid}:{a.id}")],
        [_btn("⬅️ Список", f"sco:accs:{sid}")],
    ])
    return text, kb


async def _show_aws(cq: CallbackQuery, sid: int, aws_id: int, note: str | None = None):
    text, kb = _aws_view(sid, aws_id)
    if text is None:
        await cq.answer("AWS не найден", show_alert=True)
        return
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer(note or None)


@router.callback_query(F.data.startswith("sco:aws:"))
async def sco_aws(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await _show_aws(cq, *_ids(cq))


@router.callback_query(F.data.startswith("sco:awstog:") | F.data.startswith("sco:awsreset:"))
async def sco_aws_toggle(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid, aws_id = _ids(cq)
    if cq.data.startswith("sco:awsreset:"):
        sc.reset_scope_count(sid, "aws", aws_id)
        await _show_aws(cq, sid, aws_id, "Счётчик сброшен")
        return
    with Session(_engine) as db:
        a = db.get(AwsAccount, aws_id)
        if a:
            a.is_active = not a.is_active
            db.add(a)
            db.commit()
    await _show_aws(cq, sid, aws_id, "Готово")


@router.callback_query(F.data.startswith("sco:awstest:"))
async def sco_aws_test(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid, aws_id = _ids(cq)
    with Session(_engine) as db:
        a = db.get(AwsAccount, aws_id)
    if a is None:
        await cq.answer("AWS не найден", show_alert=True)
        return
    await cq.answer("Проверяю SES…")
    from bot.services.aws_ses import check_aws
    ok, info = await check_aws(a)
    await cq.message.answer(f"{'🟢 OK' if ok else '🔴 FAIL'} — <code>{_esc(a.email_from)}</code>\n<code>{_esc(info[:300])}</code>")


@router.callback_query(F.data.startswith("sco:awsdel:"))
async def sco_aws_del(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    sid, aws_id = _ids(cq)
    with Session(_engine) as db:
        _unlink(db, sid, [], [aws_id])
    await cq.message.edit_text("⚙️ База — SMTP и AWS (⬅️ = текущий):", reply_markup=_kb_accs(sid))
    await cq.answer("Убран")

