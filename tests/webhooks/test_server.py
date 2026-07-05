"""Tests for webhook server."""
import hmac
import hashlib
from ipracticom_sweeper.webhooks import WebhookEvent, WebhookVerifier, WebhookHandler


def test_verify_github_valid():
    body = b'{"action":"deploy"}'
    secret = "test_secret"
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert WebhookVerifier.verify_github(body, sig, secret) is True


def test_verify_github_invalid():
    body = b'{"action":"deploy"}'
    assert WebhookVerifier.verify_github(body, "sha256=invalid", "secret") is False


def test_verify_github_missing_prefix():
    body = b'{"action":"deploy"}'
    assert WebhookVerifier.verify_github(body, "invalid", "secret") is False


def test_verify_generic_valid():
    body = b'data'
    secret = "s"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert WebhookVerifier.verify_generic(body, sig, secret) is True


def test_handler_deploy_suppresses():
    h = WebhookHandler(suppress_during_deploy=True)
    event = WebhookEvent(source="github", event_type="deploy", payload={}, received_at=0)
    result = h.handle(event)
    assert result["status"] == "accepted"
    assert h.is_suppressed() is True


def test_handler_non_deploy_does_not_suppress():
    h = WebhookHandler(suppress_during_deploy=True)
    event = WebhookEvent(source="github", event_type="push", payload={}, received_at=0)
    h.handle(event)
    assert h.is_suppressed() is False


def test_handler_event_count():
    h = WebhookHandler()
    assert h.event_count() == 0
    h.handle(WebhookEvent("github", "push", {}, 0))
    h.handle(WebhookEvent("github", "push", {}, 0))
    assert h.event_count() == 2
