"""Tests for the fleet aggregator adapter (ssm_to_aggregator_format)."""
from __future__ import annotations

import time

import pytest

from ipracticom_sweeper.fleet import aggregate, ssm_to_aggregator_format
from ipracticom_sweeper.fleet.aws_connector import HostSnapshot


def _snap(available: bool, data: dict | None = None, reason: str = "") -> HostSnapshot:
    return HostSnapshot(
        instance_id="i-test", available=available, reason=reason, data=data or {},
        duration_ms=100,
    )


# --- ssm_to_aggregator_format: happy paths -----------------------------

def test_healthy_host_maps_to_defcon_5_green():
    snap = _snap(True, {
        "host": "web1", "load": {"5m": 0.5}, "memory": {"used_percent": 40},
        "disk": {"used_percent": 50}, "failed_units": [],
    })
    out = ssm_to_aggregator_format("web1", snap)
    assert out["defcon"] == 5
    assert out["defcon_label"] == "green"
    assert out["problems_found"] == 0
    assert out["modules"]["cpu"] == "ok"
    assert out["modules"]["memory"] == "ok"
    assert out["modules"]["disk"] == "ok"
    assert out["modules"]["services"] == "ok"
    assert out["_raw"] == snap.data  # raw kept for modal


def test_high_load_maps_to_defcon_4_yellow():
    snap = _snap(True, {
        "load": {"5m": 5.0}, "memory": {"used_percent": 50},
        "disk": {"used_percent": 50}, "failed_units": [],
    })
    out = ssm_to_aggregator_format("h", snap)
    assert out["defcon"] == 4
    assert out["modules"]["cpu"] == "warn"


def test_high_memory_and_disk_maps_to_defcon_2_red():
    snap = _snap(True, {
        "load": {"5m": 1.0}, "memory": {"used_percent": 96},
        "disk": {"used_percent": 97}, "failed_units": [],
    })
    out = ssm_to_aggregator_format("h", snap)
    assert out["defcon"] == 2
    assert out["modules"]["memory"] == "crit"
    assert out["modules"]["disk"] == "crit"


def test_failed_services_maps_to_crit():
    snap = _snap(True, {
        "load": {"5m": 0.5}, "memory": {"used_percent": 30},
        "disk": {"used_percent": 30}, "failed_units": ["nginx.service"],
    })
    out = ssm_to_aggregator_format("h", snap)
    assert out["modules"]["services"] == "crit"
    assert out["defcon"] == 2


def test_unavailable_maps_to_defcon_1_with_reason():
    snap = _snap(False, reason="SSM timeout")
    out = ssm_to_aggregator_format("h", snap)
    assert out["defcon"] == 1
    assert out["defcon_label"] == "red"
    assert out["_reason"] == "SSM timeout"
    assert out["modules"] == {"ssm": "crit"}


# --- ssm_to_aggregator_format: defensive parsing -----------------------

def test_missing_load_defaults_to_zero():
    snap = _snap(True, {"memory": {"used_percent": 50}, "disk": {"used_percent": 50}})
    out = ssm_to_aggregator_format("h", snap)
    assert out["modules"]["cpu"] == "ok"


def test_none_values_dont_crash():
    snap = _snap(True, {
        "load": None, "memory": None, "disk": None, "failed_units": None,
    })
    out = ssm_to_aggregator_format("h", snap)
    assert out["defcon"] == 5  # all defaults → healthy


def test_empty_data_maps_to_healthy():
    out = ssm_to_aggregator_format("h", _snap(True, {}))
    assert out["defcon"] == 5


def test_iso_timestamp_falls_back_to_now():
    snap = _snap(True, {"collected_at": "2026-06-29T07:00:00Z"})
    out = ssm_to_aggregator_format("h", snap)
    # Should be roughly "now" (within last 5 sec) — ISO string can't be parsed to float
    assert 0 < time.time() - out["ts"] < 5


def test_unix_timestamp_preserved():
    snap = _snap(True, {"ts": 1234567890.0})
    out = ssm_to_aggregator_format("h", snap)
    assert out["ts"] == 1234567890.0


# --- aggregate() integration ---------------------------------------------

def test_aggregate_consumes_adapted_snapshots():
    snapshots = [
        ssm_to_aggregator_format("healthy", _snap(True, {
            "load": {"5m": 0.5}, "memory": {"used_percent": 30},
            "disk": {"used_percent": 40}, "failed_units": [],
        })),
        ssm_to_aggregator_format("broken", _snap(False, reason="timeout")),
    ]
    summary = aggregate(snapshots)
    assert summary.total_hosts == 2
    assert summary.healthy == 1
    assert summary.critical == 1
    # Sorted worst-first
    assert summary.hosts[0].host_id == "broken"
    assert summary.hosts[1].host_id == "healthy"