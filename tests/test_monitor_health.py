"""Tests for agent self-health / heartbeat."""
import json
import time
from pathlib import Path

import pytest

from ipracticom_sweeper.monitor.health import (
    HealthStatus,
    _heartbeat_path,
    check_health,
    collect,
    evaluate,
    record_run,
)


def test_record_run_writes_file(tmp_path, monkeypatch):
    """record_run should write a heartbeat file we can read back."""
    monkeypatch.setenv("HOME", str(tmp_path))  # force fallback path
    # Override _heartbeat_path to use tmp_path
    fake_file = tmp_path / "heartbeat.json"
    monkeypatch.setattr(
        "ipracticom_sweeper.monitor.health._heartbeat_path", lambda: fake_file
    )
    p = record_run(defcon=4, problems_found=2, repairs_attempted=1)
    assert p == fake_file
    assert fake_file.exists()
    data = json.loads(fake_file.read_text())
    assert data["defcon"] == 4
    assert data["problems_found"] == 2
    assert data["repairs_attempted"] == 1
    assert "ts" in data
    assert "ts_iso" in data


def test_check_health_fresh(tmp_path):
    fake = tmp_path / "hb.json"
    fake.write_text(json.dumps({
        "ts": time.time() - 60,
        "ts_iso": "2024-01-01T00:00:00Z",
        "defcon": 5,
    }))
    status = check_health(expected_interval_seconds=300, path=fake)
    assert status.state == "fresh"
    assert status.last_defcon == 5
    assert status.age_seconds is not None
    assert 55 <= status.age_seconds <= 65
    assert status.is_healthy is True


def test_check_health_stale(tmp_path):
    fake = tmp_path / "hb.json"
    fake.write_text(json.dumps({"ts": time.time() - 1800, "defcon": 4}))
    status = check_health(expected_interval_seconds=300, path=fake)  # max 600s
    assert status.state == "stale"
    assert status.is_healthy is False
    assert "1800" in (status.reason or "")


def test_check_health_missing(tmp_path):
    fake = tmp_path / "does_not_exist.json"
    status = check_health(path=fake)
    assert status.state == "missing"
    assert status.is_healthy is False
    assert "never run" in (status.reason or "")


def test_check_health_corrupt_json(tmp_path):
    fake = tmp_path / "hb.json"
    fake.write_text("this is not json")
    status = check_health(path=fake)
    assert status.state == "corrupt"
    assert status.is_healthy is False


def test_check_health_corrupt_ts_field(tmp_path):
    fake = tmp_path / "hb.json"
    fake.write_text(json.dumps({"ts": "yesterday"}))
    status = check_health(path=fake)
    assert status.state == "corrupt"
    assert "ts" in (status.reason or "")


def test_check_health_clock_skew_handled(tmp_path):
    """If the recorded ts is in the future (clock went back), don't false-alarm."""
    fake = tmp_path / "hb.json"
    fake.write_text(json.dumps({"ts": time.time() + 3600, "defcon": 5}))
    status = check_health(path=fake)
    assert status.state == "fresh"
    assert "clock skew" in (status.reason or "")


def test_check_health_at_exact_threshold(tmp_path):
    """At exactly the max age, we're still fresh (boundary check)."""
    fake = tmp_path / "hb.json"
    # Just under the 2x threshold
    fake.write_text(json.dumps({"ts": time.time() - 590}))
    status = check_health(expected_interval_seconds=300, path=fake)  # max 600
    assert status.state == "fresh"


def test_health_status_to_dict():
    s = HealthStatus(
        state="fresh", last_run_ts=100.0, last_run_iso="2024-01-01",
        last_defcon=5, age_seconds=10.0, expected_max_age=600.0,
    )
    d = s.to_dict()
    assert d["state"] == "fresh"
    assert d["is_healthy"] is True
    assert d["expected_max_age"] == 600.0


