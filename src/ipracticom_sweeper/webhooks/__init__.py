"""Webhooks: receive events (verifier+handler) + dispatch to external URLs."""
from .server import (
    WebhookEvent,
    WebhookVerifier,
    WebhookHandler,
)
from .dispatcher import (
    DispatchResult,
    WebhookDispatcher,
)

__all__ = [
    "WebhookEvent",
    "WebhookVerifier",
    "WebhookHandler",
    "DispatchResult",
    "WebhookDispatcher",
]
