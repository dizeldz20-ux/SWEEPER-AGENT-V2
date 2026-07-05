"""Token rotation: optional, off by default."""
from __future__ import annotations
import os
import time
from dataclasses import dataclass

from .._log import log_suppressed


@dataclass
class TokenInfo:
    token: str
    expires_at: float  # unix timestamp
    days_until_expiry: float


def load_token(prefix: str = "AGENT_TOKEN_") -> TokenInfo | None:
    """Find current token from env vars matching prefix_<YYYYMMDD>.

    Returns None if not found OR if rotation is disabled (AGENT_TOKEN_ROTATION_ENABLED != "true").
    """
    if os.environ.get("AGENT_TOKEN_ROTATION_ENABLED", "").lower() != "true":
        return None

    now = time.time()
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        date_str = key[len(prefix):]
        try:
            expires_at = time.mktime(time.strptime(date_str, "%Y%m%d")) + 86400  # end of day
        except ValueError as e:
            log_suppressed("secrets_parse_date", e)
            continue
        if value and expires_at > now:
            days = (expires_at - now) / 86400
            return TokenInfo(token=value, expires_at=expires_at, days_until_expiry=days)
    return None
