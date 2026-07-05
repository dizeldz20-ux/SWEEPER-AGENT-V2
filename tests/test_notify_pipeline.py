"""Tests for notify pipeline (dedup wrapper)."""
from __future__ import annotations
import time
from ipracticom_sweeper.notify.pipeline import should_send_alert, reset_dedup_state


def test_critical_always_sends():
    """DEFCON 2 = critical = always sends."""
    reset_dedup_state()
    send, fp = should_send_alert("h1", "cpu", "high_load", "crit", 2)
    assert send is True
    assert fp  # fingerprint is non-empty


def test_warn_first_sends_then_dedups():
    """DEFCON 4 = warn. First sends, second within window suppressed."""
    reset_dedup_state()
    send1, _ = should_send_alert("h1", "cpu", "high_load", "warn", 4)
    send2, _ = should_send_alert("h1", "cpu", "high_load", "warn", 4)
    assert send1 is True
    assert send2 is False


def test_different_problems_independent():
    """Different problem fingerprints don't interfere with each other."""
    reset_dedup_state()
    send_a, _ = should_send_alert("h1", "cpu", "high_load", "warn", 4)
    send_b, _ = should_send_alert("h1", "memory", "low_mem", "warn", 4)
    assert send_a is True
    assert send_b is True


def test_critical_bypasses_dedup():
    """Even if warn was already sent, a critical for the same module still sends."""
    reset_dedup_state()
    should_send_alert("h1", "cpu", "high_load", "warn", 4)
    send, _ = should_send_alert("h1", "cpu", "high_load", "crit", 2)
    assert send is True
