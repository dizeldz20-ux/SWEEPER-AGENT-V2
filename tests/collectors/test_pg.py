"""Tests for PG collector (psql calls mocked)."""
import pytest
from unittest.mock import patch, MagicMock
from ipracticom_sweeper.collectors import collect_pg_stats, defcon_from_stats, PGStats


def _mock_run(stdout, returncode=0, stderr=""):
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def test_collect_pg_stats_success():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            _mock_run("5|12|100"),  # active|idle|max_conn
            _mock_run("3|98.5"),  # db_count|cache_hit
        ]
        stats = collect_pg_stats()
    assert stats.reachable is True
    assert stats.active_connections == 5
    assert stats.idle_connections == 12
    assert stats.max_connections == 100
    assert stats.database_count == 3
    assert stats.cache_hit_ratio == 98.5


def test_collect_pg_stats_unreachable():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_run("", returncode=1, stderr="connection refused")
        stats = collect_pg_stats()
    assert stats.reachable is False
    assert "connection refused" in stats.error


def test_collect_pg_stats_timeout():
    import subprocess
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="psql", timeout=5)
        stats = collect_pg_stats()
    assert stats.reachable is False
    assert "timeout" in stats.error


def test_collect_pg_stats_unexpected_format():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_run("garbled output")
        stats = collect_pg_stats()
    assert stats.reachable is False
    assert "unexpected" in stats.error


def test_defcon_unreachable():
    stats = PGStats(0, 0, 0, 0, 0.0, reachable=False)
    assert defcon_from_stats(stats) == 1


def test_defcon_high_connection_usage():
    stats = PGStats(80, 20, 100, 1, 99.0, reachable=True)
    assert defcon_from_stats(stats) == 2  # >90%


def test_defcon_moderate_connection_usage():
    stats = PGStats(60, 20, 100, 1, 99.0, reachable=True)
    assert defcon_from_stats(stats) == 3  # >75%


def test_defcon_low_cache_hit():
    stats = PGStats(10, 20, 100, 1, 80.0, reachable=True)
    assert defcon_from_stats(stats) == 4  # cache < 90


def test_defcon_all_good():
    stats = PGStats(10, 20, 100, 1, 98.0, reachable=True)
    assert defcon_from_stats(stats) == 5
