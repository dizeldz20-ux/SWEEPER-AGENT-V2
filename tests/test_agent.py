"""Tests for AgentClient (uses Flask test_client, no real network)."""
import os
import pytest
from unittest.mock import patch, MagicMock
from ipracticom_sweeper.agent_client import AgentClient, AgentError


def _mock_response(status_code=200, json_data=None, text=""):
    m = MagicMock()
    m.status_code = status_code
    m.text = text or str(json_data)
    m.json.return_value = json_data or {}
    return m


def test_client_strips_trailing_slash():
    c = AgentClient("http://x:8787/")
    assert c.base_url == "http://x:8787"


def test_client_headers_no_token():
    c = AgentClient("http://x:8787")
    h = c._headers()
    assert "Authorization" not in h
    assert h["Accept"] == "application/json"


def test_client_headers_with_token():
    c = AgentClient("http://x:8787", token="secret")
    h = c._headers()
    assert h["Authorization"] == "Bearer secret"


def test_healthz_success():
    c = AgentClient("http://x:8787")
    with patch("httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.get.return_value = _mock_response(200, {"ok": True, "server_id": "h1"})
        mock_client.return_value.__enter__.return_value = ctx
        result = c.healthz()
    assert result["ok"] is True
    assert result["server_id"] == "h1"


def test_healthz_404_raises():
    c = AgentClient("http://x:8787")
    with patch("httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.get.return_value = _mock_response(404, {}, "not found")
        mock_client.return_value.__enter__.return_value = ctx
        with pytest.raises(AgentError):
            c.healthz()


def test_get_snapshot_returns_none_on_404():
    c = AgentClient("http://x:8787")
    with patch("httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.get.return_value = _mock_response(404, {}, "not found")
        mock_client.return_value.__enter__.return_value = ctx
        assert c.get_snapshot() is None


def test_get_snapshot_success():
    c = AgentClient("http://x:8787")
    with patch("httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.get.return_value = _mock_response(200, {"defcon": 4, "server": "h1"})
        mock_client.return_value.__enter__.return_value = ctx
        result = c.get_snapshot()
    assert result["defcon"] == 4


def test_trigger_run():
    c = AgentClient("http://x:8787")
    with patch("httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.post.return_value = _mock_response(200, {"defcon": 3})
        mock_client.return_value.__enter__.return_value = ctx
        result = c.trigger_run()
    assert result["defcon"] == 3


def test_local_identity():
    ident = AgentClient.local_identity()
    assert ident["kind"] == "local"
    assert "server_id" in ident


def test_remote_identity():
    c = AgentClient("http://x:8787")
    with patch("httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.get.return_value = _mock_response(200, {"server_id": "remote1", "auth": "token"})
        mock_client.return_value.__enter__.return_value = ctx
        ident = c.remote_identity()
    assert ident["kind"] == "remote"
    assert ident["server_id"] == "remote1"
    assert ident["base_url"] == "http://x:8787"


def test_connection_error_raises_agent_error():
    import httpx
    c = AgentClient("http://x:8787", timeout=1.0)
    with patch("httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.get.side_effect = httpx.ConnectError("refused")
        mock_client.return_value.__enter__.return_value = ctx
        with pytest.raises(AgentError):
            c.healthz()
