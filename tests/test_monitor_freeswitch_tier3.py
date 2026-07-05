"""FreeSWITCH Tier 3 (FS-10..FS-15) tests — v0.5.0 slice 2.3.

All subprocess / fs_cli / shutil / os calls are mocked.
"""
import time
from unittest.mock import patch, MagicMock

import pytest

from ipracticom_sweeper.monitor import freeswitch as fs
from ipracticom_sweeper.monitor.freeswitch import (
    check_fs10_cli_latency,
    check_fs11_active_calls,
    check_fs12_active_channels,
    check_fs13_log_disk_usage,
    check_fs14_config_drift_days,
    check_fs15_baseline_calls_per_hour,
    collect_operational,
    evaluate_operational,
)


# --- FS-10 (CLI latency) ---------------------------------------------------


def test_fs10_ok_when_fast():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "OK", ""),
    ):
        out = check_fs10_cli_latency(warn_ms=500, crit_ms=2000)
    assert out["status"] == "ok"
    assert out["values"]["fs10_elapsed_ms"] is not None
    assert out["values"]["fs10_elapsed_ms"] < 500


def test_fs10_warn_when_slow():
    """Simulate slow CLI by mocking `_run` to take time."""
    def slow_run(*args, **kwargs):
        time.sleep(0.6)
        return (0, "OK", "")

    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        side_effect=slow_run,
    ):
        out = check_fs10_cli_latency(warn_ms=500, crit_ms=2000)
    assert out["status"] == "warn"
    assert out["values"]["fs10_elapsed_ms"] >= 500


def test_fs10_crit_when_very_slow():
    def slow_run(*args, **kwargs):
        time.sleep(2.1)
        return (0, "OK", "")

    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        side_effect=slow_run,
    ):
        out = check_fs10_cli_latency(warn_ms=500, crit_ms=2000)
    assert out["status"] == "crit"
    assert out["values"]["fs10_elapsed_ms"] >= 2000


def test_fs10_warn_when_cli_missing():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value=None,
    ):
        out = check_fs10_cli_latency()
    assert out["status"] == "warn"
    assert "PATH" in out["values"]["fs10_reason"]


def test_fs10_warn_when_cli_errors():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(1, "", "auth refused"),
    ):
        out = check_fs10_cli_latency()
    assert out["status"] == "warn"
    assert out["values"]["fs10_reason"] == "cli error"


# --- FS-11 / FS-12 (active calls + channels) ------------------------------


def test_fs11_ok_when_low():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "5 entries", ""),
    ):
        out = check_fs11_active_calls()
    assert out["status"] == "ok"
    assert out["values"]["fs11_active_calls"] == 5


def test_fs11_warn_when_at_warn_threshold():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "120 entries", ""),
    ):
        out = check_fs11_active_calls(warn=100, crit=500)
    assert out["status"] == "warn"


def test_fs11_crit_when_over_crit_threshold():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "600 entries", ""),
    ):
        out = check_fs11_active_calls(warn=100, crit=500)
    assert out["status"] == "crit"


def test_fs11_warn_when_cli_fails():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(1, "", "lost connection"),
    ):
        out = check_fs11_active_calls()
    assert out["status"] == "warn"


def test_fs12_ok_when_low():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "20 entries", ""),
    ):
        out = check_fs12_active_channels()
    assert out["status"] == "ok"
    assert out["values"]["fs12_active_channels"] == 20


def test_fs12_warn_when_at_threshold():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "250 entries", ""),
    ):
        out = check_fs12_active_channels(warn=200, crit=1000)
    assert out["status"] == "warn"


def test_fs12_crit_when_over_crit():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "1500 entries", ""),
    ):
        out = check_fs12_active_channels(warn=200, crit=1000)
    assert out["status"] == "crit"


# --- FS-13 (log disk usage) ----------------------------------------------


def test_fs13_ok_when_low():
    fake = MagicMock()
    fake.used = 10 * 1024 ** 3
    fake.total = 100 * 1024 ** 3
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.disk_usage", return_value=fake):
        out = check_fs13_log_disk_usage()
    assert out["status"] == "ok"
    assert out["values"]["fs13_used_pct"] == 10.0


def test_fs13_warn_when_high():
    fake = MagicMock()
    fake.used = 85 * 1024 ** 3
    fake.total = 100 * 1024 ** 3
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.disk_usage", return_value=fake):
        out = check_fs13_log_disk_usage()
    assert out["status"] == "warn"


def test_fs13_crit_when_very_high():
    fake = MagicMock()
    fake.used = 97 * 1024 ** 3
    fake.total = 100 * 1024 ** 3
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.disk_usage", return_value=fake):
        out = check_fs13_log_disk_usage()
    assert out["status"] == "crit"


def test_fs13_warn_when_path_missing():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.disk_usage",
        side_effect=FileNotFoundError("nope"),
    ):
        out = check_fs13_log_disk_usage("/var/log/freeswitch")
    assert out["status"] == "warn"
    assert out["values"]["fs13_path"] == "/var/log/freeswitch"


