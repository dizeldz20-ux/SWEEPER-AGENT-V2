"""Notify pipeline: dedup + dispatch.

Wraps the existing Deduplicator with a fingerprint-based cache so
each unique problem (host+module+kind+severity) only fires once per
window. Critical (DEFCON <= 3) always fires.
"""
from __future__ import annotations
import time
from typing import Any

from ipracticom_sweeper.notify.deduplicator import Deduplicator
from ipracticom_sweeper.notify.fingerprint import make_fingerprint


# Module-level dedup state. In production this is per-process;
# the agent_api process can be different from the pipeline process.
_dedup = Deduplicator(window_seconds=300.0)


def should_send_alert(
    host: str,
    module: str,
    kind: str,
    severity: str,
    defcon: int,
    window_seconds: float = 300.0,
) -> tuple[bool, str]:
    """Decide whether to send an alert for this problem.

    Returns (should_send, fingerprint_string).
    Critical (defcon <= 3) always sends, regardless of window.
    """
    fp = make_fingerprint(host=host, module=module, defcon=defcon)
    # Critical → bypass dedup
    if defcon <= 3:
        return True, fp
    # For non-critical, use dedup
    global _dedup
    if window_seconds != _dedup.window:
        _dedup = Deduplicator(window_seconds=window_seconds)
    result = _dedup.check(host, module, defcon, time.time(), force=False)
    return result.should_send, fp


def reset_dedup_state() -> None:
    """Clear the dedup cache (for tests)."""
    global _dedup
    _dedup = Deduplicator(window_seconds=_dedup.window)
