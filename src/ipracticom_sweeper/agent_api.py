"""Agent HTTP API.

Exposes the sweeper state over HTTP so that remote dashboards (or any
HTTP client) can read the current state without shell access. This is
the same API the dashboard already exposes locally — but packaged as
a standalone, auth-protected service.

Routes (all return JSON):
  GET  /healthz                  liveness + identity
  GET  /api/snapshot             latest cached pipeline result
  GET  /api/notify/test          (admin only) send a test notification
  POST /api/run                  (admin only) trigger a fresh sweep

Auth: bearer token via env var AGENT_API_TOKEN. If unset, the API
runs in OPEN mode (intended for local-only deployments behind a
firewall; the bind address defaults to 127.0.0.1).

Usage:
  AGENT_API_TOKEN=secret python -m ipracticom_sweeper.agent_api --port 8787

This service is independent of the dashboard — it does not render
HTML. The dashboard, when configured with `remote_url`, will fetch
from this service instead of running the pipeline locally.
"""

from __future__ import annotations

import argparse
import collections  # for rate-limit deque
import hmac
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger("agent_api")
from functools import wraps
from typing import Any

from flask import Flask, Response, abort, jsonify, request

from ipracticom_sweeper.config import (
    Connector,
    add_connector,
    get_connector,
    get_server_id,
    load_connectors,
    load_rules,
    mark_connector_collected,
    mark_connector_error,
    remove_connector,
    update_connector,
)
# Imported from the helpers module (not ipracticom_sweeper.dashboard) so the
# dashboard can import this module and mount the API routes without a cycle.
from ipracticom_sweeper.dashboard_helpers import (
    CACHE_DIR,
    LAST_RESULT_FILE,
    read_last_result as _read_last_result,
    write_last_result as _write_last_result,
)
from ipracticom_sweeper.pipeline import run_pipeline
from ipracticom_sweeper.slack_actions.endpoint import SlackEndpoint
from ipracticom_sweeper.slack_actions.commands import SlackCommandHandler
from ipracticom_sweeper._log import log_suppressed


def _read_heartbeat(state_dir) -> dict[str, Any] | None:
    """Read /heartbeat.json if it exists, else return None.

    Heartbeat is written by the pipeline loop after every sweep — it's
    the cheapest way to know "is this host alive" without running the
    whole pipeline again.
    """
    import json as _json
    path = state_dir / "heartbeat.json"
    if not path.exists():
        return None
    try:
        return _json.loads(path.read_text())
    except Exception:
        return None


def _local_status(heartbeat: dict[str, Any] | None) -> str:
    """Classify the local host's status from its heartbeat.

    Rules:
      - no heartbeat / very old heartbeat → "unknown" or "stale"
      - defcon <= 3 → "warn" / "crit"
      - problems_found > 0 → "warn"
      - else → "ok"
    """
    if heartbeat is None:
        return "unknown"
    try:
        defcon = int(heartbeat.get("defcon", 5))
    except (TypeError, ValueError):
        defcon = 5
    if defcon <= 2:
        return "crit"
    if defcon == 3 or int(heartbeat.get("problems_found") or 0) > 0:
        return "warn"
    return "ok"


def _redact_secrets(d: dict[str, Any] | None) -> dict[str, Any]:
    """Redact values for keys that look like they carry credentials/secrets.

    Recursively walks dicts and lists. Returns a new dict — does not mutate.
    """
    SECRET_KEYS = frozenset({
        "password", "passwd", "pwd", "secret", "token", "api_key",
        "apikey", "access_key", "secret_key", "private_key", "auth",
        "authorization", "credential", "credentials", "ssh_key", "ssl_key",
    })
    REDACTED = "***REDACTED***"

    def scrub(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: ("***REDACTED***" if k.lower() in SECRET_KEYS else scrub(val))
                    for k, val in v.items()}
        if isinstance(v, list):
            return [scrub(x) for x in v]
        return v

    return scrub(d or {})


# v1.5.9: error sanitization helper. Replaces raw str(e) in client responses
# with a generic message + correlation id. The full exception is logged
# server-side for operator investigation. This prevents leaking filesystem
# paths, SQL fragments, and library versions to unauthenticated callers.
import uuid as _uuid


def _safe_error_response(exc: BaseException, status: int = 500) -> tuple[Any, int]:
    """Return a sanitized JSON error response with a correlation id.

    The full exception message is logged but never returned to the client.
    Use this for any 4xx/5xx response where `exc` was caught.
    """
    corr_id = _uuid.uuid4().hex[:8]
    logger.error("api_error_response",
                 correlation_id=corr_id,
                 error_class=type(exc).__name__,
                 error=str(exc))
    return jsonify({
        "error": "internal_error",
        "correlation_id": corr_id,
    }), status


def create_app() -> Flask:
    """Standalone agent API app — bearer-token auth, CORS, rate limits."""
    return register_api_routes(Flask(__name__))


