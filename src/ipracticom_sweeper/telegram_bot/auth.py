"""Authorization middleware for the iPracticom Sweeper Telegram bot.

A single decorator, `authorized_only`, gates every command/callback
handler on chat_id whitelist membership. Unauthorized callers get an
`UnauthorizedError`; the bot's error handler turns that into a silent
"command not for you" message (or no message at all, per design).
"""
from __future__ import annotations

import functools
from typing import Callable

from ipracticom_sweeper.telegram_bot.config import BotConfig


class UnauthorizedError(RuntimeError):
    """Raised when an update comes from a non-whitelisted chat_id."""


def _extract_config(context) -> BotConfig | None:
    """Pull the BotConfig from the PTB context's bot_data dict."""
    return getattr(context, "bot_data", {}).get("config")


def authorized_only(fn: Callable) -> Callable:
    """Decorator: only let whitelisted chat_ids reach the wrapped handler.

    Usage:
        @authorized_only
        async def my_handler(update, context):
            ...
    """

    @functools.wraps(fn)
    async def wrapper(update, context, *args, **kwargs):
        cfg = _extract_config(context)
        if cfg is None:
            raise UnauthorizedError("bot config not initialized")

        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None) if chat is not None else None
        if chat_id is None or not cfg.is_authorized(chat_id):
            raise UnauthorizedError(f"chat_id {chat_id} not authorized")
        return await fn(update, context, *args, **kwargs)

    return wrapper
