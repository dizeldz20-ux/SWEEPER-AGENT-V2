"""iPracticom Sweeper Telegram bot — entry point.

This is the live wiring: load config, build the python-telegram-bot
Application, register handlers (gated by `authorized_only`), install
an error handler, and start polling.

v0.4.2: split handlers into per-section submodules under
`ipracticom_sweeper.telegram_bot.handlers`. Each handler is a plain
async function (update, context) -> dict[str, Any]; we adapt that to
PTB's Command/Callback API in build_application() below.

Run it:
    TELEGRAM_BOT_TOKEN=***
    ALLOWED_CHAT_IDS=8351895620
    AGENT_API_URL=http://127.0.0.1:8787
    AGENT_API_TOKEN=***
    python -m ipracticom_sweeper.telegram_bot.bot
"""
from __future__ import annotations

import sys

import structlog
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ipracticom_sweeper.telegram_bot.auth import UnauthorizedError, authorized_only
from ipracticom_sweeper.telegram_bot.config import ConfigError, load_config
from ipracticom_sweeper.telegram_bot.services.agent_client import AgentClient
from ipracticom_sweeper._log import log_suppressed

# v0.4.2 handlers — one module per menu section.
from ipracticom_sweeper.telegram_bot.handlers import (
    approvals as approvals_handler,
    connectors as connectors_handler,
    dashboard as dashboard_handler,
    fleet as fleet_handler,
    history as history_handler,
    settings as settings_handler,
)

log = structlog.get_logger("ipracticom.telegram_bot.bot")


async def _send_result(target, result: dict | None) -> None:
    """Send a handler result dict via the right Telegram method.

    `target` is either `update.message` or `update.callback_query`. We
    edit the message if it's a callback (so the inline keyboard updates
    in place) or send a new message if it's a command.

    `result` may be None — the handler already sent a document/reply
    and wants the dispatcher to stay silent. We just return.
    """
    if result is None:
        return

    text = result.get("text", "")
    reply_markup = result.get("reply_markup")
    parse_mode = ParseMode.HTML

    cq = getattr(target, "callback_query", None)
    if cq is not None:
        await cq.answer()
        try:
            await cq.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            # Message not modified / inline keyboard same — ignore.
            log_suppressed("telegram_bot_edit_message", e)
        return

    msg = getattr(target, "message", None) or target
    if hasattr(msg, "reply_text"):
        await msg.reply_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    elif hasattr(msg, "edit_message_text"):
        await msg.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)


def _on_command_sync(handler):
    """Wrap a handler as a CommandHandler callback."""
    @authorized_only
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        result = await handler(update, context)
        await _send_result(update, result)
    return wrapped


def _on_callback_sync(handler):
    """Wrap a handler as a CallbackQueryHandler callback."""
    @authorized_only
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        result = await handler(update, context)
        await _send_result(update, result)
    return wrapped


