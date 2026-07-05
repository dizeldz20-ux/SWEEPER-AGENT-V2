"""Webhook dispatcher: deliver events to external URLs (Slack/Teams/custom).

The dispatcher signs outgoing requests with HMAC-SHA256 so the receiver
can verify they came from us. It uses stdlib urllib only — no requests/httpx.

Delivery is synchronous + best-effort: each call returns a result dict.
The pipeline can call this from a background thread if it wants async.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class DispatchResult:
    url: str
    success: bool
    status_code: int
    duration_ms: int
    error: str | None = None
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "success": self.success,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "attempts": self.attempts,
        }


class WebhookDispatcher:
    """Signs + POSTs webhook events to external URLs.

    Configure one dispatcher per target URL. Secret is used to sign the body
    (receiver can verify by recomputing HMAC-SHA256 of the body).
    """

    def __init__(
        self,
        url: str,
        secret: str,
        timeout_seconds: float = 5.0,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
    ):
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"url must start with http:// or https://, got: {url!r}")
        if not secret:
            raise ValueError("secret is required for signing")
        self.url = url
        self.secret = secret
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self._delivered: list[DispatchResult] = []

    @property
    def delivered_count(self) -> int:
        return len(self._delivered)

    @property
    def last_result(self) -> DispatchResult | None:
        return self._delivered[-1] if self._delivered else None

    def sign(self, body: bytes) -> str:
        """Return hex HMAC-SHA256 of body."""
        return hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()

    def dispatch(self, event: dict[str, Any]) -> DispatchResult:
        """Send a single event. Returns a result (success or last failure)."""
        body = json.dumps(event, sort_keys=True).encode("utf-8")
        signature = self.sign(body)
        headers = {
            "Content-Type": "application/json",
            "X-Sweeper-Signature": signature,
            "X-Sweeper-Timestamp": str(int(time.time())),
        }

        attempts = 0
        last_error = None
        last_status = 0
        start = time.time()

        while attempts < self.max_attempts:
            attempts += 1
            try:
                req = urllib.request.Request(
                    self.url, data=body, headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    last_status = resp.status
                    if 200 <= resp.status < 300:
                        duration_ms = int((time.time() - start) * 1000)
                        result = DispatchResult(
                            url=self.url,
                            success=True,
                            status_code=last_status,
                            duration_ms=duration_ms,
                            attempts=attempts,
                        )
                        self._delivered.append(result)
                        return result
                    # 4xx — don't retry
                    if 400 <= resp.status < 500 and resp.status != 429:
                        duration_ms = int((time.time() - start) * 1000)
                        result = DispatchResult(
                            url=self.url,
                            success=False,
                            status_code=last_status,
                            duration_ms=duration_ms,
                            error=f"client error {resp.status}, not retrying",
                            attempts=attempts,
                        )
                        self._delivered.append(result)
                        return result
                    last_error = f"server error {resp.status}"
            except urllib.error.HTTPError as e:
                last_status = e.code
                # 4xx — don't retry
                if 400 <= e.code < 500 and e.code != 429:
                    duration_ms = int((time.time() - start) * 1000)
                    result = DispatchResult(
                        url=self.url,
                        success=False,
                        status_code=e.code,
                        duration_ms=duration_ms,
                        error=f"client error {e.code}, not retrying",
                        attempts=attempts,
                    )
                    self._delivered.append(result)
                    return result
                last_error = f"server error {e.code}" if e.code >= 500 else f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_status = 0
                last_error = f"{type(e).__name__}: {e}"

            # exponential backoff between retries
            if attempts < self.max_attempts:
                time.sleep(self.backoff_base * (2 ** (attempts - 1)))

        duration_ms = int((time.time() - start) * 1000)
        result = DispatchResult(
            url=self.url,
            success=False,
            status_code=last_status,
            duration_ms=duration_ms,
            error=last_error or "unknown",
            attempts=attempts,
        )
        self._delivered.append(result)
        return result
