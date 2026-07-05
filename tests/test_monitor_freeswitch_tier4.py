"""FreeSWITCH Tier 4 (FS-16..FS-25) tests — v0.5.0 slice 2.4.

All glob / os / psutil / netstat / fail2ban-client / fs_cli calls are mocked.
"""
import time
from unittest.mock import patch, MagicMock

import pytest

from ipracticom_sweeper.monitor import freeswitch as fs
from ipracticom_sweeper.monitor.freeswitch import (
    check_fs16_cdr_backup_fresh,
    check_fs17_recordings_age,
    check_fs18_sofia_packet_loss,
    check_fs19_sofia_jitter,
    check_fs20_codec_mismatch,
    check_fs21_process_rss,
    check_fs22_process_cpu_pct,
    check_fs23_tcp_retransmit_pct,
    check_fs24_log_error_rate,
    check_fs25_fail2ban_active,
    collect_edge_cases,
    evaluate_edge_cases,
    _file_age_hours,
    _read_text,
)


# --- helpers --------------------------------------------------------------


def test_file_age_hours_missing_returns_none():
    with patch("ipracticom_sweeper.monitor.freeswitch.os.path.getmtime",
               side_effect=FileNotFoundError()):
        assert _file_age_hours("/nope") is None


def test_file_age_hours_recent():
    one_hour_ago = time.time() - 3600
    with patch("ipracticom_sweeper.monitor.freeswitch.os.path.getmtime",
               return_value=one_hour_ago):
        age = _file_age_hours("/tmp/whatever")
    assert age is not None
    assert abs(age - 1.0) < 0.1


def test_read_text_missing_returns_none():
    with patch("builtins.open", side_effect=FileNotFoundError()):
        assert _read_text("/missing") is None