# --- FS-14 (config drift) -------------------------------------------------


def test_fs14_ok_when_recent():
    """Recent mtime = small age = ok."""
    one_day_ago = time.time() - 86400
    with patch("ipracticom_sweeper.monitor.freeswitch.os.path.getmtime", return_value=one_day_ago):
        out = check_fs14_config_drift_days()
    assert out["status"] == "ok"
    assert 0 <= out["values"]["fs14_age_days"] <= 1


def test_fs14_warn_when_drifted():
    hundred_days_ago = time.time() - 100 * 86400
    with patch("ipracticom_sweeper.monitor.freeswitch.os.path.getmtime", return_value=hundred_days_ago):
        out = check_fs14_config_drift_days(warn_days=60, crit_days=180)
    assert out["status"] == "warn"
    assert out["values"]["fs14_age_days"] >= 60


def test_fs14_crit_when_very_drifted():
    two_hundred_days_ago = time.time() - 200 * 86400
    with patch("ipracticom_sweeper.monitor.freeswitch.os.path.getmtime", return_value=two_hundred_days_ago):
        out = check_fs14_config_drift_days(warn_days=60, crit_days=180)
    assert out["status"] == "crit"


def test_fs14_warn_when_missing():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.os.path.getmtime",
        side_effect=FileNotFoundError("missing"),
    ):
        out = check_fs14_config_drift_days()
    assert out["status"] == "warn"


# --- FS-15 (baseline drift) ----------------------------------------------


def test_fs15_ok_when_within_baseline():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "50 entries", ""),
    ):
        out = check_fs15_baseline_calls_per_hour(baseline_calls_per_hour=100)
    assert out["status"] == "ok"
    assert out["values"]["fs15_baseline_set"] is True
    assert out["values"]["fs15_drift_factor"] == 0.5


def test_fs15_warn_at_2x_baseline():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "210 entries", ""),
    ):
        out = check_fs15_baseline_calls_per_hour(
            baseline_calls_per_hour=100, warn_factor=2.0, crit_factor=4.0
        )
    assert out["status"] == "warn"
    assert out["values"]["fs15_drift_factor"] == 2.1


def test_fs15_crit_at_4x_baseline():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "500 entries", ""),
    ):
        out = check_fs15_baseline_calls_per_hour(
            baseline_calls_per_hour=100, warn_factor=2.0, crit_factor=4.0
        )
    assert out["status"] == "crit"


def test_fs15_ok_when_no_baseline_set():
    """Without a baseline, don't page — just report ok + baseline_set=False."""
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "9999 entries", ""),
    ):
        out = check_fs15_baseline_calls_per_hour(baseline_calls_per_hour=None)
    assert out["status"] == "ok"
    assert out["values"]["fs15_baseline_set"] is False


def test_fs15_warn_when_cli_fails():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(1, "", "lost"),
    ):
        out = check_fs15_baseline_calls_per_hour(baseline_calls_per_hour=100)
    assert out["status"] == "warn"


# --- collect_operational aggregator --------------------------------------


def test_collect_operational_returns_expected_keys():
    expected_value_keys = {
        "fs10_elapsed_ms", "fs10_warn_ms", "fs10_crit_ms",
        "fs11_active_calls", "fs11_warn", "fs11_crit",
        "fs12_active_channels", "fs12_warn", "fs12_crit",
        "fs13_used_pct", "fs13_path",
        "fs14_age_days", "fs14_warn_days", "fs14_crit_days",
        "fs15_current_calls", "fs15_baseline", "fs15_baseline_set",
        "fs15_drift_factor",
    }
    expected_status_keys = {f"fs{n}_status" for n in (10, 11, 12, 13, 14, 15)}

    with patch.object(fs, "check_fs10_cli_latency",
                      return_value={"status": "ok", "values": {
                          "fs10_elapsed_ms": 50, "fs10_warn_ms": 500,
                          "fs10_crit_ms": 2000, "fs10_cli_rc": 0,
                          "fs10_reason": None}}), \
         patch.object(fs, "check_fs11_active_calls",
                      return_value={"status": "ok", "values": {
                          "fs11_active_calls": 5, "fs11_warn": 100,
                          "fs11_crit": 500, "fs11_cli_rc": 0,
                          "fs11_reason": None}}), \
         patch.object(fs, "check_fs12_active_channels",
                      return_value={"status": "ok", "values": {
                          "fs12_active_channels": 10, "fs12_warn": 200,
                          "fs12_crit": 1000, "fs12_cli_rc": 0,
                          "fs12_reason": None}}), \
         patch.object(fs, "check_fs13_log_disk_usage",
                      return_value={"status": "ok", "values": {
                          "fs13_path": "/var/log/freeswitch",
                          "fs13_used_pct": 30.0,
                          "fs13_warn_pct": 80, "fs13_crit_pct": 95,
                          "fs13_reason": None}}), \
         patch.object(fs, "check_fs14_config_drift_days",
                      return_value={"status": "ok", "values": {
                          "fs14_path": "/etc/freeswitch/freeswitch.xml",
                          "fs14_age_days": 5,
                          "fs14_warn_days": 60, "fs14_crit_days": 180,
                          "fs14_reason": None}}), \
         patch.object(fs, "check_fs15_baseline_calls_per_hour",
                      return_value={"status": "ok", "values": {
                          "fs15_cli_rc": 0, "fs15_current_calls": 5,
                          "fs15_baseline": 100, "fs15_baseline_set": True,
                          "fs15_drift_factor": 0.05,
                          "fs15_warn_factor": 2.0, "fs15_crit_factor": 4.0}}):
        v = collect_operational()
    assert expected_value_keys.issubset(v.keys())
    assert expected_status_keys.issubset(v.keys())


