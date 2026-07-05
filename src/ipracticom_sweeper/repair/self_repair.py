"""Self-repair: the agent fixing ITSELF, automatically, without approval.

Operator directive: problems on the agent's *own* machine and its own
notification channels are repaired immediately — no approval gate — including a
self restart if the agent looks stuck. The operator is alerted ONLY when a fix
fails or the problem needs a human (e.g. a revoked bot token that only a person
can replace).

This path is deliberately separate from ``pipeline.py``'s repair loop, which
gates *monitored-machine* repairs behind an approval policy. Self-repairs bypass
that gate by construction: the agent owns its own box, so "ask a human first"
would just leave the agent broken.

Inputs come from ``snapshot["self"]`` (built by ``monitor/checks.py``'s self
phase → ``self_snapshot.build_self_section`` + ``bot_connectivity``). Nothing
here raises: every repair is best-effort and a failure becomes an alert, never
a crashed sweep.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import structlog

from .._log import log_suppressed

logger = structlog.get_logger("ipracticom_sweeper.self_repair")

# Persisted alert state — so we don't re-page every 5 minutes for the same
# unresolved issue. We re-alert an unchanged problem at most every 6 hours.
ALERT_STATE_FILE = "self_repair_alert.json"
ALERT_REPEAT_SEC = 6 * 3600

# state-dir disk % that counts as "full enough to act".
_STATE_DIR_CRIT_PCT = 95.0
# Watchdog restarts in the last hour that mean the agent is flapping.
_WATCHDOG_CRIT_COUNT = 3


def _default_state_dir() -> Path:
    return Path(os.environ.get(
        "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper",
    ))


def _do(action: str, kwargs: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """Execute one repair WITHOUT the approval gate. Never raises."""
    if dry_run:
        return {"action": action, "success": True, "dry_run": True,
                "message": f"[dry-run] {action}({kwargs})"}
    try:
        from ipracticom_sweeper.repair import execute_repair
        r = execute_repair(action, **kwargs)
        return {"action": action, "success": bool(r.success),
                "message": r.message, "error": r.error}
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("self_repair_action_failed", action=action, error=str(e))
        return {"action": action, "success": False,
                "message": f"exception: {e}", "error": str(e)}


def run_self_repairs(
    self_section: dict[str, Any] | None,
    *,
    dry_run: bool = False,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """Auto-repair the agent's own problems and alert only on failure/needs-human.

    Returns a summary::

        {"repairs": [...], "problems": [...], "needs_human": bool,
         "alert_sent": bool}
    """
    section = self_section or {}
    state_dir = state_dir or _default_state_dir()
    repairs: list[dict[str, Any]] = []
    problems: list[str] = []       # human-readable, for the alert body

    # --- 1) State-dir / disk pressure → free space (all safe, no approval) ---
    pct = section.get("state_dir_pct")
    if isinstance(pct, (int, float)) and pct >= _STATE_DIR_CRIT_PCT:
        problems.append(f"ספריית ה-state של הסוכן כמעט מלאה ({pct:.0f}%)")
        repairs.append(_do("log_truncate_journald", {"max_age_days": 7}, dry_run))
        repairs.append(_do("rotate_audit_now", {}, dry_run))
        repairs.append(_do("drop_caches", {"level": 3}, dry_run))

    # --- 2) Stuck agent (healthz crit / watchdog flapping) → self restart ----
    healthz_status = (section.get("healthz") or {}).get("status")
    watchdog = section.get("watchdog_restart_count") or 0
    if healthz_status == "crit" or watchdog >= _WATCHDOG_CRIT_COUNT:
        why = "healthz לא מגיב" if healthz_status == "crit" else f"{watchdog} restarts בשעה"
        problems.append(f"הסוכן נראה תקוע ({why}) — מבצע restart עצמי")
        # Try a self-ping first (cheap liveness confirm), then restart the unit.
        repairs.append(_do("self_healthz_ping", {}, dry_run))
        repairs.append(_do("self_agent_restart", {}, dry_run))

    # --- 3) Notification bots dead → cannot auto-fix → needs a human ----------
    bots = section.get("bots") or {}
    bots_crit = bots.get("status") == "crit"
    if bots_crit:
        problems.append(
            "חיבור בוט ההתראות נכשל — "
            + str(bots.get("summary") or "בוט לא מגיב")
            + " — נדרש טוקן/הרשאה חדשים (לא ניתן לתקן אוטומטית)"
        )

    # --- Decide whether a human needs to know --------------------------------
    # A human is needed when: a self-repair FAILED, or a problem has no auto-fix
    # (dead bot). Successful auto-repairs stay silent — the agent handled it.
    failed = [r for r in repairs
              if not r.get("success") and not r.get("dry_run")]
    needs_human = bool(failed) or bots_crit

    alert_sent = False
    if needs_human and not dry_run:
        alert_sent = _maybe_alert(state_dir, problems, failed)

    logger.info(
        "self_repairs_done",
        repairs=len(repairs),
        failed=len(failed),
        needs_human=needs_human,
        alert_sent=alert_sent,
    )
    return {
        "repairs": repairs,
        "problems": problems,
        "needs_human": needs_human,
        "alert_sent": alert_sent,
    }


# --- Alerting (edge-triggered, de-duplicated) --------------------------------


def _signature(problems: list[str], failed: list[dict[str, Any]]) -> str:
    """Stable hash of the current issue set, so an unchanged situation doesn't
    re-page every sweep."""
    basis = "|".join(sorted(problems)) + "||" + "|".join(
        sorted(f"{r.get('action')}:{r.get('message')}" for r in failed)
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _load_alert_state(state_dir: Path) -> dict[str, Any]:
    path = state_dir / ALERT_STATE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        log_suppressed("self_repair_alert_load", e)
        return {}


def _save_alert_state(state_dir: Path, signature: str) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / ALERT_STATE_FILE).write_text(
            json.dumps({"signature": signature, "alerted_at": time.time()}),
            encoding="utf-8",
        )
    except OSError as e:
        log_suppressed("self_repair_alert_save", e)


def _maybe_alert(state_dir: Path, problems: list[str],
                 failed: list[dict[str, Any]]) -> bool:
    """Send an admin alert unless we already alerted for the same issue recently."""
    signature = _signature(problems, failed)
    prev = _load_alert_state(state_dir)
    if (prev.get("signature") == signature
            and (time.time() - float(prev.get("alerted_at", 0))) < ALERT_REPEAT_SEC):
        return False  # same unresolved issue, alerted recently — stay quiet

    lines = ["🛠️ *ניטור עצמי של הסוכן — נדרשת תשומת לב*", ""]
    lines.extend(f"• {p}" for p in problems)
    if failed:
        lines.append("")
        lines.append("תיקונים עצמיים שנכשלו:")
        lines.extend(f"  ✗ {r.get('action')}: {r.get('message')}" for r in failed)
    text = "\n".join(lines)

    sent = _send_admin_alert(text)
    if sent:
        _save_alert_state(state_dir, signature)
    return sent


def _send_admin_alert(text: str) -> bool:
    """Fan out a plain alert to every channel. Never raises."""
    try:
        import asyncio
        from ipracticom_sweeper.notify.legacy import send_admin_alert
        results = asyncio.run(send_admin_alert(text))
        return any(results.values()) if isinstance(results, dict) else False
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("self_repair_alert_send_failed", error=str(e))
        return False
