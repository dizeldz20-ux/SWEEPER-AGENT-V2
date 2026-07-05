"""Flask dashboard for iPracticom AWS Sweeper.

Routes:
  GET  /                          → main dashboard (latest snapshot)
  GET  /history                   → list of recent runs
  GET  /run                       → JSON of latest pipeline result
  GET  /run/now                   → trigger a fresh pipeline run, return JSON
  GET  /api/snapshot              → raw JSON snapshot (last cached)
  POST /api/notify/test           → send a test notification (dev convenience)
  GET  /healthz                   → simple liveness check

Design goals:
  - "Classic & refined, professional" — serif headings (Cormorant Garamond),
    sans body (Inter), neutral palette with one accent color.
  - All data on one screen (no scroll walls) — operators should see DEFCON +
    modules + problems + repairs at a glance.
  - Server-rendered HTML (Jinja2) — no React/build pipeline. Fast to render,
    accessible, and editable.
  - Pure HTML/CSS — no JS framework, just a tiny sprinkle for auto-refresh.

The dashboard reads cached pipeline results from disk (written by the sweeper
after each run) — it does NOT trigger its own runs. Use `sweeper` CLI /
systemd timer to populate data, then visit the dashboard to view it.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from urllib.parse import urlparse

from ipracticom_sweeper.agent_client import AgentClient, AgentError
from ipracticom_sweeper.spa_context import shape_spa_context
from ipracticom_sweeper._log import log_suppressed
from ipracticom_sweeper.config import (
    Connector,
    get_server_id,
    load_rules,
    load_connectors,
    add_connector,
    update_connector,
    remove_connector,
)
from ipracticom_sweeper.pipeline import run_pipeline

# --- Paths -------------------------------------------------------------------

CACHE_DIR = Path("/var/lib/ipracticom-sweeper/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LAST_RESULT_FILE = CACHE_DIR / "last-result.json"


# --- Flask app ---------------------------------------------------------------

app = Flask(__name__)


# --- Chat routes (v0.5.0 slice 3.1) -------------------------------------------

try:
    from ipracticom_sweeper.chat import register_chat_routes as _register_chat
    # Dashboard's own `before_request` (basic-auth + CSRF) already gates
    # every route, including chat — don't double-gate.
    _register_chat(app, auth_required=False)
except Exception as _chat_exc:  # pragma: no cover -- optional dep path
    # Log so dashboard still boots if flask-sock missing.
    import sys
    print(f"[chat] chat routes disabled: {_chat_exc}", file=sys.stderr)


# --- Jinja filters (Hebrew translation) ---------------------------------------

_DEFCON_HE = {"green": "תקין", "yellow": "אזהרה", "orange": "חמור", "red": "קריטי", "black": "אסון"}
_MODULE_STATUS_HE = {"ok": "תקין", "warn": "אזהרה", "crit": "קריטי"}
_SEVERITY_HE = {"warn": "אזהרה", "crit": "קריטי"}


@app.template_filter("defcon_label_hebrew")
def _defcon_label_hebrew(label: str) -> str:
    return _DEFCON_HE.get((label or "").lower(), label or "")


@app.template_filter("module_status_hebrew")
def _module_status_hebrew(status: str) -> str:
    return _MODULE_STATUS_HE.get((status or "").lower(), status or "")


@app.template_filter("severity_hebrew")
def _severity_hebrew(sev: str) -> str:
    return _SEVERITY_HE.get((sev or "").lower(), sev or "")


# --- HTTP Basic auth (gated by DASHBOARD_USER / DASHBOARD_PASS env) ---------
_DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "")
_DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "")


@app.before_request
def _require_basic_auth():
    """Gate every dashboard route with HTTP Basic auth.

    /healthz is always open (used by cloudflared liveness probes).
    When DASHBOARD_USER/DASHBOARD_PASS are unset, dashboard stays open — caller
    should bind to 127.0.0.1 only.

    v1.5.9 fix: also enforces a CSRF check on POST routes via Origin/Referer.
    Browsers send the Origin header on cross-origin requests; comparing it to
    the dashboard's own host rejects form-submit CSRF from external sites.
    """
    if not (_DASHBOARD_USER and _DASHBOARD_PASS):
        return None  # open mode
    if request.path == "/healthz":
        return None  # liveness probe
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:].strip(), validate=True).decode("utf-8")
            user, _, pwd = decoded.partition(":")
            if hmac.compare_digest(user, _DASHBOARD_USER) and hmac.compare_digest(pwd, _DASHBOARD_PASS):
                # CSRF gate (POST only): require Origin to match our host.
                # v1.5.15 (C-1 fix): reject state-mutating requests with no
                # Origin header at all. Browsers always send Origin on POST;
                # the only clients that omit it are scripts/curl that are
                # trivially fixable to include it. Without this gate, an
                # attacker with credentials could bypass CSRF entirely.
                if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                    origin = request.headers.get("Origin", "")
                    if not origin:
                        resp = jsonify({"error": "csrf_origin_missing",
                                        "reason": "Origin header required on state-mutating requests"})
                        resp.status_code = 403
                        return resp
                    if not _csrf_origin_ok(origin):
                        resp = jsonify({"error": "csrf_origin_mismatch",
                                        "origin": origin})
                        resp.status_code = 403
                        return resp
                return None
        except Exception as exc:
            log_suppressed("dashboard.basic_auth_decode", exc)
    resp = jsonify({"error": "unauthorized", "reason": "missing or invalid Basic credentials"})
    resp.status_code = 401
    resp.headers["WWW-Authenticate"] = 'Basic realm="sweeper-dashboard"'
    return resp


def _csrf_origin_ok(origin: str) -> bool:
    """Allow POST/PATCH/DELETE only when Origin matches our own host.

    `origin` is the browser-supplied value (e.g. "http://127.0.0.1:8804").
    We accept loopback origins, the dashboard's own request host (so
    production deployments on a non-loopback hostname "just work"
    without DASHBOARD_TRUSTED_ORIGINS configuration), plus any host
    the operator has explicitly trusted via DASHBOARD_TRUSTED_ORIGINS
    (comma-separated).

    v1.5.17 #16: previously this only trusted loopback + the operator
    list. On a production deployment bound to e.g. http://dash.internal:8804,
    browsers sent Origin matching the request Host but no entry was in
    DASHBOARD_TRUSTED_ORIGINS, so every POST 403'd. Now we compare against
    request.host automatically.
    """
    if not origin:
        return False  # missing Origin header → reject (most browsers send it)
    parsed = urlparse(origin)
    host = parsed.hostname or ""
    # Always allow loopback (127.0.0.1, ::1, localhost).
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    # v1.5.17 #16: trust the dashboard's own host automatically.
    # request.host comes from the HTTP Host header the client sent.
    request_host = (request.host or "").split(":")[0]
    if request_host and host == request_host:
        return True
    # Operator-trusted origins (comma-separated). Exact-match hostname.
    trusted = os.environ.get("DASHBOARD_TRUSTED_ORIGINS", "").split(",")
    for t in trusted:
        t = t.strip()
        if not t:
            continue
        try:
            tp = urlparse(t)
            if (tp.hostname or "") == host and tp.scheme in ("http", "https"):
                return True
        except Exception as exc:
            log_suppressed("dashboard.is_local_url.parse", exc)
            continue
    return False


# --- Cache management --------------------------------------------------------
# Pure cache I/O lives in `dashboard_helpers` (extracted v1.5.12 to shrink
# this file below the 2k-line mark). We re-export the public names so callers
# of `from ipracticom_sweeper.dashboard import _read_last_result` (and the
# sister helpers) keep working without an import-path migration.
from ipracticom_sweeper.dashboard_helpers import (  # noqa: E402,F401
    CACHE_DIR,
    LAST_RESULT_FILE,
    read_last_result,
    trigger_pipeline_run,
    write_last_result,
)

# Back-compat aliases — old internal callers used underscore prefixes.
_read_last_result = read_last_result
_write_last_result = write_last_result


# --- Mode resolution ---------------------------------------------------------


def _remote_url() -> str | None:
    """Return the configured remote agent URL, or None for local mode."""
    return os.environ.get("SWEEPER_REMOTE_URL")


def _remote_token() -> str | None:
    return os.environ.get("SWEEPER_REMOTE_TOKEN")


def _is_remote_mode() -> bool:
    return bool(_remote_url())


def _get_agent() -> AgentClient | None:
    """Build a remote agent client if remote mode is configured."""
    url = _remote_url()
    if not url:
        return None
    return AgentClient(url, token=_remote_token())


def _fetch_snapshot() -> dict[str, Any] | None:
    """Get the latest snapshot, from remote agent if configured, else local cache."""
    if _is_remote_mode():
        try:
            return _get_agent().get_snapshot()
        except AgentError as e:
            # Log and fall back to local cache if remote fails
            app.logger.warning("remote_snapshot_failed", error=str(e))
    return _read_last_result()


def _fetch_identity() -> dict[str, Any]:
    """Get identity info for display: local server_id or remote agent."""
    if _is_remote_mode():
        try:
            return _get_agent().remote_identity()
        except AgentError:
            return {"kind": "remote", "base_url": _remote_url(), "error": "unreachable"}
    return AgentClient.local_identity()


def _fetch_rules_summary() -> dict:
    """Get rules summary. Local only — remote agent uses its own rules."""
    if _is_remote_mode():
        # Remote agent has its own rules; show a placeholder
        return {"_remote": True}
    return _summarize_rules(load_rules())


def _fetch_heartbeat():
    """Read the heartbeat from the local agent (none for remote dashboards)."""
    if _is_remote_mode():
        return None
    try:
        from ipracticom_sweeper.monitor.health import check_health
        return check_health(expected_interval_seconds=300.0)
    except Exception as e:
        app.logger.warning("heartbeat_fetch_failed", error=str(e))
        return None


def _count_pbx_hosts(summary) -> int:
    """Hosts with FreeSWITCH (FS-01..FS-25 collected). Lightweight heuristic.

    Slice 5.3 stays read-only — counts by hostname prefix or explicit tag in the
    aggregated summary. Returns 0 if data unavailable.
    """
    if not summary or not isinstance(summary, dict):
        return 0
    hosts = summary.get("hosts") or summary.get("per_host") or {}
    if isinstance(hosts, dict):
        # Any host whose name hints at PBX (fs-, freeswitch-, pbx-) counts.
        pbx_hosts = {
            name for name in hosts
            if any(tok in name.lower() for tok in ("fs-", "freeswitch", "pbx"))
        }
        return len(pbx_hosts)
    return 0


def _fetch_v6_stats() -> dict:
    """Compute the 4-card stats bar for the v6 dashboard.

    Cards:
      - total_machines: fleet summary total_hosts
      - pbx_count: hosts matching FS prefix heuristic
      - critical_count: snapshot's repairs_failed + problems_found (heuristic),
        or `needs_human` if available
      - events_today: count of events in the SQLite store for today

    Every field falls back to "—" if data unavailable (no fabricated numbers).
    """
    from datetime import datetime, timezone

    stats = {
        "total_machines": "—",
        "pbx_count": "—",
        "critical_count": "—",
        "events_today": "—",
        "defcon": None,
    }

    # Fleet (multi-host) view — only available locally.
    if not _is_remote_mode():
        try:
            from ipracticom_sweeper.fleet import aggregate, load_all_snapshots
            connectors = load_connectors()
            snapshots = load_all_snapshots()
            if connectors or snapshots:
                # Use the aggregator on whatever snapshots we have; if empty,
                # build a no-data placeholder that mirrors the fleet summary shape.
                converted = []
                for s in snapshots:
                    inner = s.get("snapshot", {}) or {}
                    converted.append({
                        "name": s.get("name", "?"),
                        "snapshot": inner,
                    })
                summary = aggregate(converted)
                stats["total_machines"] = summary.get("total_hosts", 0)
                stats["pbx_count"] = _count_pbx_hosts(summary)
        except Exception as e:
            app.logger.warning("v6_stats_fleet_failed: %s", e)

    # Snapshot-derived metrics.
    try:
        snap = _fetch_snapshot()
        if snap:
            # Critical count: prefer an explicit `critical` key, else sum
            # problems_found + repairs_failed when both are present.
            crit = snap.get("critical")
            if isinstance(crit, int):
                stats["critical_count"] = crit
            else:
                problems = snap.get("problems_found", 0)
                rep_failed = snap.get("repairs_failed", 0)
                try:
                    stats["critical_count"] = int(problems) + int(rep_failed)
                except (TypeError, ValueError) as exc:
                    log_suppressed("dashboard.stats.critical_count", exc)
            d = snap.get("defcon")
            if isinstance(d, int):
                stats["defcon"] = d
    except Exception as e:
        app.logger.warning("v6_stats_snapshot_failed: %s", e)

    # Events today from SQLite store.
    try:
        from ipracticom_sweeper.state.sqlite_store import init_db, count_events_since
        init_db()
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        n = count_events_since(today_start)
        if isinstance(n, int):
            stats["events_today"] = n
    except Exception as e:
        # Module missing or table not migrated — leave as "—".
        app.logger.debug("v6_stats_events_today_unavailable: %s", e)

    return stats


# --- Notification settings (Telegram + Slack) ----------------------------
# --- Notification settings (Telegram + Slack) ----------------------------
# Implementation lives in dashboard_notify.py. Re-exported below for
# back-compat with routes that import these symbols from this module.

# --- Routes ------------------------------------------------------------------


@app.route("/")
def index():
    """Main dashboard — the unified SPA shell (AI Studio design) with real
    snapshot data from the agent.

    /spa/a and /spa/b still exist for the side-by-side A/B comparison.
    """
    ctx = shape_spa_context(_fetch_snapshot())
    return render_template("home.html", ctx=ctx)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Notification configuration (Telegram + Slack). Local-only."""
    if _is_remote_mode():
        return render_template("error.html", message="הגדרות זמינות רק במצב מקומי"), 403

    saved_message = None
    error_message = None
    if request.method == "POST":
        # Save action
        values = {
            "SLACK_WEBHOOK_URL": request.form.get("slack_webhook_url", ""),
            "TELEGRAM_BOT_TOKEN": request.form.get("telegram_bot_token", ""),
            "TELEGRAM_CHAT_ID": request.form.get("telegram_chat_id", ""),
        }
        # Validate Slack webhook URL allowlist (SSRF guard) before persistence.
        ok_slack, err_slack = _validate_slack_webhook_url(values["SLACK_WEBHOOK_URL"])
        if not ok_slack:
            error_message = err_slack
            current = _read_notifications_env()
            return render_template(
                "settings.html",
                identity=_fetch_identity(),
                is_remote=_is_remote_mode(),
                current=current,
                saved_message=None,
                error_message=error_message,
            ), 400
        ok, err = _write_notifications_env(values)
        if ok:
            saved_message = "ההגדרות נשמרו. הסוכן יטען אותן בריצה הבאה (עד 5 דקות)."
        else:
            error_message = err or "שגיאה לא ידועה בכתיבה"

    current = _read_notifications_env()
    return render_template(
        "settings.html",
        identity=_fetch_identity(),
        is_remote=_is_remote_mode(),
        current=current,
        saved_message=saved_message,
        error_message=error_message,
    )


