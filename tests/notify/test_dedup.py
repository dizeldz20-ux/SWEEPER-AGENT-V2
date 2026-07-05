"""Tests for fingerprint + deduplicator."""
import pytest
import time
from ipracticom_sweeper.notify import make_fingerprint, Deduplicator


def test_fingerprint_deterministic():
    a = make_fingerprint("h1", "cpu", 4)
    b = make_fingerprint("h1", "cpu", 4)
    assert a == b


def test_fingerprint_unique():
    a = make_fingerprint("h1", "cpu", 4)
    b = make_fingerprint("h2", "cpu", 4)
    c = make_fingerprint("h1", "mem", 4)
    assert len({a, b, c}) == 3


def test_dedup_new_sends():
    d = Deduplicator(window_seconds=300)
    r = d.check("h1", "cpu", 4, now=1000.0)
    assert r.should_send is True
    assert r.count == 1


def test_dedup_within_window_suppresses():
    d = Deduplicator(window_seconds=300)
    d.check("h1", "cpu", 4, now=1000.0)
    r = d.check("h1", "cpu", 4, now=1100.0)
    assert r.should_send is False
    assert r.count == 2


def test_dedup_window_expired_sends():
    d = Deduplicator(window_seconds=300)
    d.check("h1", "cpu", 4, now=1000.0)
    r = d.check("h1", "cpu", 4, now=2000.0)
    assert r.should_send is True


def test_dedup_critical_always_sends():
    d = Deduplicator(window_seconds=300)
    d.check("h1", "cpu", 3, now=1000.0)
    r = d.check("h1", "cpu", 3, now=1100.0)
    assert r.should_send is True


def test_dedup_force_bypasses():
    d = Deduplicator(window_seconds=300)
    d.check("h1", "cpu", 4, now=1000.0)
    r = d.check("h1", "cpu", 4, now=1100.0, force=True)
    assert r.should_send is True