def test_collect_operational_passes_baseline_to_fs15():
    with patch.object(fs, "check_fs10_cli_latency", return_value={"status": "ok", "values": {
        "fs10_elapsed_ms": 50, "fs10_warn_ms": 500, "fs10_crit_ms": 2000,
        "fs10_cli_rc": 0, "fs10_reason": None}}), \
         patch.object(fs, "check_fs11_active_calls", return_value={"status": "ok", "values": {
             "fs11_active_calls": 5, "fs11_warn": 100, "fs11_crit": 500,
             "fs11_cli_rc": 0, "fs11_reason": None}}), \
         patch.object(fs, "check_fs12_active_channels", return_value={"status": "ok", "values": {
             "fs12_active_channels": 10, "fs12_warn": 200, "fs12_crit": 1000,
             "fs12_cli_rc": 0, "fs12_reason": None}}), \
         patch.object(fs, "check_fs13_log_disk_usage", return_value={"status": "ok", "values": {
             "fs13_path": "/x", "fs13_used_pct": 30.0,
             "fs13_warn_pct": 80, "fs13_crit_pct": 95,
             "fs13_reason": None}}), \
         patch.object(fs, "check_fs14_config_drift_days", return_value={"status": "ok", "values": {
             "fs14_path": "/y", "fs14_age_days": 5,
             "fs14_warn_days": 60, "fs14_crit_days": 180,
             "fs14_reason": None}}), \
         patch.object(fs, "check_fs15_baseline_calls_per_hour", return_value={"status": "ok", "values": {
             "fs15_cli_rc": 0, "fs15_current_calls": 5,
             "fs15_baseline": 42.0, "fs15_baseline_set": True,
             "fs15_drift_factor": 0.12,
             "fs15_warn_factor": 2.0, "fs15_crit_factor": 4.0}}) as fs15_mock:
        collect_operational(fs_baseline_calls_per_hour=42.0)
    fs15_mock.assert_called_once_with(baseline_calls_per_hour=42.0)


# --- evaluate_operational -------------------------------------------------


def test_evaluate_operational_all_ok():
    v = {f"fs{n}_status": "ok" for n in (10, 11, 12, 13, 14, 15)}
    assert evaluate_operational(v) == "ok"


def test_evaluate_operational_worst_wins():
    v = {f"fs{n}_status": "ok" for n in (10, 11, 12, 13, 14, 15)}
    v["fs13_status"] = "crit"
    assert evaluate_operational(v) == "crit"


def test_evaluate_operational_empty_defaults_warn():
    """Missing statuses → treat each as warn → overall warn."""
    assert evaluate_operational({}) == "warn"


# --- integration with run_all --------------------------------------------


def test_run_all_includes_freeswitch_operational():
    from ipracticom_sweeper.monitor.checks import run_all
    with patch.object(fs, "collect_operational", return_value={
        "fs10_elapsed_ms": 50, "fs10_warn_ms": 500, "fs10_crit_ms": 2000,
        "fs11_active_calls": 5, "fs11_warn": 100, "fs11_crit": 500,
        "fs12_active_channels": 10, "fs12_warn": 200, "fs12_crit": 1000,
        "fs13_used_pct": 30.0, "fs13_path": "/var/log/freeswitch",
        "fs14_age_days": 5, "fs14_warn_days": 60, "fs14_crit_days": 180,
        "fs15_current_calls": 5, "fs15_baseline": 100, "fs15_baseline_set": True,
        "fs15_drift_factor": 0.05,
        "fs10_status": "ok", "fs11_status": "ok", "fs12_status": "ok",
        "fs13_status": "ok", "fs14_status": "ok", "fs15_status": "ok",
    }):
        snap = run_all({}, run_freeswitch=True)
    assert "freeswitch_operational" in snap["modules"]
    assert snap["modules"]["freeswitch_operational"]["status"] == "ok"


def test_run_all_swallows_freeswitch_operational_exception():
    from ipracticom_sweeper.monitor.checks import run_all
    with patch.object(fs, "collect_operational", side_effect=RuntimeError("boom")):
        snap = run_all({}, run_freeswitch=True)
    assert "freeswitch_operational" in snap["modules"]
    assert snap["modules"]["freeswitch_operational"]["status"] == "warn"