@app.route("/settings/test", methods=["POST"])
def settings_test():
    """Test the current (or just-saved) notification channels. Returns JSON."""
    if _is_remote_mode():
        return jsonify({"ok": False, "error": "settings are local-only"}), 403
    current = _read_notifications_env()
    channel = request.form.get("channel", "")
    if channel == "telegram":
        ok, msg = _test_telegram(
            current.get("TELEGRAM_BOT_TOKEN", ""),
            current.get("TELEGRAM_CHAT_ID", ""),
        )
    elif channel == "slack":
        ok, msg = _test_slack(current.get("SLACK_WEBHOOK_URL", ""))
    else:
        return jsonify({"ok": False, "error": "unknown channel"}), 400
    return jsonify({"ok": ok, "message": msg})


@app.route("/history")
def history():
    """List recent sweep results + repairs + proposals. In remote mode, fetches from agent."""
    runs = _load_history_runs()
    repairs = _load_history_repairs()
    proposals = _load_history_proposals()

    return render_template(
        "history.html",
        runs=runs,
        repairs=repairs,
        proposals=proposals,
        identity=_fetch_identity(),
        is_remote=_is_remote_mode(),
        now_iso=datetime.now(timezone.utc).isoformat(),
    )


@app.route("/run")
def run_view():
    """JSON view of the latest pipeline result (cached)."""
    result = _read_last_result()
    if not result:
        return jsonify({"error": "no cached result"}), 404
    return jsonify(result)


