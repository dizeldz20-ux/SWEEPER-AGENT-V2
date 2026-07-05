"""Tests for the Telegram bot's agent_api client."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from ipracticom_sweeper.telegram_bot.services.agent_client import (
    AgentAPIError,
    AgentClient,
)


class _MockTransport(httpx.AsyncBaseTransport):
    """Capture requests, return canned responses."""

    def __init__(self, responses: list[tuple[int, Any]]):
        self._responses = list(responses)
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if not self._responses:
            return httpx.Response(500, json={"error": "no more canned responses"})
        status, body = self._responses.pop(0)
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=str(body))


def _client(transport: _MockTransport, token: str = "secret") -> AgentClient:
    http = httpx.AsyncClient(transport=transport, base_url="http://agent.test")
    return AgentClient(base_url="http://agent.test", token=token, http_client=http)


@pytest.mark.asyncio
async def test_healthz_returns_true_on_200():
    t = _MockTransport([(200, {"ok": True})])
    c = _client(t)
    try:
        assert await c.healthz() is True
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_healthz_returns_false_on_500():
    t = _MockTransport([(500, {"error": "boom"})])
    c = _client(t)
    try:
        assert await c.healthz() is False
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_get_snapshot_sends_bearer():
    t = _MockTransport([(200, {"defcon": 4})])
    c = _client(t, token="tok123")
    try:
        snap = await c.get_snapshot()
        assert snap == {"defcon": 4}
        auth = t.calls[0].headers.get("authorization", "")
        assert auth == "Bearer tok123"
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_get_history_unwraps_envelope():
    t = _MockTransport([(200, {"metric": "cpu", "samples": [{"v": 1}], "count": 1, "hours": 24})])
    c = _client(t)
    try:
        out = await c.get_history("cpu", hours=24)
        assert out["metric"] == "cpu"
        assert out["samples"] == [{"v": 1}]
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_get_history_wraps_bare_list():
    """Older endpoints returned a list directly — wrap for backwards compat."""
    t = _MockTransport([(200, [{"v": 1}, {"v": 2}])])
    c = _client(t)
    try:
        out = await c.get_history("cpu", hours=24)
        assert out["metric"] == "cpu"
        assert out["samples"] == [{"v": 1}, {"v": 2}]
        assert out["count"] == 2
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_history_catalog():
    t = _MockTransport([(200, {"metrics": ["cpu", "memory"], "hosts": ["localhost"]})])
    c = _client(t)
    try:
        cat = await c.get_history_catalog()
        assert cat["metrics"] == ["cpu", "memory"]
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_list_approvals():
    t = _MockTransport([(200, {"pending": [{"id": "abc"}], "count": 1})])
    c = _client(t)
    try:
        out = await c.list_approvals()
        assert out["count"] == 1
        assert out["pending"][0]["id"] == "abc"
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_approve_repair_posts_to_correct_path():
    t = _MockTransport([(200, {"ok": True, "result": {"status": "ok"}})])
    c = _client(t)
    try:
        out = await c.approve_repair("abc123")
        assert out["ok"] is True
        assert t.calls[0].method == "POST"
        assert t.calls[0].url.path.endswith("/api/approvals/abc123/approve")
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_reject_repair_posts_to_correct_path():
    t = _MockTransport([(200, {"ok": True})])
    c = _client(t)
    try:
        await c.reject_repair("xyz789")
        assert t.calls[0].url.path.endswith("/api/approvals/xyz789/reject")
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_list_fleet():
    t = _MockTransport([(200, {"hosts": [{"name": "h1"}], "count": 1})])
    c = _client(t)
    try:
        out = await c.list_fleet()
        assert out["count"] == 1
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_get_fleet_host():
    t = _MockTransport([(200, {"name": "h1", "status": "ok"})])
    c = _client(t)
    try:
        out = await c.get_fleet_host("h1")
        assert out["name"] == "h1"
        assert t.calls[0].url.path.endswith("/api/fleet/h1")
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_trigger_run():
    t = _MockTransport([(200, {"defcon": 4, "server": "x"})])
    c = _client(t)
    try:
        out = await c.trigger_run()
        assert out["defcon"] == 4
        assert t.calls[0].method == "POST"
        assert t.calls[0].url.path.endswith("/api/run")
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_non_2xx_raises_agent_api_error():
    t = _MockTransport([(503, {"error": "no data yet"})])
    c = _client(t)
    try:
        with pytest.raises(AgentAPIError) as exc_info:
            await c.get_snapshot()
        assert exc_info.value.status_code == 503
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_network_error_raises_agent_api_error():
    """A network-level failure should also become AgentAPIError (status_code=None)."""

    class _BoomTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

    http = httpx.AsyncClient(transport=_BoomTransport(), base_url="http://agent.test")
    c = AgentClient(base_url="http://agent.test", http_client=http)
    try:
        with pytest.raises(AgentAPIError) as exc_info:
            await c.get_snapshot()
        assert exc_info.value.status_code is None
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_no_token_omits_authorization_header():
    t = _MockTransport([(200, {"ok": True})])
    http = httpx.AsyncClient(transport=t, base_url="http://agent.test")
    c = AgentClient(base_url="http://agent.test", token="", http_client=http)
    try:
        await c.get_snapshot()
        assert "authorization" not in {k.lower() for k in t.calls[0].headers.keys()}
    finally:
        await c.aclose()


# ---------------------------- v0.4.3 logs ----------------------------

@pytest.mark.asyncio
async def test_get_logs_passes_tail_param():
    t = _MockTransport([(200, {"available": True, "count": 1, "logs": []})])
    c = _client(t)
    try:
        out = await c.get_logs(tail=100)
        assert out["count"] == 1
        # Verify the tail param was sent
        assert "tail=100" in str(t.calls[0].url)
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_get_logs_default_tail():
    t = _MockTransport([(200, {"available": True, "count": 0, "logs": []})])
    c = _client(t)
    try:
        await c.get_logs()
        assert "tail=50" in str(t.calls[0].url)
    finally:
        await c.aclose()


def test_get_logs_download_url_includes_token():
    c = AgentClient(base_url="http://agent.test", token="mysecret")
    url = c.get_logs_download_url(name="all")
    assert url.startswith("http://agent.test/api/logs/download?")
    assert "name=all" in url
    # Token is included as ?t= so the URL stays short
    assert "t=mysecret" in url


def test_get_logs_download_url_without_token():
    c = AgentClient(base_url="http://agent.test", token="")
    url = c.get_logs_download_url(name="repairs")
    assert url == "http://agent.test/api/logs/download?name=repairs"
    assert "t=" not in url


def test_get_logs_download_url_specific_name():
    c = AgentClient(base_url="https://api.example.com", token="x")
    url = c.get_logs_download_url(name="monitor")
    assert "name=monitor" in url
    # Test that the URL is well-formed even with https
    assert url.startswith("https://api.example.com/api/logs/download?")