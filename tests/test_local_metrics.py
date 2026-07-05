"""Tests for the local psutil metrics collector (v0.4.4)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ipracticom_sweeper.agent_api import create_app


@pytest.fixture
def client():
    """A Flask test client with auth disabled (token empty = OPEN mode)."""
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------- collect_local_metrics ----------------------------


def test_collect_local_metrics_returns_expected_keys():
    """The collector must return a dict with cpu/memory/disk/network/uptime/booted_at."""
    from ipracticom_sweeper.monitor.health import collect_local_metrics

    m = collect_local_metrics()
    assert "cpu" in m
    assert "memory" in m
    assert "disk" in m
    assert "network" in m
    assert "uptime_seconds" in m
    assert "booted_at" in m


def test_collect_local_metrics_cpu_has_percent_and_count():
    """cpu block must include a percent value and a core count."""
    from ipracticom_sweeper.monitor.health import collect_local_metrics

    cpu = collect_local_metrics()["cpu"]
    assert "percent" in cpu
    assert "cores" in cpu
    assert isinstance(cpu["percent"], (int, float))
    assert isinstance(cpu["cores"], int)
    assert 0.0 <= cpu["percent"] <= 100.0
    assert cpu["cores"] >= 1


def test_collect_local_metrics_memory_has_percent_and_absolute():
    """memory block: percent + used_mb + total_mb."""
    from ipracticom_sweeper.monitor.health import collect_local_metrics

    mem = collect_local_metrics()["memory"]
    assert "percent" in mem
    assert "used_mb" in mem
    assert "total_mb" in mem
    assert isinstance(mem["total_mb"], (int, float))
    assert mem["total_mb"] > 0
    assert 0.0 <= mem["percent"] <= 100.0


def test_collect_local_metrics_disk_has_percent_and_absolute():
    """disk block: percent + used_gb + total_gb."""
    from ipracticom_sweeper.monitor.health import collect_local_metrics

    disk = collect_local_metrics()["disk"]
    assert "percent" in disk
    assert "used_gb" in disk
    assert "total_gb" in disk
    assert disk["total_gb"] > 0
    assert 0.0 <= disk["percent"] <= 100.0


def test_collect_local_metrics_network_has_counters():
    """network block: bytes_sent + bytes_recv."""
    from ipracticom_sweeper.monitor.health import collect_local_metrics

    net = collect_local_metrics()["network"]
    assert "bytes_sent" in net
    assert "bytes_recv" in net
    assert net["bytes_sent"] >= 0
    assert net["bytes_recv"] >= 0


def test_collect_local_metrics_uptime_is_positive():
    """uptime_seconds must be > 0 on a running host."""
    from ipracticom_sweeper.monitor.health import collect_local_metrics

    m = collect_local_metrics()
    assert m["uptime_seconds"] > 0
    assert isinstance(m["booted_at"], str)


def test_collect_local_metrics_survives_psutil_failure():
    """If psutil raises, the collector must return a minimal error dict (not crash)."""
    from ipracticom_sweeper.monitor.health import collect_local_metrics

    with patch("psutil.cpu_percent", side_effect=OSError("boom")):
        m = collect_local_metrics()
    # When broken, we still get a dict with an error marker.
    assert "error" in m
    assert m["error"]  # truthy


# ---------------------------- record_run includes extra metrics ----------------------------


def test_record_run_writes_extra_metrics_to_heartbeat(tmp_path, monkeypatch):
    """record_run must persist the local psutil snapshot into heartbeat.extra."""
    from ipracticom_sweeper.monitor.health import record_run, _heartbeat_path

    # Force heartbeat to land under tmp_path.
    monkeypatch.setattr(
        "ipracticom_sweeper.monitor.health.SYSTEM_HEARTBEAT_DIR", tmp_path
    )
    monkeypatch.setattr(
        "ipracticom_sweeper.monitor.health.SYSTEM_HEARTBEAT_FILE",
        tmp_path / "heartbeat.json",
    )

    record_run(defcon=5, problems_found=0, repairs_attempted=0)
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert "extra" in hb
    # extra should now contain cpu/memory/disk/network blocks.
    assert "cpu" in hb["extra"]
    assert "memory" in hb["extra"]
    assert "disk" in hb["extra"]


# ---------------------------- /api/fleet/local surfaces metrics ----------------------------


def test_fleet_local_returns_extra_metrics(client, tmp_path, monkeypatch):
    """/api/fleet/local must surface the extra metrics block (cpu/memory/disk/etc)."""
    heartbeat = {
        "ts": 1782761586.0,
        "ts_iso": "2026-06-29T19:33:06+00:00",
        "defcon": 4,
        "problems_found": 1,
        "repairs_attempted": 0,
        "extra": {
            "cpu": {"percent": 19.7, "cores": 4},
            "memory": {"percent": 16.5, "used_mb": 3400, "total_mb": 20400},
            "disk": {"percent": 55.0, "used_gb": 139.0, "total_gb": 253.0},
            "network": {"bytes_sent": 100, "bytes_recv": 200},
            "uptime_seconds": 60000,
            "booted_at": "2026-06-29T11:57:00+00:00",
        },
    }
    (tmp_path / "heartbeat.json").write_text(json.dumps(heartbeat))
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))

    r = client.get("/api/fleet/local")
    assert r.status_code == 200
    body = r.get_json()
    assert body["name"] == "local"
    assert "extra" in body
    extra = body["extra"]
    assert extra["cpu"]["percent"] == 19.7
    assert extra["cpu"]["cores"] == 4
    assert extra["memory"]["percent"] == 16.5
    assert extra["memory"]["total_mb"] == 20400
    assert extra["disk"]["percent"] == 55.0
    assert extra["disk"]["total_gb"] == 253.0