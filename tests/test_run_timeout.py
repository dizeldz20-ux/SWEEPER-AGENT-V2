"""Test v0.4.7: trigger_run uses long timeout for /api/run."""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from ipracticom_sweeper.telegram_bot.services.agent_client import AgentAPIError, AgentClient


@pytest.mark.asyncio
async def test_trigger_run_passes_long_timeout():
    """trigger_run must pass timeout=120 (not the 10s default) to survive a full sweep."""
    client = AgentClient(base_url="http://x", token="t")

    with patch.object(client, "_post", new=AsyncMock(return_value={"defcon": 4})) as mock_post:
        await client.trigger_run()

    # Verify _post was called with the long timeout.
    assert mock_post.called
    call_args = mock_post.call_args
    assert call_args[0][0] == "/api/run"
    assert call_args.kwargs.get("timeout") == 120.0 or (len(call_args[0]) > 1 and call_args[0][1] == 120.0)
    # Docstring must mention the 15 monitors by name so future maintainers know why.
    assert "monitor" in client.trigger_run.__doc__.lower()


@pytest.mark.asyncio
async def test_post_helper_accepts_timeout_override():
    """_post must accept a timeout kwarg and pass it through to httpx."""
    client = AgentClient(base_url="http://x", token="t")

    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"ok": True}

    with patch.object(client._http, "post", new=AsyncMock(return_value=fake_response)) as mock_http_post:
        await client._post("/some/path", timeout=42.0)

    assert mock_http_post.called
    call_kwargs = mock_http_post.call_args.kwargs
    assert call_kwargs.get("timeout") == 42.0


@pytest.mark.asyncio
async def test_post_helper_default_timeout_is_none():
    """When no timeout override, _post must pass timeout=None so the client's
    default 10s applies."""
    client = AgentClient(base_url="http://x", token="t")

    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"ok": True}

    with patch.object(client._http, "post", new=AsyncMock(return_value=fake_response)) as mock_http_post:
        await client._post("/some/path")

    call_kwargs = mock_http_post.call_args.kwargs
    assert call_kwargs.get("timeout") is None