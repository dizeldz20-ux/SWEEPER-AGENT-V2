"""Tests for local time-series SQLite storage."""
from __future__ import annotations
import tempfile
from pathlib import Path
from ipracticom_sweeper.storage.timeseries import TimeSeriesDB


def test_timeseries_write_and_read_back():
    """Write a metric, read it back."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = TimeSeriesDB(db_path)
        db.write(host="server1", metric="cpu.load_5min", value=2.5)
        db.write(host="server1", metric="cpu.load_5min", value=3.0)
        rows = db.query(host="server1", metric="cpu.load_5min", limit=10)
        assert len(rows) == 2
        values = [r["value"] for r in rows]
        assert 2.5 in values
        assert 3.0 in values


def test_timeseries_query_filters_by_host():
    """Different hosts are isolated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = TimeSeriesDB(Path(tmpdir) / "test.db")
        db.write(host="server1", metric="cpu", value=1.0)
        db.write(host="server2", metric="cpu", value=2.0)
        rows_s1 = db.query(host="server1", metric="cpu", limit=10)
        rows_s2 = db.query(host="server2", metric="cpu", limit=10)
        assert len(rows_s1) == 1
        assert rows_s1[0]["value"] == 1.0
        assert len(rows_s2) == 1
        assert rows_s2[0]["value"] == 2.0


def test_timeseries_query_filters_by_metric():
    """Different metrics on same host are isolated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = TimeSeriesDB(Path(tmpdir) / "test.db")
        db.write(host="s1", metric="cpu", value=1.0)
        db.write(host="s1", metric="memory", value=50.0)
        cpu = db.query(host="s1", metric="cpu")
        mem = db.query(host="s1", metric="memory")
        assert len(cpu) == 1 and cpu[0]["value"] == 1.0
        assert len(mem) == 1 and mem[0]["value"] == 50.0


def test_timeseries_retention_prunes_old_data():
    """Data older than retention_days is pruned."""
    import time
    with tempfile.TemporaryDirectory() as tmpdir:
        db = TimeSeriesDB(Path(tmpdir) / "test.db", retention_days=1)
        # Manually insert an old record
        old_ts = int(time.time()) - (10 * 86400)  # 10 days ago
        db._conn.execute(
            "INSERT INTO metrics (host, metric, ts, value) VALUES (?, ?, ?, ?)",
            ("s1", "cpu", old_ts, 1.0),
        )
        db._conn.commit()
        db.write(host="s1", metric="cpu", value=2.0)  # recent

        # Prune with 1-day retention
        db.prune_old_data()
        rows = db.query(host="s1", metric="cpu", limit=10)
        # Only the recent one should remain
        assert len(rows) == 1
        assert rows[0]["value"] == 2.0


def test_timeseries_query_respects_limit():
    """Query with limit returns at most N rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = TimeSeriesDB(Path(tmpdir) / "test.db")
        for i in range(20):
            db.write(host="s1", metric="cpu", value=float(i))
        rows = db.query(host="s1", metric="cpu", limit=5)
        assert len(rows) == 5
