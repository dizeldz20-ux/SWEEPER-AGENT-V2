"""Tests for escalation block-markers (repair/block.py)."""
from __future__ import annotations

import pytest

from ipracticom_sweeper.repair import block


@pytest.fixture
def block_dir(tmp_path, monkeypatch):
    d = tmp_path / "pending_repairs" / "blocked"
    monkeypatch.setattr(block, "BLOCKED_DIR", d)
    return d


def test_block_then_is_blocked(block_dir):
    assert block.is_blocked("service_restart", "web-01") is False
    block.block("service_restart", "web-01", reason="restart failed")
    assert block.is_blocked("service_restart", "web-01") is True


def test_block_is_scoped_to_action_and_server(block_dir):
    block.block("service_restart", "web-01", reason="x")
    assert block.is_blocked("service_restart", "web-02") is False
    assert block.is_blocked("drop_caches", "web-01") is False


def test_unblock_key_removes(block_dir):
    block.block("service_restart", "web-01", reason="x")
    key = block.block_key("service_restart", "web-01")
    assert block.unblock_key(key) is True
    assert block.is_blocked("service_restart", "web-01") is False
    # Nothing left to remove → False.
    assert block.unblock_key(key) is False


def test_unblock_rejects_traversal_key(block_dir):
    assert block.unblock_key("../../etc/passwd") is False


def test_list_blocked_returns_records(block_dir):
    block.block("service_restart", "web-01", reason="restart failed")
    items = block.list_blocked()
    assert len(items) == 1
    rec = items[0]
    assert rec["action"] == "service_restart"
    assert rec["server"] == "web-01"
    assert rec["reason"] == "restart failed"
    assert rec["key"] == block.block_key("service_restart", "web-01")
    assert "blocked_at" in rec


def test_block_key_is_stable_and_distinct(block_dir):
    k1 = block.block_key("service_restart", "web-01")
    k2 = block.block_key("service_restart", "web-01")
    assert k1 == k2
    assert k1 != block.block_key("service_restart", "web-02")