def register_api_routes(
    app: Flask,
    auth=None,
    *,
    skip: frozenset = frozenset(),
    rate_limited: bool = True,
) -> Flask:
    """Register the full agent REST API on *app*.

    ``auth`` is a decorator applied to every authenticated view. ``None`` means
    the built-in Bearer-token check (standalone agent API). The dashboard
    mounts these same routes passing a passthrough decorator, because the
    dashboard's global HTTP Basic ``before_request`` already gates every
    route — so the browser's Basic session authenticates the API with no
    token in the DOM.

    ``skip`` is a set of endpoint (function) names NOT to register — the
    dashboard uses it to avoid colliding with its own ``/healthz``,
    ``/api/snapshot``, ``/api/notify/test`` and to keep the Slack endpoints
    off the Basic-gated app (they authenticate by request signature and
    belong to the standalone agent).

    ``rate_limited=False`` disables the per-IP token bucket — used when
    mounted behind the dashboard, whose SPA polls several endpoints on a
    short interval and is already auth-gated.
    """
    token = os.environ.get("AGENT_API_TOKEN", "")

    # --- Auth (bearer token) ----------------------------------------------
    def _bearer_auth(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not token:
                # OPEN mode (no token configured) — caller should bind to localhost
                return fn(*args, **kwargs)
            provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(provided, token):
                return jsonify({"error": "unauthorized"}), 401
            return fn(*args, **kwargs)
        return wrapper

    require_auth = auth if auth is not None else _bearer_auth

    # Registration helper honouring ``skip`` — same signature as app.route.
    def _route(rule, **options):
        def deco(fn):
            if fn.__name__ in skip:
                return fn
            return app.route(rule, **options)(fn)
        return deco

    # --- Rate limiting (built-in, no flask-limiter dependency) -----------
    # Per-IP token-bucket: configurable via env (default 100/min/IP for /api/*,
    # 600/min for /healthz). Keeps a small in-memory dict of {ip: [timestamps]}.
    _RL_API_DEFAULT = int(os.environ.get("AGENT_API_RATELIMIT_API", "100"))
    _RL_HEALTHZ_DEFAULT = int(os.environ.get("AGENT_API_RATELIMIT_HEALTHZ", "600"))
    _RL_ENABLED = os.environ.get("AGENT_API_RATELIMIT", "1") == "1"
    # Trust X-Forwarded-For ONLY when explicitly enabled (i.e. the API sits
    # behind a proxy that overwrites XFF). Otherwise XFF is client-controlled:
    # rotating it defeats the per-IP limit AND grows the bucket map without
    # bound (memory-exhaustion DoS). Default off → key on the real peer.
    _RL_TRUST_XFF = os.environ.get("AGENT_API_TRUST_XFF", "0") == "1"
    # Hard cap on distinct buckets; stale ones are pruned before this bites.
    _RL_MAX_KEYS = int(os.environ.get("AGENT_API_RATELIMIT_MAX_KEYS", "10000"))
    _rl_buckets: dict[str, collections.deque[float]] = {}
    # A single lock fully serialises bucket-map access. Cheap at these rates
    # and avoids a per-key lock dict that would itself grow without bound.
    _RL_LOCK = threading.Lock()

    def _rl_client_key() -> str:
        if _RL_TRUST_XFF:
            xff = request.headers.get("X-Forwarded-For", "")
            if xff:
                return xff.split(",")[0].strip() or "unknown"
        return request.remote_addr or "unknown"

    def _check_rate_limit(scope: str, limit_per_min: int) -> tuple[bool, int]:
        """Return (allowed, remaining). Sliding window of 60 seconds."""
        if not _RL_ENABLED:
            return True, limit_per_min
        key = f"{scope}:{_rl_client_key()}"
        now = time.monotonic()
        cutoff = now - 60.0
        with _RL_LOCK:
            # Bound memory: if the map has blown up (spoofed keys, churn),
            # drop every bucket whose newest hit has aged out of the window.
            if len(_rl_buckets) > _RL_MAX_KEYS:
                for stale in [k for k, b in _rl_buckets.items() if not b or b[-1] < cutoff]:
                    del _rl_buckets[stale]
                # Hard cap: reclaiming dead buckets only frees keys that have
                # gone quiet. Under a *live* burst of unique keys (spoofed XFF,
                # a botnet) every bucket is fresh, nothing is reclaimed, and the
                # map would otherwise grow without bound. Refuse a brand-new key
                # instead. Keys already tracked stay served, so a genuine client
                # mid-conversation is never dropped.
                if len(_rl_buckets) > _RL_MAX_KEYS and key not in _rl_buckets:
                    return False, 0
            bucket = _rl_buckets.setdefault(key, collections.deque())
            # Drop expired entries
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit_per_min:
                return False, 0
            bucket.append(now)
            return True, limit_per_min - len(bucket)

    def _rate_limit(scope: str, limit_per_min: int):
        """Decorator factory; attaches 429 + Retry-After on overage."""
        def deco(fn):
            if not rate_limited:
                return fn
            @wraps(fn)
            def wrapper(*args, **kwargs):
                allowed, remaining = _check_rate_limit(scope, limit_per_min)
                if not allowed:
                    resp = jsonify({"error": "rate_limited", "scope": scope})
                    resp.status_code = 429
                    resp.headers["Retry-After"] = "60"
                    return resp
                resp = fn(*args, **kwargs)
                # Attach informational header on success
                try:
                    if hasattr(resp, "headers"):
                        resp.headers["X-RateLimit-Remaining"] = str(remaining)
                except Exception as e:
                    log_suppressed("agent_api_rate_limit_header", e)
                return resp
            return wrapper
        return deco

    # --- CORS (localhost-only by default) --------------------------------
    # Permits the dashboard to call the API from a different local port
    # (e.g. dashboard on :5000 calling agent API on :8787). No wildcard
    # origin is ever set — operators must opt in to remote origins via
    # AGENT_API_CORS_ORIGINS=comma,separated,list.
    # Reject `*` outright: a wildcard in the allowlist would leak the
    # Authorization header to any origin.
    _cors_raw = [
        o.strip()
        for o in os.environ.get("AGENT_API_CORS_ORIGINS", "").split(",")
        if o.strip()
    ]
    if "*" in _cors_raw or any(
        o == "*" or o.endswith("/*") for o in _cors_raw
    ):
        raise RuntimeError(
            "AGENT_API_CORS_ORIGINS must not contain wildcards ('*'); "
            "explicit origins only — see OWASP CORS misconfiguration."
        )
    _CORS_ALLOWED = (
        {"http://localhost", "http://localhost:5000", "http://127.0.0.1",
         "http://127.0.0.1:5000"}
        | set(_cors_raw)
    )

    @app.after_request
    def _cors_headers(resp):
        origin = request.headers.get("Origin")
        if origin and origin in _CORS_ALLOWED:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type"
            )
            resp.headers["Access-Control-Max-Age"] = "600"
        return resp

    @_route("/<path:_any>", methods=["OPTIONS"])
    def _cors_preflight(_any):
        # Tiny preflight handler — 204 with CORS headers attached by
        # the after_request hook above. No auth, no rate-limit (CORS
        # preflight is cheap and Chrome retries it constantly).
        return ("", 204)

    # --- Healthz (highest rate limit, no auth required) ------------------

    @_route("/healthz")
    @_rate_limit("healthz", _RL_HEALTHZ_DEFAULT)
    def healthz():
        return jsonify({
            "ok": True,
            "service": "ipracticom-sweeper-agent",
            "server_id": get_server_id(),
            "ts": datetime.now(timezone.utc).isoformat(),
            "auth": "token" if token else "open",
        })

    # --- Snapshot ----------------------------------------------------------

    @_route("/api/snapshot")
    @require_auth
    def api_snapshot():
        result = _read_last_result()
        if not result:
            return jsonify({"error": "no cached snapshot"}), 404
        return jsonify(result)

    @_route("/api/snapshot/raw")
    @require_auth
    def api_snapshot_raw():
        """Return the raw JSONL monitor events (audit log)."""
        log_path = "/var/lib/ipracticom-sweeper/audit/monitor.jsonl"
        events = []
        try:
            with open(log_path) as f:
                events = [line.strip() for line in f if line.strip()][-100:]
        except FileNotFoundError:
            return jsonify({"error": "no audit log yet", "events": []}), 200
        return jsonify({"events": events, "count": len(events)})

    # --- Logs (v0.4.3) ----------------------------------------------------
    # v0.4.3: surface every audit log the agent has, in JSON form, so the
    # Telegram bot can show them to the operator. Separate endpoint for
    # download so the bot can attach the file directly (Telegram has a
    # 4096-char message limit; we want the option to send the whole log).
    @_route("/api/logs", methods=["GET"])
    @require_auth
    def api_logs():
        """List + tail every available audit log.

        Returns:
          logs: [
            {name, kind, path, size_bytes, line_count, tail: [last N lines parsed]}
          ]
          available: bool (false if the state dir is missing)
        """
        from pathlib import Path

        try:
            state_dir = Path(os.environ.get(
                "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
            ))
        except Exception:
            return jsonify({"logs": [], "available": False}), 200

        if not state_dir.exists():
            return jsonify({"logs": [], "available": False, "note": "no state dir"}), 200

        # Build a list of well-known logs. Anything missing is silently
        # skipped — operator can still see what's there.
        candidates = [
            ("repairs", state_dir / "audit" / "repairs.jsonl", "jsonl"),
            ("monitor", state_dir / "audit" / "monitor.jsonl", "jsonl"),
            ("heartbeat", state_dir / "heartbeat.json", "json"),
            ("last_result", state_dir / "cache" / "last-result.json", "json"),
        ]

        try:
            tail_n = min(int(request.args.get("tail", "50")), 500)
        except ValueError:
            tail_n = 50

        logs: list[dict[str, Any]] = []
        for name, path, kind in candidates:
            if not path.exists():
                continue
            try:
                size = path.stat().st_size
            except OSError as e:
                log_suppressed("agent_api_audit_size", e)
                continue

            tail: list[Any] = []
            line_count = 0
            try:
                if kind == "jsonl":
                    # Read last N lines, parse each as JSON.
                    # For very large files, this is O(N); we cap at tail_n.
                    with open(path) as f:
                        # Cheap line count by reading whole file once.
                        # For 100MB logs this would be slow — but our logs
                        # are small (< 10MB even after months).
                        all_lines = f.readlines()
                    line_count = len(all_lines)
                    for line in all_lines[-tail_n:]:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            tail.append(json.loads(line))
                        except json.JSONDecodeError:
                            tail.append({"_raw": line[:500]})
                else:  # json
                    import json as _json
                    with open(path) as f:
                        data = _json.load(f)
                    # For JSON files, "tail" is a single element with the
                    # full content (operator can drill in via the bot UI).
                    tail = [data]
                    line_count = 1
            except (OSError, json.JSONDecodeError) as e:
                logs.append({
                    "name": name, "kind": kind, "path": str(path),
                    "size_bytes": size, "line_count": 0,
                    "tail": [], "error": str(e),
                })
                continue

            logs.append({
                "name": name,
                "kind": kind,
                "path": str(path),
                "size_bytes": size,
                "line_count": line_count,
                "tail_count": len(tail),
                "tail": tail,
            })

        return jsonify({
            "available": True,
            "count": len(logs),
            "logs": logs,
        })

    @_route("/api/logs/download", methods=["GET"])
    @require_auth
    def api_logs_download():
        """Return one log as a single concatenated text file.

        Query params:
          name: log name (e.g. "repairs", "monitor", "last_result")
                     default: "all" — returns every available log concatenated
          max_bytes: cap output size (default 5MB, max 50MB)
        """
        from pathlib import Path
        from flask import Response

        try:
            max_bytes = min(int(request.args.get("max_bytes", str(5 * 1024 * 1024))), 50 * 1024 * 1024)
        except ValueError:
            max_bytes = 5 * 1024 * 1024

        state_dir = Path(os.environ.get(
            "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
        ))

        name = (request.args.get("name") or "all").strip()
        # Optional per-machine tag for the download filename. Logs live under the
        # local agent's state dir, so this identifies which machine the operator
        # pulled the log from rather than selecting a remote source.
        raw_host = (request.args.get("host") or "").strip()
        host_tag = "".join(c for c in raw_host if c.isalnum() or c in "-_.")[:40]
        prefix = f"{host_tag}-" if host_tag else ""

        if name == "all":
            # Concatenate every available log with separators.
            parts: list[str] = []
            for log_name, log_path, log_kind in [
                ("repairs", state_dir / "audit" / "repairs.jsonl", "jsonl"),
                ("monitor", state_dir / "audit" / "monitor.jsonl", "jsonl"),
                ("heartbeat", state_dir / "heartbeat.json", "json"),
                ("last_result", state_dir / "cache" / "last-result.json", "json"),
            ]:
                if not log_path.exists():
                    continue
                try:
                    with open(log_path) as f:
                        content = f.read()
                except OSError as e:
                    parts.append(f"=== {log_name} ({log_path}): read error: {e} ===\n")
                    continue
                parts.append(
                    f"=== {log_name} ({log_path}, {log_kind}, {len(content)} bytes) ===\n"
                    f"{content}\n"
                )
            body = "".join(parts).encode("utf-8")
            filename = f"sweeper-{prefix}logs-{int(time.time())}.txt"
        else:
            # Map friendly name → file path.
            table = {
                "repairs": state_dir / "audit" / "repairs.jsonl",
                "monitor": state_dir / "audit" / "monitor.jsonl",
                "heartbeat": state_dir / "heartbeat.json",
                "last_result": state_dir / "cache" / "last-result.json",
                "last-result": state_dir / "cache" / "last-result.json",
            }
            path = table.get(name)
            if path is None or not path.exists():
                return jsonify({"error": f"unknown or missing log: {name}"}), 404
            try:
                body = path.read_bytes()
            except OSError as e:
                return _safe_error_response(e), 500
            filename = f"sweeper-{prefix}{name}-{int(time.time())}.{path.suffix.lstrip('.') or 'txt'}"

        # Cap size — if we'd exceed, truncate and note it in the trailer.
        truncated = False
        if len(body) > max_bytes:
            body = body[:max_bytes]
            truncated = True

        # Prepend a header if we truncated.
        if truncated:
            body = (
                f"# truncated to {max_bytes} bytes (use ?max_bytes=N to increase, max 50MB)\n"
                .encode("utf-8") + body
            )

        return Response(
            body,
            mimetype="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Sweeper-Truncated": "1" if truncated else "0",
            },
        )

    # --- Run trigger -------------------------------------------------------

    @_route("/api/run", methods=["POST"])
    @_rate_limit("api", _RL_API_DEFAULT)
    @require_auth
    def api_run():
        """Trigger a fresh sweep, cache the result, return it.

        Optional form params (used by the per-machine console's manual scan):
          host:   scope diagnosis to that host's ENABLED checks only. Omit for
                  the full agent-wide sweep (back-compat).
          repair: "0"/"false"/"no"/"off" → diagnose-only, read-only run (no
                  auto-repair). Default keeps the historic auto-repair behaviour.

        A scoped (host-provided) run is NOT cached as the agent-wide snapshot —
        it is a partial view and must not clobber the global last-result.
        """
        try:
            from ipracticom_sweeper.config import load_rules
            from ipracticom_sweeper.config.host_config import enabled_monitor_modules

            host = (request.form.get("host") or "").strip()
            repair_raw = (request.form.get("repair") or "1").strip().lower()
            do_repair = repair_raw not in ("0", "false", "no", "off")

            only_modules = enabled_monitor_modules(host) if host else None

            rules = load_rules()
            result = run_pipeline(
                rules,
                auto_repair=do_repair,
                dry_run=not do_repair,
                only_modules=only_modules,
            )
            d = result.to_dict()
            d["server"] = get_server_id()
            if not host:
                _write_last_result(d)
            return jsonify(d)
        except Exception as e:
            return _safe_error_response(e), 500

    # --- Notify (admin only, gated by token) -------------------------------

    @_route("/api/notify/test", methods=["POST"])
    @_rate_limit("api", _RL_API_DEFAULT)
    @require_auth
    def api_notify_test():
        import asyncio
        from ipracticom_sweeper.notify import notify_pipeline_result

        result = _read_last_result()
        if not result:
            return jsonify({"error": "no cached snapshot"}), 404
        try:
            sent = asyncio.run(notify_pipeline_result(result, force=True))
            return jsonify({"sent": sent})
        except Exception as e:
            return _safe_error_response(e), 500

    # --- Slack Events endpoint -------------------------------------------
    # Receives button clicks from Slack (Approve / Silence / Run Repair).
    # Verifies the X-Slack-Signature using SLACK_SIGNING_SECRET, parses the
    # block_actions payload, and dispatches to SlackActionHandler.
    #
    # This endpoint is intentionally NOT gated by AGENT_API_TOKEN — Slack
    # authenticates via request signing, not bearer tokens. We *do* require
    # a valid signature (HMAC-SHA256) which is cryptographically stronger.

    @_route("/slack/events", methods=["POST"])
    @_rate_limit("slack_events", _RL_API_DEFAULT)
    def slack_events():
        # IP allowlist (optional, opt-in via env): if SLACK_ALLOWED_IPS is set,
        # reject requests from outside. Empty/missing means allow all (HMAC
        # signature still required).
        allowed_ips = {
            ip.strip() for ip in os.environ.get("SLACK_ALLOWED_IPS", "").split(",")
            if ip.strip()
        }
        if allowed_ips and request.remote_addr not in allowed_ips:
            return jsonify({
                "error": "forbidden",
                "reason": "remote_addr not in SLACK_ALLOWED_IPS",
            }), 403

        signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
        if not signing_secret:
            return jsonify({
                "error": "slack_not_configured",
                "reason": "SLACK_SIGNING_SECRET env var is empty",
            }), 503

        # Build the command handler lazily so imports stay fast for agents
        # that never receive Slack commands.
        command_handler = SlackCommandHandler()
        endpoint = SlackEndpoint()
        # Read raw body BEFORE Flask parses it (we need the exact bytes for HMAC)
        raw_body = request.get_data(cache=True) or b""
        response = endpoint.handle_request(
            body=raw_body,
            timestamp_header=request.headers.get("X-Slack-Request-Timestamp"),
            signature_header=request.headers.get("X-Slack-Signature"),
            signing_secret=signing_secret,
            command_handler=command_handler,
        )
        return jsonify(response.body), response.status_code

    # --- Connectors (AWS SSM) ----------------------------------------------
    # CRUD for remote hosts the operator wants the agent to monitor via SSM.
    # Stored in $IPRACTICOM_SWEEPER_STATE_DIR/connectors.yaml.

    @_route("/api/connectors", methods=["GET"])
    @require_auth
    def api_connectors_list():
        """List all configured SSM connectors."""
        return jsonify([c.to_dict() for c in load_connectors()])

    @_route("/api/connectors", methods=["POST"])
    @_rate_limit("api", _RL_API_DEFAULT)
    @require_auth
    def api_connectors_create():
        """Create a new connector. Body: {name, instance_id, region?, tags?, enabled?}"""
        payload = request.get_json(silent=True) or {}
        try:
            connector = Connector.from_dict(payload)
        except (ValueError, TypeError) as e:
            return _safe_error_response(e), 400
        try:
            add_connector(connector)
        except ValueError as e:  # duplicate name
            return _safe_error_response(e), 409
        return jsonify(connector.to_dict()), 201

    @_route("/api/connectors/<name>", methods=["GET"])
    @require_auth
    def api_connectors_get(name):
        """Get one connector by name."""
        c = get_connector(name)
        if c is None:
            return jsonify({"error": "not_found"}), 404
        return jsonify(c.to_dict())

    @_route("/api/connectors/<name>", methods=["PATCH"])
    @_rate_limit("api", _RL_API_DEFAULT)
    @require_auth
    def api_connectors_update(name):
        """Update fields on a connector. Body: any subset of mutable fields.

        Immutable: name (it's the identity), created_at.
        """
        payload = request.get_json(silent=True) or {}
        try:
            updated = update_connector(name, **payload)
        except KeyError:
            return jsonify({"error": "not_found"}), 404
        except ValueError as e:
            return _safe_error_response(e), 400
        return jsonify(updated.to_dict())

    @_route("/api/connectors/<name>", methods=["DELETE"])
    @_rate_limit("api", _RL_API_DEFAULT)
    @require_auth
    def api_connectors_delete(name):
        """Delete a connector. Returns 204 on success, 404 if not found."""
        if not remove_connector(name):
            return jsonify({"error": "not_found"}), 404
        return ("", 204)

    @_route("/api/connectors/<name>/test", methods=["POST"])
    @_rate_limit("api", _RL_API_DEFAULT)
    @require_auth
    def api_connectors_test(name):
        """Trigger a single SSM collection for one connector (sync, may take 5-30s).

        Used by the dashboard 'Test' button — gives operators immediate feedback
        whether their IAM/SSM setup works, instead of waiting for the next loop tick.
        """
        c = get_connector(name)
        if c is None:
            return jsonify({"error": "not_found"}), 404
        try:
            from ipracticom_sweeper.fleet import AwsSsmConnector, SsmError
            connector = AwsSsmConnector(region=c.region)
            snapshot = connector.collect_one(
                c.instance_id,
                freeswitch_enabled=getattr(c, "freeswitch_enabled", False),
            )
            mark_connector_collected(name)
            return jsonify({"ok": True, "snapshot": snapshot})
        except SsmError as e:
            mark_connector_error(name, str(e))
            return _safe_error_response(e, 502)
        except Exception as e:
            mark_connector_error(name, str(e))
            return _safe_error_response(e, 500)

    # URL verification handshake (Slack sends this once when registering the URL).
    # We reply with the challenge value to confirm we own this endpoint.
    @_route("/slack/events", methods=["GET"])
    def slack_events_challenge():
        challenge = request.args.get("challenge", "")
        return challenge, 200, {"Content-Type": "text/plain"}

    # Time-series history endpoint — read scalar metrics collected over time
    @_route("/api/history/<metric>", methods=["GET"])
    @require_auth
    def api_history(metric):
        """Return time-series samples for a single metric.

        Query params:
          host:   host id (default: current host)
          hours:  how far back to look (default 24, max 720)
          limit:  max samples (default 1000)
        """
        from pathlib import Path
        from ipracticom_sweeper.storage import TimeSeriesDB

        host = request.args.get("host") or os.environ.get(
            "IPRACTICOM_SWEEPER_HOST_ID", "localhost"
        )
        try:
            hours = min(int(request.args.get("hours", "24")), 720)
        except ValueError:
            hours = 24
        try:
            limit = min(int(request.args.get("limit", "1000")), 10000)
        except ValueError:
            limit = 1000

        state_dir = Path(os.environ.get(
            "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
        ))
        db_path = state_dir / "metrics.db"
        if not db_path.exists():
            return jsonify({
                "host": host,
                "metric": metric,
                "samples": [],
                "note": "no data yet (db file missing)",
            })

        db = TimeSeriesDB(db_path)
        try:
            since_ts = int(time.time()) - (hours * 3600)
            samples = db.query(host=host, metric=metric, since_ts=since_ts, limit=limit)
            # Reverse so it's oldest-first (for charting)
            samples.reverse()
            return jsonify({
                "host": host,
                "metric": metric,
                "hours": hours,
                "count": len(samples),
                "samples": samples,
            })
        finally:
            db.close()

    # History catalog (v0.4.2) — list distinct metrics + hosts + per-metric counts.
    @_route("/api/history", methods=["GET"])
    @require_auth
    def api_history_catalog():
        """Return the catalog of available time-series.

        Returns:
          metrics: sorted list of distinct metric names
          hosts:   sorted list of distinct host ids
          metrics_with_counts: [{metric, count, last_value, last_ts}] per metric
          hosts_with_counts:   [{host, count}] per host
          note: present if the metrics.db is missing
        """
        import sqlite3
        from pathlib import Path

        state_dir = Path(os.environ.get(
            "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
        ))
        db_path = state_dir / "metrics.db"
        if not db_path.exists():
            return jsonify({
                "metrics": [],
                "hosts": [],
                "metrics_with_counts": [],
                "hosts_with_counts": [],
                "note": "no data yet (db file missing)",
            })

        try:
            conn = sqlite3.connect(str(db_path))
            try:
                conn.row_factory = sqlite3.Row
                # Sanity check the schema — if the table isn't there yet,
                # surface a clean empty response instead of 500.
                tables = {
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                # The time-series table created by storage/timeseries.py is
                # named `metrics` (not `samples`); querying the wrong name made
                # this catalog perpetually report "no data" even with a full DB.
                if "metrics" not in tables:
                    return jsonify({
                        "metrics": [],
                        "hosts": [],
                        "metrics_with_counts": [],
                        "hosts_with_counts": [],
                        "note": "no data yet (metrics table missing)",
                    })

                metrics = sorted({
                    r[0] for r in conn.execute(
                        "SELECT DISTINCT metric FROM metrics"
                    )
                })
                hosts = sorted({
                    r[0] for r in conn.execute(
                        "SELECT DISTINCT host FROM metrics"
                    )
                })
                metrics_with_counts = [
                    dict(r) for r in conn.execute(
                        """
                        SELECT metric,
                               COUNT(*) AS count,
                               (SELECT value FROM metrics s2
                                  WHERE s2.metric = s1.metric
                                  ORDER BY ts DESC LIMIT 1) AS last_value,
                               MAX(ts) AS last_ts
                          FROM metrics s1
                         GROUP BY metric
                         ORDER BY metric
                        """
                    )
                ]
                hosts_with_counts = [
                    dict(r) for r in conn.execute(
                        """
                        SELECT host, COUNT(*) AS count, MAX(ts) AS last_ts
                          FROM metrics
                         GROUP BY host
                         ORDER BY host
                        """
                    )
                ]
            finally:
                conn.close()
        except sqlite3.DatabaseError as e:
            return jsonify({
                "metrics": [],
                "hosts": [],
                "metrics_with_counts": [],
                "hosts_with_counts": [],
                "error": f"db error: {e}",
            }), 500

        return jsonify({
            "metrics": metrics,
            "hosts": hosts,
            "metrics_with_counts": metrics_with_counts,
            "hosts_with_counts": hosts_with_counts,
        })

    # Approvals (v0.4.2) — list pending repair proposals, approve, reject.
    @_route("/api/approvals", methods=["GET"])
    @require_auth
    def api_approvals_list():
        """List all repair proposals awaiting operator decision."""
        from ipracticom_sweeper.repair.pending import list_pending

        pending = list_pending()
        return jsonify({
            "count": len(pending),
            "pending": [p.to_dict() for p in pending],
        })

    @_route("/api/approvals/<pid>/approve", methods=["POST"])
    @_rate_limit("api", _RL_API_DEFAULT)
    @require_auth
    def api_approvals_approve(pid):
        """Approve a pending proposal: execute the repair, archive as approved."""
        from ipracticom_sweeper.repair import pending as pending_mod
        from ipracticom_sweeper.repair import actions as actions_mod

        # Serialise the whole check-then-execute under the per-proposal flock
        # (same guard the dashboard route uses). Without it, two concurrent
        # approves — e.g. a double-tap in the Telegram bot, which drives THIS
        # endpoint — both pass the "is pending" check and execute the repair
        # twice (double service_restart / reboot). The status re-check must
        # live inside the lock to be atomic.
        try:
            lock_cm = pending_mod._proposal_lock(pid)
        except ValueError:
            return jsonify({"error": "not_found"}), 404

        with lock_cm:
            proposal = pending_mod.get_proposal(pid)
            if proposal is None:
                return jsonify({"error": "not_found"}), 404
            if proposal.status != "pending":
                # Refuse to re-execute an already-decided proposal.
                return jsonify({
                    "error": "already_decided",
                    "status": proposal.status,
                }), 409

            # Execute the repair. execute_repair returns RepairResult; we
            # log + archive regardless of success/failure so the operator
            # has an audit trail.
            try:
                result = actions_mod.execute_repair(
                    proposal.action, **proposal.kwargs
                )
                result_dict = {
                    "action": result.action,
                    "target": result.target,
                    "success": result.success,
                    "message": result.message,
                    "error": result.error,
                    "rollback_available": result.rollback_available,
                }
                new_status = "executed" if result.success else "failed"
            except Exception:
                app.logger.exception("approval_execute_failed for pid=%s", pid)
                result_dict = {"action": proposal.action, "success": False, "error": "internal_error"}
                new_status = "failed"

            pending_mod.set_status(pid, new_status)
            pending_mod.archive(pid, "approved")
            pending_mod.log_audit({
                "kind": "repair_executed",
                "proposal_id": pid,
                "action": proposal.action,
                "kwargs": _redact_secrets(proposal.kwargs),
                "proposed_command": proposal.proposed_command,
                "status": new_status,
                "result": result_dict,
            })
            # Escalation + multi-channel outcome push: on failure block the
            # (action, server) pair and tell every channel a human is needed;
            # on success fan out the "repair completed" confirmation.
            from ipracticom_sweeper.repair import decide as decide_mod
            decide_mod.escalate_and_notify(
                proposal, result_dict, bool(result_dict.get("success")), "operator"
            )
            return jsonify({
                "ok": result_dict.get("success", False),
                "status": new_status,
                "result": result_dict,
            })

    @_route("/api/approvals/<pid>/reject", methods=["POST"])
    @_rate_limit("api", _RL_API_DEFAULT)
    @require_auth
    def api_approvals_reject(pid):
        """Reject a pending proposal: archive as rejected (no execution).

        Requires a non-empty `reason` field in the JSON body so we have
        an audit trail explaining why the operator rejected the repair.
        """
        from ipracticom_sweeper.repair import pending as pending_mod

        # Check proposal existence first so 404 wins over 400 — that way
        # unknown-id probes don't accidentally leak "reason required" hints
        # and existing 404 tests keep passing.
        proposal = pending_mod.get_proposal(pid)
        if proposal is None:
            return jsonify({"error": "not_found"}), 404

        payload = request.get_json(silent=True) or {}
        reason = (payload.get("reason") or "").strip()
        if not reason:
            return jsonify({"error": "reason_required"}), 400

        if proposal.status != "pending":
            return jsonify({
                "error": "already_decided",
                "status": proposal.status,
            }), 409

        pending_mod.set_status(pid, "rejected")
        pending_mod.archive(pid, "rejected")
        pending_mod.log_audit({
            "kind": "repair_rejected",
            "proposal_id": pid,
            "action": proposal.action,
            "kwargs": _redact_secrets(proposal.kwargs),
            "reason": reason,
        })
        # Let every channel know the decision was made (informational).
        from ipracticom_sweeper.repair import decide as decide_mod
        decide_mod.notify_rejected(proposal, reason, "operator")
        return jsonify({"ok": True, "status": "rejected"})

    # Sprint 18.5 — CSV export of the approval audit log
    @_route("/api/approvals/export.csv", methods=["GET"])
    @require_auth
    def api_approvals_export():
        """Export approval audit log as CSV (UTF-8 BOM, Excel-friendly)."""
        from pathlib import Path
        from ipracticom_sweeper.repair import approvals_v2

        state_dir = Path(os.environ.get(
            "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
        ))
        pending_dir = state_dir / "pending_repairs"
        approved_dir = pending_dir / "approved"
        rejected_dir = pending_dir / "rejected"
        audit_log = state_dir / "audit" / "repairs.jsonl"

        since = request.args.get("since")
        until = request.args.get("until")
        csv_bytes = approvals_v2.export_audit_csv(
            pending_dir, approved_dir, rejected_dir, audit_log,
            since=since, until=until,
        )
        return Response(
            csv_bytes,
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="approvals.csv"'},
        )

    # Escalation blocks — list + clear (allow-retry). A block is created when
    # an approved repair FAILS, so the sweeper won't silently re-propose it.
    @_route("/api/approvals/blocked", methods=["GET"])
    @require_auth
    def api_approvals_blocked_list():
        """List (action, server) pairs blocked after a failed approved repair."""
        from ipracticom_sweeper.repair import block as block_mod

        items = block_mod.list_blocked()
        return jsonify({"count": len(items), "blocked": items})

    @_route("/api/approvals/blocked/<key>", methods=["DELETE"])
    @_rate_limit("api", _RL_API_DEFAULT)
    @require_auth
    def api_approvals_unblock(key):
        """Clear a block so the sweeper may propose that repair again."""
        from ipracticom_sweeper.repair import block as block_mod

        if block_mod.unblock_key(key):
            return jsonify({"ok": True, "key": key})
        return jsonify({"error": "not_found"}), 404

    # Fleet (v0.4.2) — local host + every configured SSM connector.
    @_route("/api/fleet", methods=["GET"])
    @require_auth
    def api_fleet_list():
        """Aggregate the local host + all enabled connectors into one view."""
        from ipracticom_sweeper.config import load_connectors
        from pathlib import Path

        state_dir = Path(os.environ.get(
            "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
        ))
        local_heartbeat = _read_heartbeat(state_dir)

        hosts: list[dict[str, Any]] = []
        # The local host is always present.
        local_entry: dict[str, Any] = {
            "name": "local",
            "kind": "local",
            "status": _local_status(local_heartbeat),
        }
        if local_heartbeat:
            local_entry.update({
                "last_seen": local_heartbeat.get("ts_iso"),
                "defcon": local_heartbeat.get("defcon"),
                "problems_found": local_heartbeat.get("problems_found"),
            })
        hosts.append(local_entry)

        for c in load_connectors():
            last_err = c.last_error
            status = "error" if last_err else ("ok" if c.last_collected_at else "unknown")
            hosts.append({
                "name": c.name,
                "kind": "connector",
                "instance_id": c.instance_id,
                "region": c.region,
                "enabled": c.enabled,
                "tags": c.tags,
                "status": status,
                "last_collected_at": c.last_collected_at,
                "last_error": last_err,
            })

        return jsonify({
            "count": len(hosts),
            "hosts": hosts,
        })

    @_route("/api/fleet/<host>", methods=["GET"])
    @require_auth
    def api_fleet_host(host):
        """Per-host details — local reads heartbeat; connectors read config + state."""
        from ipracticom_sweeper.config import get_connector, load_connectors
        from pathlib import Path

        state_dir = Path(os.environ.get(
            "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
        ))

        if host == "local":
            hb = _read_heartbeat(state_dir)
            if hb is None:
                return jsonify({"error": "no heartbeat yet"}), 404
            return jsonify({
                "name": "local",
                "kind": "local",
                "status": _local_status(hb),
                "defcon": hb.get("defcon"),
                "problems_found": hb.get("problems_found"),
                "repairs_attempted": hb.get("repairs_attempted"),
                "last_seen": hb.get("ts_iso"),
                "last_seen_ts": hb.get("ts"),
                "extra": hb.get("extra") or {},
            })

        c = get_connector(host)
        if c is None:
            return jsonify({"error": "not_found"}), 404
        last_err = c.last_error
        status = "error" if last_err else ("ok" if c.last_collected_at else "unknown")
        return jsonify({
            "name": c.name,
            "kind": "connector",
            "instance_id": c.instance_id,
            "region": c.region,
            "enabled": c.enabled,
            "tags": c.tags,
            "status": status,
            "last_collected_at": c.last_collected_at,
            "last_error": last_err,
            "created_at": c.created_at,
        })

    # Predictions endpoint — read time-series, return threshold crossings
    @_route("/api/predictions", methods=["GET"])
    @require_auth
    def api_predictions():
        """Return current predictions for all configured metrics."""
        from pathlib import Path
        from ipracticom_sweeper.predict.integration import collect_predictions
        from ipracticom_sweeper.config import load_rules

        state_dir = Path(os.environ.get(
            "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
        ))
        db_path = state_dir / "metrics.db"
        if not db_path.exists():
            return jsonify({
                "predictions": [],
                "note": "no data yet (db file missing)",
            })

        # Scope to a specific machine when the caller asks (per-machine console);
        # fall back to the local host id for backward compatibility.
        host = request.args.get("host") or os.environ.get(
            "IPRACTICOM_SWEEPER_HOST_ID", "localhost"
        )
        try:
            rules = load_rules()
        except Exception:
            rules = {}
        thresholds = rules.get("predict", {}).get("thresholds", None)
        preds = collect_predictions(db_path, host=host, thresholds=thresholds)
        return jsonify({
            "host": host,
            "count": len(preds),
            "predictions": [p.to_dict() for p in preds],
        })

    # Evidence export endpoint — build a signed bundle of audit + repairs
    @_route("/api/evidence/export", methods=["GET"])
    @require_auth
    def api_evidence_export():
        """Build an evidence bundle (audit log + repairs + snapshot summary).

        Query params:
          hours: how far back to look (default 24)
          format: 'json' (default) or 'inline' (returns bundle inline)
        """
        from pathlib import Path
        from ipracticom_sweeper.evidence.bundle import (
            build_evidence_bundle, export_bundle_to_json, verify_bundle,
        )

        try:
            hours = min(int(request.args.get("hours", "24")), 720)
        except ValueError:
            hours = 24

        state_dir = Path(os.environ.get(
            "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
        ))
        # Scope to a specific machine when the caller asks (per-machine console);
        # fall back to the local host id for backward compatibility.
        local_host_id = os.environ.get("IPRACTICOM_SWEEPER_HOST_ID", "localhost")
        host = request.args.get("host") or local_host_id
        audit_log = state_dir / "audit" / "repairs.jsonl"
        since_ts = time.time() - (hours * 3600)

        def _entry_for_host(entry: dict) -> bool:
            # Legacy entries carry no host tag and belong to the local agent;
            # keep them only when the requested host IS the local host.
            entry_host = entry.get("host")
            if entry_host is None:
                return host == local_host_id
            return entry_host == host

        # Read audit log (best-effort)
        audit_entries: list = []
        if audit_log.exists():
            try:
                import json as _json
                cutoff = int(since_ts)
                with open(audit_log) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = _json.loads(line)
                            ts = entry.get("ts") or 0
                            if ts >= cutoff and _entry_for_host(entry):
                                audit_entries.append(entry)
                        except _json.JSONDecodeError as e:
                            log_suppressed("agent_api_audit_parse", e)
                            continue
            except (OSError, PermissionError) as e:
                log_suppressed("agent_api_audit_read", e)

        # Build bundle (repairs list will be inside audit_entries, filtered below)
        repair_entries = [e for e in audit_entries if e.get("kind") == "repair_executed"]

        bundle = build_evidence_bundle(
            host=host,
            agent_version="0.4.0",
            audit_entries=audit_entries,
            repair_entries=repair_entries,
            since_ts=since_ts,
            until_ts=time.time(),
        )

        # Optional: write to file and return path
        if request.args.get("format") == "file":
            out_path = state_dir / "evidence" / f"bundle-{int(time.time())}.json"
            export_bundle_to_json(bundle, out_path)
            return jsonify({
                "ok": True,
                "path": str(out_path),
                "verified": verify_bundle(bundle),
                "audit_entries": len(audit_entries),
                "repair_entries": len(repair_entries),
            })

        return jsonify(bundle.to_dict())

    # -----------------------------------------------------------------------
    # v1.4.0 Slice 4 — Per-host config + module catalog routes
    # -----------------------------------------------------------------------
    #
    # Surface the slice 1+2+3 work over HTTP for the dashboard.
    # All routes are auth + rate-limit gated by the same wrappers as
    # the rest of the agent API.
    # -----------------------------------------------------------------------

    def _host_config_to_dict(cfg) -> dict:
        """Flatten a HostConfig into the JSON shape the dashboard expects."""
        return {
            "name": cfg.name,
            "description": cfg.description,
            "enabled": cfg.enabled,
            "updated_at": cfg.updated_at,
            "monitors": [
                {"name": m.name, "enabled": m.enabled,
                 "interval_sec": m.interval_sec, **m.settings}
                for m in cfg.monitors
            ],
            "repairs": [
                {"name": r.name, "enabled": r.enabled,
                 "require_approval": r.require_approval, **r.settings}
                for r in cfg.repairs
            ],
            "runbooks": [
                {"name": rb.name, "enabled": rb.enabled, **rb.settings}
                for rb in cfg.runbooks
            ],
            "suppressions": [
                {"rule": s.rule, "until": s.until, "reason": s.reason}
                for s in cfg.suppressions
            ],
        }

    def _suppression_to_dict(s) -> dict:
        return {"rule": s.rule, "until": s.until, "reason": s.reason}

    def _module_info_to_dict(m) -> dict:
        return {
            "kind": m.kind,
            "name": m.name,
            "title_en": m.title_en,
            "title_he": m.title_he,
            "description": m.description,
            "tags": list(m.tags),
            "risk": m.risk,
            "params": [
                {"name": p.name, "type": p.type, "default": p.default}
                for p in m.params
            ],
            "catalog_only": m.catalog_only,
        }

    # ---- /api/hosts ------------------------------------------------------

    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_hosts_list():
        from ipracticom_sweeper.config import host_config as _hc
        out = []
        for name in _hc.list_hosts():
            try:
                cfg = _hc.load_host(name)
            except Exception as e:
                log_suppressed("agent_api_host_load", e)
                continue
            out.append(_host_config_to_dict(cfg))
        return jsonify(out)

    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_hosts_detail(name: str):
        from ipracticom_sweeper.config import host_config as _hc
        # _host_yaml_path raises ValueError on bad host names
        try:
            _hc._host_yaml_path(name)
        except ValueError:
            return jsonify({"error": "invalid_host", "name": name}), 400
        try:
            cfg = _hc.load_host(name)
        except FileNotFoundError:
            return jsonify({"error": "not_found", "name": name}), 404
        # distinguish "unknown host" (file does not exist) from default
        if not _hc._host_yaml_path(name).exists():
            return jsonify({"error": "not_found", "name": name}), 404
        return jsonify(_host_config_to_dict(cfg))

    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_hosts_add_suppression(name: str):
        from ipracticom_sweeper.config import host_config as _hc
        try:
            _hc._host_yaml_path(name)
        except ValueError:
            return jsonify({"error": "invalid_host", "name": name}), 400
        body = request.get_json(silent=True) or {}
        rule = (body.get("rule") or "").strip()
        if not rule:
            return jsonify({"error": "invalid_rule"}), 400
        try:
            entry = _hc.add_suppression(
                name, rule,
                until=body.get("until"),
                reason=body.get("reason", ""),
            )
        except ValueError as e:
            return _safe_error_response(e, 400)
        return jsonify(_suppression_to_dict(entry)), 201

    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_hosts_list_suppressions(name: str):
        from ipracticom_sweeper.config import host_config as _hc
        try:
            _hc._host_yaml_path(name)
        except ValueError:
            return jsonify({"error": "invalid_host", "name": name}), 400
        return jsonify([_suppression_to_dict(s)
                        for s in _hc.list_active_suppressions(name)])

    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_hosts_delete_suppression(name: str, rule: str):
        from ipracticom_sweeper.config import host_config as _hc
        try:
            _hc._host_yaml_path(name)
        except ValueError:
            return jsonify({"error": "invalid_host", "name": name}), 400
        if not _hc.remove_suppression(name, rule):
            return jsonify({"error": "not_found"}), 404
        return ("", 204)

    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_hosts_cleanup_suppressions():
        from ipracticom_sweeper.config import host_config as _hc
        removed = _hc.cleanup_expired_suppressions()
        return jsonify({"removed": removed})

    def _dict_to_host_config(name: str, body: dict):
        """Build a HostConfig from the dashboard's JSON shape.

        Inverse of ``_host_config_to_dict``: each monitor/repair/runbook entry
        carries {name, enabled, ...} plus module-specific knobs (threshold_pct,
        interval_sec, ...) flattened at the top level; everything that isn't a
        reserved field is folded back into ``settings``.
        """
        from ipracticom_sweeper.config import host_config as _hc

        def _split(entry: dict, reserved: set[str]) -> tuple[str, dict]:
            entry_name = str(entry.get("name") or "").strip()
            settings = {k: v for k, v in entry.items() if k not in reserved}
            return entry_name, settings

        monitors = []
        for m in body.get("monitors") or []:
            mname, settings = _split(m, {"name", "enabled", "interval_sec"})
            if not mname:
                continue
            monitors.append(_hc.MonitorConfig(
                name=mname,
                enabled=bool(m.get("enabled", True)),
                interval_sec=int(m.get("interval_sec", 60)),
                settings=settings,
            ))
        repairs = []
        for r in body.get("repairs") or []:
            rname, settings = _split(r, {"name", "enabled", "require_approval"})
            if not rname:
                continue
            repairs.append(_hc.RepairConfig(
                name=rname,
                enabled=bool(r.get("enabled", True)),
                require_approval=bool(r.get("require_approval", True)),
                settings=settings,
            ))
        runbooks = []
        for rb in body.get("runbooks") or []:
            rbname, settings = _split(rb, {"name", "enabled"})
            if not rbname:
                continue
            runbooks.append(_hc.RunbookConfig(
                name=rbname,
                enabled=bool(rb.get("enabled", True)),
                settings=settings,
            ))
        return _hc.HostConfig(
            name=name,
            description=str(body.get("description", "")),
            enabled=bool(body.get("enabled", True)),
            monitors=monitors,
            repairs=repairs,
            runbooks=runbooks,
        )

    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_hosts_save(name: str):
        """Create/replace a host's monitor/repair/runbook configuration.

        Body mirrors ``GET /api/hosts/<name>`` so the dashboard can round-trip
        the catalog selection + per-test thresholds it built in the add-machine
        wizard. Suppressions are managed via their own routes and are preserved
        across a save (they are re-read from the existing YAML).
        """
        from ipracticom_sweeper.config import host_config as _hc
        try:
            _hc._host_yaml_path(name)
        except ValueError:
            return jsonify({"error": "invalid_host", "name": name}), 400
        body = request.get_json(silent=True) or {}
        try:
            cfg = _dict_to_host_config(name, body)
        except (ValueError, TypeError) as e:
            return _safe_error_response(e, 400)
        # Preserve any existing suppressions — the wizard doesn't manage them.
        try:
            existing = _hc.load_host(name)
            cfg.suppressions = existing.suppressions
        except Exception as e:
            log_suppressed("agent_api_host_save_preserve_suppr", e)
        try:
            _hc.save_host(cfg)
        except Exception as e:
            return _safe_error_response(e, 500)
        return jsonify(_host_config_to_dict(_hc.load_host(name)))

    app.add_url_rule("/api/hosts",
                     view_func=api_hosts_list,
                     methods=["GET"])
    app.add_url_rule("/api/hosts/<name>",
                     view_func=api_hosts_detail,
                     methods=["GET"])
    app.add_url_rule("/api/hosts/<name>",
                     view_func=api_hosts_save,
                     methods=["PUT"])
    app.add_url_rule("/api/hosts/<name>/suppressions",
                     view_func=api_hosts_add_suppression,
                     methods=["POST"])
    app.add_url_rule("/api/hosts/<name>/suppressions",
                     view_func=api_hosts_list_suppressions,
                     methods=["GET"])
    app.add_url_rule("/api/hosts/<name>/suppressions/<rule>",
                     view_func=api_hosts_delete_suppression,
                     methods=["DELETE"])
    app.add_url_rule("/api/hosts/_cleanup-suppressions",
                     view_func=api_hosts_cleanup_suppressions,
                     methods=["POST"])

    # ---- /api/modules ----------------------------------------------------

    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_modules_list():
        from ipracticom_sweeper.config import module_registry as _mr
        kind = request.args.get("kind")
        risk = request.args.get("risk")
        tag = request.args.get("tag")
        available_only = request.args.get("available_only", "0") == "1"
        # Self-monitoring modules are the agent watching itself — always-on and
        # not machine-configurable — so they are hidden from the add-machine
        # catalog by default. include_self=1 opts back in (e.g. an admin view).
        exclude_self = request.args.get("include_self", "0") != "1"
        try:
            modules = _mr.discover_modules()
            modules = _mr.filter_modules(
                modules,
                kind=kind, risk=risk, tag=tag,
                available_only=available_only,
                exclude_self=exclude_self,
            )
        except ValueError as e:
            return _safe_error_response(e, 400)
        return jsonify([_module_info_to_dict(m) for m in modules])

    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_modules_detail(kind: str, name: str):
        from ipracticom_sweeper.config import module_registry as _mr
        m = _mr.get_module(name, kind=kind)
        if m is None:
            return jsonify({"error": "not_found", "kind": kind, "name": name}), 404
        return jsonify(_module_info_to_dict(m))

    # ---- /api/checks/<monitor> -------------------------------------------
    # Sub-checks of a monitor "bundle". Today only FreeSWITCH has them: the
    # FS-01..40 checks the agent actually runs, surfaced so the operator can
    # pick/tune each one instead of an opaque "freeswitch_v2" bundle.

    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_monitor_checks(monitor: str):
        if monitor == "freeswitch":
            from ipracticom_sweeper.config.freeswitch_checks import list_freeswitch_checks
            return jsonify(list_freeswitch_checks())
        return jsonify([])

    # ---- /api/self-health ------------------------------------------------
    # The agent's OWN health: agent-machine resilience (state dir, audit,
    # watchdog, uptime) + notification-bot connectivity (Telegram + Slack).
    # Read-path only — it reads the cache written by the sweep's self phase, so
    # it never fires live network probes on a dashboard refresh.
    @require_auth
    @_rate_limit("api", _RL_API_DEFAULT)
    def api_self_health():
        import os as _os
        from pathlib import Path as _Path
        from ipracticom_sweeper.monitor import self_snapshot
        state_dir = _Path(_os.environ.get(
            "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper",
        ))
        try:
            section = self_snapshot.build_self_section(state_dir)
        except Exception as e:
            return _safe_error_response(e, 500)
        return jsonify(section)

    app.add_url_rule("/api/modules",
                     view_func=api_modules_list,
                     methods=["GET"])
    app.add_url_rule("/api/modules/<kind>/<name>",
                     view_func=api_modules_detail,
                     methods=["GET"])
    app.add_url_rule("/api/checks/<monitor>",
                     view_func=api_monitor_checks,
                     methods=["GET"])
    app.add_url_rule("/api/self-health",
                     view_func=api_self_health,
                     methods=["GET"])

    return app


def main():
    parser = argparse.ArgumentParser(description="iPracticom Sweeper Agent API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--allow-open",
        action="store_true",
        help="Explicitly allow OPEN mode (no token) on a non-loopback host. "
             "By default, OPEN mode + non-loopback is refused.",
    )
    args = parser.parse_args()

    # Fail-closed: if no token AND binding to a non-loopback address, refuse
    # to start unless --allow-open is passed. Prevents accidentally exposing
    # an unauthenticated API to the network.
    token_present = bool(os.environ.get("AGENT_API_TOKEN", ""))
    is_loopback = args.host in ("127.0.0.1", "::1", "localhost", "")
    if not token_present and not is_loopback and not args.allow_open:
        print(
            f"[agent_api] REFUSING TO START: AGENT_API_TOKEN is unset but "
            f"--host={args.host} is not loopback. This would expose the API "
            f"unauthenticated. Set AGENT_API_TOKEN or pass --allow-open.",
            file=sys.stderr,
        )
        sys.exit(1)

    app = create_app()
    print(f"[agent_api] Starting on {args.host}:{args.port}")
    print(f"[agent_api] Auth: {'token' if token_present else 'OPEN'}")

    # Start the fleet collector loop (no-op if there are no enabled connectors).
    # Imported lazily to keep cold-start fast for agents that don't use fleet mode.
    try:
        from ipracticom_sweeper.fleet import start_collector_loop
        start_collector_loop()
        print(f"[agent_api] Fleet collector loop started")
    except Exception as e:
        print(f"[agent_api] Fleet collector disabled: {e}")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()