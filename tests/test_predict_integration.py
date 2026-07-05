"""Tests for predict-integration that runs after the time-series store."""
from __future__ import annotations
import tempfile
import time
from pathlib import Path

from ipracticom_sweeper.predict.integration import (
    collect_predictions,
    Prediction,
)


def test_collect_predictions_empty_db():
    """No data = empty list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "metrics.db"
        result = collect_predictions(db_path, host="nohost")
        assert result == []


def test_collect_predictions_includes_disk_metric():
    """Disk usage trending toward 100% should produce a prediction."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "metrics.db"
        from ipracticom_sweeper.storage import TimeSeriesDB
        db = TimeSeriesDB(db_path)
        # Simulate 5 days of disk growing from 50% to 70% (4% / day)
        for day in range(5):
            ts = int(time.time()) - (5 - day) * 86400
            db.write(host="h1", metric="disk.used_percent./", value=50.0 + day * 5, ts=ts)
        db.close()

        result = collect_predictions(db_path, host="h1", thresholds={
            "disk.used_percent./": 95.0,
        })
        assert len(result) >= 1
        # Find the disk prediction
        disk = next((p for p in result if "disk" in p.metric), None)
        assert disk is not None
        assert disk.threshold == 95.0
        # Slope is positive (growing) and we're below threshold
        assert disk.slope > 0
        assert disk.current_value < 95.0


def test_collect_predictions_already_past_threshold_returns_none():
    """If value already past threshold, predicted_time_hours is None."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "metrics.db"
        from ipracticom_sweeper.storage import TimeSeriesDB
        db = TimeSeriesDB(db_path)
        for day in range(5):
            ts = int(time.time()) - (5 - day) * 86400
            db.write(host="h1", metric="memory.used_percent", value=99.0, ts=ts)
        db.close()

        result = collect_predictions(db_path, host="h1", thresholds={
            "memory.used_percent": 95.0,
        })
        # value is already past threshold — should be filtered or have predicted=None
        for p in result:
            if p.metric == "memory.used_percent":
                assert p.predicted_time_hours is None