def test_read_text_existing(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hello")
    assert _read_text(str(p)) == "hello"


# --- FS-16 (CDR backup freshness) ----------------------------------------


def test_fs16_ok_when_fresh_backup(tmp_path):
    backup = tmp_path / "cdr-2026-06-30.sql"
    backup.write_text("-- backup")
    one_hour_ago = time.time() - 3600
    with patch("ipracticom_sweeper.monitor.freeswitch.os.path.getmtime",
               return_value=one_hour_ago):
        out = check_fs16_cdr_backup_fresh(
            backup_glob_pattern=str(tmp_path / "cdr-*.sql"),
            max_age_hours=26,
        )
    assert out["status"] == "ok"
    assert out["values"]["fs16_latest_backup"] is not None


def test_fs16_crit_when_old_backup(tmp_path):
    backup = tmp_path / "cdr-old.sql"
    backup.write_text("-- old")
    two_days_ago = time.time() - 48 * 3600
    with patch("ipracticom_sweeper.monitor.freeswitch.os.path.getmtime",
               return_value=two_days_ago):
        out = check_fs16_cdr_backup_fresh(
            backup_glob_pattern=str(tmp_path / "cdr-*.sql"),
            max_age_hours=26,
        )
    assert out["status"] == "crit"
    assert out["values"]["fs16_age_hours"] >= 26


def test_fs16_warn_when_no_backups(tmp_path):
    out = check_fs16_cdr_backup_fresh(
        backup_glob_pattern=str(tmp_path / "cdr-*.sql"),
    )
    assert out["status"] == "warn"
    assert out["values"]["fs16_latest_backup"] is None


# --- FS-17 (recordings age) ----------------------------------------------


def test_fs17_ok_when_no_recordings(tmp_path):
    empty = tmp_path / "recordings-empty"
    empty.mkdir()
    out = check_fs17_recordings_age(recordings_dir=str(empty))
    assert out["status"] == "ok"


def test_fs17_warn_when_dir_missing(tmp_path):
    out = check_fs17_recordings_age(recordings_dir=str(tmp_path / "nope"))
    assert out["status"] == "warn"
    assert out["values"]["fs17_reason"] == "directory not found"


def test_fs17_ok_when_recent_recordings(tmp_path):
    rec_dir = tmp_path / "rec"
    rec_dir.mkdir()
    one_day_old = rec_dir / "old.wav"
    one_day_old.write_text("x")
    one_day_mtime = time.time() - 86400
    with patch("ipracticom_sweeper.monitor.freeswitch.os.path.getmtime",
               return_value=one_day_mtime):
        out = check_fs17_recordings_age(recordings_dir=str(rec_dir),
                                        max_age_days=90)
    assert out["status"] == "ok"


def test_fs17_warn_when_stale_recordings(tmp_path):
    rec_dir = tmp_path / "rec"
    rec_dir.mkdir()
    old = rec_dir / "stale.wav"
    old.write_text("x")
    old_mtime = time.time() - 200 * 86400  # 200 days
    with patch("ipracticom_sweeper.monitor.freeswitch.os.path.getmtime",
               return_value=old_mtime):
        out = check_fs17_recordings_age(recordings_dir=str(rec_dir),
                                        max_age_days=90)
    assert out["status"] == "warn"


# --- FS-18 (sofia packet loss) -------------------------------------------


def test_fs18_ok_when_no_loss():
    sample = "Profile: internal | State: RUNNING | Packets: 0 lost\n"
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fs_cli"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, sample, "")):
        out = check_fs18_sofia_packet_loss()
    assert out["status"] == "ok"
    assert out["values"]["fs18_packet_loss_detected"] is False


def test_fs18_warn_when_loss_present():
    sample = "Profile: carrier | Packet loss: 2.5%\n"
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fs_cli"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, sample, "")):
        out = check_fs18_sofia_packet_loss()
    assert out["status"] == "warn"
    assert out["values"]["fs18_packet_loss_detected"] is True


def test_fs18_warn_when_cli_fails():
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fs_cli"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(1, "", "boom")):
        out = check_fs18_sofia_packet_loss()
    assert out["status"] == "warn"


# --- FS-19 (sofia jitter) ------------------------------------------------


def test_fs19_ok_when_no_jitter():
    sample = "Profile: internal | State: RUNNING | 0 ms jitter\n"
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fs_cli"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, sample, "")):
        out = check_fs19_sofia_jitter()
    assert out["status"] == "ok"


def test_fs19_warn_at_threshold():
    sample = "Call 1: jitter 35ms loss 0%\n"
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fs_cli"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, sample, "")):
        out = check_fs19_sofia_jitter(jitter_warn_ms=30, jitter_crit_ms=100)
    assert out["status"] == "warn"
    assert out["values"]["fs19_max_jitter_ms"] == 35.0


def test_fs19_crit_over_crit():
    sample = "Call 1: jitter 150ms loss 5%\n"
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fs_cli"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, sample, "")):
        out = check_fs19_sofia_jitter(jitter_warn_ms=30, jitter_crit_ms=100)
    assert out["status"] == "crit"


def test_fs19_warn_when_cli_fails():
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fs_cli"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(1, "", "boom")):
        out = check_fs19_sofia_jitter()
    assert out["status"] == "warn"


# --- FS-20 (codec NEGOTIATION) -------------------------------------------


def test_fs20_ok_when_no_negotiation():
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fs_cli"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, "codec PCMU PCMA OK", "")):
        out = check_fs20_codec_mismatch()
    assert out["status"] == "ok"


def test_fs20_warn_when_negotiation_present():
    sample = "Call 1: codec NEGOTIATION error\nNEGOTIATION on leg 2\n"
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fs_cli"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, sample, "")):
        out = check_fs20_codec_mismatch()
    assert out["status"] == "warn"
    assert out["values"]["fs20_negotiation_count"] == 2


def test_fs20_warn_when_cli_fails():
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fs_cli"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(1, "", "boom")):
        out = check_fs20_codec_mismatch()
    assert out["status"] == "warn"


# --- FS-21 (RSS) ----------------------------------------------------------


def test_fs21_ok_when_low_rss():
    proc_iter = iter([
        MagicMock(info={"name": "freeswitch",
                        "memory_info": MagicMock(rss=500 * 1024 ** 2)}),
    ])
    with patch("psutil.process_iter", return_value=proc_iter):
        out = check_fs21_process_rss()
    assert out["status"] == "ok"


def test_fs21_warn_when_high_rss():
    proc_iter = iter([
        MagicMock(info={"name": "freeswitch",
                        "memory_info": MagicMock(rss=3 * 1024 ** 3)}),
    ])
    with patch("psutil.process_iter", return_value=proc_iter):
        out = check_fs21_process_rss()
    assert out["status"] == "warn"


def test_fs21_crit_when_very_high_rss():
    proc_iter = iter([
        MagicMock(info={"name": "freeswitch",
                        "memory_info": MagicMock(rss=5 * 1024 ** 3)}),
    ])
    with patch("psutil.process_iter", return_value=proc_iter):
        out = check_fs21_process_rss()
    assert out["status"] == "crit"


def test_fs21_warn_when_no_process():
    proc_iter = iter([])  # empty
    with patch("psutil.process_iter", return_value=proc_iter):
        out = check_fs21_process_rss()
    assert out["status"] == "warn"


def test_fs21_warn_when_psutil_missing():
    """Inject a fake psutil module whose import succeeds but provides no
    relevant symbols — the check function imports psutil inside its body,
    so we don't need to make the import fail; we make it succeed but
    simulate the absence differently: monkeypatch the built-in import
    inside the check function via the source module's import cache.

    Simpler: we patch the import to raise ImportError by deleting psutil.
    """
    import sys
    import builtins
    real_import = builtins.__import__
    real_psutil = sys.modules.get("psutil")

    def _fake_import(name, *args, **kwargs):
        if name == "psutil" or name.startswith("psutil."):
            raise ImportError("psutil hidden for test")
        return real_import(name, *args, **kwargs)

    # Force the test to exercise the "psutil not available" branch.
    sys.modules.pop("psutil", None)
    builtins.__import__ = _fake_import
    try:
        out = check_fs21_process_rss()
    finally:
        builtins.__import__ = real_import
        if real_psutil is not None:
            sys.modules["psutil"] = real_psutil
    assert out["status"] == "warn"
    assert out["values"]["fs21_reason"] == "psutil not available"


# --- FS-22 (CPU %) -------------------------------------------------------


def test_fs22_ok_when_low_cpu():
    proc = MagicMock()
    proc.info = {"name": "freeswitch"}
    proc.cpu_percent.return_value = 10.0
    with patch("psutil.process_iter", return_value=iter([proc])):
        out = check_fs22_process_cpu_pct(sample_seconds=0.0)
    assert out["status"] == "ok"
    assert out["values"]["fs22_cpu_pct"] == 10.0


def test_fs22_warn_at_threshold():
    proc = MagicMock()
    proc.info = {"name": "freeswitch"}
    proc.cpu_percent.return_value = 60.0
    with patch("psutil.process_iter", return_value=iter([proc])):
        out = check_fs22_process_cpu_pct(warn_pct=50, crit_pct=80,
                                         sample_seconds=0.0)
    assert out["status"] == "warn"


def test_fs22_crit_above_crit():
    proc = MagicMock()
    proc.info = {"name": "freeswitch"}
    proc.cpu_percent.return_value = 95.0
    with patch("psutil.process_iter", return_value=iter([proc])):
        out = check_fs22_process_cpu_pct(warn_pct=50, crit_pct=80,
                                         sample_seconds=0.0)
    assert out["status"] == "crit"


def test_fs22_aggregates_multiple_processes():
    p1 = MagicMock()
    p1.info = {"name": "freeswitch"}
    p1.cpu_percent.return_value = 30.0
    p2 = MagicMock()
    p2.info = {"name": "freeswitch"}
    p2.cpu_percent.return_value = 40.0
    with patch("psutil.process_iter", return_value=iter([p1, p2])):
        out = check_fs22_process_cpu_pct(warn_pct=50, crit_pct=80,
                                         sample_seconds=0.0)
    assert out["values"]["fs22_cpu_pct"] == 70.0
    assert out["status"] == "warn"


# --- FS-23 (TCP retransmit) ---------------------------------------------


def test_fs23_ok_when_low_retransmit():
    netstat_out = """
Tcp:
    2000 active connections established
    50000 segments received
    100 segments retransmitted
TcpExt:
    ...
"""
    with patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, netstat_out, "")):
        out = check_fs23_tcp_retransmit_pct()
    # denom = 2000 + 50000 = 52000; pct = 100*100/52000 ≈ 0.19%
    assert out["status"] == "ok"
    assert out["values"]["fs23_retransmit_pct"] < 1.0


