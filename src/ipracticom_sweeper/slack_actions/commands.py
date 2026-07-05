"""Slack command handler: parse and reply to slash-style messages.

Two ways Slack can deliver commands:

1. Slash commands — Slack sends form-urlencoded with `command=/defcon` etc.
   Configured in Slack app settings with a Request URL pointing at /slack/events.
   This endpoint receives them as POST with body like: command=%2Fdefcon&text=...
   The response must be a JSON {response_type, text} for immediate display, OR
   you can use response_url to POST asynchronously.

2. Message events — when the bot is mentioned or in DMs, Slack sends an
   `event_callback` with `event.type == "message"` and `event.text == "/defcon"`.
   Requires `message.im` or `app_mention` scope. Response: 200 OK within 3s,
   actual reply goes via chat.postMessage API.

This module handles BOTH transparently:

    handler = SlackCommandHandler()
    result = handler.handle_message(text="/defcon", user="daniel")
    # result == {"text": "DEFCON 4 (yellow)", "response_type": "in_channel"}

Slash command responses are formatted as the immediate JSON response.
Message events get the same `text` field and the caller is responsible for
posting it via chat.postMessage (or using response_url).

Available commands (mirror of the Telegram bot UX):
    /defcon              → overall + per-host DEFCON
    /health [host]       → latest metrics for one host (or all if no arg)
    /approve <id>        → approve a pending repair by its id
    /run                 → trigger a fresh sweep, return new DEFCON
    /connectors          → list configured AWS SSM connectors
    /help                → show command list

Implementation notes
--------------------
- Pure logic, no I/O. The handler calls existing modules (load_connectors,
  load_snapshot, etc.) and returns formatted strings.
- Defensive: unknown command → friendly help message. Missing args → usage hint.
- All responses are in Hebrew to match the dashboard UI.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


# --- Result type --------------------------------------------------------

@dataclass
class CommandResult:
    """Outcome of a command parse + dispatch.

    Attributes:
        text: The reply text (Markdown allowed).
        response_type: "ephemeral" (only visible to user) or "in_channel"
                       (visible to whole channel). Default ephemeral for safety.
        ok: True if the command was understood, False for unknown.
    """
    text: str
    response_type: str = "ephemeral"
    ok: bool = True

    def to_slack_response(self) -> dict[str, Any]:
        """Format for immediate JSON response (slash commands)."""
        return {
            "response_type": self.response_type,
            "text": self.text,
        }


# --- Handler ------------------------------------------------------------

class SlackCommandHandler:
    """Parse + dispatch slash commands. No I/O — pure logic."""

    # Map of command name → handler method
    COMMANDS = {
        "defcon": "cmd_defcon",
        "health": "cmd_health",
        "approve": "cmd_approve",
        "reject": "cmd_reject",
        "run": "cmd_run",
        "connectors": "cmd_connectors",
        "help": "cmd_help",
    }

    def handle_message(self, text: str, user: str = "unknown") -> CommandResult:
        """Parse a free-form message and dispatch.

        Accepts both "/defcon arg1 arg2" and "defcon arg1 arg2" (so it works
        whether the user types a slash or just mentions the command name).
        """
        if not text or not text.strip():
            return CommandResult(text="(הודעה ריקה)", ok=False)

        line = text.strip()
        # Strip leading slash if present
        if line.startswith("/"):
            line = line[1:]
        parts = line.split(None, 1)
        cmd = parts[0].lower().lstrip("/")
        args = parts[1].split() if len(parts) > 1 else []

        method_name = self.COMMANDS.get(cmd)
        if method_name is None:
            return self._unknown_command(cmd)
        method = getattr(self, method_name)
        try:
            return method(args=args, user=user)
        except Exception as e:
            return CommandResult(text=f"⚠️ שגיאה פנימית: {e}", ok=False)

    # --- Command implementations ------------------------------------------

    def cmd_defcon(self, args: list[str], user: str) -> CommandResult:
        """Show overall DEFCON + per-host breakdown."""
        from ipracticom_sweeper.fleet import aggregate, load_all_snapshots, ssm_to_aggregator_format
        from ipracticom_sweeper.config import load_connectors
        from ipracticom_sweeper.fleet.aws_connector import HostSnapshot

        connectors = load_connectors()
        snapshots_raw = {s["name"]: s for s in load_all_snapshots()}

        # Local box DEFCON (from cached pipeline result)
        try:
            from ipracticom_sweeper.dashboard import _read_last_result
            local = _read_last_result() or {}
            local_defcon = local.get("defcon", "—")
            local_label = local.get("defcon_label", "—")
        except Exception:
            local_defcon, local_label = "—", "—"

        lines = [
            f"*DEFCON כללי: {local_defcon}* ({local_label})",
            "",
        ]

        if not connectors:
            lines.append("_לא הוגדרו connectors._")
            lines.append(f"להוספה: <https://serial-texas-levitra-tough.trycloudflare.com/settings/connectors|הגדרות>")
            return CommandResult(text="\n".join(lines))

        # Build aggregator snapshots
        converted = []
        for conn in connectors:
            if conn.name not in snapshots_raw:
                converted.append({
                    "server": conn.name, "defcon": 1, "defcon_label": "red",
                    "problems_found": 1, "ts": 0.0, "modules": {"ssm": "crit"},
                    "_reason": "no data yet",
                })
                continue
            entry = snapshots_raw[conn.name]
            snap_dict = entry.get("snapshot", {})
            if not snap_dict.get("available", False):
                converted.append({
                    "server": conn.name, "defcon": 1, "defcon_label": "red",
                    "problems_found": 1, "ts": 0.0, "modules": {"ssm": "crit"},
                    "_reason": snap_dict.get("reason", "unknown"),
                })
                continue
            class _S:
                available = True
                data = snap_dict.get("data") or {}
                reason = ""
            converted.append(ssm_to_aggregator_format(conn.name, _S()))

        summary = aggregate(converted)

        lines.append(f"צי: {summary.total_hosts} שרתים · "
                     f"תקינים: {summary.healthy} · אזהרה: {summary.warning} · קריטי: {summary.critical}")
        lines.append("")
        for h in summary.hosts:
            emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(h.defcon_label, "⚪")
            problems = f" ({h.problems_count} בעיות)" if h.problems_count > 0 else ""
            lines.append(f"{emoji} *{h.host_id}* — DEFCON {h.defcon}{problems}")

        return CommandResult(text="\n".join(lines))

    def cmd_health(self, args: list[str], user: str) -> CommandResult:
        """Show latest metrics for one host (or all if no arg)."""
        from ipracticom_sweeper.fleet import load_snapshot
        from ipracticom_sweeper.config import get_connector, load_connectors

        target = args[0] if args else None

        if target:
            conn = get_connector(target)
            if conn is None:
                return CommandResult(text=f"❌ connector '{target}' לא נמצא. נסה /connectors.")
            snap = load_snapshot(target)
            return self._format_host_health(target, conn.instance_id, conn.region, snap)

        # No arg: show all
        connectors = load_connectors()
        if not connectors:
            return CommandResult(text="לא הוגדרו connectors. נסה /connectors.")

        lines = [f"*בריאות {len(connectors)} שרתים:*", ""]
        for c in connectors:
            snap = load_snapshot(c.name)
            lines.append(self._format_host_health_inline(c.name, snap))
        return CommandResult(text="\n".join(lines))

    def _format_host_health(self, name: str, instance_id: str, region: str,
                            snap: dict | None) -> CommandResult:
        if not snap or not snap.get("snapshot", {}).get("available"):
            reason = "—"
            if snap and snap.get("snapshot", {}).get("reason"):
                reason = snap["snapshot"]["reason"]
            return CommandResult(text=f"*{name}* (`{instance_id}`)\n⚠️ אין נתונים: {reason}")

        d = snap["snapshot"].get("data") or {}
        load = d.get("load") or {}
        mem = d.get("memory") or {}
        disk = d.get("disk") or {}

        lines = [
            f"*{name}* (`{instance_id}` · {region})",
            f"• Load (5m): {load.get('5m', 0):.2f}",
            f"• Memory: {mem.get('used_percent', 0):.1f}%",
            f"• Disk: {disk.get('used_percent', 0):.1f}%",
            f"• Uptime: {int(d.get('uptime_seconds', 0) // 3600)}h",
            f"• Kernel: {d.get('kernel', '—')}",
        ]
        failed = d.get("failed_units") or []
        if failed:
            lines.append(f"• ⚠️ Failed units: {', '.join(failed[:5])}")
        return CommandResult(text="\n".join(lines))

    def _format_host_health_inline(self, name: str, snap: dict | None) -> str:
        if not snap or not snap.get("snapshot", {}).get("available"):
            return f"🔴 *{name}* — אין נתונים"
        d = snap["snapshot"].get("data") or {}
        load = d.get("load") or {}
        mem = d.get("memory") or {}
        disk = d.get("disk") or {}
        return (f"🟡 *{name}* — load {load.get('5m', 0):.1f} · "
                f"mem {mem.get('used_percent', 0):.0f}% · "
                f"disk {disk.get('used_percent', 0):.0f}%")

    def cmd_approve(self, args: list[str], user: str) -> CommandResult:
        """Approve a pending repair by id — actually executes it (same flow as
        the dashboard/API/Telegram)."""
        if not args:
            return CommandResult(text="שימוש: `/approve <id>`")
        repair_id = args[0]
        from ipracticom_sweeper.repair import decide, pending as pending_mod

        try:
            pending_mod._validate_pid(repair_id)
        except ValueError:
            return CommandResult(text=f"❌ מזהה לא תקין: {repair_id!r}")

        res = decide.approve(repair_id, actor=user)
        err = res.get("error")
        if err == "not_found":
            return CommandResult(text=f"❌ '{repair_id}' לא נמצא.")
        if err == "already_decided":
            return CommandResult(text=f"⚠️ ההצעה כבר {res.get('status')}.")
        if res.get("ok"):
            return CommandResult(text=f"✅ אושר ובוצע · approved & executed ({repair_id}) — {user}")
        return CommandResult(
            text=(f"🚨 התיקון נכשל · repair failed ({repair_id}).\n"
                  "חשוב לערב כאן בן אדם לפני ניסיון נוסף — ראו את ההתראה בערוצים.")
        )

    def cmd_reject(self, args: list[str], user: str) -> CommandResult:
        """Reject a pending repair by id, with an optional reason."""
        if not args:
            return CommandResult(text="שימוש: `/reject <id> <סיבה>`")
        repair_id = args[0]
        reason = " ".join(args[1:]).strip() if len(args) > 1 else "rejected via Slack"
        from ipracticom_sweeper.repair import decide, pending as pending_mod

        try:
            pending_mod._validate_pid(repair_id)
        except ValueError:
            return CommandResult(text=f"❌ מזהה לא תקין: {repair_id!r}")

        res = decide.reject(repair_id, actor=user, reason=reason)
        err = res.get("error")
        if err == "not_found":
            return CommandResult(text=f"❌ '{repair_id}' לא נמצא.")
        if err == "already_decided":
            return CommandResult(text=f"⚠️ ההצעה כבר {res.get('status')}.")
        return CommandResult(text=f"🚫 נדחתה · rejected ({repair_id}) — {user}")

    def cmd_run(self, args: list[str], user: str) -> CommandResult:
        """Trigger a fresh sweep. May take a few seconds."""
        try:
            from ipracticom_sweeper.config import load_rules
            from ipracticom_sweeper.pipeline import run_pipeline
            from ipracticom_sweeper.dashboard import _write_last_result, get_server_id
            # Lazy import to avoid heavy pipeline import at module load
            from ipracticom_sweeper.config import get_server_id as _gsid
            rules = load_rules()
            result = run_pipeline(rules, auto_repair=True, dry_run=False)
            d = result.to_dict()
            d["server"] = _gsid()
            _write_last_result(d)
            defcon = d.get("defcon", "?")
            label = d.get("defcon_label", "?")
            problems = len(d.get("problems", []) or [])
            return CommandResult(text=f"✅ sweep הסתיים\nDEFCON: *{defcon}* ({label})\nבעיות שזוהו: {problems}")
        except Exception as e:
            return CommandResult(text=f"⚠️ sweep נכשל: {e}")

    def cmd_connectors(self, args: list[str], user: str) -> CommandResult:
        """List configured AWS SSM connectors."""
        from ipracticom_sweeper.config import load_connectors

        connectors = load_connectors()
        if not connectors:
            return CommandResult(text="לא הוגדרו connectors.\n"
                                    "להוספה: /settings/connectors בדאשבורד.")

        lines = [f"*Connectors ({len(connectors)}):*", ""]
        for c in connectors:
            status = "🟢" if c.enabled else "⚪"
            last = "אף פעם"
            if c.last_collected_at:
                last = f"לפני {int(time.time() - c.last_collected_at)}s"
            err = f" · ⚠️ {c.last_error[:50]}" if c.last_error else ""
            lines.append(f"{status} *{c.name}* — `{c.instance_id}` ({c.region})\n   איסוף אחרון: {last}{err}")
        return CommandResult(text="\n".join(lines))

    def cmd_help(self, args: list[str], user: str) -> CommandResult:
        return CommandResult(text=(
            "*פקודות זמינות:*\n"
            "• `/defcon` — DEFCON כללי + כל השרתים\n"
            "• `/health [host]` — מטריקות עדכניות\n"
            "• `/connectors` — רשימת connectors\n"
            "• `/approve <id>` — אישור וביצוע תיקון ממתין\n"
            "• `/reject <id> <סיבה>` — דחיית תיקון ממתין\n"
            "• `/run` — הרצת sweep חדש\n"
            "• `/help` — הודעה זו"
        ))

    def _unknown_command(self, cmd: str) -> CommandResult:
        return CommandResult(
            text=f"❓ פקודה לא מוכרת: `/{cmd}`. נסה `/help`.",
            ok=False,
        )