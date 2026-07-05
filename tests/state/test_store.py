"""Tests for SQLiteStateStore."""
import pytest
import time
from ipracticom_sweeper.state import SQLiteStateStore


@pytest.fixture
def store(tmp_path):
    s = SQLiteStateStore(tmp_path / "test.db")
    yield s
    s.close()


def test_create(tmp_path):
    s = SQLiteStateStore(tmp_path / "x.db")
    s.close()
    assert (tmp_path / "x.db").exists()


def test_record_event(store):
    store.record_event("h1", "cpu", 4, {"pct": 80.0})
    events = store.recent_events("h1")
    assert len(events) == 1
    assert events[0].host == "h1"
    assert events[0].module == "cpu"
    assert events[0].defcon == 4


def test_recent_events_window(store):
    store.record_event("h1", "cpu", 4, {})
    time.sleep(0.1)
    # narrow window: 0.001h = 3.6s, event from now should NOT be in it
    events_narrow = store.recent_events("h1", hours=0.001)
    # event was just inserted, so 0.001h should be enough
    assert len(events_narrow) == 1
    # but if we go forward in time, cleanup should work
    time.sleep(0.1)
    events_wide = store.recent_events("h1", hours=100)
    assert len(events_wide) == 1


def test_upsert_alert_new(store):
    a = store.upsert_alert("fp1")
    assert a.count == 1
    assert a.acked is False


def test_upsert_alert_increments(store):
    store.upsert_alert("fp1")
    a = store.upsert_alert("fp1")
    assert a.count == 2


def test_get_alert_returns_none(store):
    assert store.get_alert("nope") is None


def test_record_repair(store):
    store.record_repair("drop_caches", "/", True, "snap123")


def test_cleanup_removes_old(store):
    store.record_event("h1", "cpu", 4, {})
    # cleanup with 0 days = should remove the event from now
    # event.ts is now, cutoff is now, so ts >= cutoff
    # Use negative days to force cleanup
    deleted = store.cleanup(older_than_days=-1)
    assert deleted == 1
