"""Tests for WebhookDispatcher: signs + POSTs + retries."""
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ipracticom_sweeper.webhooks.dispatcher import (
    DispatchResult,
    WebhookDispatcher,
)


class _CollectHandler(BaseHTTPRequestHandler):
    """Captures every request into a class-level list. Returns 200."""

    received: list = []
    response_status: int = 200
    response_body: bytes = b"ok"

    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler contract)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).received.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "body": body,
            }
        )
        self.send_response(type(self).response_status)
        self.send_header("Content-Length", str(len(type(self).response_body)))
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, format, *args):  # silence test noise
        pass


@pytest.fixture
def http_target():
    """Start a local HTTP server on a random port, return its URL."""
    _CollectHandler.received = []
    _CollectHandler.response_status = 200
    _CollectHandler.response_body = b"ok"
    server = HTTPServer(("127.0.0.1", 0), _CollectHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/hook"
    server.shutdown()
    server.server_close()


def test_dispatch_post_signed_body(http_target):
    d = WebhookDispatcher(url=http_target, secret="s3cret", max_attempts=1)
    event = {"type": "sweep_done", "defcon": 4, "host": "h1"}
    result = d.dispatch(event)

    assert isinstance(result, DispatchResult)
    assert result.success is True
    assert result.status_code == 200
    assert result.attempts == 1
    assert len(_CollectHandler.received) == 1

    sent = _CollectHandler.received[0]
    assert sent["path"] == "/hook"
    # Body is canonical JSON (sorted keys)
    assert json.loads(sent["body"]) == event
    # Signature header present + valid
    sig = sent["headers"].get("X-Sweeper-Signature", "")
    expected = hmac.new(b"s3cret", sent["body"], hashlib.sha256).hexdigest()
    assert hmac.compare_digest(sig, expected)
    # Timestamp header present
    assert "X-Sweeper-Timestamp" in sent["headers"]


def test_dispatch_rejects_invalid_url():
    with pytest.raises(ValueError, match="http://"):
        WebhookDispatcher(url="ftp://nope", secret="x")
    with pytest.raises(ValueError, match="secret is required"):
        WebhookDispatcher(url="http://x", secret="")


def test_dispatch_records_in_history(http_target):
    d = WebhookDispatcher(url=http_target, secret="s", max_attempts=1)
    assert d.delivered_count == 0
    d.dispatch({"a": 1})
    d.dispatch({"b": 2})
    assert d.delivered_count == 2
    assert d.last_result.success is True


def test_dispatch_client_error_4xx_no_retry(http_target):
    _CollectHandler.response_status = 404
    _CollectHandler.response_body = b"not found"
    d = WebhookDispatcher(url=http_target, secret="s", max_attempts=5)
    result = d.dispatch({"x": 1})
    assert result.success is False
    assert result.status_code == 404
    assert result.attempts == 1  # 4xx must NOT retry
    assert "client error" in (result.error or "")


def test_dispatch_429_does_retry(http_target):
    # First call returns 429, second returns 200
    statuses = iter([429, 200])
    _CollectHandler.response_status = 200  # default; we override per-request

    class FlakyHandler(_CollectHandler):
        received = []
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(next(statuses))
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *a, **k): pass

    # Replace the running server's handler for this test
    server = HTTPServer(("127.0.0.1", 0), FlakyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        d = WebhookDispatcher(
            url=f"http://127.0.0.1:{port}/hook",
            secret="s",
            max_attempts=3,
            backoff_base=0.01,  # fast for tests
        )
        result = d.dispatch({"x": 1})
        assert result.success is True
        assert result.attempts == 2
        assert result.status_code == 200
    finally:
        server.shutdown()
        server.server_close()


def test_dispatch_server_error_5xx_retries_then_fails():
    _CollectHandler.received = []
    _CollectHandler.response_status = 503
    _CollectHandler.response_body = b"down"
    server = HTTPServer(("127.0.0.1", 0), _CollectHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        d = WebhookDispatcher(
            url=f"http://127.0.0.1:{port}/hook",
            secret="s",
            max_attempts=2,
            backoff_base=0.01,
        )
        result = d.dispatch({"x": 1})
        assert result.success is False
        assert result.status_code == 503
        assert result.attempts == 2
        assert "server error" in (result.error or "")
    finally:
        server.shutdown()
        server.server_close()


def test_dispatch_result_to_dict(http_target):
    d = WebhookDispatcher(url=http_target, secret="s", max_attempts=1)
    result = d.dispatch({"k": "v"})
    d_dict = result.to_dict()
    assert d_dict["success"] is True
    assert d_dict["status_code"] == 200
    assert "duration_ms" in d_dict
    assert "attempts" in d_dict


def test_dispatch_sign_helper():
    d = WebhookDispatcher(url="http://x.invalid", secret="topsecret")
    body = b'{"hello":"world"}'
    sig = d.sign(body)
    expected = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(sig, expected)
