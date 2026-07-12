import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ChatAction, ParseMode

import database as db
from keyboards.main_kb import (
    disclaimer_keyboard, user_panel_keyboard, admin_main_keyboard,
    search_submenu_keyboard, join_channel_keyboard, home_inline_keyboard,
)
from keyboards.search_kb import search_menu_keyboard
from keyboards.premium_kb import premium_menu_keyboard
from utils.helpers import check_channel_membership, get_user_display_name, fmt_date
from utils.msg_tracker import track, cleanup_all
from config import (
    CHANNEL_INVITE_LINK, BOT_NAME, MAINTENANCE_MODE,
    ADMIN_IDS, FREE_DAILY_SEARCHES,
)

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

DISCLAIMER_MSG = """
⚠️ <b>DISCLAIMER</b>

This bot is intended only for <b>authorized and legal use</b>.

By clicking <b>"✅ I Agree"</b> you confirm that:

• You will use the bot legally.
• You understand all responsibility remains with the user.
• The bot owner is not responsible for misuse.
• Unauthorized activity is prohibited.
""".strip()

HELP_TEXT = """
╔══════════════════════════╗
║     ℹ️  HELP  GUIDE      ║
╚══════════════════════════╝

🔍 <b>Search</b> — Open search menu (keyboard)
💎 <b>Premium</b> — Upgrade for unlimited access
👤 <b>My Account</b> — Profile & statistics
📜 <b>Remaining Credit</b> — Daily search balance
🔄 <b>Refresh</b> — Reload welcome screen
👑 <b>Admin Panel</b> — Admin controls (admin only)

━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>🔍 Search Types Available:</b>
  📱 Number Lookup
  📞 Telegram Lookup
  🪪 Aadhaar Lookup
  👨‍👩‍👧‍👦 Family Lookup
  📍 Pincode Lookup
  🏦 IFSC Lookup
  🚗 Vehicle Lookup

━━━━━━━━━━━━━━━━━━━━━━━━━━

👤 <b>Free Users:</b> {free_limit} searches/day
💎 <b>Premium:</b> Unlimited + VIP access

━━━━━━━━━━━━━━━━━━━━━━━━━━
🔥 <b>CYBER WILD WAVE</b>
""".strip()


def _now_ist():
    now = datetime.now(IST)
    return now.strftime("%d-%m-%Y"), now.strftime("%I:%M:%S %p")


def _get_keyboard(user_id: int):
    return admin_main_keyboard() if user_id in ADMIN_IDS else user_panel_keyboard()


def _build_welcome(name: str, premium: bool, is_adm: bool) -> str:
    date_str, time_str = _now_ist()
    status = "💎 VIP PREMIUM" if premium else "👤 Free User"
    role_line = "👑 Admin  │  " if is_adm else ""

    return (
        "╔══════════════════════════╗\n"
        "║  🔥  CYBER WILD WAVE  🔥  ║\n"
        "╚══════════════════════════╝\n\n"
        "⚡ <b>Premium OSINT Intelligence System</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>User</b>    :  {name}\n"
        f"💎 <b>Status</b>  :  {role_line}{status}\n"
        f"📅 <b>Date</b>    :  {date_str}\n"
        f"🕒 <b>Time</b>    :  {time_str} IST\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🚀 Fast Search System  <b>Activated</b>\n"
        "🔒 Secure & Professional Lookup\n\n"
        "👇 <b>Choose an option below</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ── /start ────────────────────────────────────────────────────────────────────

async def start_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    try:
        await update.message.delete()
    except Exception:
        pass

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    await db.upsert_user(
        user.id, user.username or "",
        user.first_name or "", user.last_name or ""
    )

    if await db.is_banned(user.id):
        await update.message.reply_text("🚫 You have been banned from using this bot.")
        return

    if MAINTENANCE_MODE and user.id not in ADMIN_IDS:
        await update.message.reply_text(
            "🛠 <b>Maintenance Mode</b>\n\nThe bot is under maintenance. Please try again later.",
            parse_mode=ParseMode.HTML,
        )
        return

    row = await db.get_user(user.id)
    agreed = row["agreed_disclaimer"] if row else 0

    if not agreed:
        await update.message.reply_text(
            DISCLAIMER_MSG, parse_mode=ParseMode.HTML,
            reply_markup=disclaimer_keyboard()
        )
        return

    if CHANNEL_INVITE_LINK:
        joined = await check_channel_membership(ctx.bot, user.id)
        if not joined:
            await update.message.reply_text(
                "📢 <b>Join Required</b>\n\nYou must join our channel to use this bot.",
                parse_mode=ParseMode.HTML,
                reply_markup=join_channel_keyboard(CHANNEL_INVITE_LINK)
            )
            return

    # Reset keyboard mode to main
    ctx.user_data["_kbd_mode"] = "main"
    await _send_welcome(update.message, user, ctx)


async def _send_welcome(message, user, ctx=None):
    """Send premium welcome banner with correct keyboard. Track for cleanup if ctx provided."""
    premium = await db.is_premium(user.id)
    is_adm  = user.id in ADMIN_IDS
    name    = get_user_display_name(user)
    text    = _build_welcome(name, premium, is_adm)

    sent = await message.reply_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=_get_keyboard(user.id)
    )
    if ctx:
        track(ctx, sent)
        ctx.user_data["_kbd_mode"] = "main"


