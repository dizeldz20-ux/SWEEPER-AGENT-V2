"""Tests for the v0.4.3 settings handler (Telegram-only)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ipracticom_sweeper.telegram_bot.config import BotConfig
from ipracticom_sweeper.telegram_bot.handlers import settings as settings_handler


def _update(chat_id: int = 42, callback_data: str | None = None):
    chat = SimpleNamespace(id=chat_id)
    if callback_data is None:
        return SimpleNamespace(effective_chat=chat, message=None, callback_query=None)
    cq = SimpleNamespace(
        data=callback_data, from_user=SimpleNamespace(id=chat_id), message=None
    )
    return SimpleNamespace(effective_chat=chat, callback_query=cq, message=None)


def _ctx(cfg: BotConfig, agent=None):
    return SimpleNamespace(bot_data={"config": cfg, "agent": agent})


@pytest.mark.asyncio
async def test_settings_menu_has_only_telegram_button():
    """v0.4.3: settings only has the Telegram test (no Slack, no API)."""
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg)
    update = _update(chat_id=42)
    result = await settings_handler.settings(update, ctx)
    cbs = [b.callback_data for row in result["reply_markup"].inline_keyboard for b in row]
    # Exactly one test button: set:test:tg
    assert "set:test:tg" in cbs
    # No more slack/api tests
    assert "set:test:slack" not in cbs
    assert "set:test:api" not in cbs
    # And back to main
    assert "menu:main" in cbs


@pytest.mark.asyncio
async def test_test_tg_returns_bot_identity():
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg)

    class FakeBot:
        async def get_me(self):
            return SimpleNamespace(
                id=12345, username="Sweeperhermes_bot", first_name="Sweeperbbi"
            )

    ctx.bot = FakeBot()
    update = _update(chat_id=42, callback_data="set:test:tg")
    result = await settings_handler.test_tg(update, ctx)
    assert "✅" in result["text"]
    assert "Sweeperhermes_bot" in result["text"]
    assert "12345" in result["text"]


@pytest.mark.asyncio
async def test_test_tg_handles_get_me_failure():
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg)

    class BrokenBot:
        async def get_me(self):
            raise RuntimeError("network down")

    ctx.bot = BrokenBot()
    update = _update(chat_id=42, callback_data="set:test:tg")
    result = await settings_handler.test_tg(update, ctx)
    assert "❌" in result["text"]
    assert "network down" in result["text"]
