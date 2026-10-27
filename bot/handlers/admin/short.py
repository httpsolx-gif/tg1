import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from common.config import settings
from common.db import get_engine
from sqlmodel import Session

from bot.keyboards.short_menu import (
    kb_short_root,
    kb_short_targets,
    kb_short_scope,
    kb_short_countries,
    kb_short_services,
    kb_short_providers,
    kb_short_clear,
    kb_short_back_to,
)
from bot.services.short_policy import set_binding as set_short_binding

try:
    from bot.services.short_policy import clear_short_bindings  # type: ignore
except Exception:  # pragma: no cover
    clear_short_bindings = None  # type: ignore

router = Router(name="short-admin")
log = logging.getLogger("bot")
_engine = get_engine()


def _is_admin(cq: CallbackQuery) -> bool:
    return cq.from_user.id in settings.admin_ids_set


async def _edit(cq: CallbackQuery, text: str, markup) -> None:
    """Безопасно перерисовать текущее сообщение."""
    try:
        await cq.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        # сообщение не изменилось / устарело — просто подтверждаем
        pass
    await cq.answer()


# --- Корень SHORT ---
@router.callback_query(F.data == "short:root")
async def show_short_root(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await _edit(cq, "🔗 Short menu", kb_short_root())


# «Назад» внутри раздела Short → корень раздела
@router.callback_query(F.data == "short:back")
async def short_back_to_root(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await _edit(cq, "🔗 Short menu", kb_short_root())


# --- SETTINGS L1: выбор таргета ---
@router.callback_query(F.data == "short:settings")
async def show_short_targets(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await _edit(cq, "⚙️ Settings — выберите таргет:", kb_short_targets())


# --- SETTINGS L2: выбор области (global/countries) ---
@router.callback_query(F.data.startswith("short:t:"))
async def show_short_scope(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    target = cq.data.split(":", 2)[2]
    await _edit(cq, f"🎯 {target} — выберите область:", kb_short_scope(target))


# --- SETTINGS L3: global → провайдеры, countries → список стран ---
@router.callback_query(F.data.startswith("short:sc:"))
async def show_short_scope_branch(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        _, _, target, scope = cq.data.split(":", 3)
    except ValueError:
        await cq.answer("Неверные данные", show_alert=True)
        return

    if scope == "global":
        await _edit(
            cq,
            f"🔗 {target} → Global — выберите сокращалку:",
            kb_short_providers(target, "global"),
        )
    elif scope == "countries":
        await _edit(cq, f"🗺 {target} → выберите страну:", kb_short_countries(target))
    else:
        await cq.answer("Неизвестная область", show_alert=True)


# --- SETTINGS L3b: услуги выбранной страны ---
@router.callback_query(F.data.startswith("short:c:"))
async def show_short_services(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        _, _, target, code = cq.data.split(":", 3)
    except ValueError:
        await cq.answer("Неверные данные", show_alert=True)
        return
    await _edit(cq, f"🗺 {target} → {code} — выберите сервис:", kb_short_services(target, code))


# --- SETTINGS L4: провайдеры для сервиса ---
@router.callback_query(F.data.startswith("short:sv:"))
async def show_short_service_providers(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        _, _, target, code, service = cq.data.split(":", 4)
    except ValueError:
        await cq.answer("Неверные данные", show_alert=True)
        return
    await _edit(
        cq,
        f"🔗 {target} → {code} → {service} — выберите сокращалку:",
        kb_short_providers(target, "service", country=code, service=service),
    )


# --- Применение выбора провайдера ---
@router.callback_query(F.data.startswith("short:p:"))
async def apply_short_provider(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    # short:p:{target}:{scope}:{country}:{service}:{provider}
    try:
        _, _, target, scope, country, service, provider = cq.data.split(":", 6)
    except ValueError:
        await cq.answer("Неверные данные", show_alert=True)
        return

    country_val = None if country in ("", "-") else country
    service_val = None if service in ("", "-") else service

    try:
        with Session(_engine) as db:
            set_short_binding(
                db,
                adapter=provider,
                service=service_val,
                country=country_val,
                target=target,
            )
    except Exception as e:  # не роняем UI
        log.warning("SHORT set_binding failed: %s", e)
        await cq.answer(f"Ошибка: {e}", show_alert=True)
        return

    summary = ["✅ Сокращалка закреплена:", f"• Provider: <b>{provider}</b>", f"• Target: <b>{target}</b>", f"• Scope: <b>{scope}</b>"]
    if country_val:
        summary.append(f"• Country: <b>{country_val}</b>")
    if service_val:
        summary.append(f"• Service: <b>{service_val}</b>")
    try:
        await cq.message.edit_text("\n".join(summary), reply_markup=kb_short_root())
    except TelegramBadRequest:
        await cq.message.answer("\n".join(summary), reply_markup=kb_short_root())
    await cq.answer("Закреплено")


# --- CLEAR ---
@router.callback_query(F.data == "short:clear")
async def show_short_clear(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await _edit(cq, "🧹 Short → Очистка:", kb_short_clear())


@router.callback_query(F.data == "short:clear:pick")
async def short_clear_pick(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await _edit(
        cq,
        "🗑 Short → Очистка → Выборочно\n\nФункция в разработке.",
        kb_short_back_to("short:clear"),
    )


@router.callback_query(F.data == "short:clear:unused")
async def short_clear_unused(cq: CallbackQuery):
    if not _is_admin(cq):
        await cq.answer("Нет доступа", show_alert=True)
        return
    removed = 0
    if clear_short_bindings is not None:
        try:
            with Session(_engine) as db:
                removed = clear_short_bindings(db, scope="unused")
        except Exception as e:
            log.warning("SHORT clear unused failed: %s", e)
            await cq.answer(f"Ошибка: {e}", show_alert=True)
            return
    await _edit(
        cq,
        f"🧹 Short → Очистка → Неиспользуемые\n\nДеактивировано: <b>{removed}</b>",
        kb_short_back_to("short:clear"),
    )


# «пустышка» для неактивных кнопок (например, «нет сервисов»)
@router.callback_query(F.data == "short:noop")
async def short_noop(cq: CallbackQuery):
    await cq.answer()
