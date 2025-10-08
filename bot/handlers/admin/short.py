from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from bot.keyboards.short_menu import (
    kb_short_root,
    kb_short_settings_scope,
    kb_short_target_picker,
    kb_short_provider_picker,
)
from bot.services.short_policy import set_binding as set_short_binding

router = Router(name="short-admin")


# --- Корень SHORT ---
@router.callback_query(F.data == "short:root")
async def show_short_root(call: CallbackQuery):
    try:
        await call.message.edit_text("🔗 Short menu", reply_markup=kb_short_root())
    except TelegramBadRequest:
        await call.answer()



# --- SETTINGS ROOT (выбор TARGET сперва: gmail/main) ---
@router.callback_query(F.data == "short:settings")
async def show_short_settings_target(call: CallbackQuery):
    try:
        # сначала даем выбрать target (gmail/main). scope выберем на следующем шаге
        await call.message.edit_text(
            "⚙️ Settings – choose target",
            reply_markup=kb_short_target_picker(None),  # кнопки с payload только target
        )
    except TelegramBadRequest:
        await call.answer()


# --- TARGET PICKER ---
@router.callback_query(F.data.startswith("short:settings:scope:"))
async def show_short_targets(call: CallbackQuery):
    scope = call.data.split(":")[-1]
    try:
        await call.message.edit_text(
            f"🎯 Choose target (scope={scope})",
            reply_markup=kb_short_target_picker(scope),
        )
    except TelegramBadRequest:
        await call.answer()


# --- PROVIDER PICKER ---
@router.callback_query(F.data.startswith("short:settings:target:"))
async def show_short_providers(call: CallbackQuery):
    _, _, _, payload = call.data.split(":", 3)
    parts = payload.split("|")
    # Если пришёл только target без scope — покажем выбор scope, чтобы не зависать с ошибкой
    if len(parts) < 2 or not parts[1]:
        try:
            await call.message.edit_text(
                "🌍 Choose scope",
                reply_markup=kb_short_settings_scope(),
            )
        except TelegramBadRequest:
            await call.answer()
        return
    target, scope = parts[0], parts[1]
    country = parts[2] if len(parts) > 2 and parts[2] else None
    service = parts[3] if len(parts) > 3 and parts[3] else None

    try:
        await call.message.edit_text(
            f"🔗 Choose provider (target={target}, scope={scope})",
            reply_markup=kb_short_provider_picker(scope=scope, target=target, country=country, service=service),
        )
    except TelegramBadRequest:
        await call.answer()


# --- ВЫБОР ПРОВАЙДЕРА ---
@router.callback_query(F.data.startswith("short:settings:provider:"))
async def apply_short_provider(call: CallbackQuery):
    # expected payload: "short:settings:provider:{provider}|{target}|{scope}|{country?}|{service?}"
    try:
        _, _, _, payload = call.data.split(":", 3)
        parts = payload.split("|")
        provider = parts[0] if len(parts) > 0 else None
        target   = parts[1] if len(parts) > 1 else None
        scope    = parts[2] if len(parts) > 2 else None
        country  = parts[3] if len(parts) > 3 and parts[3] else None
        service  = parts[4] if len(parts) > 4 and parts[4] else None

        if not (provider and target and scope):
            await call.answer("Некорректные данные кнопки", show_alert=True)
            return

        # persist choice
        await set_short_binding(scope=scope, target=target, provider=provider, country=country, service=service)

        # confirm and show root again
        summary = [
            "✅ Сокращалка выбрана:",
            f"• Provider: <b>{provider}</b>",
            f"• Target: <b>{target}</b>",
            f"• Scope: <b>{scope}</b>",
        ]
        if country:
            summary.append(f"• Country: <b>{country}</b>")
        if service:
            summary.append(f"• Service: <b>{service}</b>")

        try:
            await call.message.edit_text("\n".join(summary), reply_markup=kb_short_root(), parse_mode="HTML")
        except TelegramBadRequest:
            await call.message.answer("\n".join(summary), reply_markup=kb_short_root(), parse_mode="HTML")
    except TelegramBadRequest:
        await call.answer()
    except Exception as e:
        # bubble error to UI to understand what's wrong
        try:
            await call.answer(f"Ошибка: {e}", show_alert=True)
        except TelegramBadRequest:
            pass