def test_health_status_is_healthy_property():
    assert HealthStatus(state="fresh", last_run_ts=0, last_run_iso="",
                        last_defcon=5, age_seconds=1, expected_max_age=600).is_healthy is True
    assert HealthStatus(state="stale", last_run_ts=0, last_run_iso="",
                        last_defcon=5, age_seconds=999, expected_max_age=600).is_healthy is False
    assert HealthStatus(state="missing", last_run_ts=None, last_run_iso=None,
                        last_defcon=None, age_seconds=None, expected_max_age=600).is_healthy is False


def test_evaluate_fresh_is_ok():
    assert evaluate({"state": "fresh", "age_seconds": 10}, {}) == "ok"


def test_evaluate_missing_is_warn():
    """Missing heartbeat = warn, not crit (could be disk permissions)."""
    assert evaluate({"state": "missing", "age_seconds": None}, {}) == "warn"


def test_evaluate_corrupt_is_warn():
    assert evaluate({"state": "corrupt", "age_seconds": None}, {}) == "warn"


def test_evaluate_stale_warn_threshold():
    assert evaluate({"state": "stale", "age_seconds": 700}, {}) == "warn"


def test_evaluate_stale_crit_threshold():
    assert evaluate({"state": "stale", "age_seconds": 2000}, {}) == "crit"


def test_evaluate_stale_short_age_is_ok():
    # If state is "stale" but age is under threshold, still ok
    assert evaluate({"state": "stale", "age_seconds": 30}, {}) == "ok"


def test_evaluate_uses_custom_thresholds():
    values = {"state": "stale", "age_seconds": 100}
    rules = {"health": {"stale_warn_seconds": 50, "stale_crit_seconds": 1000}}
    # 100 > 50 (warn) but < 1000 (crit)
    assert evaluate(values, rules) == "warn"


def test_collect_returns_health_snapshot(tmp_path, monkeypatch):
    fake = tmp_path / "hb.json"
    fake.write_text(json.dumps({"ts": time.time() - 30, "defcon": 3, "ts_iso": "2024-01-01"}))
    snap = collect(path=fake)
    assert snap["state"] == "fresh"
    assert snap["last_defcon"] == 3
    assert snap["is_healthy"] is True
    assert "collected_at" in snap


def test_collect_handles_missing_file(tmp_path):
    snap = collect(path=tmp_path / "missing.json")
    assert snap["state"] == "missing"
    assert snap["is_healthy"] is False
    assert snap["last_run_ts"] is None


def test_record_then_check_round_trip(tmp_path, monkeypatch):
    """Full cycle: record a run, then check that health is fresh."""
    fake = tmp_path / "hb.json"
    monkeypatch.setattr(
        "ipracticom_sweeper.monitor.health._heartbeat_path", lambda: fake
    )
    record_run(defcon=5, problems_found=0, repairs_attempted=0)
    status = check_health(expected_interval_seconds=300, path=fake)
    assert status.state == "fresh"
    assert status.last_defcon == 5


def test_record_run_extra_field(tmp_path, monkeypatch):
    fake = tmp_path / "hb.json"
    monkeypatch.setattr(
        "ipracticom_sweeper.monitor.health._heartbeat_path", lambda: fake
    )
    record_run(defcon=2, extra={"reboot": True, "host": "web-01"})
    data = json.loads(fake.read_text())
    assert data["extra"]["reboot"] is True
    assert data["extra"]["host"] == "web-01"


def test_heartbeat_path_fallback_to_home(tmp_path, monkeypatch):
    """If /var/lib path is not writable, fall back to ~/.ipracticom-sweeper."""
    from ipracticom_sweeper.monitor import health
    # Force mkdir to fail only for the SYSTEM path
    real_mkdir = health.Path.mkdir
    def fake_mkdir(self, *args, **kwargs):
        if str(self).startswith("/var/lib"):
            raise PermissionError("no access to /var/lib")
        return real_mkdir(self, *args, **kwargs)
    monkeypatch.setattr(health.Path, "mkdir", fake_mkdir)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = health._heartbeat_path()
    assert p == tmp_path / ".ipracticom-sweeper" / "heartbeat.json"
