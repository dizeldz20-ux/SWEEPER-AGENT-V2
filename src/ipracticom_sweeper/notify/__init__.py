"""Notification subsystem: dedup, formatting, senders, queue."""
from .fingerprint import make_fingerprint
from .deduplicator import Deduplicator, DedupResult
from .queue import TelegramQueue, QueuedMessage
from .suppression import SuppressionDecision, should_notify, mark_notified
from . import legacy
from . import store
from . import suppression

# Re-export legacy functions
format_slack_message = legacy.format_slack_message
format_telegram_message = legacy.format_telegram_message
notify_pipeline_result = legacy.notify_pipeline_result

# Re-export private helpers (used by tests via patch)
_send_slack = legacy._send_slack
_send_telegram = legacy._send_telegram
notify = legacy.notify

# Re-export config helpers used by tests
from ipracticom_sweeper.config import (
    notifications_enabled,
    slack_webhook_url,
    telegram_bot_token,
    telegram_chat_id,
)

__all__ = [
    "make_fingerprint",
    "Deduplicator",
    "DedupResult",
    "TelegramQueue",
    "QueuedMessage",
    "SuppressionDecision",
    "should_notify",
    "mark_notified",
    "suppression",
    "store",
    "format_slack_message",
    "format_telegram_message",
    "notify_pipeline_result",
    "notify",
    "_send_slack",
    "_send_telegram",
    "notifications_enabled",
    "slack_webhook_url",
    "telegram_bot_token",
    "telegram_chat_id",
]
