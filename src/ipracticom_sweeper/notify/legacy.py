"""Multi-channel notifier: Slack + Telegram.

Triggered when overall_status is warn/crit. Each channel is opt-in via env:
- SLACK_WEBHOOK_URL
- TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import structlog

from ipracticom_sweeper.audit import alert_event

logger = structlog.get_logger()


# --- Formatters --------------------------------------------------------------


def _status_icon(status: str) -> str:
    return {"ok": "✅", "warn": "⚠️", "crit": "🔴"}.get(status, "❓")


def _defcon_icon(defcon_label: str) -> str:
    return {
        "green": "✅",
        "yellow": "⚠️",
        "orange": "🟠",
        "red": "🔴",
        "black": "🚨",
    }.get(defcon_label, "❓")


def format_slack_message(snapshot_or_result: dict[str, Any]) -> dict[str, Any]:
    """Format snapshot OR PipelineResult as Slack Block Kit message."""
    is_pipeline = "defcon" in snapshot_or_result and "defcon_label" in snapshot_or_result

    if is_pipeline:
        defcon = snapshot_or_result["defcon"]
        defcon_label = snapshot_or_result["defcon_label"]
        icon = _defcon_icon(defcon_label)
        header = f"{icon} Sweeper DEFCON {defcon} ({defcon_label})"
        problems = snapshot_or_result.get("problems_found", 0)
        repairs_ok = snapshot_or_result.get("repairs_succeeded", 0)
        repairs_total = snapshot_or_result.get("repairs_attempted", 0)
        server = snapshot_or_result.get("server", "unknown")
        summary = snapshot_or_result.get("diagnosis", {}).get("summary", "")

        body = [f"*Server*: `{server}`"]
        body.append(f"*Status*: {header}")
        body.append(f"*Summary*: {summary}")
        body.append(f"*Repairs*: {repairs_ok}/{repairs_total} succeeded")

        problems_list = snapshot_or_result.get("diagnosis", {}).get("problems", [])
        if problems_list:
            body.append("")
            body.append("*Problems:*")
            for p in problems_list[:10]:
                body.append(
                    f"  • `{p.get('kind')}` ({p.get('severity')}): {p.get('detail')}"
                )

        return {
            "text": header,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": header}},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n".join(body)},
                },
            ],
        }

    overall = snapshot_or_result["overall_status"]
    icon = {"warn": ":warning:", "crit": ":rotating_light:"}.get(overall, ":white_check_mark:")

    lines = []
    _mod_icons = {"ok": ":white_check_mark:", "warn": ":warning:", "crit": ":rotating_light:"}
    for mod, data in snapshot_or_result["modules"].items():
        mod_status = data.get("status", "unknown")
        # .get() not [] — a module status the pipeline can emit but that isn't
        # in the map (e.g. "unknown"/"degraded") would otherwise KeyError here,
        # and since notify is wrapped in try/except the whole alert is dropped.
        mod_icon = _mod_icons.get(mod_status, ":grey_question:")
        lines.append(f"{mod_icon} *{mod}*: {mod_status}")

    server = snapshot_or_result.get("server", "unknown")

    return {
        "text": f"{icon} iPracticom Sweeper: {overall.upper()}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{icon} Sweeper: {overall.upper()}"}
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Server: *{server}*"}
                ]
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)}
            }
        ],
    }


def format_telegram_message(snapshot_or_result: dict[str, Any]) -> str:
    """Format snapshot OR PipelineResult as plain text for Telegram (Markdown)."""
    is_pipeline = "defcon" in snapshot_or_result and "defcon_label" in snapshot_or_result

    if is_pipeline:
        defcon = snapshot_or_result["defcon"]
        defcon_label = snapshot_or_result["defcon_label"]
        icon = _defcon_icon(defcon_label)
        server = snapshot_or_result.get("server", "unknown")
        summary = snapshot_or_result.get("diagnosis", {}).get("summary", "")
        repairs_ok = snapshot_or_result.get("repairs_succeeded", 0)
        repairs_total = snapshot_or_result.get("repairs_attempted", 0)
        needs_human = snapshot_or_result.get("needs_human", 0)

        lines = [
            f"{icon} *iPracticom Sweeper* — DEFCON {defcon} ({defcon_label})",
            f"Server: `{server}`",
            "",
            f"_{summary}_",
            f"Repairs: {repairs_ok}/{repairs_total} succeeded",
        ]
        if needs_human:
            lines.append(f"⚠️ Needs human attention: {needs_human}")

        problems_list = snapshot_or_result.get("diagnosis", {}).get("problems", [])
        if problems_list:
            lines.append("")
            lines.append("*Problems:*")
            for p in problems_list[:10]:
                sev_emoji = {"warn": "⚠️", "crit": "🔴"}.get(p.get("severity"), "•")
                lines.append(f"  {sev_emoji} `{p.get('kind')}` — {p.get('detail')}")
        return "\n".join(lines)

    overall = snapshot_or_result["overall_status"]
    icon = {"warn": "⚠️", "crit": "🔴"}.get(overall, "✅")

    lines = [
        f"{icon} *iPracticom Sweeper*: {overall.upper()}",
        f"Server: `{snapshot_or_result.get('server', 'unknown')}`",
        "",
    ]
    for mod, data in snapshot_or_result["modules"].items():
        mod_icon = _status_icon(data["status"])
        lines.append(f"  {mod_icon} {mod}: {data['status']}")
    return "\n".join(lines)


# --- Channel senders ---------------------------------------------------------


async def _send_slack(message: dict[str, Any]) -> bool:
    from ipracticom_sweeper import config as _cfg
    url = _cfg.slack_webhook_url()
    if not url:
        return False
    # v1.5.16 hardening:
    # - Use try/finally (instead of async with) so the client always closes
    #   even if the await raises mid-flight — guarantees no resource leak.
    # - The client is created fresh per call because asyncio.run() creates
    #   a new event loop each time, and an AsyncClient is bound to the loop
    #   in which it was created. Reusing a stale client across loops
    #   raises "Event loop is closed".
    client = httpx.AsyncClient(timeout=5.0)
    try:
        resp = await client.post(url, json=message)
        success = resp.status_code == 200
        alert_event("slack", {"status_code": resp.status_code}, "info" if success else "error")
        return success
    except Exception as e:
        alert_event("slack", {"error": str(e)}, "error")
        logger.error("slack_send_failed", error=str(e))
        return False
    finally:
        await client.aclose()


async def _send_telegram_to(
    text: str, token: str | None, chat_id: str | None, markdown: bool = True
) -> bool:
    """Send ``text`` to an EXPLICIT Telegram bot/chat target.

    Extracted from :func:`_send_telegram` so the multi-bot fan-out (see
    :func:`_store_fanout_tasks`) reuses the exact same delivery logic —
    including the Markdown→plain-text retry — for every configured bot, not
    just the single legacy env target.
    """
    if not (token and chat_id):
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": False,
    }
    if markdown:
        payload["parse_mode"] = "Markdown"
    # v1.5.16 hardening: try/finally (see _send_slack above for rationale).
    client = httpx.AsyncClient(timeout=5.0)
    try:
        resp = await client.post(url, json=payload)
        # Dynamic content (summaries, hostnames, error details) can contain
        # stray Markdown metacharacters (_ * ` [) that make Telegram reject the
        # message with 400 "can't parse entities" — which would silently drop a
        # real alert. If Markdown parsing fails, resend as plain text so the
        # alert always gets through, just without formatting. Callers that pass
        # markdown=False already sent plain text, so there is nothing to retry.
        if markdown and resp.status_code == 400:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "disable_notification": False,
            })
        success = resp.status_code == 200
        alert_event("telegram", {"status_code": resp.status_code}, "info" if success else "error")
        return success
    except Exception as e:
        alert_event("telegram", {"error": str(e)}, "error")
        logger.error("telegram_send_failed", error=str(e))
        return False
    finally:
        await client.aclose()


async def _send_telegram(text: str, markdown: bool = True) -> bool:
    """Send to the single legacy env Telegram target (SLACK/TELEGRAM env vars).

    Thin wrapper over :func:`_send_telegram_to`; kept as-is so existing callers
    and the ``notify._send_telegram`` re-export (patched by tests) are unchanged.
    """
    from ipracticom_sweeper import config as _cfg
    return await _send_telegram_to(
        text, _cfg.telegram_bot_token(), _cfg.telegram_chat_id(), markdown=markdown
    )


async def _send_slack_app(message: dict[str, Any], bot_token: str, channel: str) -> bool:
    """Send a Block Kit ``message`` via the Slack Web API (chat.postMessage).

    This is the Slack *App* path (bot token ``xoxb-…`` + channel), as opposed to
    the legacy incoming-webhook path in :func:`_send_slack`. The endpoint host is
    the fixed ``slack.com`` so there is no SSRF surface. Slack returns HTTP 200
    with a JSON body whose ``ok`` field is the real success flag, so both are
    checked. Never raises — a broken bot must not take down the sweep.
    """
    if not (bot_token and channel):
        return False
    payload = {"channel": channel, **message}
    client = httpx.AsyncClient(timeout=5.0)
    try:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload,
        )
        ok = False
        if resp.status_code == 200:
            try:
                ok = bool(resp.json().get("ok"))
            except Exception:
                ok = False
        alert_event("slack_app", {"status_code": resp.status_code}, "info" if ok else "error")
        return ok
    except Exception as e:
        alert_event("slack_app", {"error": str(e)}, "error")
        logger.error("slack_app_send_failed", error=str(e))
        return False
    finally:
        await client.aclose()


async def send_admin_alert(text: str) -> dict[str, bool]:
    """Fire a one-off plain-text alert to every configured channel.

    For out-of-band callers (e.g. the systemd watchdog's restart-storm alert)
    that have an urgent message but no pipeline snapshot. Returns
    ``{channel: sent?}`` and never raises — a broken channel must not take
    down the caller. Sent as plain text (no Markdown) so arbitrary content
    can't trip Telegram's entity parser.
    """
    results: dict[str, bool] = {}
    try:
        # Plain text from the start: this path carries arbitrary operator/agent
        # content, so never let Telegram's Markdown entity parser reject it.
        results["telegram"] = await _send_telegram(text, markdown=False)
    except Exception as e:  # pragma: no cover - defensive
        logger.error("admin_alert_telegram_failed", error=str(e))
        results["telegram"] = False
    try:
        results["slack"] = await _send_slack({"text": text})
    except Exception as e:  # pragma: no cover - defensive
        logger.error("admin_alert_slack_failed", error=str(e))
        results["slack"] = False

    # Multi-bot fan-out (plain text). No-op when the store is empty.
    from ipracticom_sweeper.notify import store as _store
    for b in _store.telegram_bots():
        label = f"telegram:{b.get('id', '?')}"
        try:
            results[label] = await _send_telegram_to(
                text, b.get("bot_token"), b.get("chat_id"), markdown=False
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.error("admin_alert_telegram_failed", error=str(e))
            results[label] = False
    for b in _store.slack_bots():
        label = f"slack:{b.get('id', '?')}"
        try:
            results[label] = await _send_slack_app(
                {"text": text}, b.get("bot_token", ""), b.get("channel", "")
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.error("admin_alert_slack_failed", error=str(e))
            results[label] = False
    return results


# --- Top-level ---------------------------------------------------------------


def _store_fanout_tasks(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    """Build (label, coroutine) send tasks for every bot in the multi-bot store.

    Additive to the legacy env channels: returns ``[]`` when the store is empty
    (the default, and the state in every test that doesn't opt in), so callers
    behave exactly as before when no extra bots are configured. Message
    formatting is shared with the legacy path. The store's entries are disjoint
    from the legacy env target by construction, so there is no double-send.
    """
    from ipracticom_sweeper.notify import store as _store

    tasks: list[tuple[str, Any]] = []
    tg = _store.telegram_bots()
    if tg:
        tmsg = format_telegram_message(payload)
        for b in tg:
            tasks.append((
                f"telegram:{b.get('id', '?')}",
                _send_telegram_to(tmsg, b.get("bot_token"), b.get("chat_id")),
            ))
    sl = _store.slack_bots()
    if sl:
        smsg = format_slack_message(payload)
        for b in sl:
            tasks.append((
                f"slack:{b.get('id', '?')}",
                _send_slack_app(smsg, b.get("bot_token", ""), b.get("channel", "")),
            ))
    return tasks


async def notify(snapshot: dict[str, Any], force: bool = False) -> dict[str, bool]:
    """Send notification to all configured channels.

    force=True: send even if status is ok (for periodic "still alive" pings).
    Returns dict of {channel: success}.
    """
    overall = snapshot["overall_status"]
    if not force and overall == "ok":
        return {}

    from ipracticom_sweeper import config as _cfg
    from ipracticom_sweeper.notify import store as _store
    if not (_cfg.notifications_enabled() or _store.has_any_bot()):
        logger.warning("notifications_enabled_but_no_channels")
        return {}

    results = {}
    tasks = []

    if _cfg.slack_webhook_url():
        msg = format_slack_message(snapshot)
        tasks.append(("slack", _send_slack(msg)))

    if _cfg.telegram_bot_token() and _cfg.telegram_chat_id():
        msg = format_telegram_message(snapshot)
        tasks.append(("telegram", _send_telegram(msg)))

    tasks.extend(_store_fanout_tasks(snapshot))

    for channel, coro in tasks:
        try:
            results[channel] = await coro
        except Exception as e:
            results[channel] = False
            logger.error(f"{channel}_notify_failed", error=str(e))

    return results


async def notify_pipeline_result(result_dict: dict[str, Any], force: bool = False) -> dict[str, bool]:
    """Send notification for a PipelineResult (newer shape with DEFCON).

    Silent-by-default: the send/suppress decision is delegated to
    :mod:`ipracticom_sweeper.notify.suppression`, which stays quiet for green
    runs, dedupes a persistent problem "until resolved", and throttles flapping.
    ``force=True`` (manual test endpoints) bypasses the gate. Skips entirely if
    no channels are configured. See docs/silent-agent.md.
    """
    from ipracticom_sweeper.notify.suppression import mark_notified, should_notify

    decision = should_notify(result_dict, force=force)
    if not decision.send:
        logger.debug("notify_suppressed", reason=decision.reason)
        return {}

    from ipracticom_sweeper import config as _cfg
    from ipracticom_sweeper.notify import store as _store
    if not (_cfg.notifications_enabled() or _store.has_any_bot()):
        logger.debug("no_notification_channels_configured")
        return {}

    results: dict[str, bool] = {}
    tasks: list[tuple[str, Any]] = []
    if _cfg.slack_webhook_url():
        tasks.append(("slack", _send_slack(format_slack_message(result_dict))))
    if _cfg.telegram_bot_token() and _cfg.telegram_chat_id():
        tasks.append(("telegram", _send_telegram(format_telegram_message(result_dict))))
    tasks.extend(_store_fanout_tasks(result_dict))

    for channel, coro in tasks:
        try:
            results[channel] = await coro
        except Exception as e:
            results[channel] = False
            logger.error(f"{channel}_notify_failed", error=str(e))

    # Record the send only after a channel actually confirmed delivery, so a
    # dropped alert is retried next run rather than silently marked "seen".
    # force=True is a manual test and must not perturb the dedup state.
    if not force and any(results.values()):
        mark_notified(result_dict)
    return results


if __name__ == "__main__":
    import sys
    snapshot = json.loads(sys.stdin.read())
    result = asyncio.run(notify(snapshot))
    print(json.dumps(result))
