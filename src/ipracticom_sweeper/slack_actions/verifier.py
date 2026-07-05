"""Slack request signing verifier.

Slack signs every interactive payload (button click, modal submit) with:
    signature v0 = HMAC-SHA256(signing_secret, "v0:" + timestamp + ":" + body)

The receiver must:
  1. Read the raw body bytes.
  2. Read the X-Slack-Request-Timestamp header.
  3. Reject if timestamp is more than 5 minutes old (replay protection).
  4. Recompute the signature and compare with the X-Slack-Signature header.

Reference: https://api.slack.com/authentication/verifying-requests-from-slack
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass


# Replay protection window: 5 minutes per Slack's spec.
MAX_TIMESTAMP_AGE_SECONDS = 300


@dataclass
class VerificationResult:
    valid: bool
    reason: str | None = None  # why it failed, if invalid


def _constant_time_eq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def verify_slack_signature(
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    signing_secret: str,
    now: float | None = None,
) -> VerificationResult:
    """Verify a Slack-signed request.

    Returns VerificationResult with valid=False + reason if:
      - signing_secret is empty
      - timestamp or signature header is missing
      - timestamp is not a valid integer
      - timestamp is too old (replay) or too far in the future
      - signature does not match
    """
    if not signing_secret:
        return VerificationResult(False, "signing_secret not configured")
    if not timestamp_header or not signature_header:
        return VerificationResult(False, "missing timestamp or signature header")

    # 1. timestamp window check
    try:
        ts = int(timestamp_header)
    except ValueError:
        return VerificationResult(False, "timestamp is not a valid integer")

    now = now if now is not None else time.time()
    if abs(now - ts) > MAX_TIMESTAMP_AGE_SECONDS:
        return VerificationResult(False, "timestamp outside replay window")

    # 2. recompute signature
    # Slack's spec uses version "v0"
    base = b"v0:" + timestamp_header.encode("ascii") + b":" + body
    expected = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), base, hashlib.sha256
    ).hexdigest()

    if not _constant_time_eq(expected, signature_header):
        return VerificationResult(False, "signature mismatch")

    return VerificationResult(True)
