"""Tests for SlackEndpoint: signed request → parsed payload → handler dispatch."""
import hashlib
import hmac
import json
import time
from urllib.parse import quote

from ipracticom_sweeper.slack_actions import (
    EndpointResponse,
    SlackActionType,
    SlackEndpoint,
    SlackActionHandler,
)


SECRET = "slack_signing_secret_xyz"


def _sign(body: bytes, timestamp: str) -> str:
    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    return "v0=" + hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()


def _build_form_body(payload_dict: dict) -> bytes:
    return ("payload=" + quote(json.dumps(payload_dict))).encode("utf-8")


def _ack_payload(fingerprint: str = "abc123", user: str = "daniel") -> dict:
    return {
        "type": "block_actions",
        "user": {"id": "U123", "username": user},
        "actions": [{"action_id": "acknowledge", "value": fingerprint}],
    }


def test_endpoint_rejects_unsigned_request():
    ep = SlackEndpoint()
    body = _build_form_body(_ack_payload())
    resp = ep.handle_request(body, "1700000000", "v0=garbage", SECRET, now=1700000000.0)
    assert resp.status_code == 401
    assert resp.body["error"] == "invalid_signature"


def test_endpoint_rejects_missing_signature():
    ep = SlackEndpoint()
    body = _build_form_body(_ack_payload())
    resp = ep.handle_request(body, "1700000000", None, SECRET, now=1700000000.0)
    assert resp.status_code == 401


def test_endpoint_rejects_bad_payload_missing_field():
    ep = SlackEndpoint()
    body = b"not_payload_form_data"
    ts = "1700000000"
    sig = _sign(body, ts)
    resp = ep.handle_request(body, ts, sig, SECRET, now=1700000000.0)
    assert resp.status_code == 400
    assert resp.body["error"] == "bad_payload"


def test_endpoint_rejects_bad_payload_wrong_type():
    ep = SlackEndpoint()
    payload = {"type": "view_submission", "user": {"username": "x"}, "actions": []}
    body = _build_form_body(payload)
    ts = "1700000000"
    sig = _sign(body, ts)
    resp = ep.handle_request(body, ts, sig, SECRET, now=1700000000.0)
    assert resp.status_code == 400
    assert resp.body["error"] == "bad_action"


def test_endpoint_rejects_unknown_action_id():
    ep = SlackEndpoint()
    payload = _ack_payload()
    payload["actions"][0]["action_id"] = "delete_everything"
    body = _build_form_body(payload)
    ts = "1700000000"
    sig = _sign(body, ts)
    resp = ep.handle_request(body, ts, sig, SECRET, now=1700000000.0)
    assert resp.status_code == 400


def test_endpoint_rejects_missing_fingerprint():
    ep = SlackEndpoint()
    payload = _ack_payload()
    payload["actions"][0]["value"] = ""
    body = _build_form_body(payload)
    ts = "1700000000"
    sig = _sign(body, ts)
    resp = ep.handle_request(body, ts, sig, SECRET, now=1700000000.0)
    assert resp.status_code == 400
    assert "fingerprint" in resp.body["reason"]


def test_endpoint_handles_acknowledge():
    handler = SlackActionHandler()
    ep = SlackEndpoint(handler=handler)
    payload = _ack_payload(fingerprint="h1.cpu.high", user="daniel")
    body = _build_form_body(payload)
    ts = "1700000000"
    sig = _sign(body, ts)
    resp = ep.handle_request(body, ts, sig, SECRET, now=1700000000.0)
    assert resp.status_code == 200
    assert resp.body["status"] == "acknowledged"
    assert resp.body["fingerprint"] == "h1.cpu.high"
    assert handler.is_acked("h1.cpu.high") is True
    assert handler.action_count() == 1


def test_endpoint_handles_silence():
    handler = SlackActionHandler()
    ep = SlackEndpoint(handler=handler)
    payload = _ack_payload(fingerprint="h1.disk.full")
    payload["actions"][0]["action_id"] = "silence"
    body = _build_form_body(payload)
    ts = "1700000000"
    sig = _sign(body, ts)
    resp = ep.handle_request(body, ts, sig, SECRET, now=1700000000.0)
    assert resp.status_code == 200
    assert resp.body["status"] == "silenced"
    assert handler.is_silenced("h1.disk.full", now=1700000000.0) is True


def test_endpoint_handles_run_repair():
    handler = SlackActionHandler()
    ep = SlackEndpoint(handler=handler)
    payload = _ack_payload(fingerprint="h1.zombie")
    payload["actions"][0]["action_id"] = "run_repair"
    body = _build_form_body(payload)
    ts = "1700000000"
    sig = _sign(body, ts)
    resp = ep.handle_request(body, ts, sig, SECRET, now=1700000000.0)
    assert resp.status_code == 200
    assert resp.body["status"] == "repair_triggered"


def test_endpoint_rejects_tampered_body():
    ep = SlackEndpoint()
    payload = _ack_payload()
    body = _build_form_body(payload)
    ts = "1700000000"
    sig = _sign(body, ts)
    # Attacker changes fingerprint but keeps signature
    payload["actions"][0]["value"] = "different.fp"
    tampered = _build_form_body(payload)
    resp = ep.handle_request(tampered, ts, sig, SECRET, now=1700000000.0)
    assert resp.status_code == 401


def test_endpoint_rejects_old_timestamp_replay():
    ep = SlackEndpoint()
    body = _build_form_body(_ack_payload())
    ts = "1700000000"
    sig = _sign(body, ts)
    # 6 minutes later
    resp = ep.handle_request(body, ts, sig, SECRET, now=1700000000.0 + 360)
    assert resp.status_code == 401
    assert "replay" in resp.body["reason"]


def test_endpoint_passes_user_from_payload():
    handler = SlackActionHandler()
    ep = SlackEndpoint(handler=handler)
    payload = _ack_payload(user="alice")
    body = _build_form_body(payload)
    ts = "1700000000"
    sig = _sign(body, ts)
    resp = ep.handle_request(body, ts, sig, SECRET, now=1700000000.0)
    assert resp.status_code == 200
    # handler should have logged the action with username "alice"
    last = handler._action_log[-1]
    assert last.user == "alice"


def test_endpoint_passes_action_type():
    handler = SlackActionHandler()
    ep = SlackEndpoint(handler=handler)
    payload = _ack_payload()
    payload["actions"][0]["action_id"] = "run_repair"
    body = _build_form_body(payload)
    ts = "1700000000"
    sig = _sign(body, ts)
    ep.handle_request(body, ts, sig, SECRET, now=1700000000.0)
    assert handler._action_log[-1].action_type == SlackActionType.RUN_REPAIR
