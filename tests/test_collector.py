"""Tests for the fleet collector loop and snapshot persistence."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ipracticom_sweeper.config import (
    Connector,
    add_connector,
    load_connectors,
    mark_connector_collected,
    mark_connector_error,
)
from ipracticom_sweeper.fleet import collector as coll


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))


def _add_named(name: str, region: str = "il-central-1") -> Connector:
    c = Connector(name=name, instance_id=f"i-{name}", region=region)
    add_connector(c)
    return c


def test_snapshots_dir_creates_under_state():
    d = coll.snapshots_dir()
    assert d.exists()
    assert d.is_dir()


def test_write_snapshot_then_load_roundtrip():
    _add_named("web1")
    path = coll.write_snapshot("web1", {"available": True, "data": {"cpu": 5}})
    assert path.exists()
    loaded = coll.load_snapshot("web1")
    assert loaded["name"] == "web1"
    assert loaded["snapshot"]["data"]["cpu"] == 5
    assert "collected_at" in loaded


def test_load_snapshot_missing_returns_none():
    assert coll.load_snapshot("nope") is None


def test_load_all_snapshots_sorted_by_name():
    _add_named("charlie")
    _add_named("alpha")
    _add_named("bravo")
    coll.write_snapshot("charlie", {"available": True})
    coll.write_snapshot("alpha", {"available": True})
    coll.write_snapshot("bravo", {"available": True})
    names = [s["name"] for s in coll.load_all_snapshots()]
    assert names == ["alpha", "bravo", "charlie"]


def test_load_all_snapshots_skips_corrupt_files():
    _add_named("good")
    coll.write_snapshot("good", {"available": True})
    # Write a file that's not valid JSON
    coll.snapshots_dir().joinpath("bad.json").write_text("not json")
    out = coll.load_all_snapshots()
    assert len(out) == 1
    assert out[0]["name"] == "good"


def test_collect_once_no_enabled_returns_empty():
    _add_named("disabled1")
    # Disable it
    from ipracticom_sweeper.config import update_connector
    update_connector("disabled1", enabled=False)
    # Mock the SSM connector to ensure it's NEVER called
    with patch("ipracticom_sweeper.fleet.collector.AwsSsmConnector") as mock_ssm:
        result = coll.collect_once()
    assert result == {}
    mock_ssm.assert_not_called()


def test_collect_once_calls_ssm_per_region():
    _add_named("us-east-host", region="us-east-1")
    _add_named("eu-west-host", region="eu-west-1")
    _add_named("il-host", region="il-central-1")

    def fake_collect_one(self, instance_id):
        snap = MagicMock()
        snap.instance_id = instance_id
        snap.available = True
        snap.reason = None
        snap.data = {"host": instance_id}
        snap.duration_ms = 100
        return snap

    with patch.object(coll.AwsSsmConnector, "__init__", return_value=None), \
         patch.object(coll.AwsSsmConnector, "collect_all", side_effect=lambda ids, **_kw: [
             MagicMock(instance_id=i, available=True, reason=None, data={"id": i}, duration_ms=50)
             for i in ids
         ]):
        result = coll.collect_once()

    assert set(result.keys()) == {"us-east-host", "eu-west-host", "il-host"}
    assert all(s["available"] for s in result.values())


def test_collect_once_records_success_in_state():
    _add_named("web1")
    with patch.object(coll.AwsSsmConnector, "__init__", return_value=None), \
         patch.object(coll.AwsSsmConnector, "collect_all", side_effect=lambda ids, **_kw: [
             MagicMock(instance_id=ids[0], available=True, reason=None, data={"ok": True}, duration_ms=50)
         ]):
        coll.collect_once()
    c = next(c for c in load_connectors() if c.name == "web1")
    assert c.last_collected_at is not None
    assert c.last_error is None


def test_collect_once_records_failure_in_state():
    _add_named("web1")
    with patch.object(coll.AwsSsmConnector, "__init__", return_value=None), \
         patch.object(coll.AwsSsmConnector, "collect_all", side_effect=lambda ids, **_kw: [
             MagicMock(instance_id=ids[0], available=False, reason="SSM timeout", data=None, duration_ms=50)
         ]):
        coll.collect_once()
    c = next(c for c in load_connectors() if c.name == "web1")
    assert c.last_error == "SSM timeout"


def test_collect_once_handles_region_init_failure():
    _add_named("web1", region="us-east-1")
    with patch.object(coll.AwsSsmConnector, "__init__", side_effect=coll.SsmError("no creds")):
        result = coll.collect_once()
    assert result["web1"]["available"] is False
    c = next(c for c in load_connectors() if c.name == "web1")
    assert "no creds" in c.last_error


def test_start_stop_loop_is_idempotent():
    # Don't actually wait 5 minutes — patch the interval to 0 and let it tick once.
    import threading
    original_interval = coll.COLLECT_INTERVAL_SEC
    coll.COLLECT_INTERVAL_SEC = 0
    try:
        coll.start_collector_loop()
        coll.start_collector_loop()  # second call should be a no-op
        assert coll._loop_thread is not None
        assert coll._loop_thread.is_alive()
    finally:
        coll.stop_collector_loop(timeout=2.0)
        coll.COLLECT_INTERVAL_SEC = original_interval


def test_snapshot_writes_are_atomic(tmp_path):
    """write_snapshot should leave no .tmp file behind."""
    coll.write_snapshot("web1", {"available": True})
    leftover = list(coll.snapshots_dir().glob("*.json.tmp"))
    assert leftover == []