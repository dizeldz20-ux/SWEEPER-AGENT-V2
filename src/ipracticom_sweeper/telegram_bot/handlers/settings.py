"""Settings handler — Telegram connectivity test (v0.4.3).

Per user feedback (2026-06-29): only the Telegram connectivity test is
useful from the bot. Slack/API/identity are noise — they're either
operator-level concerns (not bot-driven) or duplicated info.

Owns:
  - menu:settings: minimal settings submenu with one button
  - set:test:tg: confirm Telegram is reachable + show bot identity
"""
from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ipracticom_sweeper.telegram_bot.keyboards import back_to_main


# ---------- menu:settings ----------

async def settings(update, context) -> dict[str, Any]:
    """Show minimal settings: just the Telegram connection test."""
    return {
        "text": (
            "⚙️ <b>הגדרות</b>\n\n"
            "בדיקת חיבור לטלגרם — שולח הודעה חוזרת ומאמת שהבוט פעיל."
        ),
        "reply_markup": InlineKeyboardMarkup([
            [InlineKeyboardButton("🧪 בדוק חיבור לטלגרם", callback_data="set:test:tg")],
            [InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")],
        ]),
    }


# ---------- set:test:tg ----------

async def test_tg(update, context) -> dict[str, Any]:
    """Send a confirmation back to the calling chat.

    The act of receiving a reply is the test. We also call getMe to
    verify the bot token is still valid.
    """
    try:
        me = await context.bot.get_me()
    except Exception as e:
        return {
            "text": f"❌ <b>טלגרם לא זמין</b>\n<i>{e}</i>",
            "reply_markup": back_to_main(),
        }
    return {
        "text": (
            "✅ <b>חיבור לטלגרם תקין</b>\n\n"
            f"  bot: <code>{me.username}</code> (id={me.id})\n"
            f"  שם: {me.first_name}\n"
            "\n"
            "<i>אם קראת את ההודעה הזאת — החיבור תקין.</i>"
        ),
        "reply_markup": back_to_main(),
    }
