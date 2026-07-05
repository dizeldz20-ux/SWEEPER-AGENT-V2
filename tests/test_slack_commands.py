"""Tests for the Slack command handler and endpoint dispatch."""
from __future__ import annotations

import hmac
import hashlib
import time

import pytest

from ipracticom_sweeper.config import add_connector, load_connectors
from ipracticom_sweeper.config.connectors import Connector
from ipracticom_sweeper.slack_actions import (
    CommandResult,
    SlackCommandHandler,
    SlackEndpoint,
)
from ipracticom_sweeper.slack_actions.commands import SlackCommandHandler as DirectHandler


# --- Command handler: parsing -------------------------------------------

def test_parses_slash_prefix():
    h = SlackCommandHandler()
    r = h.handle_message("/help")
    assert r.ok
    assert "פקודות זמינות" in r.text


def test_parses_without_slash_prefix():
    h = SlackCommandHandler()
    r = h.handle_message("help")
    assert r.ok
    assert "פקודות זמינות" in r.text


def test_unknown_command_returns_help_hint():
    h = SlackCommandHandler()
    r = h.handle_message("/foobar")
    assert not r.ok
    assert "לא מוכרת" in r.text
    assert "/help" in r.text


def test_empty_message_returns_invalid():
    h = SlackCommandHandler()
    r = h.handle_message("")
    assert not r.ok
    r2 = h.handle_message("   ")
    assert not r2.ok


def test_command_is_case_insensitive():
    h = SlackCommandHandler()
    assert h.handle_message("/HELP").ok
    assert h.handle_message("/Help").ok


def test_to_slack_response_format():
    r = CommandResult(text="hello", response_type="in_channel")
    assert r.to_slack_response() == {"response_type": "in_channel", "text": "hello"}


# --- Command handler: /defcon ------------------------------------------

def test_defcon_with_no_connectors_shows_empty_hint():
    h = SlackCommandHandler()
    r = h.handle_message("/defcon")
    assert "לא הוגדרו connectors" in r.text


def test_defcon_with_connectors_lists_them(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    add_connector(Connector(name="web1", instance_id="i-111"))
    add_connector(Connector(name="web2", instance_id="i-222"))

    h = SlackCommandHandler()
    r = h.handle_message("/defcon")
    assert "צי: 2 שרתים" in r.text
    assert "web1" in r.text
    assert "web2" in r.text
    # Hosts without data should show as critical (DEFCON 1)
    assert "DEFCON 1" in r.text


def test_defcon_with_healthy_collector_data(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    add_connector(Connector(name="web1", instance_id="i-111"))
    from ipracticom_sweeper.fleet.collector import write_snapshot
    write_snapshot("web1", {
        "available": True,
        "data": {
            "load": {"5m": 0.3},
            "memory": {"used_percent": 20},
            "disk": {"used_percent": 30},
            "failed_units": [],
        },
    })
    h = SlackCommandHandler()
    r = h.handle_message("/defcon")
    assert "DEFCON 5" in r.text or "🟢" in r.text


# --- Command handler: /health ------------------------------------------

def test_health_with_unknown_host_returns_error(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    h = SlackCommandHandler()
    r = h.handle_message("/health nonexistent")
    assert "לא נמצא" in r.text


def test_health_with_host_returns_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    add_connector(Connector(name="web1", instance_id="i-111", region="il-central-1"))
    from ipracticom_sweeper.fleet.collector import write_snapshot
    write_snapshot("web1", {
        "available": True,
        "data": {
            "load": {"5m": 0.5},
            "memory": {"used_percent": 30, "total_kb": 8 * 1024 * 1024},
            "disk": {"used_percent": 40},
            "uptime_seconds": 86400,
            "kernel": "5.15.0",
            "failed_units": [],
        },
    })
    h = SlackCommandHandler()
    r = h.handle_message("/health web1")
    assert "Load (5m): 0.50" in r.text
    assert "Memory: 30.0%" in r.text
    assert "Disk: 40.0%" in r.text
    assert "il-central-1" in r.text


def test_health_with_no_arg_lists_all(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    add_connector(Connector(name="web1", instance_id="i-111"))
    add_connector(Connector(name="web2", instance_id="i-222"))
    h = SlackCommandHandler()
    r = h.handle_message("/health")
    assert "web1" in r.text and "web2" in r.text
    # Hosts without data should be marked red
    assert "אין נתונים" in r.text


# --- Command handler: /connectors ---------------------------------------

def test_connectors_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    h = SlackCommandHandler()
    r = h.handle_message("/connectors")
    assert "לא הוגדרו connectors" in r.text


def test_connectors_lists_with_status(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    add_connector(Connector(name="web1", instance_id="i-111", region="il-central-1"))
    h = SlackCommandHandler()
    r = h.handle_message("/connectors")
    assert "Connectors (1)" in r.text
    assert "i-111" in r.text
    assert "il-central-1" in r.text


# --- Command handler: /approve -----------------------------------------

def test_approve_without_arg_returns_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    h = SlackCommandHandler()
    r = h.handle_message("/approve")
    assert "שימוש" in r.text


def test_approve_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    h = SlackCommandHandler()
    r = h.handle_message("/approve nonexistent")
    assert "לא נמצא" in r.text


# --- Command handler: /help --------------------------------------------

def test_help_lists_all_commands():
    h = SlackCommandHandler()
    r = h.handle_message("/help")
    for cmd in ("/defcon", "/health", "/approve", "/run", "/connectors", "/help"):
        assert cmd in r.text, f"missing {cmd} in help text"


# --- Endpoint dispatch: slash command ----------------------------------

def _sign(secret: str, body: bytes, ts: str) -> str:
    """Compute Slack signature header value."""
    base = f"v0:{ts}:{body.decode('utf-8')}"
    digest = hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return f"v0={digest}"


def test_endpoint_dispatches_slash_command(tmp_path, monkeypatch):
    """A signed slash-command POST should return the command response."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    secret = "test_signing_secret_abc123"
    ts = str(int(time.time()))
    body = b"command=%2Fdefcon&text=&user_name=daniel"
    sig = _sign(secret, body, ts)

    endpoint = SlackEndpoint()
    handler = SlackCommandHandler()
    resp = endpoint.handle_request(
        body=body,
        timestamp_header=ts,
        signature_header=sig,
        signing_secret=secret,
        command_handler=handler,
    )
    assert resp.status_code == 200
    assert resp.body["response_type"] == "ephemeral"
    assert "DEFCON" in resp.body["text"]


def test_endpoint_dispatches_event_callback_message(tmp_path, monkeypatch):
    """A signed event_callback POST should reply with the command output."""
    import json as _json
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    secret = "test_signing_secret_abc123"
    ts = str(int(time.time()))
    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "text": "/help",
            "user": {"id": "U123", "username": "daniel"},
            "channel": "C123",
        },
    }
    body = _json.dumps(payload).encode("utf-8")
    sig = _sign(secret, body, ts)

    endpoint = SlackEndpoint()
    handler = SlackCommandHandler()
    resp = endpoint.handle_request(
        body=body,
        timestamp_header=ts,
        signature_header=sig,
        signing_secret=secret,
        command_handler=handler,
    )
    assert resp.status_code == 200
    assert resp.body["ok"] is True
    assert "פקודות זמינות" in resp.body["reply"]


def test_endpoint_handles_url_verification_challenge(tmp_path, monkeypatch):
    """Slack sends url_verification once during app setup; must echo challenge."""
    import json as _json
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    secret = "test_signing_secret_abc123"
    ts = str(int(time.time()))
    payload = {"type": "url_verification", "challenge": "test_challenge_xyz"}
    body = _json.dumps(payload).encode("utf-8")
    sig = _sign(secret, body, ts)

    endpoint = SlackEndpoint()
    resp = endpoint.handle_request(
        body=body, timestamp_header=ts, signature_header=sig, signing_secret=secret,
        command_handler=SlackCommandHandler(),
    )
    assert resp.status_code == 200
    assert resp.body["challenge"] == "test_challenge_xyz"


def test_endpoint_rejects_bad_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    body = b"command=%2Fdefcon"
    endpoint = SlackEndpoint()
    resp = endpoint.handle_request(
        body=body, timestamp_header=str(int(time.time())),
        signature_header="v0=deadbeef", signing_secret="real_secret",
        command_handler=SlackCommandHandler(),
    )
    assert resp.status_code == 401
    assert resp.body["error"] == "invalid_signature"


def test_endpoint_ignores_bot_messages(tmp_path, monkeypatch):
    """A bot's own message shouldn't trigger another reply (loop protection)."""
    import json as _json
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    secret = "test_signing_secret_abc123"
    ts = str(int(time.time()))
    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "text": "/help",
            "user": {"id": "U999"},
            "bot_id": "B123",  # <-- marks this as a bot message
        },
    }
    body = _json.dumps(payload).encode("utf-8")
    sig = _sign(secret, body, ts)

    endpoint = SlackEndpoint()
    resp = endpoint.handle_request(
        body=body, timestamp_header=ts, signature_header=sig, signing_secret=secret,
        command_handler=SlackCommandHandler(),
    )
    assert resp.status_code == 200
    assert resp.body["ignored"] == "bot/subtype"