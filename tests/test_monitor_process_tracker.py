"""Tests for process tracker (top resource hogs + service restarts)."""
from __future__ import annotations
from unittest.mock import patch, MagicMock, mock_open
from ipracticom_sweeper.monitor.process_tracker import (
    get_top_processes,
    parse_journalctl_restarts,
    TopProcess,
    ServiceRestart,
)


def test_top_process_has_required_fields():
    """TopProcess carries pid/name/cpu/mem/runtime."""
    proc = TopProcess(pid=1234, name="nginx", cpu_percent=12.5, mem_percent=4.2, runtime_seconds=3600)
    assert proc.pid == 1234
    assert proc.name == "nginx"
    assert proc.cpu_percent == 12.5
    assert proc.mem_percent == 4.2
    assert proc.runtime_seconds == 3600


def test_parse_journalctl_restarts_aggregates():
    """Multiple restarts of the same service are aggregated into one entry."""
    output = """\
Jun 29 10:00:01 host systemd[1]: Started nginx.service.
Jun 29 10:05:01 host systemd[1]: Started nginx.service.
Jun 29 10:10:01 host systemd[1]: Started nginx.service.
Jun 29 10:00:01 host systemd[1]: Started mysql.service.
"""
    restarts = parse_journalctl_restarts(output, window_minutes=60)
    by_service = {r.service: r for r in restarts}
    assert by_service["nginx.service"].count == 3
    assert by_service["mysql.service"].count == 1


def test_parse_journalctl_handles_empty():
    """Empty output = no restarts."""
    assert parse_journalctl_restarts("", window_minutes=60) == []


def test_parse_journalctl_filters_irrelevant_lines():
    """Lines that aren't service-start events are ignored."""
    output = """\
Jun 29 10:00:01 host cron[1234]: (root) CMD (test)
Jun 29 10:00:02 host kernel: normal kernel message
"""
    restarts = parse_journalctl_restarts(output, window_minutes=60)
    assert restarts == []


def test_get_top_processes_returns_n_limited():
    """Should return at most N processes sorted by CPU+MEM."""
    fake_processes = [
        {"pid": 1, "name": "a", "cpu_percent": 1.0, "mem_percent": 1.0, "runtime_seconds": 100},
        {"pid": 2, "name": "b", "cpu_percent": 50.0, "mem_percent": 1.0, "runtime_seconds": 100},
        {"pid": 3, "name": "c", "cpu_percent": 1.0, "mem_percent": 50.0, "runtime_seconds": 100},
    ]
    with patch("ipracticom_sweeper.monitor.process_tracker._scan_processes", return_value=fake_processes):
        result = get_top_processes(top_n=2)
    assert len(result) == 2
