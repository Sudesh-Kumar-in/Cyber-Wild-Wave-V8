import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from telegram.constants import ChatAction, ParseMode

import database as db
from keyboards.main_kb import (
    user_panel_keyboard, admin_main_keyboard, search_submenu_keyboard
)
from keyboards.search_kb import back_to_search_keyboard
from services import api_service
from services.api_service import ERR_NOT_CONFIGURED, ERR_API_FAILED, ERR_NO_DATA
from utils.rate_limiter import check_rate_limit, set_pending_action, get_pending_action, clear_pending_action
from utils.msg_tracker import track, cleanup_all
from utils.formatters import (
    format_number_lookup, format_telegram_lookup, format_aadhaar_lookup,
    format_family_lookup, format_pincode_lookup, format_ifsc_lookup,
    format_vehicle_lookup,
)
from config import FREE_DAILY_SEARCHES, ADMIN_IDS, is_service_configured
import config as cfg

logger = logging.getLogger(__name__)


def _get_main_kb(user_id: int):
    """Return the correct persistent main keyboard for this user."""
    return admin_main_keyboard() if user_id in ADMIN_IDS else user_panel_keyboard()


# ── Keyboard-button → action mapping ─────────────────────────────────────────

SEARCH_KB_MAP = {
    "📱 NUMBER LOOKUP":   "search_number",
    "📞 TELEGRAM LOOKUP": "search_telegram",
    "🪪 AADHAAR LOOKUP":  "search_aadhaar",
    "👨‍👩‍👧‍👦 FAMILY LOOKUP":  "search_family",
    "📍 PINCODE LOOKUP":  "search_pincode",
    "🏦 IFSC LOOKUP":     "search_ifsc",
    "🚗 VEHICLE LOOKUP":  "search_vehicle",
}

# Maps action key → service key used by is_service_configured()
ACTION_SERVICE_MAP = {
    "search_number":   "number",
    "search_telegram": "telegram",
    "search_aadhaar":  "aadhaar",
    "search_family":   "family",
    "search_pincode":  "pincode",
    "search_ifsc":     "ifsc",
    "search_vehicle":  "vehicle",
}

NOT_CONFIGURED_MSG = "⚠️ Service Not Configured Yet\n\n🔥 <b>CYBER WILD WAVE</b>"

# Inline Back button shown on every lookup prompt
_PROMPT_NAV = InlineKeyboardMarkup([[
    InlineKeyboardButton("⬅️ Back", callback_data="prompt_back"),
]])

SEARCH_KB_BUTTONS = set(SEARCH_KB_MAP.keys())

SEARCH_PROMPTS = {
    "search_number":   ("📱", "Number Lookup",   "Enter the <b>10-digit mobile number</b>"),
    "search_telegram": ("📞", "Telegram Lookup", "Enter the <b>Telegram username</b> (without @) or user ID"),
    "search_aadhaar":  ("🪪", "Aadhaar Lookup",  "Enter the <b>12-digit Aadhaar number</b>"),
    "search_family":   ("👨‍👩‍👧‍👦", "Family Lookup",  "Enter <b>Aadhaar number or full name</b> to find family data"),
    "search_pincode":  ("📍", "Pincode Lookup",  "Enter the <b>6-digit Pincode</b>"),
    "search_ifsc":     ("🏦", "IFSC Lookup",     "Enter the <b>IFSC Code</b> (e.g. SBIN0001234)"),
    "search_vehicle":  ("🚗", "Vehicle Lookup",  "Enter the <b>Vehicle Registration Number</b> (e.g. MH12AB1234)"),
}

FORMATTERS = {
    "search_number":   format_number_lookup,
    "search_telegram": format_telegram_lookup,
    "search_aadhaar":  format_aadhaar_lookup,
    "search_family":   format_family_lookup,
    "search_pincode":  format_pincode_lookup,
    "search_ifsc":     format_ifsc_lookup,
    "search_vehicle":  format_vehicle_lookup,
}

API_FUNCS = {
    "search_number":   api_service.lookup_number,
    "search_telegram": api_service.lookup_telegram,
    "search_aadhaar":  api_service.lookup_aadhaar,
    "search_family":   api_service.lookup_family,
    "search_pincode":  api_service.lookup_pincode,
    "search_ifsc":     api_service.lookup_ifsc,
    "search_vehicle":  api_service.lookup_vehicle,
}


async def _safe_delete(bot, chat_id: int, msg_id: int):
    if not msg_id:
        return
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


