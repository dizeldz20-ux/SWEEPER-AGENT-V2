"""Tests for telegram_bot.services.agent_client.

`AgentClient` is a thin async wrapper around the iPracticom Sweeper
agent_api HTTP endpoints. Tests cover the happy path, auth, and
graceful failure (network/timeout/4xx/5xx).
"""
import pytest
import httpx

from ipracticom_sweeper.telegram_bot.services.agent_client import (
    AgentClient,
    AgentAPIError,
)


@pytest.mark.asyncio
async def test_get_snapshot_returns_dict():
    """get_snapshot returns the parsed JSON body on 2xx."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"defcon": 3, "modules": {"cpu": "ok"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = AgentClient(base_url="http://agent", token="t", http_client=http)
        result = await client.get_snapshot()
    assert result == {"defcon": 3, "modules": {"cpu": "ok"}}


@pytest.mark.asyncio
async def test_get_snapshot_sends_bearer():
    """When token is set, the Authorization header is sent."""

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = AgentClient(base_url="http://agent", token="my-token", http_client=http)
        await client.get_snapshot()
    assert captured["auth"] == "Bearer my-token"


@pytest.mark.asyncio
async def test_get_snapshot_no_token_no_header():
    """When token is empty, no Authorization header is sent."""

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = AgentClient(base_url="http://agent", token="", http_client=http)
        await client.get_snapshot()
    assert captured["auth"] is None


@pytest.mark.asyncio
async def test_get_snapshot_401_raises():
    """401 from agent_api raises AgentAPIError with status code."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = AgentClient(base_url="http://agent", token="bad", http_client=http)
        with pytest.raises(AgentAPIError) as exc_info:
            await client.get_snapshot()
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_snapshot_500_raises():
    """5xx from agent_api raises AgentAPIError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = AgentClient(base_url="http://agent", token="t", http_client=http)
        with pytest.raises(AgentAPIError) as exc_info:
            await client.get_snapshot()
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_get_history_returns_envelope():
    """get_history returns the v0.4.2 envelope with samples inside."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"ts": 1, "value": 0.5}, {"ts": 2, "value": 0.6}])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = AgentClient(base_url="http://agent", token="t", http_client=http)
        result = await client.get_history("defcon", hours=24)
    # v0.4.2 client wraps a bare list in an envelope for backwards-compat.
    assert result["metric"] == "defcon"
    assert result["samples"] == [{"ts": 1, "value": 0.5}, {"ts": 2, "value": 0.6}]
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_get_predictions_returns_dict():
    """get_predictions returns the parsed dict."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"defcon": 4, "confidence": 0.8, "horizon_min": 60})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = AgentClient(base_url="http://agent", token="t", http_client=http)
        result = await client.get_predictions()
    assert result == {"defcon": 4, "confidence": 0.8, "horizon_min": 60}


@pytest.mark.asyncio
async def test_evidence_export_returns_dict():
    """export_evidence returns the parsed bundle."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"snapshot": {"x": 1}, "signature": "abc"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = AgentClient(base_url="http://agent", token="t", http_client=http)
        result = await client.export_evidence(hours=12)
    assert result == {"snapshot": {"x": 1}, "signature": "abc"}
