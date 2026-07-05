"""Configuration loader for the iPracticom Sweeper Telegram bot.

Reads from environment variables, validates, and exposes a typed
`BotConfig` object. Fail-fast: any missing required value raises
`ConfigError` at startup so the bot doesn't run in a half-configured state.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    """Raised when the bot cannot be configured from the environment."""


def parse_allowed_chat_ids(raw: str) -> set[int]:
    """Parse a comma-separated list of chat_ids into a set of ints.

    Strips whitespace; raises ConfigError on any non-numeric value.
    """
    if not raw or not raw.strip():
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError as e:
            raise ConfigError(f"Invalid chat_id in ALLOWED_CHAT_IDS: {part!r}") from e
    return out


@dataclass(frozen=True)
class BotConfig:
    """Immutable bot configuration."""

    bot_token: str
    allowed_chat_ids: set[int] = field(default_factory=set)
    agent_api_url: str = "http://127.0.0.1:8787"
    agent_api_token: str = ""

    def is_authorized(self, chat_id: int) -> bool:
        """Return True if chat_id is in the whitelist."""
        return chat_id in self.allowed_chat_ids


def load_config() -> BotConfig:
    """Read config from env vars. Raises ConfigError on missing/invalid values.

    Required:
      - TELEGRAM_BOT_TOKEN
      - ALLOWED_CHAT_IDS (must contain at least one valid id)
    Optional:
      - AGENT_API_URL (default http://127.0.0.1:8787)
      - AGENT_API_TOKEN (empty = no auth, only safe for local)
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is required")

    raw_ids = os.environ.get("ALLOWED_CHAT_IDS", "").strip()
    if not raw_ids:
        raise ConfigError("ALLOWED_CHAT_IDS is required (at least one chat_id)")
    allowed = parse_allowed_chat_ids(raw_ids)
    if not allowed:
        raise ConfigError("ALLOWED_CHAT_IDS is required (at least one chat_id)")

    agent_url = os.environ.get("AGENT_API_URL", "http://127.0.0.1:8787").strip()
    agent_token = os.environ.get("AGENT_API_TOKEN", "").strip()

    return BotConfig(
        bot_token=token,
        allowed_chat_ids=allowed,
        agent_api_url=agent_url,
        agent_api_token=agent_token,
    )