@app.route("/run/now", methods=["POST"])
def run_now():
    """Trigger a fresh sweep. In remote mode, asks the remote agent.

    POST-only (v1.5.16 hardening). GET is rejected with 405 because:
    - Pipeline runs are NOT idempotent (HTTP semantics require GET to be).
    - Browser pre-fetch, link previews, and crawlers can otherwise fire
      full pipelines at will.
    - GET bypasses the CSRF Origin check enforced on POST/PUT/PATCH/DELETE.

    Returns JSON by default; redirects to dashboard when called from the UI
    (?ui=1 or Accept: text/html).
    """
    wants_html = (
        request.args.get("ui") == "1"
        or "text/html" in request.headers.get("Accept", "")
    )

    if _is_remote_mode():
        try:
            result = _get_agent().trigger_run()
        except AgentError as e:
            app.logger.exception("run_now_remote_failed")
            if wants_html:
                return render_template(
                    "error.html",
                    message="שגיאה בהפעלה מרחוק (פרטים בלוג)",
                ), 502
            return _safe_error_response(e, 502)
        if wants_html:
            return _redirect_to_dashboard()
        return jsonify(result)

    try:
        result = trigger_pipeline_run()
    except Exception as e:
        app.logger.exception("run_now_failed")
        if wants_html:
            return render_template(
                "error.html",
                message="שגיאה בהפעלה (פרטים בלוג)",
            ), 500
        return _safe_error_response(e, 500)

    if wants_html:
        return _redirect_to_dashboard()
    return jsonify(result)


def _redirect_to_dashboard():
    from flask import redirect, url_for
    return redirect(url_for("index"))


@app.route("/api/snapshot")
def api_snapshot():
    """Same as /run — alias for consistency with agent API."""
    return run_view()


# --- SPA dashboard variants (A / B chooser) ---------------------------------


@app.route("/spa")
def spa_chooser():
    """Legacy chooser — A is now the design of record, redirect to root.

    /spa/a and /spa/b remain available for visual A/B review.
    """
    return redirect(url_for("index"))


@app.route("/spa/a")
def spa_variant_a():
    """Variant A — faithful Google AI Studio port, rendered with real data."""
    ctx = shape_spa_context(_fetch_snapshot())
    return render_template("spa_variant_a.html", ctx=ctx)


@app.route("/spa/b")
def spa_variant_b():
    """Variant B — impeccable-polished dashboard, rendered with real data."""
    ctx = shape_spa_context(_fetch_snapshot())
    return render_template("spa_variant_b.html", ctx=ctx)


@app.route("/api/notify/test", methods=["POST"])
def api_notify_test():
    """Send a test notification. In remote mode, asks the remote agent."""
    if _is_remote_mode():
        try:
            return jsonify(_get_agent().send_test_notify())
        except AgentError as e:
            return _safe_error_response(e, 502)

    import asyncio
    from ipracticom_sweeper.notify import notify_pipeline_result

    result = _read_last_result()
    if not result:
        return jsonify({"error": "no cached result to use as template"}), 404

    try:
        sent = asyncio.run(notify_pipeline_result(result, force=True))
        return jsonify({"sent": sent})
    except Exception as e:
        return _safe_error_response(e, 500)


@app.route("/approvals")
def approvals_view():
    """List all pending repair proposals awaiting operator approval."""
    if _is_remote_mode():
        return render_template("error.html", message="אישורים זמינים רק במצב מקומי"), 403

    from ipracticom_sweeper.repair.pending import list_pending, cleanup_stale_pending
    cleanup_stale_pending()

    return render_template(
        "approvals.html",
        identity=_fetch_identity(),
        is_remote=_is_remote_mode(),
        pending=list_pending(),
        now_iso=datetime.now(timezone.utc).isoformat(),
    )


@app.route("/approvals/<pid>")
def approval_detail(pid):
    """Single-proposal detail view with full context."""
    if _is_remote_mode():
        return render_template("error.html", message="אישורים זמינים רק במצב מקומי"), 403

    from ipracticom_sweeper.repair.pending import get_proposal
    p = get_proposal(pid)
    if p is None:
        return render_template("error.html", message=f"בקשה {pid} לא נמצאה"), 404
    return render_template(
        "approval_detail.html",
        identity=_fetch_identity(),
        is_remote=_is_remote_mode(),
        proposal=p,
        now_iso=datetime.now(timezone.utc).isoformat(),
    )


@app.route("/approvals/<pid>/approve", methods=["POST"])
def approval_approve(pid):
    """Approve a pending repair: run it, log the action, archive the proposal."""
    if _is_remote_mode():
        return render_template("error.html", message="אישורים זמינים רק במצב מקומי"), 403

    from ipracticom_sweeper.repair import execute_repair
    from ipracticom_sweeper.repair.pending import (
        _proposal_lock, archive, get_proposal, log_audit, set_status,
    )

    # v1.5.15 (C-2 fix): per-proposal flock — see agent_api approve for rationale.
    from ipracticom_sweeper.repair import decide as decide_mod
    with _proposal_lock(pid):
        p = get_proposal(pid)
        if p is None:
            return render_template("error.html", message=f"בקשה {pid} לא נמצאה"), 404
        if p.status != "pending":
            return render_template("error.html", message=f"הבקשה כבר {p.status}"), 409

        # SECURITY: actor from authenticated principal (never request.form);
        # redact secrets from kwargs before audit (mirrors agent_api).
        actor = _actor_from_request()
        safe_kwargs = _redact_secrets(p.kwargs) if hasattr(p, "kwargs") else {}
        log_audit({
            "kind": "repair_approved",
            "actor": actor,
            "proposal_id": pid,
            "action": p.action,
            "kwargs": safe_kwargs,
        })

        try:
            result = execute_repair(p.action, **p.kwargs)
            set_status(pid, "executed")
            archive(pid, "approved")
            log_audit({
                "kind": "repair_executed",
                "actor": actor,
                "proposal_id": pid,
                "action": result.action,
                "target": result.target,
                "success": result.success,
                "duration_ms": result.duration_ms,
                "snapshot_id": result.snapshot_id,
                "error": result.error,
                "message": result.message,
            })
            # Escalation + outcome push to every channel (block on failure).
            decide_mod.escalate_and_notify(p, result, bool(result.success), actor)
            return _redirect_to_dashboard()
        except Exception as e:
            app.logger.exception("approval_execute_failed")
            set_status(pid, "failed")
            log_audit({
                "kind": "repair_failed", "actor": actor, "proposal_id": pid,
                "action": p.action, "kwargs": safe_kwargs, "error": str(e),
            })
            decide_mod.escalate_and_notify(p, {"action": p.action, "success": False, "error": str(e)}, False, actor)
        return render_template("error.html", message="שגיאה בביצוע. בדוק את היומנים."), 500


@app.route("/approvals/<pid>/reject", methods=["POST"])
def approval_reject(pid):
    """Reject a pending repair: archive, log, do not execute."""
    if _is_remote_mode():
        return render_template("error.html", message="אישורים זמינים רק במצב מקומי"), 403

    from ipracticom_sweeper.repair.pending import (
        _proposal_lock, archive, get_proposal, log_audit, set_status,
    )

    # v1.5.15 (C-2 fix): same flock as approval_approve. Without it, a
    # concurrent approve + reject could both pass the status check and
    # both write — leaving the proposal in an inconsistent state.
    with _proposal_lock(pid):
        p = get_proposal(pid)
        if p is None:
            return render_template("error.html", message=f"בקשה {pid} לא נמצאה"), 404
        if p.status != "pending":
            return render_template("error.html", message=f"הבקשה כבר {p.status}"), 409

        # SECURITY: derive actor from authenticated principal, never from request.form.
        actor = _actor_from_request()
        safe_kwargs = _redact_secrets(p.kwargs) if hasattr(p, "kwargs") else {}
        reason = request.form.get("reason", "")

        set_status(pid, "rejected")
        archive(pid, "rejected")
        log_audit({
            "kind": "repair_rejected",
            "actor": actor,
            "proposal_id": pid,
            "action": p.action,
            "kwargs": safe_kwargs,
            "reason": reason,
        })
        # Inform every channel the decision was made (no block on rejection).
        from ipracticom_sweeper.repair import decide as decide_mod
        decide_mod.notify_rejected(p, reason, actor)
        return _redirect_to_dashboard()