async def _on_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free-text message handler — only the connector form flow uses it.

    We route to connectors.connector_text_input; if no form is active,
    the handler returns None and we silently ignore the message.
    """
    cfg = context.bot_data.get("config")
    if cfg is None:
        return
    chat = getattr(update, "effective_chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None or not cfg.is_authorized(chat_id):
        log.warning("unauthorized", chat_id=chat_id)
        return

    result = await connectors_handler.connector_text_input(update, context)
    if result is not None:
        await _send_result(update, result)


async def _on_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silent rejection for callback queries that don't match any pattern.

    Per anti-pattern guidance: do not echo, do not error out, do not
    waste API calls.
    """
    chat = getattr(update, "effective_chat", None)
    cid = getattr(chat, "id", None)
    log.warning("unauthorized", chat_id=cid)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: log, do not crash the polling loop."""
    log.exception("handler error", error=str(context.error))


def build_application() -> Application:
    """Construct the PTB Application with all handlers wired."""
    cfg = load_config()
    agent = AgentClient(base_url=cfg.agent_api_url, token=cfg.agent_api_token)

    app = ApplicationBuilder().token(cfg.bot_token).build()
    app.bot_data["config"] = cfg
    app.bot_data["agent"] = agent

    # --- Command handlers ---
    app.add_handler(CommandHandler("start", _on_command_sync(dashboard_handler.start)))
    app.add_handler(CommandHandler("help", _on_command_sync(dashboard_handler.start)))

    # --- Free-text handler for connector form flow ---
    # Filters.TEXT & ~Filters.COMMAND catches any text message that's not
    # a slash command. This is the lowest-priority handler — only the
    # connector form uses it.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _on_free_text)
    )

    # --- Callback handlers (inline keyboards) ---
    # Order matters: specific patterns first, catch-all last.

    # Dashboard section
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(dashboard_handler.back_to_main), pattern=r"^menu:main$"
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(dashboard_handler.dashboard), pattern=r"^menu:dashboard$"
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(dashboard_handler.run_now), pattern=r"^dash:run_now$"
    ))

    # History section
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(history_handler.history), pattern=r"^menu:history$"
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(history_handler.metric_drill),
        pattern=r"^hist:metric:.+$",
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(history_handler.metric_range),
        pattern=r"^hist:range:[^:]+:\d+$",
    ))

    # Approvals section
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(approvals_handler.approvals), pattern=r"^menu:approvals$"
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(approvals_handler.approval_list), pattern=r"^appr:list$"
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(approvals_handler.approval_detail),
        pattern=r"^appr:detail:[A-Za-z0-9_-]+$",
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(approvals_handler.approve),
        pattern=r"^appr:approve:[A-Za-z0-9_-]+$",
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(approvals_handler.reject),
        pattern=r"^appr:reject:[A-Za-z0-9_-]+$",
    ))

    # Connectors section
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(connectors_handler.connectors), pattern=r"^menu:connectors$"
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(connectors_handler.connector_add), pattern=r"^conn:add$"
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(connectors_handler.connector_view),
        pattern=r"^conn:view:[A-Za-z0-9_-]+$",
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(connectors_handler.connector_test),
        pattern=r"^conn:test:[A-Za-z0-9_-]+$",
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(connectors_handler.connector_edit),
        pattern=r"^conn:edit:[A-Za-z0-9_-]+$",
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(connectors_handler.connector_delete),
        pattern=r"^conn:delete:[A-Za-z0-9_-]+$",
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(connectors_handler.connector_delete_confirm),
        pattern=r"^conn:delete_confirm:[A-Za-z0-9_-]+$",
    ))

    # Fleet section
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(fleet_handler.fleet), pattern=r"^menu:fleet$"
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(fleet_handler.fleet_host),
        pattern=r"^fleet:host:[A-Za-z0-9_-]+$",
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(fleet_handler.fleet_logs),
        pattern=r"^fleet:logs:[A-Za-z0-9_-]+$",
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(fleet_handler.fleet_download),
        pattern=r"^fleet:download:[A-Za-z0-9_-]+$",
    ))

    # Settings section — v0.4.3: only the Telegram test remains.
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(settings_handler.settings), pattern=r"^menu:settings$"
    ))
    app.add_handler(CallbackQueryHandler(
        _on_callback_sync(settings_handler.test_tg), pattern=r"^set:test:tg$"
    ))

    # Catch-all unauthorized/unknown → silent log
    app.add_handler(CallbackQueryHandler(_on_unauthorized))

    # --- Error handler ---
    app.add_error_handler(_on_error)

    return app


def main() -> int:
    """Entry point: build + run the bot until SIGINT/SIGTERM."""
    import logging  # for configuring the root bridge only (Telegram uses stdlib)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        app = build_application()
    except ConfigError as e:
        log.error("config error", error=str(e))
        return 2

    log.info("starting Telegram bot polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    sys.exit(main())