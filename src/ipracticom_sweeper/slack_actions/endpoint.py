"""Slack interactive endpoint: parse payload, verify signature, dispatch action.

Slack sends button clicks as application/x-www-form-urlencoded with a single
field `payload` containing a JSON string. The JSON has this shape:
    {
      "type": "block_actions",
      "user": {"id": "U123", "username": "daniel"},
      "actions": [{"action_id": "acknowledge", "value": "<fingerprint>"}],
      ...
    }

The action_id maps to SlackActionType. The value carries the alert fingerprint.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

from .handler import SlackAction, SlackActionHandler, SlackActionType
from .verifier import verify_slack_signature
from .commands import CommandResult, SlackCommandHandler


# action_id -> SlackActionType mapping
ACTION_ID_MAP = {
    "acknowledge": SlackActionType.ACKNOWLEDGE,
    "silence": SlackActionType.SILENCE,
    "run_repair": SlackActionType.RUN_REPAIR,
    "approve": SlackActionType.APPROVE,
    "reject": SlackActionType.REJECT,
}


@dataclass
class EndpointResponse:
    status_code: int
    body: dict[str, Any]


class SlackEndpoint:
    """Wires signature verification + payload parsing + handler dispatch."""

    def __init__(self, handler: SlackActionHandler | None = None):
        self.handler = handler or SlackActionHandler()

    def parse_form_body(self, body: bytes) -> dict[str, Any]:
        """Parse Slack's form-urlencoded payload into a dict.

        Slack sends: payload=<urlencoded json>
        """
        if not body:
            raise ValueError("empty body")
        decoded = body.decode("utf-8")
        parsed = parse_qs(decoded)
        if "payload" not in parsed:
            raise ValueError("missing 'payload' field")
        return json.loads(parsed["payload"][0])

    def payload_to_action(
        self, payload: dict[str, Any], timestamp: float
    ) -> SlackAction:
        """Convert Slack's block_actions payload into a SlackAction."""
        if payload.get("type") != "block_actions":
            raise ValueError(f"unsupported payload type: {payload.get('type')!r}")
        actions = payload.get("actions") or []
        if not actions:
            raise ValueError("no actions in payload")
        first = actions[0]
        action_id = first.get("action_id")
        if action_id not in ACTION_ID_MAP:
            raise ValueError(f"unknown action_id: {action_id!r}")
        fingerprint = first.get("value")
        if not fingerprint:
            raise ValueError("missing fingerprint (action value)")
        user_obj = payload.get("user") or {}
        user = user_obj.get("username") or user_obj.get("id") or "unknown"
        return SlackAction(
            action_type=ACTION_ID_MAP[action_id],
            fingerprint=fingerprint,
            user=user,
            timestamp=timestamp,
        )

    def handle_request(
        self,
        body: bytes,
        timestamp_header: str | None,
        signature_header: str | None,
        signing_secret: str,
        now: float | None = None,
        command_handler: "SlackCommandHandler | None" = None,
    ) -> EndpointResponse:
        """Process a Slack HTTP request. Returns a response (status + body).

        Handles two kinds of payloads:
          1. Slash command (form-urlencoded: command=/defcon&text=...) → reply JSON
          2. Block actions (form-urlencoded: payload=<json>) → ack JSON
          3. Event callback (JSON: {type: event_callback, event: {type: message, text: ...}})
             → 200 OK immediately; reply posted via chat.postMessage (TODO)
        """
        # 1. verify signature
        verification = verify_slack_signature(
            body, timestamp_header, signature_header, signing_secret, now=now
        )
        if not verification.valid:
            return EndpointResponse(
                status_code=401,
                body={"error": "invalid_signature", "reason": verification.reason},
            )

        # 2. peek at content-type / shape to decide how to parse
        #    Slack slash commands: form-urlencoded with `command` field
        #    Slack block actions: form-urlencoded with `payload` field
        #    Slack events API: JSON with `type` field
        if body.startswith(b"{"):
            return self._handle_event_callback(body, now=now, command_handler=command_handler)

        # form-urlencoded — parse defensively (slash commands don't have 'payload')
        try:
            from urllib.parse import parse_qs
            form_raw = parse_qs(body.decode("utf-8"))
            # parse_qs returns lists; flatten to single values (first wins)
            form = {k: v[0] if v else "" for k, v in form_raw.items()}
        except (UnicodeDecodeError, ValueError) as e:
            return EndpointResponse(
                status_code=400,
                body={"error": "bad_form", "reason": str(e)},
            )

        # Slash command: {command, text, user_id, response_url, ...}
        if "command" in form and "payload" not in form:
            return self._handle_slash_command(form, command_handler=command_handler)

        # Block actions: {payload: <json>}
        if "payload" not in form:
            return EndpointResponse(
                status_code=400,
                body={"error": "bad_payload", "reason": "missing 'payload' field"},
            )
        try:
            payload = json.loads(form["payload"])
        except json.JSONDecodeError as e:
            return EndpointResponse(
                status_code=400,
                body={"error": "bad_payload_json", "reason": str(e)},
            )

        # 3. build action
        try:
            action = self.payload_to_action(payload, timestamp=now or 0.0)
        except ValueError as e:
            return EndpointResponse(
                status_code=400, body={"error": "bad_action", "reason": str(e)}
            )

        # 4. dispatch
        result = self.handler.handle(action)
        return EndpointResponse(status_code=200, body=result)

    # --- Slash command & event_callback handling --------------------------

    def _handle_slash_command(
        self, form: dict[str, Any], command_handler: "SlackCommandHandler | None"
    ) -> EndpointResponse:
        """Handle a slash command POST. Returns the immediate Slack response JSON."""
        cmd = form.get("command", "")
        text = form.get("text", "") or ""
        user = form.get("user_name") or form.get("user_id") or "unknown"

        if command_handler is None:
            return EndpointResponse(
                status_code=200,
                body={"response_type": "ephemeral",
                      "text": f"פקודה `{cmd}` התקבלה אבל command handler לא מוגדר."},
            )

        # Synthesize the command text (slash commands already include the /)
        full_text = cmd + (" " + text if text else "")
        result = command_handler.handle_message(text=full_text, user=user)
        return EndpointResponse(status_code=200, body=result.to_slack_response())

    def _handle_event_callback(
        self, body: bytes, now: float | None,
        command_handler: "SlackCommandHandler | None"
    ) -> EndpointResponse:
        """Handle an Events API callback (event_callback or url_verification)."""
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return EndpointResponse(status_code=400, body={"error": "bad_json", "reason": str(e)})

        ev_type = data.get("type")

        # URL verification handshake — same as the GET endpoint, but POSTed
        if ev_type == "url_verification":
            return EndpointResponse(
                status_code=200,
                body={"challenge": data.get("challenge", "")},
            )

        if ev_type != "event_callback":
            return EndpointResponse(status_code=200, body={"ok": True, "ignored": ev_type})

        event = data.get("event") or {}
        # Only handle message events for now
        if event.get("type") not in ("message", "app_mention"):
            return EndpointResponse(status_code=200, body={"ok": True, "ignored": event.get("type")})

        # Ignore bot messages (avoid loops)
        if event.get("bot_id") or event.get("subtype"):
            return EndpointResponse(status_code=200, body={"ok": True, "ignored": "bot/subtype"})

        text = event.get("text", "")
        user_obj = event.get("user") or {}
        user = user_obj if isinstance(user_obj, str) else (
            user_obj.get("username") or user_obj.get("id") or "unknown"
        )

        if command_handler is None:
            return EndpointResponse(
                status_code=200,
                body={"ok": True, "note": "command handler not configured"},
            )

        result = command_handler.handle_message(text=text, user=user)

        # For event_callback we must return 200 immediately; the actual reply
        # would be posted via chat.postMessage. We attach the reply text in the
        # body for visibility during dev/testing.
        return EndpointResponse(
            status_code=200,
            body={"ok": True, "reply": result.text, "response_type": result.response_type},
        )