# Hostname validation + secret redactor + safe error response
# + maintenance state — implementations live in dashboard_maintenance.py.
# Re-exported below for back-compat with routes/tests that import these
# symbols from this module.

# _actor_from_request stays here because it depends on Flask's `request`
# context (cannot be cleanly extracted without restructuring the request
# lifecycle — future work if needed).


def _actor_from_request() -> str:
    """Derive the audit `actor` from the authenticated principal.

    SECURITY: never trust request.form['actor'] for the audit log. That would
    allow any operator who can submit a form to spoof another operator's
    approval/rejection in the audit trail. Instead, derive the actor from:
      1. HTTP basic auth (request.authorization.username) — preferred
      2. DASHBOARD_USER env var — for setups where reverse-proxy auth is used
    Form-supplied actor is silently ignored.
    """
    auth = request.authorization
    if auth and auth.username:
        return auth.username
    env_user = os.environ.get("DASHBOARD_USER", "")
    if env_user:
        return env_user
    return "operator"  # last-resort fallback when no auth is configured


@app.route("/v6/machines/<host>/maintenance", methods=["POST"])
def v6_machines_maintenance(host: str):
    """v0.6.0 — slice 6.2: enable maintenance mode for a host.

    Body (form-encoded):
        duration_min: int  — 15 / 60 / 240 / 0 (= indefinite). Validated.
    Rejects unknown durations with 400.

    This is metadata-only — the monitoring agents check this file before
    auto-repair. It is NOT a destructive op and therefore does NOT route
    through the approvals queue (operator's standing rule).
    """
    valid_durations = (15, 60, 240, 0)
    raw = request.form.get("duration_min", "15")
    try:
        duration = int(raw)
    except ValueError:
        return jsonify({"ok": False, "error": f"duration_min must be int, got {raw!r}"}), 400
    if duration not in valid_durations:
        return jsonify({
            "ok": False,
            "error": f"duration_min must be one of {sorted(valid_durations)}, got {duration}",
        }), 400

    now = datetime.now(timezone.utc)
    expires_at = None if duration == 0 else (
        now.timestamp() + duration * 60
    )
    state = {
        "host": host,
        "enabled_at": now.isoformat(),
        "duration_min": duration,
        "expires_at_ts": expires_at,
    }
    _save_maintenance_state(host, state)
    return jsonify({"ok": True, "state": state})


@app.route("/v6/machines/<host>/maintenance/off", methods=["POST"])
def v6_machines_maintenance_off(host: str):
    """v0.6.0 — slice 6.2: exit maintenance mode (instant clear)."""
    prev = _save_maintenance_state(host, None)
    return jsonify({"ok": True, "previous": prev})


def _enqueue_machine_action_proposal(*, host: str, action: str, reason: str, command: str) -> dict:
    """Write a `RepairProposal` so the operator must approve in `/approvals`.

    Used for destructive ops (reboot, agent_restart, ssm_connect). The proposal
    shows up in the existing approvals queue with the exact command that would
    be executed on approval. No state mutation happens here.
    """
    from ipracticom_sweeper.repair.pending import create_proposal
    proposal = create_proposal(
        action=action,
        kwargs={"host": host},
        reason=reason,
        problem={"host": host, "source": "v6_machines"},
        proposed_command=command,
    )
    return proposal.to_dict()


@app.route("/v6/machines/<host>/action", methods=["POST"])
def v6_machines_action(host: str):
    """v0.6.0 — slice 6.2: enqueue a destructive machine action.

    Body (form-encoded): `action` in {agent_restart, reboot, ssm_connect}.
    Every action writes a RepairProposal and returns the proposal id. The
    operator must visit `/approvals/<pid>/approve` to actually execute it.

    No state mutation here — this slice is queue-only.
    """
    _validate_hostname(host)
    if _is_remote_mode():
        return jsonify({"ok": False, "error": "machine actions local-only"}), 400

    op = (request.form.get("action") or "").strip()
    if op not in ("agent_restart", "reboot", "ssm_connect"):
        return jsonify({
            "ok": False,
            "error": f"unknown action {op!r}; expected agent_restart|reboot|ssm_connect",
        }), 400

    quoted = shlex.quote(host)
    if op == "agent_restart":
        proposal = _enqueue_machine_action_proposal(
            host=host,
            action="agent_restart",
            reason=f"agent restart requested for {host} via v6 machines page",
            command=f"systemctl restart ipracticom-sweeper-agent@{quoted}",
        )
    elif op == "reboot":
        proposal = _enqueue_machine_action_proposal(
            host=host,
            action="reboot",
            reason=f"reboot requested for {host} via v6 machines page",
            command=f"ssh {quoted} 'sudo shutdown -r now'",
        )
    else:  # ssm_connect
        proposal = _enqueue_machine_action_proposal(
            host=host,
            action="ssm_connect",
            reason=f"SSM session requested for {host} via v6 machines page",
            command=(
                f"aws ssm start-session --target $(aws ssm describe-instances "
                f"--filters Name=tag:Name,Values={quoted} "
                f"--query 'Reservations[].Instances[].InstanceId' --output text)"
            ),
        )

    return jsonify({"ok": True, "queued": True, "proposal": proposal})