def test_fs23_warn_when_moderate():
    netstat_out = """
Tcp:
    500 active connections established
    50000 segments received
    1500 segments retransmitted
"""
    with patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, netstat_out, "")):
        out = check_fs23_tcp_retransmit_pct(warn_pct=1.0, crit_pct=5.0)
    # denom = 500 + 50000 = 50500; pct = 1500*100/50500 ≈ 2.97% → warn
    assert out["status"] == "warn"


def test_fs23_crit_when_high():
    netstat_out = """
Tcp:
    500 active connections established
    50000 segments received
    5000 segments retransmitted
"""
    with patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, netstat_out, "")):
        out = check_fs23_tcp_retransmit_pct(warn_pct=1.0, crit_pct=5.0)
    # denom = 50500; pct = 5000*100/50500 ≈ 9.9% → crit
    assert out["status"] == "crit"


def test_fs23_warn_when_netstat_missing():
    with patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(127, "", "netstat not found")):
        out = check_fs23_tcp_retransmit_pct()
    assert out["status"] == "warn"


def test_fs23_warn_when_no_retransmit_line():
    netstat_out = """
Tcp:
    1000 active connections openings
"""
    with patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, netstat_out, "")):
        out = check_fs23_tcp_retransmit_pct()
    assert out["status"] == "warn"
    assert "no segments retransmitted" in out["values"]["fs23_reason"]


