"""Unified bilingual (Hebrew + English) approval message templates.

Pure formatting functions — no I/O. Every channel (Telegram, Slack,
dashboard) renders the same structured content so an operator sees an
identical, tidy layout everywhere: header → host → detection → fix →
decision, with generous spacing and one topic per paragraph.

Two message kinds:
  * request  — "please approve this repair" (with approve/reject buttons)
  * result   — outcome after a decision (success, or failure + human
               escalation)

Telegram text uses HTML parse_mode (matches the existing bot formatter);
Slack uses Block Kit blocks with native buttons.
"""
from __future__ import annotations

from typing import Any


# Dual-language escalation copy, reused verbatim across channels so the
# "get a human involved" message is identical everywhere.
HUMAN_ESCALATION_HE_EN = (
    "חשוב לערב כאן בן אדם לפני ניסיון תיקון נוסף.\n"
    "הסוכן לא ינסה שוב אוטומטית עד להתערבות אנושית.\n"
    "Please involve a human before any further repair attempt.\n"
    "The agent will NOT retry automatically."
)

_SEVERITY_EMOJI = {"crit": "🚨", "critical": "🚨", "warn": "⚠️", "warning": "⚠️", "info": "ℹ️"}


def _esc(text: str) -> str:
    """Escape HTML metacharacters for Telegram HTML parse_mode."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _host(proposal: dict) -> str:
    return str(proposal.get("server") or "").strip() or "unknown"


def _problem(proposal: dict) -> dict:
    p = proposal.get("problem")
    return p if isinstance(p, dict) else {}


def _detection(proposal: dict) -> tuple[str, str, str]:
    """Return (reason, severity, metrics_str) for the detected problem."""
    prob = _problem(proposal)
    reason = str(proposal.get("reason") or prob.get("detail") or "").strip()
    severity = str(prob.get("severity") or "warn").lower()
    metrics = prob.get("metrics") if isinstance(prob.get("metrics"), dict) else {}
    metrics_str = " · ".join(f"{k}={v}" for k, v in list(metrics.items())[:6])
    return reason, severity, metrics_str


def _fix(proposal: dict) -> str:
    cmd = str(proposal.get("proposed_command") or "").strip()
    if cmd:
        return cmd
    action = str(proposal.get("action") or "?")
    kwargs = proposal.get("kwargs") if isinstance(proposal.get("kwargs"), dict) else {}
    if kwargs:
        return action + "(" + ", ".join(f"{k}={v}" for k, v in kwargs.items()) + ")"
    return action


# --------------------------------------------------------------------------
# Telegram (HTML text)
# --------------------------------------------------------------------------


def approval_request_text(proposal: dict) -> str:
    """Bilingual approval-request message for Telegram (HTML)."""
    host = _host(proposal)
    action = str(proposal.get("action") or "?")
    reason, severity, metrics_str = _detection(proposal)
    sev_emoji = _SEVERITY_EMOJI.get(severity, "⚠️")
    fix = _fix(proposal)
    pid = str(proposal.get("id") or "?")

    lines: list[str] = [
        "🔧 <b>בקשת אישור לתיקון · Repair Approval Needed</b>",
        "",
        "🖥️ <b>מכונה · Host</b>",
        f"   <code>{_esc(host)}</code>",
        "",
        f"{sev_emoji} <b>מה זוהה · Detected</b>",
    ]
    if reason:
        lines.append(f"   {_esc(reason)}")
    lines.append(f"   חומרה · Severity:  <b>{_esc(severity)}</b>")
    if metrics_str:
        lines.append(f"   מדדים · Metrics:  <code>{_esc(metrics_str)}</code>")
    lines += [
        "",
        "🔧 <b>תיקון מוצע · Proposed fix</b>",
        f"   פעולה · Action:  <code>{_esc(action)}</code>",
        f"<pre>{_esc(fix)}</pre>",
        "",
        "⏳ <b>ממתין להחלטתך · Awaiting your decision</b>",
        "",
        f"🆔 <code>{_esc(pid)}</code>",
    ]
    return "\n".join(lines)


def approval_result_text(proposal: dict, result: dict, ok: bool) -> str:
    """Bilingual outcome message for Telegram (HTML).

    ok=True  → success confirmation.
    ok=False → failure + human-escalation block.
    """
    host = _host(proposal)
    action = str(proposal.get("action") or "?")
    pid = str(proposal.get("id") or "?")
    actor = str(result.get("operator") or result.get("actor") or "operator")
    msg = str(result.get("message") or result.get("error") or "").strip()

    if ok:
        lines = [
            "✅ <b>התיקון בוצע · Repair Completed</b>",
            "",
            f"🖥️ מכונה · Host:   <code>{_esc(host)}</code>",
            f"🔧 פעולה · Action: <code>{_esc(action)}</code>",
            f"👤 אושר ע״י · By:   <code>{_esc(actor)}</code>",
        ]
        if msg:
            lines += ["", "📋 <b>תוצאה · Result</b>", f"   {_esc(msg)}"]
        lines += ["", f"🆔 <code>{_esc(pid)}</code>"]
        return "\n".join(lines)

    lines = [
        "🚨 <b>התיקון נכשל — נדרשת התערבות אנושית</b>",
        "🚨 <b>Repair Failed — Human Intervention Required</b>",
        "",
        f"🖥️ מכונה · Host:   <code>{_esc(host)}</code>",
        f"🔧 פעולה · Action: <code>{_esc(action)}</code>",
        f"👤 אושר ע״י · By:   <code>{_esc(actor)}</code>",
    ]
    if msg:
        lines += ["", "❌ <b>שגיאה · Error</b>", f"   {_esc(msg)}"]
    lines += [
        "",
        "⚠️ <b>חשוב · Important</b>",
        f"   {_esc(HUMAN_ESCALATION_HE_EN).replace(chr(10), chr(10) + '   ')}",
        "",
        f"🆔 <code>{_esc(pid)}</code>",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Slack (Block Kit)
# --------------------------------------------------------------------------


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def approval_request_blocks(proposal: dict) -> list[dict[str, Any]]:
    """Slack Block Kit blocks for an approval request, with native buttons.

    The buttons carry ``value == pid`` and ``action_id`` "approve"/"reject";
    the /slack/events endpoint routes them to the real approval flow.
    """
    host = _host(proposal)
    action = str(proposal.get("action") or "?")
    reason, severity, metrics_str = _detection(proposal)
    fix = _fix(proposal)
    pid = str(proposal.get("id") or "?")

    detected = f"*{severity}* — {reason}" if reason else f"*{severity}*"
    if metrics_str:
        detected += f"\n`{metrics_str}`"

    return [
        {"type": "header", "text": {"type": "plain_text", "text": "🔧 בקשת אישור לתיקון · Repair Approval Needed"}},
        _section(f"🖥️ *מכונה · Host*\n`{host}`"),
        _section(f"⚠️ *מה זוהה · Detected*\n{detected}"),
        _section(f"🔧 *תיקון מוצע · Proposed fix* — `{action}`\n```{fix}```"),
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ אשר והפעל · Approve"},
                    "style": "primary",
                    "action_id": "approve",
                    "value": pid,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ דחה · Reject"},
                    "style": "danger",
                    "action_id": "reject",
                    "value": pid,
                },
            ],
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"🆔 `{pid}`"}]},
    ]


def approval_result_blocks(proposal: dict, result: dict, ok: bool) -> list[dict[str, Any]]:
    """Slack Block Kit blocks for a decision outcome."""
    host = _host(proposal)
    action = str(proposal.get("action") or "?")
    pid = str(proposal.get("id") or "?")
    actor = str(result.get("operator") or result.get("actor") or "operator")
    msg = str(result.get("message") or result.get("error") or "").strip()

    if ok:
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "✅ התיקון בוצע · Repair Completed"}},
            _section(f"🖥️ *מכונה · Host*: `{host}`\n🔧 *פעולה · Action*: `{action}`\n👤 *אושר ע״י · By*: `{actor}`"),
        ]
        if msg:
            blocks.append(_section(f"📋 *תוצאה · Result*\n{msg}"))
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"🆔 `{pid}`"}]})
        return blocks

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🚨 נכשל — נדרשת התערבות אנושית · Human Intervention Required"}},
        _section(f"🖥️ *מכונה · Host*: `{host}`\n🔧 *פעולה · Action*: `{action}`\n👤 *אושר ע״י · By*: `{actor}`"),
    ]
    if msg:
        blocks.append(_section(f"❌ *שגיאה · Error*\n```{msg}```"))
    blocks.append(_section(f"⚠️ *חשוב · Important*\n{HUMAN_ESCALATION_HE_EN}"))
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"🆔 `{pid}`"}]})
    return blocks


def approval_rejected_text(proposal: dict, reason: str, actor: str) -> str:
    """Telegram (HTML) note that a proposal was rejected — informational, not
    a failure. Lets the other channels know the decision was made."""
    host = _host(proposal)
    action = str(proposal.get("action") or "?")
    pid = str(proposal.get("id") or "?")
    lines = [
        "🚫 <b>ההצעה נדחתה · Proposal Rejected</b>",
        "",
        f"🖥️ מכונה · Host:   <code>{_esc(host)}</code>",
        f"🔧 פעולה · Action: <code>{_esc(action)}</code>",
        f"👤 נדחה ע״י · By:   <code>{_esc(actor)}</code>",
    ]
    if reason:
        lines += ["", "📝 <b>סיבה · Reason</b>", f"   {_esc(reason)}"]
    lines += ["", f"🆔 <code>{_esc(pid)}</code>"]
    return "\n".join(lines)


def approval_rejected_blocks(proposal: dict, reason: str, actor: str) -> list[dict[str, Any]]:
    """Slack blocks for a rejection note."""
    host = _host(proposal)
    action = str(proposal.get("action") or "?")
    pid = str(proposal.get("id") or "?")
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🚫 ההצעה נדחתה · Proposal Rejected"}},
        _section(f"🖥️ *מכונה · Host*: `{host}`\n🔧 *פעולה · Action*: `{action}`\n👤 *נדחה ע״י · By*: `{actor}`"),
    ]
    if reason:
        blocks.append(_section(f"📝 *סיבה · Reason*\n{reason}"))
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"🆔 `{pid}`"}]})
    return blocks