@app.route("/v6/metrics/events_heatmap")
def v6_metrics_events_heatmap():
    """v0.6.0 — slice 7.3: 24h × 7d heatmap of event counts.

    Returns a 7×24 grid (rows = days, cols = hours UTC) of event counts
    aggregated from the monitor audit log. Empty grid when the log is
    unavailable (no fabricated zeros).
    """
    days = 7
    hours = 24
    grid = [[0 for _ in range(hours)] for _ in range(days)]
    audit = Path("/var/lib/ipracticom-sweeper/audit/monitor.jsonl")
    if not audit.exists():
        return jsonify({"grid": grid, "days": days, "hours": hours, "source": "no-data"})
    from datetime import datetime as _dt
    import json as _json
    now_ts = datetime.now(timezone.utc).timestamp()
    try:
        with audit.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = _json.loads(line)
                except _json.JSONDecodeError as exc:
                    log_suppressed("dashboard.events_heatmap.json_decode", exc)
                    continue
                ts_str = ev.get("ts", "")
                if not ts_str:
                    continue
                try:
                    ev_dt = _dt.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError as exc:
                    log_suppressed("dashboard.events_heatmap.iso_parse", exc)
                    continue
                if ev_dt.tzinfo is None:
                    ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                delta_s = now_ts - ev_dt.timestamp()
                if delta_s < 0 or delta_s > days * 24 * 3600:
                    continue
                day_idx = int(delta_s // (24 * 3600))     # 0 = today
                hour_idx = ev_dt.hour
                if 0 <= day_idx < days and 0 <= hour_idx < hours:
                    grid[days - 1 - day_idx][hour_idx] += 1
    except OSError as e:
        app.logger.warning("v6_metrics_heatmap_read_failed: %s", e)
    return jsonify({"grid": grid, "days": days, "hours": hours, "source": str(audit)})


@app.route("/v6/metrics/uptime_30d")
def v6_metrics_uptime_30d():
    """v0.6.0 — slice 7.3: 30-day uptime area data.

    Per-day ratio of non-critical events. Reads from the same audit log.
    Returns a list of 30 {date, ratio} entries (newest last).
    """
    days = 30
    out = []
    audit = Path("/var/lib/ipracticom-sweeper/audit/monitor.jsonl")
    if not audit.exists():
        return jsonify({"points": out, "days": days, "source": "no-data"})
    from collections import Counter
    from datetime import datetime as _dt
    import json as _json
    per_day = Counter()
    crit_per_day = Counter()
    now = datetime.now(timezone.utc)
    cutoff = now - _td(days=days)
    try:
        with audit.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = _json.loads(line)
                except _json.JSONDecodeError as exc:
                    log_suppressed("dashboard.uptime_30d.json_decode", exc)
                    continue
                ts_str = ev.get("ts", "")
                if not ts_str:
                    continue
                try:
                    ev_dt = _dt.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError as exc:
                    log_suppressed("dashboard.uptime_30d.iso_parse", exc)
                    continue
                if ev_dt.tzinfo is None:
                    ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                if ev_dt < cutoff:
                    continue
                key = ev_dt.date().isoformat()
                per_day[key] += 1
                if (ev.get("status") or "").lower() in ("crit", "red", "orange"):
                    crit_per_day[key] += 1
    except OSError as e:
        app.logger.warning("v6_metrics_uptime_read_failed: %s", e)
    # Build day-by-day series even on days with no events (ratio=1.0).
    for d in range(days - 1, -1, -1):
        day = (now - _td(days=d)).date().isoformat()
        total = per_day.get(day, 0)
        crit = crit_per_day.get(day, 0)
        ratio = 1.0 - (crit / total) if total > 0 else 1.0
        out.append({"date": day, "ratio": round(ratio, 3)})
    return jsonify({"points": out, "days": days, "source": str(audit)})


from datetime import timedelta as _td  # noqa: E402  (used by 7.3 endpoints above)


@app.route("/v6/metrics/page")
def v6_metrics_page():
    """v0.6.0 — slice 7.3: HTML wrapper around /v6/metrics/* JSON."""
    return render_template(
        "v6_metrics.html",
        identity=_fetch_identity(),
        is_remote=_is_remote_mode(),
        now_iso=datetime.now(timezone.utc).isoformat(),
    )


@app.route("/v6/logs")
def v6_logs():
    """v0.6.0 — slice 7.2: tail the agent's monitor log.

    Returns JSON with the LAST N lines from the chosen log file. Defaults to
    the FreeSWITCH log if present; otherwise falls back to the sweeper's own
    monitor audit log. Pure read — never touch the host.
    """
    target = _pick_v6_log_target()
    lines = _tail_log_file(target, max_lines=200)
    return jsonify({
        "log": target.name if target else None,
        "log_path": str(target) if target else None,
        "lines": lines,
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def _pick_v6_log_target() -> Path | None:
    """Pick the best-available log to tail.

    Priority: FreeSWITCH log if it exists, else sweeper monitor audit.
    Returns None if neither is reachable.
    """
    for path in (
        Path("/var/log/freeswitch/freeswitch.log"),
        Path("/var/log/freeswitch/freeswitch.log.1"),
        Path("/var/lib/ipracticom-sweeper/audit/monitor.jsonl"),
    ):
        try:
            if path.exists() and path.is_file():
                return path
        except OSError as exc:
            log_suppressed("dashboard.pick_v6_log_target.stat", exc)
            continue
    return None


def _tail_log_file(path: Path | None, max_lines: int = 200) -> list[str]:
    """Return the last N lines of `path` as a list of strings (no NL)."""
    if path is None or not path.exists():
        return []
    try:
        # Efficient tail: seek to ~64KB from EOF for big logs.
        size = path.stat().st_size
        chunk = 64 * 1024
        with path.open("rb") as f:
            if size > chunk:
                f.seek(size - chunk)
                f.readline()  # skip partial line at boundary
            data = f.read().decode("utf-8", errors="replace")
        return data.splitlines()[-max_lines:]
    except OSError:
        return []


@app.route("/v6/logs/page")
def v6_logs_page():
    """v0.6.0 — slice 7.2: HTML wrapper around /v6/logs JSON."""
    return render_template(
        "v6_logs.html",
        identity=_fetch_identity(),
        is_remote=_is_remote_mode(),
        now_iso=datetime.now(timezone.utc).isoformat(),
    )


@app.route("/v6/alerts")
def v6_alerts():
    """v0.6.0 — slice 7.1: live alerts feed.

    Aggregates recent non-ok events from the monitor audit log. Read-only in
    this slice — snooze/mark-resolved arrive in slice 7.2 via the existing
    approvals gate. Tabs in the URL (?tab=network|performance|security|system)
    filter the visible events. Polled client-side every 5s by `/_v6/alerts.js`.
    """
    runs = _load_history_runs()
    # Map events to "alerts" (status != ok). `ts` is an ISO string.
    alerts = []
    for r in runs:
        status = (r.get("status") or "").lower()
        if status in ("crit", "warn", "yellow", "red", "orange"):
            alerts.append({
                "ts": r.get("ts", ""),
                "module": r.get("module", "?"),
                "status": status,
                "host": r.get("host", "—") if isinstance(r, dict) else "—",
            })
    # Heuristic tab classification (by module keyword).
    def classify(mod: str) -> str:
        m = (mod or "").lower()
        if any(t in m for t in ("net", "dns", "tcp", "udp", "socket", "port", "ssl")):
            return "network"
        if any(t in m for t in ("cpu", "mem", "disk", "io", "swap", "monitor")):
            return "performance"
        if any(t in m for t in ("auth", "ssh", "sudo", "sec", "fail2ban")):
            return "security"
        if any(t in m for t in ("fs", "freeswitch", "sip", "rtp", "pbx")):
            return "system"
        return "other"

    for a in alerts:
        a["tab"] = classify(a["module"])

    tab = (request.args.get("tab") or "all").lower()
    if tab != "all":
        alerts = [a for a in alerts if a["tab"] == tab]

    crit_count = sum(1 for a in alerts if a["status"] in ("crit", "red", "orange"))
    return jsonify({
        "alerts": alerts[:50],          # cap
        "tab": tab,
        "count": len(alerts),
        "crit_count": crit_count,
        "ts": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/v6/alerts/page")
def v6_alerts_page():
    """v0.6.0 — slice 7.1: HTML wrapper around /v6/alerts JSON."""
    return render_template(
        "v6_alerts.html",
        identity=_fetch_identity(),
        is_remote=_is_remote_mode(),
        now_iso=datetime.now(timezone.utc).isoformat(),
    )


@app.route("/v6/alerts/<alert_id>/resolve", methods=["POST"])
def v6_alerts_resolve(alert_id: str):
    """v0.6.0 — slice 7.1: enqueue a 'mark resolved' proposal (approval-gated).

    Body may include a free-text `note` describing the resolution.
    The destructive-op rule: this slice writes a RepairProposal that the
    operator approves via /approvals/<pid>/approve. No mutation here.
    """
    if _is_remote_mode():
        return jsonify({"ok": False, "error": "alerts local-only"}), 400
    note = (request.form.get("note") or "").strip()[:500]
    from ipracticom_sweeper.repair.pending import create_proposal
    p = create_proposal(
        action="mark_resolved",
        kwargs={"alert_id": alert_id},
        reason=note or f"mark-resolved requested for alert {alert_id} via v6 alerts",
        problem={"alert_id": alert_id, "source": "v6_alerts"},
        proposed_command=f"# mark alert {alert_id} resolved (operator note: {note!r})",
    )
    return jsonify({"ok": True, "queued": True, "proposal": p.to_dict()})


@app.route("/v6/alerts/<alert_id>/snooze", methods=["POST"])
def v6_alerts_snooze(alert_id: str):
    """v0.6.0 — slice 7.1: enqueue a snooze proposal (approval-gated).

    Body: `duration_min` ∈ {15, 60, 1440}. Validated.
    """
    if _is_remote_mode():
        return jsonify({"ok": False, "error": "alerts local-only"}), 400
    raw = request.form.get("duration_min", "60")
    try:
        dur = int(raw)
    except ValueError:
        return jsonify({"ok": False, "error": f"duration_min must be int, got {raw!r}"}), 400
    if dur not in (15, 60, 1440):
        return jsonify({
            "ok": False,
            "error": f"duration_min must be one of [15, 60, 1440], got {dur}",
        }), 400
    from ipracticom_sweeper.repair.pending import create_proposal
    p = create_proposal(
        action="snooze",
        kwargs={"alert_id": alert_id, "duration_min": dur},
        reason=f"snooze alert {alert_id} for {dur}min via v6 alerts",
        problem={"alert_id": alert_id, "source": "v6_alerts"},
        proposed_command=f"# snooze alert {alert_id} for {dur} minutes",
    )
    return jsonify({"ok": True, "queued": True, "proposal": p.to_dict()})


@app.route("/v6/machines")
def v6_machines():
    """v0.6.0 — slice 6.1: dark table view of the fleet.

    Reads the same fleet aggregator as `/fleet` but renders a compact,
    v6-styled table. Read-only in this slice — actions come in 6.2.
    """
    from ipracticom_sweeper.config import load_connectors
    from ipracticom_sweeper.fleet import aggregate, load_all_snapshots

    connectors = load_connectors()
    snapshots_raw = load_all_snapshots()

    converted = []
    raw_by_name = {s["name"]: s for s in snapshots_raw}
    for conn in connectors:
        if conn.name not in raw_by_name:
            converted.append({
                "name": conn.name, "server": conn.name,
                "defcon": 0, "defcon_label": "black",
                "problems_found": 0, "ts": 0.0, "modules": {},
                "_unavailable_reason": "no data yet — waiting for first collection",
            })
            continue
        entry = raw_by_name[conn.name]
        snap_dict = entry.get("snapshot", {}) or {}
        if not snap_dict.get("available", False):
            converted.append({
                "name": conn.name, "server": conn.name,
                "defcon": 0, "defcon_label": "black",
                "problems_found": 0, "ts": 0.0, "modules": {},
                "_unavailable_reason": snap_dict.get("reason", "unknown"),
            })
            continue
        converted.append({
            "name": conn.name,
            "server": snap_dict.get("server", conn.name),
            "defcon": snap_dict.get("defcon", 5),
            "defcon_label": snap_dict.get("defcon_label", "green"),
            "problems_found": snap_dict.get("problems_found", 0),
            "ts": snap_dict.get("ts", 0.0),
            "modules": snap_dict.get("modules", {}),
        })

    summary = aggregate(converted) if converted else None

    # Maintenance map: host → state dict (None if absent).
    maint_hosts = {}
    if summary and getattr(summary, "hosts", None):
        for h in summary.hosts:
            ms = _get_maintenance_state(h.host_id)
            if ms:
                maint_hosts[h.host_id] = ms

    return render_template(
        "v6_machines.html",
        summary=summary,
        hosts=summary.hosts if summary else [],
        connectors_count=len(connectors),
        maint_hosts=maint_hosts,
        now_iso=datetime.now(timezone.utc).isoformat(),
        identity=_fetch_identity(),
        is_remote=_is_remote_mode(),
    )


@app.route("/v6")
def v6_index():
    """v0.6.0 — slice 5.2 + 5.3: dark slate sidebar + 4-card stats bar.

    Stats are pulled from real data sources:
      - total_machines / pbx_count ← fleet aggregator
      - critical_count / defcon     ← pipeline snapshot
      - events_today                ← SQLite event store
    All fields fall back to "—" when the source is unavailable so we never
    show a fabricated number on a live dashboard.
    """
    heartbeat = _fetch_heartbeat()
    stats = _fetch_v6_stats()
    return render_template(
        "v6_index.html",
        heartbeat=heartbeat,
        stats=stats,
        now_iso=datetime.now(timezone.utc).isoformat(),
        identity=_fetch_identity(),
        is_remote=_is_remote_mode(),
    )


@app.route("/healthz")
def healthz():
    """Liveness check. In remote mode, proxies to remote agent."""
    if _is_remote_mode():
        try:
            remote = _get_agent().healthz()
            remote["mode"] = "remote"
            remote["dashboard_id"] = get_server_id()
            return jsonify(remote)
        except AgentError as e:
            return _safe_error_response(e, 503, extra={
                "ok": False,
                "mode": "remote",
                "remote_url": _remote_url(),
            })
    return jsonify({
        "ok": True,
        "mode": "local",
        "server_id": get_server_id(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "last_result_age_sec": _last_result_age_sec(),
    })


# --- Helpers -----------------------------------------------------------------


# --- History loaders -------------------------------------------------------


def _load_history_runs() -> list[dict]:
    """Load recent sweep results from the monitor audit log."""
    if _is_remote_mode():
        # AgentClient has no get_audit_events method (pre-existing gap).
        # Return an empty list rather than 500ing the page; the operator can
        # still see /run/now + /api/snapshot for live data.
        return []

    audit_log = Path("/var/lib/ipracticom-sweeper/audit/monitor.jsonl")
    if not audit_log.exists():
        return []
    runs = []
    try:
        with open(audit_log) as f:
            lines = f.readlines()[-100:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                runs.append({
                    "ts": ev.get("ts", ""),
                    "module": ev.get("module", ""),
                    "status": ev.get("status", ""),
                })
            except json.JSONDecodeError as exc:
                log_suppressed("dashboard.history_runs.json_decode", exc)
                continue
    except OSError as exc:
        log_suppressed("dashboard.history_runs.read", exc)
    return runs


def _load_history_repairs() -> list[dict]:
    """Load recent repair events from the repairs audit log.

    Returns list of dicts sorted newest-first, each with:
      ts, kind (proposed/approved/executed/failed/rejected), action, target,
      actor (auto/operator), success, message, error, proposal_id, duration_ms
    """
    audit_log = Path("/var/lib/ipracticom-sweeper/audit/repairs.jsonl")
    if not audit_log.exists():
        return []
    out = []
    try:
        with open(audit_log) as f:
            lines = f.readlines()[-200:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError as exc:
                log_suppressed("dashboard.history_repairs.json_decode", exc)
                continue
            out.append({
                "ts": ev.get("logged_at", ""),
                "kind": ev.get("kind", ""),
                "action": ev.get("action", ""),
                "target": ev.get("target") or (ev.get("kwargs") or {}).get("unit", ""),
                "actor": ev.get("actor", "?"),
                "proposal_id": ev.get("proposal_id", ""),
                "success": ev.get("success"),
                "duration_ms": ev.get("duration_ms"),
                "message": ev.get("message", ""),
                "error": ev.get("error", ""),
                "reason": ev.get("reason", ""),
            })
    except OSError as exc:
        log_suppressed("dashboard.history_repairs.read", exc)
    out.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return out


def _load_history_proposals() -> list[dict]:
    """Load all repair proposals (pending + approved + rejected) for the
    proposals table. Newest first.
    """
    out = []
    try:
        from ipracticom_sweeper.repair.pending import PENDING_DIR, APPROVED_DIR, REJECTED_DIR
        for subdir, status in (
            (PENDING_DIR, "pending"),
            (APPROVED_DIR, "approved"),
            (REJECTED_DIR, "rejected"),
        ):
            if not subdir.exists():
                continue
            for p in subdir.glob("*.json"):
                try:
                    data = json.loads(p.read_text())
                except (json.JSONDecodeError, OSError) as exc:
                    log_suppressed("dashboard.history_proposals.parse", exc,
                                   extras={"path": str(p)})
                    continue
                out.append({
                    "id": data.get("id", p.stem),
                    "action": data.get("action", ""),
                    "kwargs": data.get("kwargs", {}),
                    "reason": data.get("reason", ""),
                    "created_at": data.get("created_at", ""),
                    "status": data.get("status", status),
                })
    except Exception as exc:
        log_suppressed("dashboard.history_proposals.load", exc)
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


def _last_result_age_sec() -> int | None:
    if not LAST_RESULT_FILE.exists():
        return None
    age = time.time() - LAST_RESULT_FILE.stat().st_mtime
    return int(age)


def _summarize_rules(rules: dict) -> dict[str, dict]:
    """Compact summary of thresholds for the sidebar."""
    summary = {}
    for module in ("cpu", "memory", "disk", "services", "security"):
        section = rules.get(module, {})
        # Take only the warn/crit thresholds
        summary[module] = {
            k: v for k, v in section.items()
            if "warn" in k or "crit" in k or "list" in k
        }
    return summary


# --- Fleet view --------------------------------------------------------------


def _fleet_urls() -> list[str]:
    """Read SWEEPER_FLEET_URLS env var (comma-separated)."""
    import os

    raw = os.environ.get("SWEEPER_FLEET_URLS", "")
    return [u.strip() for u in raw.split(",") if u.strip()]


def _fetch_fleet_snapshots() -> list[dict[str, Any]]:
    """Fetch snapshots from all configured fleet agents.

    In local mode (no SWEEPER_FLEET_URLS), returns the local snapshot as a
    single-host fleet.
    """
    urls = _fleet_urls()
    if not urls:
        local = _fetch_snapshot()
        return [local] if local else []

    snapshots = []
    for url in urls:
        try:
            client = AgentClient(url, token=_remote_token())
            snap = client.get_snapshot()
            if snap:
                snapshots.append(snap)
        except Exception as e:
            # One bad agent shouldn't break the whole fleet view
            print(f"[dashboard] fleet fetch failed for {url}: {e}")
    return snapshots


@app.route("/fleet")
def fleet_view():
    """Multi-host overview — shows one card per configured connector.

    Reads per-host snapshots from $IPRACTICOM_SWEEPER_STATE_DIR/fleet/snapshots/
    (written by the collector loop). Falls back to legacy SWEEPER_FLEET_URLS
    behavior when no connectors are configured.

    Empty state: link to /settings/connectors.
    """
    from ipracticom_sweeper.fleet import aggregate, load_all_snapshots

    connectors = load_connectors()
    snapshots_raw = load_all_snapshots()  # list of {name, collected_at, snapshot}

    # Convert raw snapshots → aggregator format. We build a HostSnapshot-shaped
    # object on the fly because load_all_snapshots stores the dict form already.
    converted = []
    raw_by_name = {s["name"]: s for s in snapshots_raw}
    for conn in connectors:
        if conn.name not in raw_by_name:
            # No data yet for this connector — show as unavailable
            from ipracticom_sweeper.fleet.aws_connector import HostSnapshot
            converted.append(
                _ssm_unavailable(conn.name, "no data yet — waiting for first collection")
            )
            continue
        entry = raw_by_name[conn.name]
        snap_dict = entry.get("snapshot", {})
        if not snap_dict.get("available", False):
            converted.append(_ssm_unavailable(conn.name, snap_dict.get("reason", "unknown")))
            continue
        # Reconstruct a HostSnapshot-ish object for the adapter
        class _Snap:
            pass
        s = _Snap()
        s.available = True
        s.data = snap_dict.get("data") or {}
        s.reason = ""
        converted.append(
            __import__("ipracticom_sweeper.fleet", fromlist=["ssm_to_aggregator_format"])
            .ssm_to_aggregator_format(conn.name, s)
        )

    summary = aggregate(converted)
    identity = _fetch_identity()
    return render_template(
        "fleet.html",
        summary=summary,
        snapshots=converted,
        connectors=connectors,
        raw_snapshots=snapshots_raw,
        identity=identity,
        now_iso=datetime.now(timezone.utc).isoformat(),
    )


def _ssm_unavailable(name: str, reason: str) -> dict[str, Any]:
    """Build an aggregator-format dict representing 'we couldn't reach this host'."""
    return {
        "server": name,
        "defcon": 1,
        "defcon_label": "red",
        "problems_found": 1,
        "ts": 0.0,
        "modules": {"ssm": "crit"},
        "_reason": reason,
    }


@app.route("/fleet/host/<name>")
def fleet_host_detail(name: str):
    """Per-host detail (used by the modal in fleet.html).

    Returns a JSON object with: connector metadata, latest snapshot data,
    run history (from audit log), repair history (filtered by host), and
    pending approvals (filtered by host when metadata.host matches).
    """
    from ipracticom_sweeper.fleet import load_snapshot
    from ipracticom_sweeper.config import get_connector

    conn = get_connector(name)
    if conn is None:
        return jsonify({"error": "not_found", "name": name}), 404

    snap = load_snapshot(name) or {}
    raw = snap.get("snapshot", {}) if snap else {}

    # Run history: read the global audit log, filter by mentions of this host.
    # The local box audit log captures all runs; for SSM-collected hosts the
    # "runs" are the collector cycles. We return both as a unified timeline.
    audit_path = "/var/lib/ipracticom-sweeper/audit/repairs.jsonl"
    repairs = []
    try:
        import json
        with open(audit_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    log_suppressed("dashboard.fleet_host_detail.repairs_json_decode", exc)
                    continue
                # Match if name appears in any field (best-effort for SSM hosts
                # where the host field might equal instance_id)
                if name in json.dumps(entry) or conn.instance_id in json.dumps(entry):
                    repairs.append(entry)
        repairs = repairs[-50:]
    except FileNotFoundError as exc:
        log_suppressed("dashboard.fleet_host_detail.repairs_read", exc)

    # Pending approvals
    pending = []
    try:
        from ipracticom_sweeper.repair import pending as pending_mod
        for p in pending_mod.list_pending():
            if name in str(p) or conn.instance_id in str(p):
                pending.append(p)
    except Exception as exc:
        log_suppressed("dashboard.fleet_host_detail.pending_list", exc)

    return jsonify({
        "name": name,
        "connector": conn.to_dict(),
        "snapshot": raw,
        "snapshot_age_seconds": (time.time() - snap.get("collected_at", 0)) if snap else None,
        "repairs": repairs,
        "pending_approvals": pending,
    })


# --- Connectors settings (AWS SSM) -------------------------------------

@app.route("/settings/connectors", methods=["GET", "POST"])
def settings_connectors():
    """Manage AWS SSM connectors (remote hosts to monitor).

    Local mode: writes directly to connectors.yaml via config module.
    Remote mode: proxies through AgentClient to the remote agent API.
    """
    is_remote = _is_remote_mode()
    saved_message = None
    error_message = None

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "delete":
            name = (request.form.get("name") or "").strip()
            if not name:
                error_message = "חסר שם connector"
            else:
                try:
                    if is_remote:
                        _get_agent().delete_connector(name)
                    else:
                        if not remove_connector(name):
                            error_message = f"connector '{name}' לא נמצא"
                    if not error_message:
                        saved_message = f"connector '{name}' נמחק"
                except AgentError as e:
                    error_message = f"מחיקה נכשלה: {e}"

        elif action == "toggle":
            name = (request.form.get("name") or "").strip()
            enabled = request.form.get("enabled") == "1"
            if not name:
                error_message = "חסר שם connector"
            else:
                try:
                    if is_remote:
                        _get_agent().update_connector(name, {"enabled": enabled})
                    else:
                        update_connector(name, enabled=enabled)
                    saved_message = f"connector '{name}' עודכן"
                except (AgentError, KeyError, ValueError) as e:
                    error_message = f"עדכון נכשל: {e}"

        elif action == "test":
            name = (request.form.get("name") or "").strip()
            if not name:
                error_message = "חסר שם connector"
            else:
                try:
                    if is_remote:
                        result = _get_agent().test_connector(name)
                    else:
                        # Local: call the same logic the API uses
                        from ipracticom_sweeper.config import (
                            get_connector,
                            mark_connector_collected,
                            mark_connector_error,
                        )
                        from ipracticom_sweeper.fleet import AwsSsmConnector, SsmError
                        c = get_connector(name)
                        if c is None:
                            error_message = f"connector '{name}' לא נמצא"
                        else:
                            try:
                                ssm = AwsSsmConnector(region=c.region)
                                result = {"ok": True, "snapshot": ssm.collect_one(
                                    c.instance_id,
                                    freeswitch_enabled=getattr(c, "freeswitch_enabled", False),
                                )}
                                mark_connector_collected(name)
                            except SsmError as e:
                                mark_connector_error(name, str(e))
                                error_message = f"SSM: {e}"
                except AgentError as e:
                    error_message = f"בדיקה נכשלה: {e}"

        elif action == "create":
            name = (request.form.get("name") or "").strip()
            instance_id = (request.form.get("instance_id") or "").strip()
            region = (request.form.get("region") or "il-central-1").strip()
            tags_raw = (request.form.get("tags") or "").strip()
            if not name or not instance_id:
                error_message = "חובה למלא שם ו-instance_id"
            else:
                tags = {}
                if tags_raw:
                    for pair in tags_raw.split(","):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            tags[k.strip()] = v.strip()
                try:
                    if is_remote:
                        _get_agent().create_connector({
                            "name": name, "instance_id": instance_id,
                            "region": region, "tags": tags, "enabled": True,
                        })
                    else:
                        add_connector(Connector(
                            name=name, instance_id=instance_id,
                            region=region, tags=tags, enabled=True,
                        ))
                    saved_message = f"connector '{name}' נוסף"
                except (AgentError, ValueError) as e:
                    error_message = f"הוספה נכשלה: {e}"

    # Reload connectors for display (post-mutation)
    try:
        if is_remote:
            connectors = _get_agent().list_connectors()
        else:
            connectors = [c.to_dict() for c in load_connectors()]
    except AgentError as e:
        connectors = []
        error_message = f"טעינה נכשלה: {e}"

    return render_template(
        "connectors.html",
        connectors=connectors,
        is_remote=is_remote,
        identity=_fetch_identity(),
        saved_message=saved_message,
        error_message=error_message,
    )


# --- Catalogue (read-only check registry) ---------------------------------


@app.route("/catalogue")
def catalogue_view():
    """Catalogue index — list of registered checks (v0.5.0 slice 1.2).

    Additive route. Shows every check we know about, with current threshold
    param counts loaded from rules.yaml. Read-only — operators inspect what
    exists; editing the live file is a future slice.
    """
    from ipracticom_sweeper.catalogue import render_catalogue
    from ipracticom_sweeper.config import load_rules

    try:
        rules = load_rules() or {}
    except Exception:
        rules = {}

    checks = render_catalogue(rules)

    return render_template(
        "catalogue.html",
        mode="index",
        checks=checks,
        identity=_fetch_identity(),
    )


@app.route("/catalogue/<module_key>")
def catalogue_module(module_key: str):
    """Per-check editor view (v0.5.0 slice 1.2).

    Additive route. Shows the rule_keys defined for a module and their current
    values from rules.yaml. Read-only — changing values requires a separate
    write/approval flow.
    """
    from ipracticom_sweeper.catalogue import render_check
    from ipracticom_sweeper.config import load_rules

    try:
        rules = load_rules() or {}
    except Exception:
        rules = {}

    data = render_check(module_key, rules)
    if data is None:
        return jsonify({"error": "unknown_module", "key": module_key}), 404

    return render_template(
        "catalogue.html",
        mode="module",
        module_key=module_key,
        entry=data["entry"],
        params=data["params"],
        all_current=data["all_current"],
        identity=_fetch_identity(),
    )


# --- Inspector (per-host check inspection) ---------------------------------


@app.route("/inspector")
def inspector_view():
    """Per-host check inspector — pick a host, see modules + their last status.

    Additive route for v0.5.0 slice 1.1. Renders the local host's last
    snapshot modules (cpu/memory/disk/...) and lists configured connectors so
    operators can pick a remote host to inspect via /inspector/host/<name>.

    Empty state: links to /settings/connectors if no hosts are configured.
    """
    from ipracticom_sweeper.config import load_connectors

    connectors = load_connectors()

    # Local snapshot (last pipeline result) — extract module summary
    local_snapshot = _read_last_result() or {}
    local_modules = []
    for module_key, module_data in (local_snapshot.get("modules") or {}).items():
        if not isinstance(module_data, dict):
            continue
        local_modules.append({
            "key": module_key,
            "status": module_data.get("status", "unknown"),
            "summary": _summarize_module(module_data),
        })
    local_modules.sort(key=lambda m: m["key"])

    return render_template(
        "inspector.html",
        host="localhost",
        module_kind="local",
        connectors=connectors,
        modules=local_modules,
        snapshot_age=_last_result_age_sec(),
        identity=_fetch_identity(),
    )


@app.route("/inspector/host/<name>")
def inspector_host(name: str):
    """Per-host inspector — shows the latest snapshot modules for a remote host.

    Additive route for v0.5.0 slice 1.1. Reads collector snapshot via fleet
    module, then drills into modules + their values for operator inspection.

    Empty state: 404 if the connector is not configured.
    """
    from ipracticom_sweeper.config import get_connector
    from ipracticom_sweeper.fleet import load_snapshot

    conn = get_connector(name)
    if conn is None:
        return jsonify({"error": "not_found", "name": name}), 404

    snap = load_snapshot(name) or {}
    raw = snap.get("snapshot", {}) if snap else {}
    modules_data = raw.get("modules") or {}

    modules = []
    for module_key, module_data in modules_data.items():
        if not isinstance(module_data, dict):
            continue
        modules.append({
            "key": module_key,
            "status": module_data.get("status", "unknown"),
            "summary": _summarize_module(module_data),
            "values": module_data.get("values", {}),
        })
    modules.sort(key=lambda m: m["key"])

    from ipracticom_sweeper.config import load_connectors
    connectors = load_connectors()

    return render_template(
        "inspector.html",
        host=name,
        module_kind="remote",
        connectors=connectors,
        modules=modules,
        snapshot_age=(time.time() - snap.get("collected_at", 0)) if snap else None,
        identity=_fetch_identity(),
    )


def _summarize_module(module_data: dict) -> str:
    """One-line Hebrew summary for a module dict.

    Additive helper for v0.5.0 slice 1.1. Looks for the first scalar numeric
    value or the first string in `values` and returns a short label. If nothing
    useful is found, returns the module status string.
    """
    values = module_data.get("values") or {}
    # Prefer a primary scalar if present
    for key in ("cpu.idle_percent", "memory.used_percent", "disk.used_percent"):
        v = values.get(key)
        if isinstance(v, (int, float)):
            if "cpu.idle" in key:
                return f"idle {v:.0f}%"
            if "memory.used" in key:
                return f"used {v:.0f}%"
            if "disk.used" in key:
                return f"used {v:.0f}%"
    # Fallback: first numeric or short string
    for k, v in values.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f"{k}={v}"
        if isinstance(v, str) and 0 < len(v) <= 40:
            return f"{k}={v[:40]}"
        if isinstance(v, list) and v:
            return f"{k} ({len(v)})"
    return module_data.get("status", "unknown")


# --- React SPA + agent REST API + settings JSON API (v1.5.18 split) ----------
# Webui concerns live in dashboard_webui.py (module size budget). Must run
# BEFORE the __main__ guard — `python -m` blocks in main() and never returns.
from ipracticom_sweeper.dashboard_webui import register_webui as _register_webui  # noqa: E402

_register_webui(app, is_remote_mode=_is_remote_mode)


# --- CLI entry point ---------------------------------------------------------


def main():
    """Run the dashboard. Default 127.0.0.1:8804.

    Fail-closed: if DASHBOARD_USER/PASS are unset AND --host is not loopback,
    refuse to start unless --allow-open is passed. Mirrors agent_api.main() —
    prevents accidentally exposing the unauthenticated dashboard to the network.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="iPracticom Sweeper Dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8804)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--allow-open",
        action="store_true",
        help="Explicitly allow OPEN mode (no basic auth) on a non-loopback host. "
             "By default, OPEN mode + non-loopback is refused.",
    )
    args = parser.parse_args()

    auth_present = bool(
        os.environ.get("DASHBOARD_USER", "") and os.environ.get("DASHBOARD_PASS", "")
    )
    is_loopback = args.host in ("127.0.0.1", "::1", "localhost", "")
    if not auth_present and not is_loopback and not args.allow_open:
        print(
            f"[dashboard] REFUSING TO START: DASHBOARD_USER/PASS unset but "
            f"--host={args.host} is not loopback. This would expose the dashboard "
            f"unauthenticated (it has /approvals/<pid>/approve which executes repairs). "
            f"Set DASHBOARD_USER/DASHBOARD_PASS or pass --allow-open.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[dashboard] Starting on {args.host}:{args.port}")
    print(f"[dashboard] Auth: {'basic' if auth_present else 'OPEN'}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()


# --- Back-compat re-exports -------------------------------------------------
# Code split in v1.5.13 — these symbols moved to sibling helper modules but
# tests + internal callers still reach into this module. Re-exporting keeps
# the public surface unchanged.

from ipracticom_sweeper.dashboard_helpers import (  # noqa: E402, F401
    CACHE_DIR,
    LAST_RESULT_FILE,
    read_last_result as _read_last_result,
    write_last_result as _write_last_result,
    trigger_pipeline_run,
)

from ipracticom_sweeper.dashboard_notify import (  # noqa: E402, F401
    NOTIFICATIONS_ENV_FILE,
    _read_notifications_env,
    _write_notifications_env,
    _test_telegram,
    _validate_slack_webhook_url,
    _test_slack,
)

from ipracticom_sweeper.dashboard_maintenance import (  # noqa: E402, F401
    _HOSTNAME_RE,
    _validate_hostname,
    _redact_secrets,
    _safe_error_response,
    _SECRET_KEYS,
    _save_maintenance_state,
    _get_maintenance_state,
)