# --- FS-24 (log error rate) ---------------------------------------------


def test_fs24_ok_when_low_errors(tmp_path):
    log = tmp_path / "fs.log"
    lines = ["INFO heartbeat ok"] * 100 + ["ERROR something"] * 2
    log.write_text("\n".join(lines))
    out = check_fs24_log_error_rate(log_path=str(log), window_min=5)
    assert out["status"] == "ok"
    assert out["values"]["fs24_errors_count"] == 2


def test_fs24_warn_when_moderate(tmp_path):
    log = tmp_path / "fs.log"
    lines = ["ERROR boom"] * 30 + ["INFO ok"] * 100
    log.write_text("\n".join(lines))
    out = check_fs24_log_error_rate(log_path=str(log), window_min=5,
                                    warn_per_min=5, crit_per_min=50)
    # 30 / 5 = 6 per min > 5 → warn
    assert out["status"] == "warn"


def test_fs24_crit_when_high(tmp_path):
    log = tmp_path / "fs.log"
    lines = ["ERROR boom"] * 300 + ["INFO ok"] * 100
    log.write_text("\n".join(lines))
    out = check_fs24_log_error_rate(log_path=str(log), window_min=5,
                                    warn_per_min=5, crit_per_min=50)
    # 300 / 5 = 60 per min > 50 → crit
    assert out["status"] == "crit"


def test_fs24_warn_when_log_missing(tmp_path):
    out = check_fs24_log_error_rate(log_path=str(tmp_path / "missing.log"))
    assert out["status"] == "warn"


# --- FS-25 (fail2ban) ----------------------------------------------------


def test_fs25_warn_when_f2b_client_missing():
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value=None):
        out = check_fs25_fail2ban_active()
    assert out["status"] == "warn"
    assert out["values"]["fs25_reason"] == "fail2ban-client not on PATH"


def test_fs25_ok_when_jail_active():
    out_lines = [
        "Status for the jail: freeswitch",
        "|- Filter list:    1",
        "|- Currently banned:   3",
        "|- Total banned:       7",
    ]
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fail2ban-client"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(0, "\n".join(out_lines), "")):
        out = check_fs25_fail2ban_active()
    assert out["status"] == "ok"
    assert out["values"]["fs25_banned"] == 7


def test_fs25_warn_when_jail_status_fails():
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which",
               return_value="/usr/bin/fail2ban-client"), \
         patch("ipracticom_sweeper.monitor.freeswitch._run",
               return_value=(1, "", "jail not found")):
        out = check_fs25_fail2ban_active()
    assert out["status"] == "warn"


# --- collect_edge_cases aggregator -------------------------------------