# ── Typing-action keepalive ────────────────────────────────────────────────────

async def _keep_typing(bot, chat_id: int) -> None:
    """Re-send ChatAction.TYPING every 4 s so 'typing…' stays visible during API calls."""
    try:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


# ── Animated progress message ──────────────────────────────────────────────────

_PROGRESS_STAGES = [
    (0,  "📞 Checking database..."),
    (20, "📞 Checking database..."),
    (40, "🔍 Fetching records..."),
    (60, "🔍 Fetching records..."),
    (80, "📊 Processing data..."),
    (95, "📊 Processing data..."),
]


def _progress_text(stage: int) -> str:
    pct, label = _PROGRESS_STAGES[stage % len(_PROGRESS_STAGES)]
    filled = pct // 10
    bar = "█" * filled + "░" * (10 - filled)
    return (
        f"🔎 <b>Searching...</b>\n\n"
        f"[{bar}]\n\n"
        f"⚡ <b>{pct}% Complete</b>\n"
        f"{label}"
    )


async def _animate_progress(bot, chat_id: int, msg_id: int) -> None:
    """Cycle the progress message through stages then hold at 95% until cancelled."""
    for stage in range(1, len(_PROGRESS_STAGES)):
        await asyncio.sleep(0.8)
        try:
            await bot.edit_message_text(
                _progress_text(stage),
                chat_id=chat_id,
                message_id=msg_id,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    while True:
        await asyncio.sleep(1)


# ── Keyboard-based search type selection ─────────────────────────────────────

async def handle_search_kb_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Called when user taps a search-type button from the search submenu keyboard.
    Swaps keyboard back to main, sends a clean prompt, tracks it, sets pending action.
    """
    user    = update.effective_user
    text    = (update.message.text or "").strip()
    action  = SEARCH_KB_MAP.get(text)
    if not action:
        return

    try:
        await update.message.delete()
    except Exception:
        pass

    chat_id = update.effective_chat.id
    kb      = _get_main_kb(user.id)

    async def _cleanup_nav() -> None:
        """Delete the SEARCH tap and current lookup-type tap from the chat."""
        await cleanup_all(ctx.bot, chat_id, ctx)
        for mid in ctx.user_data.pop("_user_msg_ids", []):
            await _safe_delete(ctx.bot, chat_id, mid)
        await _safe_delete(ctx.bot, chat_id, update.message.message_id)

    if not check_rate_limit(user.id):
        await _cleanup_nav()
        sent = await update.effective_chat.send_message(
            "⏳ <b>Slow down!</b>\n\nPlease wait a moment before searching again.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        track(ctx, sent)
        return

    if cfg.MAINTENANCE_MODE and user.id not in ADMIN_IDS:
        await _cleanup_nav()
        sent = await update.effective_chat.send_message(
            "🛠 <b>Maintenance Mode</b>\n\nThe bot is under maintenance. Please try again later.\n\n🔥 <b>CYBER WILD WAVE</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        track(ctx, sent)
        return

    banned, premium = await asyncio.gather(
        db.is_banned(user.id),
        db.is_premium(user.id),
    )

    if banned:
        await _cleanup_nav()
        sent = await update.effective_chat.send_message(
            "🚫 <b>Banned</b>\n\nYou are banned from using this bot.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        track(ctx, sent)
        return

    if not premium:
        used = await db.get_daily_search_count(user.id)
        if used >= FREE_DAILY_SEARCHES:
            await _cleanup_nav()
            sent = await update.effective_chat.send_message(
                f"❌ <b>Daily Limit Reached</b>\n\n"
                f"You've used all <b>{FREE_DAILY_SEARCHES}</b> free searches today.\n\n"
                f"💎 Upgrade to <b>Premium</b> for unlimited searches!\n\n"
                f"🔥 <b>CYBER WILD WAVE</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            track(ctx, sent)
            ctx.user_data["_kbd_mode"] = "main"
            return

    # ── Service configuration check ───────────────────────────────────────────
    service_key = ACTION_SERVICE_MAP.get(action, "")
    if not is_service_configured(service_key):
        await _cleanup_nav()
        # Send with inline ⬅️ Back only — do NOT send a new ReplyKeyboard so the
        # existing search submenu keyboard stays visible below the chat box.
        sent = await update.effective_chat.send_message(
            NOT_CONFIGURED_MSG,
            parse_mode=ParseMode.HTML,
            reply_markup=_PROMPT_NAV,
        )
        track(ctx, sent)
        return

    emoji, label, prompt = SEARCH_PROMPTS[action]
    set_pending_action(user.id, action)

    # Dismiss the search submenu reply keyboard with a ghost message, then delete it.
    # This lets us attach an InlineKeyboardMarkup directly to the prompt message.
    try:
        ghost = await update.effective_chat.send_message(".", reply_markup=ReplyKeyboardRemove())
        await ghost.delete()
    except Exception:
        pass

    sent = await update.effective_chat.send_message(
        f"╔══════════════════════════╗\n"
        f"║  {emoji}  {label.upper():<18} ║\n"
        f"╚══════════════════════════╝\n\n"
        f"{prompt}:\n\n"
        f"<i>Type and send below ↓</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=_PROMPT_NAV,
    )
    track(ctx, sent)
    ctx.user_data["_kbd_mode"] = "search_input"
    ctx.user_data["_prompt_id"] = sent.message_id


# ── Legacy inline search callbacks (kept for back-compat) ────────────────────

async def search_menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔍 <b>SEARCH MENU</b>\n\nSelect the type of lookup:",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_search_keyboard()
    )


async def search_type_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles inline search-type button taps from the search menu."""
    query  = update.callback_query
    await query.answer()
    action = query.data

    if action not in SEARCH_PROMPTS:
        return

    user    = update.effective_user
    chat_id = update.effective_chat.id

    if not check_rate_limit(user.id):
        await query.edit_message_text(
            "⏳ <b>Slow down!</b>\n\nPlease wait a moment before searching again.",
            parse_mode=ParseMode.HTML,
        )
        return

    if cfg.MAINTENANCE_MODE and user.id not in ADMIN_IDS:
        await query.edit_message_text(
            "🛠 <b>Maintenance Mode</b>\n\nThe bot is under maintenance. Please try again later.\n\n🔥 <b>CYBER WILD WAVE</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    banned, premium = await asyncio.gather(
        db.is_banned(user.id),
        db.is_premium(user.id),
    )

    if banned:
        await query.edit_message_text(
            "🚫 <b>Banned</b>\n\nYou are banned from using this bot.",
            parse_mode=ParseMode.HTML,
        )
        return

    if not premium:
        used = await db.get_daily_search_count(user.id)
        if used >= FREE_DAILY_SEARCHES:
            await query.edit_message_text(
                f"❌ <b>Daily Limit Reached</b>\n\n"
                f"You've used all <b>{FREE_DAILY_SEARCHES}</b> free searches today.\n\n"
                f"💎 Upgrade to <b>Premium</b> for unlimited searches!\n\n"
                f"🔥 <b>CYBER WILD WAVE</b>",
                parse_mode=ParseMode.HTML,
            )
            return

    service_key = ACTION_SERVICE_MAP.get(action, "")
    if not is_service_configured(service_key):
        await query.edit_message_text(
            NOT_CONFIGURED_MSG,
            parse_mode=ParseMode.HTML,
            reply_markup=_PROMPT_NAV,
        )
        return

    emoji, label, prompt = SEARCH_PROMPTS[action]
    set_pending_action(user.id, action)

    # Edit the inline menu into the prompt and attach the inline ⬅️ Back button
    await query.edit_message_text(
        f"╔══════════════════════════╗\n"
        f"║  {emoji}  {label.upper():<18} ║\n"
        f"╚══════════════════════════╝\n\n"
        f"{prompt}:\n\n"
        f"<i>Type and send below ↓</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=_PROMPT_NAV,
    )

    # Keyboard was already hidden when the user tapped 🔍 SEARCH — nothing extra needed here.


# ── Main search text handler ──────────────────────────────────────────────────

async def handle_search_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Flow:
    1. Validate query
    2. Cleanup ALL previously tracked bot messages (welcome, menus, prompts, etc.)
    3. Send loading placeholder (no keyboard — temporary)
    4. Call API
    5. Send result WITH the correct persistent keyboard
    6. Delete loading
    Chat result: only user query + final result card remain visible.
    Keyboard: immediately restored after result.
    """
    user   = update.effective_user
    action = get_pending_action(user.id)
    if not action or action not in SEARCH_PROMPTS:
        return

    if await db.is_banned(user.id):
        return

    query_text = (update.message.text or "").strip()
    if not query_text:
        return

    clear_pending_action(user.id)

    chat_id = update.effective_chat.id
    kb      = _get_main_kb(user.id)

    # Pull the prompt message ID out BEFORE cleanup so it stays visible during the lookup.
    # It will be explicitly deleted after the result is delivered.
    prompt_id = ctx.user_data.pop("_prompt_id", None)
    if prompt_id and "_bot_msgs" in ctx.user_data:
        ctx.user_data["_bot_msgs"] = [
            mid for mid in ctx.user_data["_bot_msgs"] if mid != prompt_id
        ]

    # Delete all other tracked bot messages (menus, welcome, etc.) but NOT the prompt
    await cleanup_all(ctx.bot, chat_id, ctx)

    # Show typing indicator + animated progress message simultaneously
    progress_msg = await update.effective_chat.send_message(
        _progress_text(0),
        parse_mode=ParseMode.HTML,
    )
    typing_task = asyncio.create_task(_keep_typing(ctx.bot, chat_id))
    anim_task   = asyncio.create_task(
        _animate_progress(ctx.bot, chat_id, progress_msg.message_id)
    )

    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or None

    DATA_NOT_FOUND = "❌ Data Not Found\n\n🔥 <b>CYBER WILD WAVE</b>"

    result_text = DATA_NOT_FOUND
    found       = False

    try:
        api_fn = API_FUNCS[action]
        data   = await api_fn(query_text)

        # ── Error sentinels ────────────────────────────────────────────────
        if isinstance(data, dict) and "_error" in data:
            if data["_error"] == "not_configured":
                result_text = NOT_CONFIGURED_MSG
            else:
                result_text = DATA_NOT_FOUND
            found = False
        elif data and (isinstance(data, list) or isinstance(data, dict)):
            found = True
            result_text = FORMATTERS[action](data)
        else:
            result_text = DATA_NOT_FOUND
            found = False

    except Exception as exc:
        logger.exception("Search error [%s]: %s", action, exc)
        result_text = DATA_NOT_FOUND
        found       = False

    finally:
        # Stop both tasks and delete the progress message
        anim_task.cancel()
        typing_task.cancel()
        try:
            await asyncio.gather(anim_task, typing_task, return_exceptions=True)
        except Exception:
            pass
        await _safe_delete(ctx.bot, chat_id, progress_msg.message_id)

    # Send result WITH correct keyboard — keyboard restored here.
    # For not-configured errors, attach inline ⬅️ Back instead of the main keyboard
    # so the user can return to the Search Menu and all lookup buttons are restored.
    result_kb = _PROMPT_NAV if result_text == NOT_CONFIGURED_MSG else kb
    await update.effective_chat.send_message(
        result_text,
        parse_mode=ParseMode.HTML,
        reply_markup=result_kb,
    )

    # Delete the lookup prompt now that the result is delivered
    await _safe_delete(ctx.bot, chat_id, prompt_id)

    await asyncio.gather(
        db.increment_daily_search(user.id),
        db.log_search(user.id, action, query_text, found,
                      username=user.username, full_name=full_name),
    )


# ── Prompt navigation callbacks (Back / Cancel inline buttons) ────────────────

async def prompt_back_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """⬅️ Back — cancel lookup, return to the search submenu."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    chat_id = update.effective_chat.id

    clear_pending_action(user.id)
    ctx.user_data.pop("_prompt_id", None)
    ctx.user_data["_kbd_mode"] = "search"

    # Delete the prompt message and all other tracked bot messages
    try:
        await query.message.delete()
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    await cleanup_all(ctx.bot, chat_id, ctx)

    # Re-show the search submenu keyboard
    is_adm = user.id in ADMIN_IDS
    sent = await update.effective_chat.send_message(
        "╔══════════════════════════╗\n"
        "║    🔍  SEARCH  MENU    ║\n"
        "╚══════════════════════════╝\n\n"
        "Select a search type from the keyboard below ↓",
        parse_mode=ParseMode.HTML,
        reply_markup=search_submenu_keyboard(is_admin=is_adm),
    )
    track(ctx, sent)


async def prompt_cancel_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """❌ Cancel — cancel lookup, return to main menu."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    chat_id = update.effective_chat.id

    clear_pending_action(user.id)
    ctx.user_data.pop("_prompt_id", None)
    ctx.user_data["_kbd_mode"] = "main"

    # Delete the prompt message and all other tracked bot messages
    try:
        await query.message.delete()
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    await cleanup_all(ctx.bot, chat_id, ctx)

    # Restore main keyboard
    kb = admin_main_keyboard() if user.id in ADMIN_IDS else user_panel_keyboard()
    sent = await update.effective_chat.send_message(
        "🏠 <b>Main Menu</b>\n\n📲 Use the buttons below ↓",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    track(ctx, sent)