# ── Disclaimer / channel gate ─────────────────────────────────────────────────

async def disclaimer_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data == "disclaimer_exit":
        await query.edit_message_text("👋 Goodbye! Come back when you're ready.")
        return

    await db.set_disclaimer_agreed(user.id)

    if CHANNEL_INVITE_LINK:
        joined = await check_channel_membership(ctx.bot, user.id)
        if not joined:
            await query.edit_message_text(
                "📢 <b>Join Required</b>\n\nJoin our channel to use the bot.",
                parse_mode=ParseMode.HTML,
                reply_markup=join_channel_keyboard(CHANNEL_INVITE_LINK)
            )
            return

    premium = await db.is_premium(user.id)
    is_adm  = user.id in ADMIN_IDS
    name    = get_user_display_name(user)
    await query.edit_message_text(
        _build_welcome(name, premium, is_adm), parse_mode=ParseMode.HTML
    )
    sent = await query.message.reply_text(
        "📲 Buttons activated ↓",
        reply_markup=_get_keyboard(user.id)
    )
    track(ctx, sent)
    ctx.user_data["_kbd_mode"] = "main"


async def verify_join_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    joined = await check_channel_membership(ctx.bot, user.id)
    if not joined:
        await query.answer("❌ You haven't joined yet! Please join first.", show_alert=True)
        return

    premium = await db.is_premium(user.id)
    is_adm  = user.id in ADMIN_IDS
    name    = get_user_display_name(user)
    await query.edit_message_text(
        _build_welcome(name, premium, is_adm), parse_mode=ParseMode.HTML
    )
    sent = await query.message.reply_text(
        "📲 Buttons activated ↓",
        reply_markup=_get_keyboard(user.id)
    )
    track(ctx, sent)
    ctx.user_data["_kbd_mode"] = "main"


# ── Inline back / help / credits ──────────────────────────────────────────────

async def menu_back_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    # Delete the current menu message instead of editing it
    try:
        await query.message.delete()
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    await _send_welcome(query.message, user, ctx)


async def help_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        HELP_TEXT.format(free_limit=FREE_DAILY_SEARCHES),
        parse_mode=ParseMode.HTML
    )


async def credits_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    premium = await db.is_premium(user.id)
    if premium:
        text = "📜 <b>Remaining Credits</b>\n\n💎 <b>Premium User</b>\n✅ Unlimited searches available!\n\n🔥 <b>CYBER WILD WAVE</b>"
    else:
        used = await db.get_daily_search_count(user.id)
        remaining = max(0, FREE_DAILY_SEARCHES - used)
        text = (
            f"📜 <b>Remaining Credits</b>\n\n"
            f"🔍 Daily Limit: <b>{FREE_DAILY_SEARCHES}</b>\n"
            f"✅ Used Today: <b>{used}</b>\n"
            f"📜 Remaining: <b>{remaining}</b>\n\n"
            f"💎 Upgrade to Premium for unlimited searches!\n\n"
            f"🔥 <b>CYBER WILD WAVE</b>"
        )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML)


# ── ReplyKeyboard button handler — user panel ─────────────────────────────────

USER_PANEL_BUTTONS = {
    "🔍 SEARCH", "💎 PREMIUM", "👤 MY ACCOUNT",
    "📜 REMAINING CREDIT", "ℹ️ HELP", "🔄 REFRESH",
}


