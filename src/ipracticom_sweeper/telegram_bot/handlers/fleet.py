"""Fleet handler — host list + per-host details (v0.4.3).

Owns:
  - menu:fleet: every host in the fleet
  - fleet:host:<name>: per-host details (status + CPU/memory/disk/network from the latest snapshot, plus log + download buttons)
  - fleet:logs:<name>: show the last N entries of the agent's audit logs (in-chat)
  - fleet:download:<name>: download the log as a Telegram document
"""
from __future__ import annotations

import json
import os
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ipracticom_sweeper.telegram_bot.formatter import (
    format_error,
    format_fleet_host,
    format_fleet_list,
)
from ipracticom_sweeper.telegram_bot.keyboards import (
    back_to_main,
    fleet_menu,
)
from ipracticom_sweeper.telegram_bot.services.agent_client import (
    AgentAPIError,
    AgentClient,
)
from ipracticom_sweeper._log import log_suppressed


def _agent(context) -> AgentClient:
    return context.bot_data["agent"]


# ---------- menu:fleet ----------

async def fleet(update, context) -> dict[str, Any]:
    """Show the fleet: local + every SSM connector."""
    try:
        data = await _agent(context).list_fleet()
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}

    hosts = data.get("hosts") or []
    return {
        "text": format_fleet_list(hosts),
        "reply_markup": fleet_menu(hosts),
    }


# ---------- fleet:host:<name> ----------

async def fleet_host(update, context) -> dict[str, Any]:
    """Show one host's details.

    For the local host: pull the latest snapshot so the operator sees
    real CPU/memory/disk/network numbers (not just the heartbeat). For
    remote connectors: show config + last error.
    """
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    name = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not name:
        return {"text": "❌ שם מארח חסר", "reply_markup": back_to_main()}

    try:
        host = await _agent(context).get_fleet_host(name)
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}

    text = format_fleet_host(host)

    # v0.4.5: The host dict already contains the psutil snapshot (extra block).
    # The legacy /api/snapshot call is no longer needed for the local host —
    # format_fleet_host already rendered the metrics inline. We append the
    # secondary metrics block only as a fallback if format_fleet_host didn't
    # surface them (e.g. legacy connector-style hosts).
    # In v0.4.5, format_fleet_host already covers the local case; the
    # _format_local_metrics helper stays for any caller that still wants
    # the snapshot fallback path.

    # Log buttons — every host gets them.
    log_buttons = [
        [InlineKeyboardButton("📜 הצג לוגים", callback_data=f"fleet:logs:{name}")],
        [InlineKeyboardButton("⬇️ הורד לוג כקובץ", callback_data=f"fleet:download:{name}")],
    ]
    log_buttons.append([InlineKeyboardButton("⬅️ חזור", callback_data="menu:fleet")])

    return {"text": text, "reply_markup": InlineKeyboardMarkup(log_buttons)}


# ---------- fleet:logs:<name> ----------

async def fleet_logs(update, context) -> dict[str, Any]:
    """Show the last N lines of every available audit log, in-chat.

    For very long logs we truncate to fit Telegram's 4000-char message
    limit and offer the download button as the next step.
    """
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    name = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not name:
        return {"text": "❌ שם מארח חסר", "reply_markup": back_to_main()}

    try:
        data_resp = await _agent(context).get_logs(tail=20)
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}

    if not data_resp.get("available"):
        return {
            "text": "⚠️ אין תיקיית state זמינה ב-agent_api.",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("⬇️ הורד לוג כקובץ", callback_data=f"fleet:download:{name}")],
                [InlineKeyboardButton("⬅️ חזור", callback_data=f"fleet:host:{name}")],
            ]),
        }

    logs = data_resp.get("logs") or []
    if not logs:
        return {
            "text": "📜 <b>לוגים</b>\n<i>אין לוגים זמינים כרגע.</i>",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ חזור", callback_data=f"fleet:host:{name}")],
            ]),
        }

    lines: list[str] = [f"📜 <b>לוגים (20 אחרונים מכל קובץ)</b>", ""]
    for log in logs:
        lname = log.get("name", "?")
        lkind = log.get("kind", "?")
        size = log.get("size_bytes", 0)
        line_count = log.get("line_count", 0)
        lines.append(f"━━ <b>{escape(lname)}</b> ({lkind}, {size} בייטים, {line_count} שורות) ━━")
        tail = log.get("tail") or []
        if not tail:
            lines.append("  <i>(ריק)</i>")
        else:
            for entry in tail:
                lines.append(f"  <code>{escape(_one_line(entry))}</code>")
        lines.append("")

    body = "\n".join(lines)
    # Truncate if needed (Telegram 4096 limit; we leave headroom for markup).
    if len(body) > 3800:
        body = body[:3700] + "\n\n<i>... (טקסט קוצר — לחץ 'הורד לוג כקובץ' לקובץ המלא)</i>"

    return {
        "text": body,
        "reply_markup": InlineKeyboardMarkup([
            [InlineKeyboardButton("⬇️ הורד לוג כקובץ", callback_data=f"fleet:download:{name}")],
            [InlineKeyboardButton("⬅️ חזור", callback_data=f"fleet:host:{name}")],
        ]),
    }


# ---------- fleet:download:<name> ----------

