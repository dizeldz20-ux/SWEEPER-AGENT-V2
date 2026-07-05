"""Tests for the v0.4.3 fleet handler (host details + logs + download)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ipracticom_sweeper.telegram_bot.config import BotConfig
from ipracticom_sweeper.telegram_bot.handlers import fleet as fleet_handler


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
async def test_fleet_host_local_shows_live_metrics():
    """Local host view should pull a snapshot and surface CPU/memory/disk."""

    class FakeAgent:
        async def get_fleet_host(self, name):
            return {
                "name": "local", "kind": "local", "status": "ok",
                "defcon": 4, "problems_found": 1,
                "extra": {
                    "cpu": {"percent": 23.5, "cores": 4},
                    "memory": {"percent": 61.0, "used_mb": 12400, "total_mb": 20400},
                    "disk": {"percent": 91.2, "used_gb": 230.5, "total_gb": 253.0},
                    "network": {"bytes_sent": 1000, "bytes_recv": 2000},
                    "uptime_seconds": 60862,
                    "booted_at": "2026-06-29T11:57:00+00:00",
                },
            }

        async def get_snapshot(self):
            # v0.4.5: no longer used for local host — extra block already
            # carries the metrics. Returned here only to verify backward
            # compatibility for any caller that still passes it.
            return {}

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="fleet:host:local")
    result = await fleet_handler.fleet_host(update, ctx)
    text = result["text"]
    # Live metrics block — now sourced from extra (v0.4.4 psutil snapshot).
    assert "CPU" in text
    assert "23.5%" in text
    assert "61.0%" in text
    assert "91.2%" in text
    # Has log + download buttons
    cbs = [b.callback_data for row in result["reply_markup"].inline_keyboard for b in row]
    assert "fleet:logs:local" in cbs
    assert "fleet:download:local" in cbs


@pytest.mark.asyncio
async def test_fleet_host_connector_no_live_metrics():
    """For remote connectors, we show config but no live CPU/memory."""

    class FakeAgent:
        async def get_fleet_host(self, name):
            return {
                "name": "prod-web", "kind": "connector", "status": "error",
                "instance_id": "i-aaaa", "region": "il-central-1",
                "last_error": "SSM unreachable",
            }

        async def get_snapshot(self):
            # Should NOT be called for non-local hosts
            raise AssertionError("get_snapshot should not be called for non-local")

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="fleet:host:prod-web")
    result = await fleet_handler.fleet_host(update, ctx)
    assert "prod-web" in result["text"]
    assert "SSM unreachable" in result["text"]


@pytest.mark.asyncio
async def test_fleet_host_missing_name_returns_error():
    class FakeAgent:
        async def get_fleet_host(self, name):
            return {}

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    # callback data without the trailing name part
    update = _update(chat_id=42, callback_data="fleet:host:")
    result = await fleet_handler.fleet_host(update, ctx)
    assert "❌" in result["text"]


@pytest.mark.asyncio
async def test_fleet_logs_shows_each_log_with_tail():
    class FakeAgent:
        async def get_logs(self, tail=50):
            return {
                "available": True,
                "count": 2,
                "logs": [
                    {
                        "name": "repairs", "kind": "jsonl", "path": "/x",
                        "size_bytes": 200, "line_count": 2, "tail_count": 2,
                        "tail": [
                            {"action": "drop_caches"},
                            {"action": "service_restart"},
                        ],
                    },
                    {
                        "name": "heartbeat", "kind": "json", "path": "/y",
                        "size_bytes": 100, "line_count": 1, "tail_count": 1,
                        "tail": [{"defcon": 4}],
                    },
                ],
            }

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="fleet:logs:local")
    result = await fleet_handler.fleet_logs(update, ctx)
    text = result["text"]
    assert "repairs" in text
    assert "heartbeat" in text
    assert "drop_caches" in text
    # Has download + back buttons
    cbs = [b.callback_data for row in result["reply_markup"].inline_keyboard for b in row]
    assert "fleet:download:local" in cbs
    assert "fleet:host:local" in cbs


@pytest.mark.asyncio
async def test_fleet_logs_handles_no_logs():
    class FakeAgent:
        async def get_logs(self, tail=50):
            return {"available": True, "count": 0, "logs": []}

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="fleet:logs:local")
    result = await fleet_handler.fleet_logs(update, ctx)
    assert "אין לוגים" in result["text"]


@pytest.mark.asyncio
async def test_fleet_logs_truncates_long_output():
    class FakeAgent:
        async def get_logs(self, tail=50):
            huge_line = {"x": "y" * 200}
            return {
                "available": True,
                "count": 1,
                "logs": [{
                    "name": "repairs", "kind": "jsonl", "path": "/x",
                    "size_bytes": 99999, "line_count": 1000, "tail_count": 50,
                    "tail": [huge_line] * 50,
                }],
            }

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="fleet:logs:local")
    result = await fleet_handler.fleet_logs(update, ctx)
    # Telegram limit is 4096, our cap is 3800
    assert len(result["text"]) <= 3900


@pytest.mark.asyncio
async def test_fleet_logs_unavailable_returns_helpful():
    class FakeAgent:
        async def get_logs(self, tail=50):
            return {"available": False, "count": 0, "logs": []}

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="fleet:logs:local")
    result = await fleet_handler.fleet_logs(update, ctx)
    assert "state" in result["text"].lower() or "תיקיית" in result["text"]


# ---------------------------- format_local_metrics ----------------------------

def test_format_local_metrics_renders_known_modules():
    snap = {
        "defcon": 3,
        "modules": {
            "cpu": {"status": "ok", "details": {"percent": 12.3}},
            "memory": {"status": "warn", "details": {"used_percent": 80}},
            "disk": {"status": "crit", "details": {"percent": 95.0}},
            "network": {"status": "ok", "details": {}},
        },
    }
    out = fleet_handler._format_local_metrics(snap)
    assert "CPU" in out
    assert "12.3%" in out
    assert "80.0%" in out
    assert "95.0%" in out
    assert "DEFCON" in out
    assert "3" in out


def test_format_local_metrics_handles_missing_modules():
    out = fleet_handler._format_local_metrics({"defcon": 5, "modules": {}})
    # Should still list the 4 standard modules, even if no data
    assert "CPU" in out
    assert "זיכרון" in out
    assert "דיסק" in out
    assert "רשת" in out


def test_one_line_compacts_json_for_chat():
    obj = {"a": 1, "b": [1, 2, 3], "c": "hello"}
    out = fleet_handler._one_line(obj)
    # No newlines
    assert "\n" not in out
    # No excessive length (under 400 chars)
    assert len(out) < 400
    assert "a" in out and "1" in out
