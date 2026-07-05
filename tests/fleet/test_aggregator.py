"""Tests for fleet aggregator."""
import time
from ipracticom_sweeper.fleet import aggregate


def _snap(host, defcon, problems=0):
    return {
        "server": host,
        "defcon": defcon,
        "defcon_label": {1: "black", 2: "red", 3: "orange", 4: "yellow", 5: "green"}.get(defcon, "green"),
        "problems_found": problems,
        "ts": time.time(),
        "modules": {"cpu": "ok", "memory": "ok"},
    }


def test_aggregate_empty():
    s = aggregate([])
    assert s.total_hosts == 0
    assert s.healthy == 0
    assert s.overall_defcon == 5


def test_aggregate_all_healthy():
    s = aggregate([_snap("h1", 5), _snap("h2", 5)])
    assert s.total_hosts == 2
    assert s.healthy == 2
    assert s.warning == 0
    assert s.critical == 0
    assert s.overall_defcon == 5


def test_aggregate_with_warning():
    s = aggregate([_snap("h1", 5), _snap("h2", 4)])
    assert s.warning == 1
    assert s.overall_defcon == 4


def test_aggregate_with_critical():
    s = aggregate([_snap("h1", 5), _snap("h2", 2), _snap("h3", 4)])
    assert s.critical == 1
    assert s.warning == 1
    assert s.overall_defcon == 2  # any critical → DEFCON 2


def test_aggregate_sorted_worst_first():
    s = aggregate([_snap("h1", 5), _snap("h2", 2), _snap("h3", 4)])
    assert s.hosts[0].host_id == "h2"  # critical first
    assert s.hosts[1].host_id == "h3"  # then warning
    assert s.hosts[2].host_id == "h1"  # then healthy


def test_aggregate_modules_preserved():
    s = aggregate([_snap("h1", 4)])
    assert s.hosts[0].modules == {"cpu": "ok", "memory": "ok"}