async def fleet_download(update, context) -> dict[str, Any]:
    """Download the log file as a Telegram document.

    The agent_client returns a URL with an inline token; the bot then
    fetches the file from the agent (using its own httpx) and uploads
    it to Telegram via `reply_document`. The user gets a file in their
    chat.
    """
    import asyncio
    import httpx
    from pathlib import Path as _Path

    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    name = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not name:
        return {"text": "❌ שם מארח חסר", "reply_markup": back_to_main()}

    agent = _agent(context)
    url = agent.get_logs_download_url(name="all")
    token = agent._token  # type: ignore[attr-defined]

    # Acknowledge the callback immediately so Telegram doesn't time out.
    if cq is not None:
        try:
            await cq.answer("מוריד לוג...")
        except Exception as e:
            log_suppressed("fleet_log_download_ack", e)

    # Fetch the file from the agent_api.
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            resp = await http.get(url, headers=headers)
        if resp.status_code != 200:
            return {
                "text": f"❌ הורדה נכשלה: HTTP {resp.status_code}\n<i>{resp.text[:200]}</i>",
                "reply_markup": InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ חזור", callback_data=f"fleet:host:{name}")],
                ]),
            }
    except Exception as e:
        return {
            "text": f"❌ שגיאת רשת: <i>{e}</i>",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ חזור", callback_data=f"fleet:host:{name}")],
            ]),
        }

    # Save to a tmp file and send as document. Telegram needs a real file.
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".txt", prefix="sweeper-logs-", delete=False
        ) as f:
            f.write(resp.content)
            tmp_path = f.name
    except Exception as e:
        return {
            "text": f"❌ שמירה מקומית נכשלה: <i>{e}</i>",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ חזור", callback_data=f"fleet:host:{name}")],
            ]),
        }

    # Send the document. We use the raw update (not callback) because
    # reply_document doesn't work on a callback message.
    try:
        update_obj = update
        # Find a target to reply to: prefer the original message, fall
        # back to the callback's message.
        target_msg = getattr(update_obj, "message", None)
        if target_msg is None and cq is not None:
            target_msg = getattr(cq, "message", None)
        if target_msg is None:
            return {
                "text": f"✅ לוג הורד ({len(resp.content)} בייטים) — אבל לא הצלחתי לשלוח כקובץ (אין הודעה לענות לה).",
                "reply_markup": InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ חזור", callback_data=f"fleet:host:{name}")],
                ]),
            }
        with open(tmp_path, "rb") as f:
            await target_msg.reply_document(
                document=f,
                filename=f"sweeper-logs-{name}.txt",
                caption=f"📎 לוג מ-{name} ({len(resp.content)} בייטים)",
            )
        # Also edit the original callback message so the user sees a confirmation.
        if cq is not None:
            try:
                await cq.edit_message_text(
                    text=f"✅ לוג נשלח כקובץ ({len(resp.content)} בייטים)",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ חזור", callback_data=f"fleet:host:{name}")],
                    ]),
                )
            except Exception as e:
                log_suppressed("fleet_log_download_edit", e)
        return None  # signal: don't send another message
    except Exception as e:
        return {
            "text": f"❌ שליחה כקובץ נכשלה: <i>{e}</i>",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ חזור", callback_data=f"fleet:host:{name}")],
            ]),
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError as e:
            log_suppressed("fleet_log_download_cleanup", e)


# ---------- helpers ----------

def _format_local_metrics(snap: dict) -> str:
    """Pull CPU/memory/disk/network out of a fleet/local payload (v0.4.4 extra shape).

    Accepts either:
    - /api/fleet/local response (has 'extra' block with cpu/memory/disk/network keys)
    - /api/snapshot response (has 'modules' block with cpu/memory/disk/network modules)
    """
    lines: list[str] = ["", "━━ <b>מדדים עדכניים</b> ━━", ""]

    # Detect shape: v0.4.4 extra block vs old /api/snapshot modules block.
    if isinstance(snap, dict) and isinstance(snap.get("extra"), dict):
        mods = snap["extra"]
        is_extra = True
    else:
        mods = (snap.get("modules") or {}) if isinstance(snap, dict) else {}
        is_extra = False

    def _pct_from_extra(info: dict) -> str | None:
        if not isinstance(info, dict):
            return None
        pct = info.get("percent")
        if isinstance(pct, (int, float)):
            return f"{pct:.1f}%"
        return None

    def _pct_from_snapshot(info: dict) -> str | None:
        if not isinstance(info, dict):
            return None
        d = info.get("details") if isinstance(info.get("details"), dict) else {}
        for key in ("percent", "used_percent", "usage_percent", "value"):
            v = d.get(key) if isinstance(d, dict) else None
            if isinstance(v, (int, float)):
                return f"{v:.1f}%"
        return None

    _pct = _pct_from_extra if is_extra else _pct_from_snapshot

    for label, mod_name, emoji in [
        ("CPU", "cpu", "🖥️"),
        ("זיכרון", "memory", "🧠"),
        ("דיסק", "disk", "💾"),
        ("רשת", "network", "🌐"),
    ]:
        info = mods.get(mod_name) or {}
        status = info.get("status", "unknown") if isinstance(info, dict) else "unknown"
        pct = _pct(info)
        s_emoji = {"ok": "✅", "warn": "⚠️", "crit": "🚨"}.get(status, "❓")
        if pct:
            lines.append(f"  {emoji} <b>{label}</b>: {pct} {s_emoji}")
        else:
            lines.append(f"  {emoji} <b>{label}</b>: {s_emoji} <i>(אין נתון)</i>")

    defcon = snap.get("defcon") if isinstance(snap, dict) else None
    if defcon is not None:
        lines.append(f"\n<i>DEFCON: {defcon}</i>")

    return "\n".join(lines)


def _one_line(obj: Any) -> str:
    """Compact single-line representation of a JSON object (for chat display)."""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    return s[:400]


def escape(text: str) -> str:
    """HTML escape shortcut for inline strings."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )