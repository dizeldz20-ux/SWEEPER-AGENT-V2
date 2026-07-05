"""Tests for the v0.4.3 connectors handler (seed-data detection + delete confirm)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ipracticom_sweeper.telegram_bot.config import BotConfig
from ipracticom_sweeper.telegram_bot.handlers import connectors as connectors_handler


def _update(chat_id: int = 42, callback_data: str | None = None):
    chat = SimpleNamespace(id=chat_id)
    if callback_data is None:
        return SimpleNamespace(effective_chat=chat, message=None, callback_query=None)
    cq = SimpleNamespace(
        data=callback_data, from_user=SimpleNamespace(id=chat_id), message=None
    )
    return SimpleNamespace(effective_chat=chat, callback_query=cq, message=None)


def _ctx(cfg: BotConfig, agent):
    return SimpleNamespace(bot_data={"config": cfg, "agent": agent})


@pytest.mark.asyncio
async def test_connectors_menu_flags_seed_data():
    """If every connector has last_error + no last_collected, show a warning."""

    class FakeAgent:
        async def list_fleet(self):
            return {
                "count": 2,
                "hosts": [
                    {
                        "name": "prod-web", "kind": "connector", "status": "error",
                        "instance_id": "i-aaaa", "region": "il",
                        "last_error": "Unable to locate credentials",
                        "last_collected_at": None,
                    },
                    {
                        "name": "prod-db", "kind": "connector", "status": "error",
                        "instance_id": "i-bbbb", "region": "il",
                        "last_error": "Unable to locate credentials",
                        "last_collected_at": None,
                    },
                ],
            }

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="menu:connectors")
    result = await connectors_handler.connectors(update, ctx)
    text = result["text"]
    # Header explains what a connector is
    assert "SSM" in text
    # Warning about seed data
    assert "seed" in text.lower() or "⚠️" in text


@pytest.mark.asyncio
async def test_connectors_menu_no_warning_for_real_data():
    """If any connector has been collected, don't show the seed warning."""

    class FakeAgent:
        async def list_fleet(self):
            return {
                "count": 1,
                "hosts": [
                    {
                        "name": "real-server", "kind": "connector", "status": "ok",
                        "instance_id": "i-real", "region": "il",
                        "last_error": None,
                        "last_collected_at": 1782761586.0,
                    },
                ],
            }

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="menu:connectors")
    result = await connectors_handler.connectors(update, ctx)
    text = result["text"]
    # No seed warning when real data is present
    assert "seed" not in text.lower()


@pytest.mark.asyncio
async def test_delete_confirm_200_returns_success():
    class FakeAgent:
        def __init__(self):
            self._http = SimpleNamespace()
        @property
        def _url(self):
            return lambda p: f"http://x{p}"
        @property
        def _headers(self):
            return lambda: {}

    class FakeResp:
        status_code = 204

    class FakeHTTP:
        async def delete(self, url, headers):
            return FakeResp()

    agent = SimpleNamespace(_http=FakeHTTP(), _token="t",
                            _url=lambda p: f"http://x{p}",
                            _headers=lambda: {})
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, agent)
    update = _update(chat_id=42, callback_data="conn:delete_confirm:prod-web")
    result = await connectors_handler.connector_delete_confirm(update, ctx)
    assert "✅" in result["text"]
    assert "prod-web" in result["text"]


@pytest.mark.asyncio
async def test_delete_confirm_404_returns_graceful():
    class FakeResp:
        status_code = 404
        text = "not found"

    class FakeHTTP:
        async def delete(self, url, headers):
            return FakeResp()

    agent = SimpleNamespace(_http=FakeHTTP(), _token="t",
                            _url=lambda p: f"http://x{p}",
                            _headers=lambda: {})
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, agent)
    update = _update(chat_id=42, callback_data="conn:delete_confirm:gone")
    result = await connectors_handler.connector_delete_confirm(update, ctx)
    assert "ℹ️" in result["text"]
    assert "gone" in result["text"]


@pytest.mark.asyncio
async def test_delete_confirm_500_returns_error():
    class FakeResp:
        status_code = 500
        text = "internal error"

    class FakeHTTP:
        async def delete(self, url, headers):
            return FakeResp()

    agent = SimpleNamespace(_http=FakeHTTP(), _token="t",
                            _url=lambda p: f"http://x{p}",
                            _headers=lambda: {})
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, agent)
    update = _update(chat_id=42, callback_data="conn:delete_confirm:oops")
    result = await connectors_handler.connector_delete_confirm(update, ctx)
    assert "❌" in result["text"]
    assert "500" in result["text"]


@pytest.mark.asyncio
async def test_delete_confirm_network_error():
    class FakeHTTP:
        async def delete(self, url, headers):
            raise ConnectionError("no route")

    agent = SimpleNamespace(_http=FakeHTTP(), _token="t",
                            _url=lambda p: f"http://x{p}",
                            _headers=lambda: {})
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, agent)
    update = _update(chat_id=42, callback_data="conn:delete_confirm:x")
    result = await connectors_handler.connector_delete_confirm(update, ctx)
    assert "❌" in result["text"] or "שגיאה" in result["text"]