async def handle_user_panel_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    if text == "🔍 SEARCH":
        is_adm = user.id in ADMIN_IDS
        sent = await update.message.reply_text(
            "╔══════════════════════════╗\n"
            "║    🔍  SEARCH  MENU    ║\n"
            "╚══════════════════════════╝\n\n"
            "Select a search type from the keyboard below ↓",
            parse_mode=ParseMode.HTML,
            reply_markup=search_submenu_keyboard(is_admin=is_adm),
        )
        track(ctx, sent)
        ctx.user_data["_kbd_mode"] = "search"

    elif text == "💎 PREMIUM":
        from handlers.premium import PREMIUM_MENU_TEXT
        sent = await update.message.reply_text(
            PREMIUM_MENU_TEXT, parse_mode=ParseMode.HTML,
            reply_markup=premium_menu_keyboard()
        )
        track(ctx, sent)

    elif text == "👤 MY ACCOUNT":
        row       = await db.get_user(user.id)
        premium   = await db.is_premium(user.id)
        prem_info = await db.get_premium_info(user.id)
        used      = await db.get_daily_search_count(user.id)
        name      = get_user_display_name(user)
        username  = f"@{user.username}" if user.username else "N/A"
        badge     = "💎 VIP PREMIUM" if premium else "👤 Free User"
        status    = "✅" if premium else "❌"
        total     = row["total_searches"] if row else 0
        is_adm    = "👑 Admin" if user.id in ADMIN_IDS else ""

        if premium and prem_info:
            expiry = fmt_date(prem_info["expires_at"])
            plan   = prem_info["plan_key"].upper() if prem_info["plan_key"] else "N/A"
        else:
            expiry = "N/A"
            plan   = "N/A"

        cred = "Unlimited ♾️" if premium else f"{max(0, FREE_DAILY_SEARCHES - used)} / {FREE_DAILY_SEARCHES}"
        vip  = "🏅 <b>VIP MEMBER</b>" if premium else "🔓 Free Member"
        date_str, time_str = _now_ist()

        account_text = (
            "╔══════════════════════════╗\n"
            "║     👤  MY  ACCOUNT     ║\n"
            "╚══════════════════════════╝\n\n"
            f"👤 <b>Name</b>      :  {name}\n"
            f"📛 <b>Username</b>  :  {username}\n"
            f"🆔 <b>User ID</b>   :  <code>{user.id}</code>\n"
            + (f"🛡 <b>Role</b>      :  {is_adm}\n" if is_adm else "") +
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💎 <b>Premium</b>   :  {status} {badge}\n"
            f"📋 <b>Plan</b>      :  {plan}\n"
            f"📅 <b>Expiry</b>    :  {expiry}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 <b>Total Searches</b>   :  {total}\n"
            f"🔍 <b>Today's Searches</b> :  {used}\n"
            f"📜 <b>Credits Left</b>     :  {cred}\n\n"
            f"🕒 <b>Time</b>  :  {time_str} IST\n\n"
            f"{vip}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔥 <b>CYBER WILD WAVE</b>"
        )
        sent = await update.message.reply_text(
            account_text, parse_mode=ParseMode.HTML,
            reply_markup=_get_keyboard(user.id),
        )
        track(ctx, sent)

    elif text == "📜 REMAINING CREDIT":
        premium = await db.is_premium(user.id)
        if premium:
            credit_text = (
                "╔══════════════════════════╗\n"
                "║   📜  REMAINING CREDIT  ║\n"
                "╚══════════════════════════╝\n\n"
                "💎 <b>Premium User</b>\n"
                "✅ <b>Unlimited searches</b> available!\n"
                "♾️ No daily limit for VIP members.\n\n"
                "🔥 <b>CYBER WILD WAVE</b>"
            )
        else:
            used      = await db.get_daily_search_count(user.id)
            remaining = max(0, FREE_DAILY_SEARCHES - used)
            bar_filled = int((remaining / FREE_DAILY_SEARCHES) * 10)
            bar        = "█" * bar_filled + "░" * (10 - bar_filled)
            credit_text = (
                "╔══════════════════════════╗\n"
                "║   📜  REMAINING CREDIT  ║\n"
                "╚══════════════════════════╝\n\n"
                f"🔍 <b>Daily Limit</b>   :  {FREE_DAILY_SEARCHES}\n"
                f"✅ <b>Used Today</b>    :  {used}\n"
                f"📜 <b>Remaining</b>    :  {remaining}\n\n"
                f"[{bar}] {remaining}/{FREE_DAILY_SEARCHES}\n\n"
                "💎 Upgrade to <b>Premium</b> for unlimited searches!\n\n"
                "🔥 <b>CYBER WILD WAVE</b>"
            )
        sent = await update.message.reply_text(
            credit_text, parse_mode=ParseMode.HTML,
            reply_markup=_get_keyboard(user.id),
        )
        track(ctx, sent)

    elif text == "ℹ️ HELP":
        sent = await update.message.reply_text(
            HELP_TEXT.format(free_limit=FREE_DAILY_SEARCHES),
            parse_mode=ParseMode.HTML,
            reply_markup=_get_keyboard(user.id),
        )
        track(ctx, sent)

    elif text == "🔄 REFRESH":
        ctx.user_data["_kbd_mode"] = "main"
        await _send_welcome(update.message, user, ctx)
