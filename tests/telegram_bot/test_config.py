"""Tests for telegram_bot.config — env loading + chat_id parsing.

These tests validate that config reads from environment variables
in a fail-fast, predictable way.
"""
import os
import pytest

from ipracticom_sweeper.telegram_bot.config import (
    BotConfig,
    load_config,
    ConfigError,
    parse_allowed_chat_ids,
)


def test_parse_allowed_chat_ids_single():
    """Single chat_id parses to a set of one int."""
    assert parse_allowed_chat_ids("12345") == {12345}


def test_parse_allowed_chat_ids_multiple():
    """Comma-separated chat_ids parse to a set of ints."""
    assert parse_allowed_chat_ids("12345,67890,11111") == {12345, 67890, 11111}


def test_parse_allowed_chat_ids_strips_whitespace():
    """Whitespace around ids is stripped."""
    assert parse_allowed_chat_ids(" 12345 , 67890 ") == {12345, 67890}


def test_parse_allowed_chat_ids_empty():
    """Empty string parses to empty set."""
    assert parse_allowed_chat_ids("") == set()


def test_parse_allowed_chat_ids_invalid_raises():
    """Non-numeric chat_id raises ConfigError."""
    with pytest.raises(ConfigError, match="Invalid chat_id"):
        parse_allowed_chat_ids("12345,not-a-number")


def test_load_config_minimal(monkeypatch):
    """Minimal config: bot token + at least one allowed chat_id."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "8351895620")
    cfg = load_config()
    assert cfg.bot_token == "123:abc"
    assert cfg.allowed_chat_ids == {8351895620}
    assert cfg.agent_api_url == "http://127.0.0.1:8787"  # default


def test_load_config_with_agent_url(monkeypatch):
    """Agent API URL is read from env when provided."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "1")
    monkeypatch.setenv("AGENT_API_URL", "http://internal:9999")
    cfg = load_config()
    assert cfg.agent_api_url == "http://internal:9999"


def test_load_config_missing_token_raises(monkeypatch):
    """Missing TELEGRAM_BOT_TOKEN raises ConfigError."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "1")
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config()


def test_load_config_missing_chat_ids_raises(monkeypatch):
    """Missing ALLOWED_CHAT_IDS raises ConfigError."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.delenv("ALLOWED_CHAT_IDS", raising=False)
    with pytest.raises(ConfigError, match="ALLOWED_CHAT_IDS"):
        load_config()


def test_load_config_empty_chat_ids_raises(monkeypatch):
    """Empty ALLOWED_CHAT_IDS raises ConfigError (zero users = locked out)."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "")
    with pytest.raises(ConfigError, match="ALLOWED_CHAT_IDS"):
        load_config()


def test_bot_config_is_authorized():
    """BotConfig.is_authorized checks chat_id against whitelist."""
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42, 99})
    assert cfg.is_authorized(42) is True
    assert cfg.is_authorized(99) is True
    assert cfg.is_authorized(1) is False