def test_collect_edge_cases_returns_expected_keys():
    expected_value_keys = {
        "fs16_latest_backup", "fs16_age_hours", "fs16_max_age_hours",
        "fs17_oldest_sample_days", "fs17_path",
        "fs18_packet_loss_detected",
        "fs19_max_jitter_ms",
        "fs20_negotiation_count",
        "fs21_rss_bytes",
        "fs22_cpu_pct",
        "fs23_retransmit_pct",
        "fs24_errors_per_min",
        "fs25_banned",
    }
    expected_status_keys = {f"fs{n}_status" for n in range(16, 26)}

    # Mock every check to a known ok result
    with patch.object(fs, "check_fs16_cdr_backup_fresh",
                      return_value={"status": "ok", "values": {
                          "fs16_pattern": "/x", "fs16_latest_backup": "/x/y",
                          "fs16_age_hours": 1.0, "fs16_max_age_hours": 26,
                          "fs16_reason": None}}), \
         patch.object(fs, "check_fs17_recordings_age",
                      return_value={"status": "ok", "values": {
                          "fs17_path": "/r", "fs17_oldest_newest_sample_days": 1.0,
                          "fs17_max_age_days": 90, "fs17_reason": None}}), \
         patch.object(fs, "check_fs18_sofia_packet_loss",
                      return_value={"status": "ok", "values": {
                          "fs18_cli_rc": 0, "fs18_packet_loss_detected": False,
                          "fs18_reason": None}}), \
         patch.object(fs, "check_fs19_sofia_jitter",
                      return_value={"status": "ok", "values": {
                          "fs19_cli_rc": 0, "fs19_max_jitter_ms": None,
                          "fs19_warn_ms": 30, "fs19_crit_ms": 100,
                          "fs19_reason": None}}), \
         patch.object(fs, "check_fs20_codec_mismatch",
                      return_value={"status": "ok", "values": {
                          "fs20_cli_rc": 0, "fs20_negotiation_count": 0,
                          "fs20_reason": None}}), \
         patch.object(fs, "check_fs21_process_rss",
                      return_value={"status": "ok", "values": {
                          "fs21_rss_bytes": 100, "fs21_warn_bytes": 1,
                          "fs21_crit_bytes": 2, "fs21_reason": None}}), \
         patch.object(fs, "check_fs22_process_cpu_pct",
                      return_value={"status": "ok", "values": {
                          "fs22_cpu_pct": 10.0, "fs22_warn_pct": 50,
                          "fs22_crit_pct": 80, "fs22_reason": None}}), \
         patch.object(fs, "check_fs23_tcp_retransmit_pct",
                      return_value={"status": "ok", "values": {
                          "fs23_netstat_rc": 0, "fs23_retransmit_pct": 0.5,
                          "fs23_warn_pct": 1.0, "fs23_crit_pct": 5.0,
                          "fs23_reason": None}}), \
         patch.object(fs, "check_fs24_log_error_rate",
                      return_value={"status": "ok", "values": {
                          "fs24_path": "/l", "fs24_window_min": 5,
                          "fs24_errors_count": 1, "fs24_errors_per_min": 0.2,
                          "fs24_warn_per_min": 5, "fs24_crit_per_min": 50,
                          "fs24_reason": None}}), \
         patch.object(fs, "check_fs25_fail2ban_active",
                      return_value={"status": "ok", "values": {
                          "fs25_jail": "freeswitch", "fs25_banned": 3,
                          "fs25_rc": 0, "fs25_reason": None}}):
        v = collect_edge_cases()
    assert expected_value_keys.issubset(v.keys())
    assert expected_status_keys.issubset(v.keys())


# --- evaluate_edge_cases -------------------------------------------------


def test_evaluate_edge_cases_all_ok():
    v = {f"fs{n}_status": "ok" for n in range(16, 26)}
    assert evaluate_edge_cases(v) == "ok"


def test_evaluate_edge_cases_worst_wins():
    v = {f"fs{n}_status": "ok" for n in range(16, 26)}
    v["fs23_status"] = "crit"
    assert evaluate_edge_cases(v) == "crit"


def test_evaluate_edge_cases_empty_defaults_warn():
    assert evaluate_edge_cases({}) == "warn"


# --- run_all integration ------------------------------------------------


def test_run_all_includes_freeswitch_edge_module():
    from ipracticom_sweeper.monitor.checks import run_all
    fake_values = {
        "fs16_latest_backup": "/x/y", "fs16_age_hours": 1.0,
        "fs16_max_age_hours": 26,
        "fs17_oldest_sample_days": 1.0, "fs17_path": "/r",
        "fs18_packet_loss_detected": False,
        "fs19_max_jitter_ms": None,
        "fs20_negotiation_count": 0,
        "fs21_rss_bytes": 100,
        "fs22_cpu_pct": 10.0,
        "fs23_retransmit_pct": 0.2,
        "fs24_errors_per_min": 0.2,
        "fs25_banned": 0,
        "fs16_status": "ok", "fs17_status": "ok", "fs18_status": "ok",
        "fs19_status": "ok", "fs20_status": "ok", "fs21_status": "ok",
        "fs22_status": "ok", "fs23_status": "ok", "fs24_status": "ok",
        "fs25_status": "ok",
    }
    with patch.object(fs, "collect_edge_cases", return_value=fake_values):
        snap = run_all({}, run_freeswitch=True)
    assert "freeswitch_edge" in snap["modules"]
    assert snap["modules"]["freeswitch_edge"]["status"] == "ok"


def test_run_all_swallows_freeswitch_edge_exception():
    from ipracticom_sweeper.monitor.checks import run_all
    with patch.object(fs, "collect_edge_cases", side_effect=RuntimeError("boom")):
        snap = run_all({}, run_freeswitch=True)
    assert "freeswitch_edge" in snap["modules"]
    assert snap["modules"]["freeswitch_edge"]["status"] == "warn"
