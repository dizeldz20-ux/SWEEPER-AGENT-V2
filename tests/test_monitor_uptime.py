"""Tests for uptime/boot_time monitor."""
import time
from unittest.mock import patch, mock_open

from ipracticom_sweeper.monitor.uptime import (
    format_uptime,
    get_boot_time,
    get_uptime_seconds,
    collect,
    evaluate,
)


def test_format_uptime_seconds():
    assert format_uptime(0) == "0s"
    assert format_uptime(45) == "45s"


def test_format_uptime_minutes():
    assert format_uptime(60) == "1m 0s"
    assert format_uptime(125) == "2m 5s"
    assert format_uptime(3599) == "59m 59s"


def test_format_uptime_hours():
    assert format_uptime(3600) == "1h 0m"
    assert format_uptime(7384) == "2h 3m"


def test_format_uptime_days():
    assert format_uptime(86400) == "1d 0h"
    assert format_uptime(90061) == "1d 1h"


def test_format_uptime_negative():
    # Defensive: negative means clock skew, don't crash
    assert format_uptime(-5) == "unknown"


def test_get_boot_time_parses_proc_stat():
    fake_proc = (
        "cpu  100 0 50 1000 5 0 0 0 0 0\n"
        "btime 1700000000\n"
        "intr 12345 0 0\n"
    )
    with patch("ipracticom_sweeper.monitor.uptime.Path.open", mock_open(read_data=fake_proc)):
        result = get_boot_time()
    assert result == 1700000000.0


def test_get_boot_time_missing_btime():
    fake_proc = "cpu  100 0 50 1000 5 0 0 0 0 0\nintr 12345 0 0\n"
    with patch("ipracticom_sweeper.monitor.uptime.Path.open", mock_open(read_data=fake_proc)):
        assert get_boot_time() is None


def test_get_boot_time_handles_oserror():
    def boom(*args, **kwargs):
        raise OSError("no /proc")
    with patch("ipracticom_sweeper.monitor.uptime.Path.open", boom):
        assert get_boot_time() is None


def test_get_uptime_seconds_calculation():
    boot = 1700000000.0
    fake_proc = f"btime {boot}\n"
    with patch("ipracticom_sweeper.monitor.uptime.Path.open", mock_open(read_data=fake_proc)):
        with patch("ipracticom_sweeper.monitor.uptime.time.time", return_value=boot + 3600):
            assert get_uptime_seconds() == 3600.0


def test_get_uptime_seconds_clamps_negative():
    # Clock went backwards — return 0, not negative
    boot = 1700000000.0
    fake_proc = f"btime {boot}\n"
    with patch("ipracticom_sweeper.monitor.uptime.Path.open", mock_open(read_data=fake_proc)):
        with patch("ipracticom_sweeper.monitor.uptime.time.time", return_value=boot - 100):
            assert get_uptime_seconds() == 0.0


def test_get_uptime_seconds_returns_none_when_missing():
    fake_proc = "cpu  100 0 50 1000 5 0 0 0 0 0\n"
    with patch("ipracticom_sweeper.monitor.uptime.Path.open", mock_open(read_data=fake_proc)):
        assert get_uptime_seconds() is None


def test_collect_returns_full_dict():
    boot = 1700000000.0
    fake_proc = f"btime {boot}\n"
    with patch("ipracticom_sweeper.monitor.uptime.Path.open", mock_open(read_data=fake_proc)):
        with patch("ipracticom_sweeper.monitor.uptime.time.time", return_value=boot + 7200):
            result = collect()
    assert result["boot_time"] == boot
    assert result["uptime_seconds"] == 7200.0
    assert result["uptime_human"] == "2h 0m"
    assert result["boot_time_iso"] is not None
    assert "T" in result["boot_time_iso"]  # ISO8601 has T separator
    assert result["collected_at"] == boot + 7200


def test_collect_handles_missing_boot_time():
    fake_proc = "cpu  100 0 50 1000 5 0 0 0 0 0\n"
    with patch("ipracticom_sweeper.monitor.uptime.Path.open", mock_open(read_data=fake_proc)):
        result = collect()
    assert result["boot_time"] is None
    assert result["uptime_seconds"] is None
    assert result["uptime_human"] == "unknown"


def test_evaluate_ok_for_long_uptime():
    values = {"uptime_seconds": 86400 * 7}  # 7 days
    assert evaluate(values, {}) == "ok"


def test_evaluate_warn_for_short_uptime():
    values = {"uptime_seconds": 120}  # 2 minutes
    assert evaluate(values, {}) == "warn"


def test_evaluate_crit_for_very_short_uptime():
    values = {"uptime_seconds": 30}  # 30 seconds
    assert evaluate(values, {}) == "crit"


def test_evaluate_uses_custom_thresholds():
    values = {"uptime_seconds": 500}
    rules = {"uptime": {"short_uptime_warn_seconds": 600, "short_uptime_crit_seconds": 200}}
    # 500 < 600 → warn
    assert evaluate(values, rules) == "warn"


def test_evaluate_ok_when_uptime_unknown():
    # Don't alert on unknown — better to be silent than to false-positive
    assert evaluate({"uptime_seconds": None}, {}) == "ok"


def test_evaluate_handles_missing_rules_key():
    # No rules dict at all — should fall back to defaults
    values = {"uptime_seconds": 30}
    assert evaluate(values, {}) == "crit"
