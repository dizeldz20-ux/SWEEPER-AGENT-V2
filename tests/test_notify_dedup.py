"""Tests for notify deduplication logic."""
from __future__ import annotations
import time
from ipracticom_sweeper.notify.deduplicator import Deduplicator


def test_first_alert_always_sends():
    """First time we see a fingerprint, alert should send."""
    d = Deduplicator(window_seconds=300)
    now = time.time()
    result = d.check("host1", "cpu", 5, now)
    assert result.should_send is True
    assert result.count == 1


def test_duplicate_within_window_suppresses():
    """Same fingerprint within 5 min → suppress."""
    d = Deduplicator(window_seconds=300)
    now = time.time()
    d.check("host1", "cpu", 5, now)
    result = d.check("host1", "cpu", 5, now + 60)  # 1 min later
    assert result.should_send is False
    assert result.count == 2  # count incremented


def test_after_window_resends():
    """Same fingerprint after window expires → resend."""
    d = Deduplicator(window_seconds=300)
    now = time.time()
    d.check("host1", "cpu", 5, now)
    result = d.check("host1", "cpu", 5, now + 400)  # > 5 min later
    assert result.should_send is True
    assert result.count == 1  # window reset


def test_critical_defcon_always_sends():
    """DEFCON <= 3 (critical) always sends, even within window."""
    d = Deduplicator(window_seconds=300)
    now = time.time()
    d.check("host1", "cpu", 2, now)
    result = d.check("host1", "cpu", 2, now + 60)  # critical, 1 min later
    assert result.should_send is True


def test_force_bypasses_dedup():
    """force=True bypasses deduplication window."""
    d = Deduplicator(window_seconds=300)
    now = time.time()
    d.check("host1", "cpu", 5, now)
    result = d.check("host1", "cpu", 5, now + 60, force=True)
    assert result.should_send is True
