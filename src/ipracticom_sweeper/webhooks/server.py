"""Webhook server: receive events from GitHub Actions / GitLab / Jenkins."""
from __future__ import annotations
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class WebhookEvent:
    source: str  # "github" | "gitlab" | "jenkins" | "generic"
    event_type: str  # "deploy" | "build" | "alert" | etc.
    payload: dict[str, Any]
    received_at: float


class WebhookVerifier:
    """HMAC signature verification for incoming webhooks."""

    @staticmethod
    def verify_github(body: bytes, signature_header: str, secret: str) -> bool:
        """GitHub sends 'sha256=<hex>'. Returns True if valid."""
        if not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature_header[7:], expected)

    @staticmethod
    def verify_generic(body: bytes, signature_header: str, secret: str) -> bool:
        """Generic HMAC-SHA256 verification. Header is hex digest."""
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature_header, expected)


class WebhookHandler:
    def __init__(self, suppress_during_deploy: bool = True):
        self.suppress_during_deploy = suppress_during_deploy
        self._deploy_until: float = 0.0
        self._events: list[WebhookEvent] = []

    def handle(self, event: WebhookEvent) -> dict[str, Any]:
        """Process an event. Returns response dict."""
        self._events.append(event)
        if event.event_type == "deploy" and self.suppress_during_deploy:
            # Suppress alerts for 10 minutes after deploy
            self._deploy_until = time.time() + 600
        return {"status": "accepted", "event_type": event.event_type}

    def is_suppressed(self) -> bool:
        return time.time() < self._deploy_until

    def event_count(self) -> int:
        return len(self._events)
