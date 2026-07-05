"""React SPA serving + settings JSON API + agent REST mount for the dashboard.

Split out of dashboard.py (v1.5.18) to keep that module inside its size
budget (see test_v612_dashboard_notify_split). Everything here is registered
on the dashboard's Flask app by :func:`register_webui`, so it inherits the
dashboard's global HTTP Basic auth ``before_request``.

Three concerns live here:

1. **SPA static serving** — the React dashboard (built with Vite) is served
   from this same Flask origin under ``/app``, so it calls ``/api/*`` and
   ``/v6/*`` with zero CORS and reuses the Basic session the browser already
   attaches to every same-origin request. Built assets live in ``webui/dist/``.

2. **Settings JSON API** — the Jinja ``/settings`` form is form-encoded +
   HTML-rendered; the SPA needs JSON. These reuse the same notifications.env
   helpers. Secrets are NEVER returned — only booleans plus the non-secret
   chat id. Thresholds are slider-editable and persist to the state-dir
   override file (``rules.local.yaml``) via ``save_rules_override``.

3. **Agent REST mount** — the full agent_api route set is registered with a
   passthrough auth decorator (Basic already gates it), skipping endpoints
   the dashboard defines itself and the Slack webhooks (signature-authed,
   they belong to the standalone agent). Rate limiting stays off: the SPA
   polls several endpoints on a short interval and is already auth-gated.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from ipracticom_sweeper.config import load_rules, save_rules_override
from ipracticom_sweeper.dashboard_notify import (
    _read_notifications_env,
    _write_notifications_env,
    _test_telegram,
    _test_slack,
    _test_slack_app,
    _validate_slack_webhook_url,
)
from ipracticom_sweeper.notify import store as bots_store

WEBUI_DIST = Path(__file__).resolve().parent / "webui" / "dist"

# Slider-editable thresholds: section → key → (min, max). Anything outside this
# allowlist is rejected, so the API can't be used to inject arbitrary rules.
_THRESHOLD_KEYS = {
    "cpu": {
        "load_avg_5min_warn": (0.1, 64.0),
        "load_avg_5min_crit": (0.1, 64.0),
        "iowait_percent_warn": (1.0, 100.0),
    },
    "memory": {
        "used_percent_warn": (1.0, 100.0),
        "used_percent_crit": (1.0, 100.0),
        "swap_used_percent_warn": (1.0, 100.0),
    },
    "disk": {
        "used_percent_warn": (1.0, 100.0),
        "used_percent_crit": (1.0, 100.0),
        "inode_used_percent_warn": (1.0, 100.0),
    },
}

# warn must stay strictly below its crit twin (per section).
_WARN_CRIT_PAIRS = {
    "cpu": [("load_avg_5min_warn", "load_avg_5min_crit")],
    "memory": [("used_percent_warn", "used_percent_crit")],
    "disk": [("used_percent_warn", "used_percent_crit")],
}


def register_webui(app: Flask, *, is_remote_mode) -> None:
    """Register SPA serving, the settings JSON API, and the agent REST mount.

    ``is_remote_mode`` is dashboard.py's ``_is_remote_mode`` callable —
    settings mutate local files and are refused in remote (agent-proxy) mode.
    """

    # --- Settings JSON API (for the React SPA) -------------------------------

    @app.route("/api/settings/notifications", methods=["GET"])
    def api_settings_notifications_get():
        """Notification config for the SPA — secrets masked. Local-only."""
        if is_remote_mode():
            return jsonify({"error": "settings are local-only"}), 403
        current = _read_notifications_env()
        return jsonify({
            "telegram_bot_token_set": bool(current.get("TELEGRAM_BOT_TOKEN")),
            "telegram_chat_id": current.get("TELEGRAM_CHAT_ID", ""),
            "slack_webhook_set": bool(current.get("SLACK_WEBHOOK_URL")),
        })

    @app.route("/api/settings/notifications", methods=["PUT"])
    def api_settings_notifications_put():
        """Update notification config. Omitted/blank fields keep their current
        value so the SPA never has to resend a secret it only shows masked."""
        if is_remote_mode():
            return jsonify({"error": "settings are local-only"}), 403
        payload = request.get_json(silent=True) or {}
        current = _read_notifications_env()
        merged = {
            "SLACK_WEBHOOK_URL": current.get("SLACK_WEBHOOK_URL", ""),
            "TELEGRAM_BOT_TOKEN": current.get("TELEGRAM_BOT_TOKEN", ""),
            "TELEGRAM_CHAT_ID": current.get("TELEGRAM_CHAT_ID", ""),
        }
        for src_key, dst_key in (
            ("slack_webhook_url", "SLACK_WEBHOOK_URL"),
            ("telegram_bot_token", "TELEGRAM_BOT_TOKEN"),
            ("telegram_chat_id", "TELEGRAM_CHAT_ID"),
        ):
            if src_key in payload and str(payload[src_key]).strip():
                merged[dst_key] = str(payload[src_key]).strip()
        ok_slack, err_slack = _validate_slack_webhook_url(merged["SLACK_WEBHOOK_URL"])
        if not ok_slack:
            return jsonify({"ok": False, "error": err_slack}), 400
        ok, err = _write_notifications_env(merged)
        if not ok:
            return jsonify({"ok": False, "error": err or "write failed"}), 500
        return jsonify({"ok": True})

    @app.route("/api/settings/notifications/test", methods=["POST"])
    def api_settings_notifications_test():
        """JSON mirror of /settings/test. Body: {channel: 'telegram'|'slack'}."""
        if is_remote_mode():
            return jsonify({"ok": False, "error": "settings are local-only"}), 403
        payload = request.get_json(silent=True) or {}
        channel = (payload.get("channel") or "").strip()
        current = _read_notifications_env()
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

    # --- Multi-bot notification store (Telegram bots + Slack App bots) --------
    #
    # Additive to the single-channel notifications.env above: the SPA manages a
    # LIST of bots per platform. The GET merges the legacy env target (shown as
    # a read-only "legacy" card) with the JSON store so the operator sees every
    # destination in one list; secrets are never returned.

    def _bots_view() -> dict:
        env = _read_notifications_env()
        telegram = []
        if env.get("TELEGRAM_BOT_TOKEN"):
            telegram.append({
                "id": "legacy-telegram",
                "name": "Telegram (הגדרה קיימת)",
                "platform": "telegram",
                "chat_id": env.get("TELEGRAM_CHAT_ID", ""),
                "token_set": True,
                "legacy": True,
            })
        slack = []
        if env.get("SLACK_WEBHOOK_URL"):
            slack.append({
                "id": "legacy-slack",
                "name": "Slack Webhook (הגדרה קיימת)",
                "platform": "slack",
                "channel": "",
                "kind": "webhook",
                "token_set": True,
                "legacy": True,
            })
        masked = bots_store.masked_list()
        telegram.extend(masked["telegram"])
        slack.extend(masked["slack"])
        return {"telegram": telegram, "slack": slack}

    @app.route("/api/settings/bots", methods=["GET"])
    def api_settings_bots_get():
        """Merged, secret-free list of every notification bot. Local-only."""
        if is_remote_mode():
            return jsonify({"error": "settings are local-only"}), 403
        return jsonify(_bots_view())

    @app.route("/api/settings/bots", methods=["POST"])
    def api_settings_bots_post():
        """Add a bot. Body: {platform, name?, bot_token, chat_id|channel}."""
        if is_remote_mode():
            return jsonify({"ok": False, "error": "settings are local-only"}), 403
        payload = request.get_json(silent=True) or {}
        platform = (payload.get("platform") or "").strip()
        created, err = bots_store.add_bot(platform, payload)
        if err:
            return jsonify({"ok": False, "error": err}), 400
        return jsonify({"ok": True, "bot": created}), 201

    @app.route("/api/settings/bots/<bot_id>", methods=["DELETE"])
    def api_settings_bots_delete(bot_id):
        """Remove a bot. The synthetic legacy ids clear the env target instead."""
        if is_remote_mode():
            return jsonify({"ok": False, "error": "settings are local-only"}), 403
        if bot_id in ("legacy-telegram", "legacy-slack"):
            current = _read_notifications_env()
            merged = {
                "SLACK_WEBHOOK_URL": current.get("SLACK_WEBHOOK_URL", ""),
                "TELEGRAM_BOT_TOKEN": current.get("TELEGRAM_BOT_TOKEN", ""),
                "TELEGRAM_CHAT_ID": current.get("TELEGRAM_CHAT_ID", ""),
            }
            if bot_id == "legacy-telegram":
                merged["TELEGRAM_BOT_TOKEN"] = ""
                merged["TELEGRAM_CHAT_ID"] = ""
            else:
                merged["SLACK_WEBHOOK_URL"] = ""
            ok, err = _write_notifications_env(merged)
            if not ok:
                return jsonify({"ok": False, "error": err or "write failed"}), 500
            return jsonify({"ok": True})
        removed = bots_store.delete_bot(bot_id)
        if not removed:
            return jsonify({"ok": False, "error": "bot not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/settings/bots/<bot_id>/test", methods=["POST"])
    def api_settings_bots_test(bot_id):
        """Send a live test message through a specific bot (by id)."""
        if is_remote_mode():
            return jsonify({"ok": False, "error": "settings are local-only"}), 403
        if bot_id == "legacy-telegram":
            env = _read_notifications_env()
            ok, msg = _test_telegram(
                env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", ""),
            )
            return jsonify({"ok": ok, "message": msg})
        if bot_id == "legacy-slack":
            env = _read_notifications_env()
            ok, msg = _test_slack(env.get("SLACK_WEBHOOK_URL", ""))
            return jsonify({"ok": ok, "message": msg})
        platform, bot = bots_store.find_bot(bot_id)
        if bot is None:
            return jsonify({"ok": False, "error": "bot not found"}), 404
        if platform == "telegram":
            ok, msg = _test_telegram(bot.get("bot_token", ""), bot.get("chat_id", ""))
        else:
            ok, msg = _test_slack_app(bot.get("bot_token", ""), bot.get("channel", ""))
        return jsonify({"ok": ok, "message": msg})

    @app.route("/api/settings/thresholds", methods=["GET"])
    def api_settings_thresholds_get():
        """Current alert thresholds from the merged rules (incl. overrides)."""
        try:
            rules = load_rules()
        except Exception as e:
            return jsonify({"error": f"cannot load rules: {e}"}), 500
        return jsonify({
            "cpu": rules.get("cpu", {}),
            "memory": rules.get("memory", {}),
            "disk": rules.get("disk", {}),
            "editable": not is_remote_mode(),
        })

    @app.route("/api/settings/thresholds", methods=["PUT"])
    def api_settings_thresholds_put():
        """Persist slider changes to the state-dir override (rules.local.yaml).

        Body: partial {cpu: {...}, memory: {...}, disk: {...}} — only
        allowlisted numeric keys. The agent picks the new values up on its
        next sweep because every pipeline run calls load_rules() fresh.
        """
        if is_remote_mode():
            return jsonify({"ok": False, "error": "settings are local-only"}), 403
        payload = request.get_json(silent=True) or {}
        partial: dict = {}
        for section, keys in _THRESHOLD_KEYS.items():
            body = payload.get(section)
            if not isinstance(body, dict):
                continue
            for key, (lo, hi) in keys.items():
                if key not in body:
                    continue
                try:
                    value = float(body[key])
                except (TypeError, ValueError):
                    return jsonify({"ok": False, "error": f"{section}.{key} must be a number"}), 400
                if not (lo <= value <= hi):
                    return jsonify({"ok": False, "error": f"{section}.{key} must be between {lo} and {hi}"}), 400
                partial.setdefault(section, {})[key] = value
        if not partial:
            return jsonify({"ok": False, "error": "no editable thresholds in payload"}), 400

        # Cross-field sanity on the post-merge result (payload may set one side).
        merged_preview = load_rules()
        for section, body in partial.items():
            merged_preview.setdefault(section, {}).update(body)
        for section, pairs in _WARN_CRIT_PAIRS.items():
            for warn_key, crit_key in pairs:
                warn = merged_preview.get(section, {}).get(warn_key)
                crit = merged_preview.get(section, {}).get(crit_key)
                if warn is not None and crit is not None and float(warn) >= float(crit):
                    return jsonify({
                        "ok": False,
                        "error": f"{section}.{warn_key} ({warn}) must be below {section}.{crit_key} ({crit})",
                    }), 400

        try:
            merged = save_rules_override(partial)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except OSError as e:
            return jsonify({"ok": False, "error": f"cannot write override file: {e}"}), 500
        return jsonify({
            "ok": True,
            "cpu": merged.get("cpu", {}),
            "memory": merged.get("memory", {}),
            "disk": merged.get("disk", {}),
        })

    @app.route("/api/settings/filter_rules", methods=["GET"])
    def api_settings_filter_rules_get():
        """Read-only placeholder for UI log-pattern rules.

        The agent currently enforces threshold rules, not regex/log-pattern
        rules. Returning an explicit empty, unenforced list lets the SPA
        render the section honestly without inventing a recovery subsystem
        behind the operator's back.
        """
        return jsonify({
            "rules": [],
            "enforced": False,
            "note": "חוקי סינון (regex) אינם נאכפים עדיין ע\"י מנוע הניטור — הסוכן פועל כרגע לפי חוקי סף.",
        })

    # --- React SPA (webui) ----------------------------------------------------

    def _spa_index():
        """Serve the SPA entry (index.html). 503 until the frontend is built."""
        index = WEBUI_DIST / "index.html"
        if not index.exists():
            return (
                "<h1>React dashboard not built yet</h1>"
                "<p>Build the frontend into <code>webui/dist/</code> "
                "(<code>make webui</code> or <code>scripts/build-webui.sh</code>).</p>",
                503,
                {"Content-Type": "text/html; charset=utf-8"},
            )
        resp = send_from_directory(WEBUI_DIST, "index.html")
        # index.html must never be cached — it points at fingerprinted assets
        # that change on every build.
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    @app.route("/app/assets/<path:filename>")
    def spa_assets(filename):
        """Fingerprinted JS/CSS/media — safe to cache forever (hashed names)."""
        assets_dir = WEBUI_DIST / "assets"
        if not (assets_dir / filename).is_file():
            abort(404)
        resp = send_from_directory(assets_dir, filename)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @app.route("/app")
    @app.route("/app/")
    @app.route("/app/<path:subpath>")
    def spa_entry(subpath=""):
        """SPA entry + client-side routing catch-all (scoped to /app/*)."""
        return _spa_index()

    # --- Mount the full agent REST API (same-origin, Basic-gated) ------------

    from ipracticom_sweeper.agent_api import register_api_routes

    register_api_routes(
        app,
        auth=lambda fn: fn,
        skip=frozenset({
            "healthz",
            "api_snapshot",
            "api_notify_test",
            "slack_events",
            "slack_events_challenge",
            "_cors_preflight",
        }),
        rate_limited=False,
    )
