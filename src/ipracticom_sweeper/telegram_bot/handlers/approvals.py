"""Approvals handler — list pending repairs + approve/reject (v0.4.2).

Owns:
  - menu:approvals: count badge + drill-down
  - appr:list: paged list of pending proposals with action buttons
  - appr:approve:<id>: execute the repair now
  - appr:reject:<id>: archive as rejected

Approve = immediate execute (per user request — not just mark approved).
"""
from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ipracticom_sweeper.telegram_bot.formatter import (
    format_approval_result,
    format_approvals_list,
    format_error,
)
from ipracticom_sweeper.telegram_bot.keyboards import (
    approval_action_kb,
    approvals_menu,
    back_to_main,
)
from ipracticom_sweeper.telegram_bot.services.agent_client import (
    AgentAPIError,
    AgentClient,
)
from ipracticom_sweeper.telegram_bot.services.pager import paged_keyboard


def _agent(context) -> AgentClient:
    return context.bot_data["agent"]


# ---------- menu:approvals ----------

async def approvals(update, context) -> dict[str, Any]:
    """Show the approvals submenu: count badge + drill-down."""
    try:
        data = await _agent(context).list_approvals()
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}

    count = int(data.get("count", 0))
    if count == 0:
        return {
            "text": format_approvals_list([]),
            "reply_markup": approvals_menu(0),
        }
    # One button to drill into the list.
    return {
        "text": format_approvals_list(data.get("pending") or []),
        "reply_markup": InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 הצג רשימה", callback_data="appr:list")],
            [InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")],
        ]),
    }


# ---------- appr:list (paginated) ----------

async def approval_list(update, context) -> dict[str, Any]:
    """Render the paged list of pending proposals with action buttons per row."""
    try:
        data = await _agent(context).list_approvals()
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}

    pending = data.get("pending") or []

    def render(proposal: dict, _idx: int) -> InlineKeyboardButton:
        pid = proposal.get("id", "?")
        action = proposal.get("action", "?")
        return InlineKeyboardButton(
            f"📋 {pid[:8]} — {action}",
            callback_data=f"appr:detail:{pid}",
        )

    markup, total_pages = paged_keyboard(
        pending,
        page=0,
        page_size=6,
        prefix="appr",
        render_item=render,
        back_callback="menu:approvals",
    )
    text = f"📋 <b>תיקונים ממתינים</b> ({len(pending)})\n<i>לחץ על תיקון לאישור/דחייה</i>"
    return {"text": text, "reply_markup": markup}


# ---------- appr:detail:<id> ----------

async def approval_detail(update, context) -> dict[str, Any]:
    """Show one proposal + Approve/Reject buttons."""
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    pid = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not pid:
        return {"text": "❌ proposal id חסר", "reply_markup": back_to_main()}

    # Fetch fresh list so we can show the proposal's reason + command.
    try:
        data_resp = await _agent(context).list_approvals()
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}

    proposal = next(
        (p for p in (data_resp.get("pending") or []) if p.get("id") == pid),
        None,
    )
    if proposal is None:
        return {
            "text": f"❌ הצעה <code>{pid[:8]}</code> לא נמצאה (אולי אושרה?)",
            "reply_markup": back_to_main(),
        }

    text = (
        f"📋 <b>הצעה <code>{escape(pid[:8])}</code></b>\n\n"
        f"  פעולה: <b>{escape(str(proposal.get('action', '?')))}</b>\n"
        f"  kwargs: <code>{escape(str(proposal.get('kwargs', {})))[:200]}</code>\n"
        f"  סיבה: <i>{escape(str(proposal.get('reason', '')))[:200]}</i>\n"
        f"  פקודה: <code>{escape(str(proposal.get('proposed_command', '')))[:200]}</code>"
    )
    return {
        "text": text,
        "reply_markup": InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ אשר והפעל", callback_data=f"appr:approve:{pid}"),
                InlineKeyboardButton("❌ דחה", callback_data=f"appr:reject:{pid}"),
            ],
            [InlineKeyboardButton("⬅️ חזור לרשימה", callback_data="appr:list")],
        ]),
    }


# ---------- appr:approve:<id> ----------

async def approve(update, context) -> dict[str, Any]:
    """Approve + execute a pending repair."""
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    pid = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not pid:
        return {"text": "❌ proposal id חסר", "reply_markup": back_to_main()}
    try:
        result = await _agent(context).approve_repair(pid)
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}
    return {
        "text": format_approval_result(result),
        "reply_markup": back_to_main(),
    }


# ---------- appr:reject:<id> ----------

async def reject(update, context) -> dict[str, Any]:
    """Reject (archive) a pending repair."""
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    pid = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not pid:
        return {"text": "❌ proposal id חסר", "reply_markup": back_to_main()}
    try:
        result = await _agent(context).reject_repair(pid)
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}
    return {
        "text": format_approval_result({"ok": True, "status": "rejected"}),
        "reply_markup": back_to_main(),
    }


def escape(text: str) -> str:
    """HTML escape shortcut for inline strings."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )