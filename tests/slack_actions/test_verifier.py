"""Tests for Slack signature verification."""
import hashlib
import hmac
import time

from ipracticom_sweeper.slack_actions.verifier import (
    MAX_TIMESTAMP_AGE_SECONDS,
    VerificationResult,
    verify_slack_signature,
)


def _sign(body: bytes, timestamp: str, secret: str) -> str:
    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    secret = "test_secret_xyz"
    ts = "1700000000"
    body = b'payload=%7B%22action%22%3A%22ack%22%7D'
    sig = _sign(body, ts, secret)
    result = verify_slack_signature(body, ts, sig, secret, now=1700000000.0)
    assert result.valid is True
    assert result.reason is None


def test_missing_secret_rejected():
    result = verify_slack_signature(b"body", "1700000000", "v0=abc", "")
    assert result.valid is False
    assert "signing_secret" in (result.reason or "")


def test_missing_timestamp_rejected():
    result = verify_slack_signature(b"body", None, "v0=abc", "secret")
    assert result.valid is False
    assert "missing" in (result.reason or "")


def test_missing_signature_rejected():
    result = verify_slack_signature(b"body", "1700000000", None, "secret")
    assert result.valid is False
    assert "missing" in (result.reason or "")


def test_non_integer_timestamp_rejected():
    result = verify_slack_signature(b"body", "not_a_number", "v0=abc", "secret")
    assert result.valid is False
    assert "integer" in (result.reason or "")


def test_old_timestamp_rejected_for_replay():
    secret = "s"
    ts = "1700000000"
    body = b"body"
    sig = _sign(body, ts, secret)
    # 6 minutes later
    now = 1700000000.0 + 360
    result = verify_slack_signature(body, ts, sig, secret, now=now)
    assert result.valid is False
    assert "replay" in (result.reason or "")


def test_future_timestamp_rejected():
    secret = "s"
    ts = "1700000000"
    body = b"body"
    sig = _sign(body, ts, secret)
    # 6 minutes in the future
    now = 1700000000.0 - 360
    result = verify_slack_signature(body, ts, sig, secret, now=now)
    assert result.valid is False
    assert "replay" in (result.reason or "")


def test_wrong_signature_rejected():
    result = verify_slack_signature(
        b"body", "1700000000", "v0=deadbeef", "secret", now=1700000000.0
    )
    assert result.valid is False
    assert "mismatch" in (result.reason or "")


def test_tampered_body_rejected():
    secret = "s"
    ts = "1700000000"
    original = b"original_body"
    sig = _sign(original, ts, secret)
    # Attacker changes the body but keeps the signature
    tampered = b"tampered_body"
    result = verify_slack_signature(
        tampered, ts, sig, secret, now=1700000000.0
    )
    assert result.valid is False


def test_max_age_constant_is_300():
    # 5 minutes per Slack spec
    assert MAX_TIMESTAMP_AGE_SECONDS == 300
