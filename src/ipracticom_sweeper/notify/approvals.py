"""Push approval requests and outcomes to every configured channel.

When the pipeline creates a repair proposal it calls
:func:`notify_approval_request`, which fans out an interactive
approval-request message (with approve/reject buttons) to every Telegram
and Slack bot in the multi-bot store *and* the legacy env target. After a
decision executes, :func:`notify_approval_result` fans out the outcome
(success, or failure + human-escalation).

Message bodies come from :mod:`ipracticom_sweeper.notify.templates`; the
channel senders are reused from :mod:`ipracticom_sweeper.notify.legacy`.

Never raises — a broken channel must not take down the sweep or the
approval route. Returns ``{label: sent?}``; ``{}`` when no channel exists.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from ipracticom_sweeper.notify import store as _store
from ipracticom_sweeper.notify import templates
from ipracticom_sweeper.notify.legacy import _send_slack, _send_slack_app

logger = structlog.get_logger()


def _telegram_keyboard(pid: str) -> dict[str, Any]:
    """Inline keyboard whose callbacks match the existing bot handlers
    (``appr:approve:<pid>`` / ``appr:reject:<pid>``)."""
    return {
        "inline_keyboard": [[
            {"text": "✅ אשר והפעל", "callback_data": f"appr:approve:{pid}"},
            {"text": "❌ דחה", "callback_data": f"appr:reject:{pid}"},
        ]]
    }


async def _send_telegram_html(
    text: str,
    token: str | None,
    chat_id: str | None,
    reply_markup: dict | None = None,
) -> bool:
    """Send an HTML Telegram message to an explicit bot/chat, optionally with
    an inline keyboard. Never raises. Retries as plain text on a 400 so a
    stray metacharacter can't silently drop the alert (keyboard preserved)."""
    if not (token and chat_id):
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": False,
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    client = httpx.AsyncClient(timeout=5.0)
    try:
        resp = await client.post(url, json=payload)
        if resp.status_code == 400:
            payload.pop("parse_mode", None)
            resp = await client.post(url, json=payload)
        return resp.status_code == 200
    except Exception as e:
        logger.error("approval_telegram_send_failed", error=str(e))
        return False
    finally:
        await client.aclose()


def _has_channels() -> bool:
    from ipracticom_sweeper import config as _cfg
    return bool(_cfg.notifications_enabled() or _store.has_any_bot())


async def _fan_out(
    tg_text: str,
    slack_msg: dict[str, Any],
    reply_markup: dict | None,
) -> dict[str, bool]:
    """Send ``tg_text`` (+optional keyboard) to every Telegram target and
    ``slack_msg`` to every Slack target. Shared by request and result."""
    from ipracticom_sweeper import config as _cfg

    tasks: list[tuple[str, Any]] = []

    # Legacy env Telegram target.
    if _cfg.telegram_bot_token() and _cfg.telegram_chat_id():
        tasks.append((
            "telegram",
            _send_telegram_html(
                tg_text, _cfg.telegram_bot_token(), _cfg.telegram_chat_id(), reply_markup
            ),
        ))
    # Legacy Slack incoming webhook (renders blocks; buttons inert without
    # interactivity, but the details still display).
    if _cfg.slack_webhook_url():
        tasks.append(("slack", _send_slack(slack_msg)))
    # Multi-bot store — Telegram.
    for b in _store.telegram_bots():
        tasks.append((
            f"telegram:{b.get('id', '?')}",
            _send_telegram_html(tg_text, b.get("bot_token"), b.get("chat_id"), reply_markup),
        ))
    # Multi-bot store — Slack App.
    for b in _store.slack_bots():
        tasks.append((
            f"slack:{b.get('id', '?')}",
            _send_slack_app(slack_msg, b.get("bot_token", ""), b.get("channel", "")),
        ))

    results: dict[str, bool] = {}
    for label, coro in tasks:
        try:
            results[label] = await coro
        except Exception as e:
            results[label] = False
            logger.error("approval_notify_failed", channel=label, error=str(e))
    return results


async def notify_approval_request(proposal: dict) -> dict[str, bool]:
    """Fan out an interactive approval-request to every channel."""
    if not _has_channels():
        return {}
    pid = str(proposal.get("id") or "?")
    tg_text = templates.approval_request_text(proposal)
    slack_msg = {
        "text": "בקשת אישור לתיקון · Repair Approval Needed",
        "blocks": templates.approval_request_blocks(proposal),
    }
    return await _fan_out(tg_text, slack_msg, _telegram_keyboard(pid))


async def notify_approval_result(proposal: dict, result: dict, ok: bool) -> dict[str, bool]:
    """Fan out the outcome (success, or failure + human escalation)."""
    if not _has_channels():
        return {}
    tg_text = templates.approval_result_text(proposal, result, ok)
    fallback = "התיקון בוצע · Completed" if ok else "התיקון נכשל · Failed — human needed"
    slack_msg = {
        "text": fallback,
        "blocks": templates.approval_result_blocks(proposal, result, ok),
    }
    return await _fan_out(tg_text, slack_msg, None)


async def notify_approval_rejected(proposal: dict, reason: str, actor: str) -> dict[str, bool]:
    """Fan out a rejection note so every channel knows the decision was made."""
    if not _has_channels():
        return {}
    tg_text = templates.approval_rejected_text(proposal, reason, actor)
    slack_msg = {
        "text": "ההצעה נדחתה · Proposal rejected",
        "blocks": templates.approval_rejected_blocks(proposal, reason, actor),
    }
    return await _fan_out(tg_text, slack_msg, None)
