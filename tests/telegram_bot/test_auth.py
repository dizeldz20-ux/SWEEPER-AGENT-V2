"""Tests for telegram_bot.auth — chat_id whitelist enforcement.

The auth module exposes a single `authorized_only` decorator that
gates any handler on chat_id whitelist membership. It is a tight
contract: silent return on unauthorized, no echo, no error trace.
"""
import pytest

from ipracticom_sweeper.telegram_bot.auth import authorized_only, UnauthorizedError
from ipracticom_sweeper.telegram_bot.config import BotConfig


class FakeUpdate:
    """Minimal stand-in for a python-telegram-bot Update."""

    def __init__(self, chat_id: int | None):
        self.effective_chat = type("Chat", (), {"id": chat_id})() if chat_id is not None else None
        self.message = None  # not used by decorator


class FakeContext:
    """Minimal stand-in for python-telegram-bot ContextTypes.DEFAULT_TYPE."""

    def __init__(self, bot_data: dict | None = None):
        self.bot_data = bot_data or {}


@pytest.mark.asyncio
async def test_authorized_only_allows_whitelisted_chat_id():
    """Whitelisted chat_id executes the wrapped function."""
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = FakeContext(bot_data={"config": cfg})
    update = FakeUpdate(chat_id=42)

    called = []

    @authorized_only
    async def handler(update, context):
        called.append(update.effective_chat.id)
        return "ok"

    result = await handler(update, ctx)
    assert result == "ok"
    assert called == [42]


@pytest.mark.asyncio
async def test_authorized_only_blocks_unknown_chat_id():
    """Non-whitelisted chat_id raises UnauthorizedError, no execution."""
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = FakeContext(bot_data={"config": cfg})
    update = FakeUpdate(chat_id=999)

    called = []

    @authorized_only
    async def handler(update, context):
        called.append(True)
        return "ok"

    with pytest.raises(UnauthorizedError):
        await handler(update, ctx)
    assert called == []  # the wrapped function never ran


@pytest.mark.asyncio
async def test_authorized_only_blocks_missing_chat_id():
    """Update with no chat raises UnauthorizedError."""
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = FakeContext(bot_data={"config": cfg})
    update = FakeUpdate(chat_id=None)

    @authorized_only
    async def handler(update, context):
        return "ok"

    with pytest.raises(UnauthorizedError):
        await handler(update, ctx)


@pytest.mark.asyncio
async def test_authorized_only_missing_config_raises():
    """If config is missing from bot_data, raises UnauthorizedError."""
    ctx = FakeContext(bot_data={})  # no config
    update = FakeUpdate(chat_id=42)

    @authorized_only
    async def handler(update, context):
        return "ok"

    with pytest.raises(UnauthorizedError):
        await handler(update, ctx)
