"""FreeSWITCH Tier 5 — FS-29..FS-40 tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ipracticom_sweeper.monitor.freeswitch_v2_part2 import (
    check_fs29_rtp_silence,
    check_fs30_options_keepalive,
    check_fs31_sip_parse_errors,
    check_fs32_dialplan_latency,
    check_fs33_conference_participants,
    check_fs34_voicemail_quota,
    check_fs35_mod_load,
    check_fs36_esl_backlog,
    check_fs37_max_procs,
    check_fs38_cdr_db_pool,
    check_fs39_license,
    check_fs40_trunk_tps,
)


def _ok(stdout: str = "") -> dict:
    return {"rc": 0, "stdout": stdout, "stderr": ""}


def _fail(rc: int = 1) -> dict:
    return {"rc": rc, "stdout": "", "stderr": "fail"}


# ============= FS-29 RTP silence ===========================================

def test_fs29_ok_low_silence() -> None:
    body = "Call-ID: abc, RTP: in=1200 lost=24 silence=85 (2.0%)"
    out = check_fs29_rtp_silence(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "ok"
    assert out["values"]["fs29_max_silence_pct"] == 2.0


def test_fs29_warn_5_to_15pct() -> None:
    body = "Call-ID: abc, RTP: silence=120 (8.0%)"
    out = check_fs29_rtp_silence(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "warn"


def test_fs29_crit_above_15pct() -> None:
    body = "Call-ID: abc, RTP: silence=300 (20.0%)"
    out = check_fs29_rtp_silence(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "crit"


def test_fs29_handles_no_active_calls() -> None:
    out = check_fs29_rtp_silence(fs_cli_runner=lambda _: _ok(""))
    assert out["status"] == "ok"


def test_fs29_window_per_call() -> None:
    body = (
        "Call-ID: a, RTP: silence=10 (1.0%)\n"
        "Call-ID: b, RTP: silence=900 (50.0%)\n"
    )
    out = check_fs29_rtp_silence(fs_cli_runner=lambda _: _ok(body))
    assert out["values"]["fs29_silent_legs"] == 1


# ============= FS-30 OPTIONS keepalive =====================================

def test_fs30_ok_3_of_3_providers() -> None:
    out = check_fs30_options_keepalive(
        providers=[
            {"name": "p1", "host": "1.1.1.1", "port": 5060},
            {"name": "p2", "host": "2.2.2.2", "port": 5060},
            {"name": "p3", "host": "3.3.3.3", "port": 5060},
        ],
        probe_runner=lambda h, p, t: (True, 50.0),
    )
    assert out["status"] == "ok"
    assert out["values"]["fs30_failed_count"] == 0


def test_fs30_warn_1_of_3_down() -> None:
    def probe(h, p, t):
        return (h != "2.2.2.2", 100.0)
    out = check_fs30_options_keepalive(
        providers=[
            {"name": "p1", "host": "1.1.1.1", "port": 5060},
            {"name": "p2", "host": "2.2.2.2", "port": 5060},
            {"name": "p3", "host": "3.3.3.3", "port": 5060},
        ],
        probe_runner=probe,
    )
    assert out["status"] == "warn"


def test_fs30_crit_2_of_3_down() -> None:
    """2 of 3 fail on retry too → crit."""
    def probe(h, p, t):
        # Only host 3.3.3.3 ever succeeds; the other 2 always fail
        return (h == "3.3.3.3", 100.0)

    out = check_fs30_options_keepalive(
        providers=[
            {"name": "p1", "host": "1.1.1.1", "port": 5060},
            {"name": "p2", "host": "2.2.2.2", "port": 5060},
            {"name": "p3", "host": "3.3.3.3", "port": 5060},
        ],
        probe_runner=probe,
    )
    assert out["status"] == "crit"
    assert out["values"]["fs30_failed_count"] == 2


def test_fs30_handles_no_providers() -> None:
    out = check_fs30_options_keepalive(providers=[])
    assert out["status"] == "disabled"


def test_fs30_records_response_time() -> None:
    out = check_fs30_options_keepalive(
        providers=[{"name": "p1", "host": "1.1.1.1", "port": 5060}],
        probe_runner=lambda h, p, t: (True, 250.0),
    )
    assert out["values"]["fs30_results"]["p1"]["response_ms"] == 250.0


def test_fs30_retry_once_on_transient_fail() -> None:
    """First call fails, second (retry) succeeds → ok."""
    call_count = {"n": 0}

    def probe(h, p, t):
        call_count["n"] += 1
        return (call_count["n"] >= 2, 50.0)

    out = check_fs30_options_keepalive(
        providers=[{"name": "p1", "host": "1.1.1.1", "port": 5060}],
        probe_runner=probe,
    )
    assert out["status"] == "ok"


# ============= FS-31 SIP parse errors ======================================

def test_fs31_ok_no_errors(tmp_path: Path) -> None:
    log = tmp_path / "freeswitch.log"
    log.write_text("INFO normal line\nINFO another line\n")
    out = check_fs31_sip_parse_errors(log_path=str(log))
    assert out["status"] == "ok"
    assert out["values"]["fs31_parse_errors"] == 0


def test_fs31_warn_1_to_5(tmp_path: Path) -> None:
    log = tmp_path / "freeswitch.log"
    log.write_text("Failed to parse SIP message\n" * 3)
    out = check_fs31_sip_parse_errors(log_path=str(log))
    assert out["status"] == "warn"


def test_fs31_crit_above_5(tmp_path: Path) -> None:
    log = tmp_path / "freeswitch.log"
    log.write_text("Failed to parse SIP message\n" * 9)
    out = check_fs31_sip_parse_errors(log_path=str(log))
    assert out["status"] == "crit"


def test_fs31_matches_sofia_parse_regex(tmp_path: Path) -> None:
    log = tmp_path / "freeswitch.log"
    log.write_text("Failed to parse SIP request\n" * 6)
    out = check_fs31_sip_parse_errors(log_path=str(log))
    assert out["values"]["fs31_parse_errors"] == 6


def test_fs31_ignores_other_lines(tmp_path: Path) -> None:
    log = tmp_path / "freeswitch.log"
    log.write_text("INFO normal log\nDEBUG another\n")
    out = check_fs31_sip_parse_errors(log_path=str(log))
    assert out["values"]["fs31_parse_errors"] == 0


def test_fs31_handles_log_missing(tmp_path: Path) -> None:
    out = check_fs31_sip_parse_errors(log_path=str(tmp_path / "no.log"))
    assert out["status"] == "ok"
    assert "no signal" in out["values"]["fs31_reason"]


# ============= FS-32 dialplan latency =======================================

def test_fs32_ok_p95_under_500ms() -> None:
    # 12 lines with dialplan_time=200
    body = "\n".join(f"uuid=u{i}, dialplan_time=200ms" for i in range(12))
    out = check_fs32_dialplan_latency(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "ok"


def test_fs32_warn_p95_500_to_2000() -> None:
    body = "\n".join(f"uuid=u{i}, dialplan_time=1200ms" for i in range(12))
    out = check_fs32_dialplan_latency(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "warn"


def test_fs32_crit_p95_above_2000() -> None:
    body = "\n".join(f"uuid=u{i}, dialplan_time=3500ms" for i in range(12))
    out = check_fs32_dialplan_latency(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "crit"


def test_fs32_handles_no_calls() -> None:
    out = check_fs32_dialplan_latency(fs_cli_runner=lambda _: _ok(""))
    assert out["status"] == "ok"
    assert out["values"]["fs32_samples"] == 0


def test_fs32_min_sample_size_10() -> None:
    body = "\n".join(f"uuid=u{i}, dialplan_time=100ms" for i in range(5))
    out = check_fs32_dialplan_latency(fs_cli_runner=lambda _: _ok(body))
    assert "insufficient" in out["values"].get("fs32_reason", "")


def test_fs32_metadata_p50_p95_p99() -> None:
    body = "\n".join(f"uuid=u{i}, dialplan_time=100ms" for i in range(20))
    out = check_fs32_dialplan_latency(fs_cli_runner=lambda _: _ok(body))
    assert "fs32_p50_ms" in out["values"]
    assert "fs32_p95_ms" in out["values"]
    assert "fs32_p99_ms" in out["values"]


# ============= FS-33 conference participants ==============================

def test_fs33_ok_below_max() -> None:
    body = "Conference 3000 (members: 10)"
    out = check_fs33_conference_participants(
        max_participants=100, fs_cli_runner=lambda _: _ok(body)
    )
    assert out["status"] == "ok"


def test_fs33_warn_80pct_full() -> None:
    body = "Conference 3000 (members: 85)"
    out = check_fs33_conference_participants(
        max_participants=100, fs_cli_runner=lambda _: _ok(body)
    )
    assert out["status"] == "warn"


def test_fs33_crit_above_max() -> None:
    body = "Conference 3000 (members: 110)"
    out = check_fs33_conference_participants(
        max_participants=100, fs_cli_runner=lambda _: _ok(body)
    )
    assert out["status"] == "crit"


def test_fs33_handles_no_active_conferences() -> None:
    out = check_fs33_conference_participants(
        max_participants=100, fs_cli_runner=lambda _: _ok("")
    )
    assert out["status"] == "ok"


def test_fs33_handles_multiple_conferences() -> None:
    body = "Conference 3000 (members: 50)\nConference 3001 (members: 110)"
    out = check_fs33_conference_participants(
        max_participants=100, fs_cli_runner=lambda _: _ok(body)
    )
    assert out["status"] == "crit"
    assert "3001" in out["values"]["fs33_over_limit"]


def test_fs33_metadata_per_conference() -> None:
    body = "Conference 3000 (members: 42)\nConference 3001 (members: 7)"
    out = check_fs33_conference_participants(
        max_participants=100, fs_cli_runner=lambda _: _ok(body)
    )
    assert out["values"]["fs33_conferences"]["3000"] == 42
    assert out["values"]["fs33_conferences"]["3001"] == 7


# ============= FS-34 voicemail quota =======================================

def test_fs34_ok_under_quota(tmp_path: Path) -> None:
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "msg1.wav").write_bytes(b"x" * 1024 * 100)  # 100KB
    fs_xml = tmp_path / "freeswitch.xml"
    fs_xml.write_text('<param name="quota" value="100"/>')  # 100 MB quota
    out = check_fs34_voicemail_quota(vm_dir=str(vm), fs_xml=str(fs_xml))
    assert out["status"] == "ok"


def test_fs34_warn_80pct(tmp_path: Path) -> None:
    vm = tmp_path / "vm"
    vm.mkdir()
    # Write 85 MB
    (vm / "msg.wav").write_bytes(b"x" * 85 * 1024 * 1024)
    fs_xml = tmp_path / "freeswitch.xml"
    fs_xml.write_text('<param name="quota" value="100"/>')
    out = check_fs34_voicemail_quota(vm_dir=str(vm), fs_xml=str(fs_xml))
    assert out["status"] == "warn"


def test_fs34_crit_95pct(tmp_path: Path) -> None:
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "msg.wav").write_bytes(b"x" * 96 * 1024 * 1024)
    fs_xml = tmp_path / "freeswitch.xml"
    fs_xml.write_text('<param name="quota" value="100"/>')
    out = check_fs34_voicemail_quota(vm_dir=str(vm), fs_xml=str(fs_xml))
    assert out["status"] == "crit"


def test_fs34_handles_missing_voicemail_dir(tmp_path: Path) -> None:
    out = check_fs34_voicemail_quota(vm_dir=str(tmp_path / "nope"))
    assert out["status"] == "disabled"


def test_fs34_handles_zero_quota(tmp_path: Path) -> None:
    vm = tmp_path / "vm"
    vm.mkdir()
    fs_xml = tmp_path / "freeswitch.xml"
    fs_xml.write_text("<xml/>")  # no quota param
    out = check_fs34_voicemail_quota(vm_dir=str(vm), fs_xml=str(fs_xml))
    assert out["status"] == "ok"
    assert "no quota" in out["values"]["fs34_reason"]


# ============= FS-35 mod load ==============================================

def test_fs35_ok_all_required_loaded() -> None:
    body = "\n".join(f"mod_{m},type,api" for m in
                     ("sofia", "dptools", "console", "logfile", "event_socket"))
    out = check_fs35_mod_load(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "ok"
    assert out["values"]["fs35_missing"] == []


def test_fs35_warn_one_missing() -> None:
    body = "\n".join(f"mod_{m},type" for m in
                     ("sofia", "dptools", "logfile", "event_socket"))  # no console
    out = check_fs35_mod_load(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "warn"


def test_fs35_crit_core_missing() -> None:
    body = "mod_dptools,type\nmod_logfile,type"  # no mod_sofia
    out = check_fs35_mod_load(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "crit"


def test_fs35_handles_fs_cli_failure() -> None:
    out = check_fs35_mod_load(fs_cli_runner=lambda _: _fail())
    assert out["status"] == "warn"


def test_fs35_parses_show_modules_output() -> None:
    body = "mod_sofia,api,enabled\nmod_dptools,api,enabled"
    out = check_fs35_mod_load(
        required_modules=("mod_sofia", "mod_dptools"),
        fs_cli_runner=lambda _: _ok(body),
    )
    assert out["status"] == "ok"


# ============= FS-36 ESL backlog ============================================

def test_fs36_ok_backlog_under_10() -> None:
    body = "event queue depth: 3"
    out = check_fs36_esl_backlog(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "ok"
    assert out["values"]["fs36_queue_depth"] == 3


def test_fs36_warn_10_to_50() -> None:
    body = "event queue depth: 25"
    out = check_fs36_esl_backlog(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "warn"


def test_fs36_crit_above_50() -> None:
    body = "event queue depth: 70"
    out = check_fs36_esl_backlog(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "crit"


def test_fs36_handles_esl_unavailable() -> None:
    out = check_fs36_esl_backlog(fs_cli_runner=lambda _: _fail())
    assert out["status"] == "warn"


def test_fs36_handles_unparseable_output() -> None:
    out = check_fs36_esl_backlog(fs_cli_runner=lambda _: _ok("garbage"))
    assert out["status"] == "warn"


# ============= FS-37 max-procs =============================================

def test_fs37_ok_under_80pct_max() -> None:
    body = "Name: internal\nregistered: 50\nMax-Procs: 200\n"
    out = check_fs37_max_procs(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "ok"


def test_fs37_warn_80_to_95_pct() -> None:
    body = "Name: internal\nregistered: 175\nMax-Procs: 200\n"
    out = check_fs37_max_procs(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "warn"


def test_fs37_crit_above_95_pct() -> None:
    body = "Name: internal\nregistered: 195\nMax-Procs: 200\n"
    out = check_fs37_max_procs(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "crit"


def test_fs37_handles_max_procs_unset() -> None:
    body = "Name: internal\nregistered: 50\n"
    out = check_fs37_max_procs(fs_cli_runner=lambda _: _ok(body))
    assert out["status"] == "ok"


def test_fs37_per_profile_breakdown() -> None:
    body = (
        "Name: internal\nregistered: 195\nMax-Procs: 200\n"
        "Name: external\nregistered: 50\nMax-Procs: 200\n"
    )
    out = check_fs37_max_procs(fs_cli_runner=lambda _: _ok(body))
    assert "internal" in out["values"]["fs37_profiles"]
    assert "external" in out["values"]["fs37_profiles"]


# ============= FS-38 CDR DB pool ===========================================

def test_fs38_ok_under_70pct() -> None:
    """7/10 = 70% → borderline; threshold is ">=70" = warn, so 6/10 = 60% = ok."""
    out = check_fs38_cdr_db_pool(psql_runner=lambda: (6, 10))
    assert out["status"] == "ok"
    assert out["values"]["fs38_pct"] == 60.0


def test_fs38_warn_70_to_90_pct() -> None:
    out = check_fs38_cdr_db_pool(psql_runner=lambda: (8, 10))
    assert out["status"] == "warn"


def test_fs38_crit_above_90_pct() -> None:
    out = check_fs38_cdr_db_pool(psql_runner=lambda: (10, 10))
    assert out["status"] == "crit"


def test_fs38_handles_no_db_config() -> None:
    """When psql returns 0 max, treat as unknown."""
    out = check_fs38_cdr_db_pool(psql_runner=lambda: (5, 0))
    assert out["status"] == "warn"
    assert out["values"]["fs38_reason"] == "max_connections unknown"


# ============= FS-39 license ==============================================

def test_fs39_ok_under_70pct_license() -> None:
    out = check_fs39_license(license_max_concurrent=200, fs_cli_runner=lambda _: _ok("50"))
    assert out["status"] == "ok"


def test_fs39_warn_70_to_90_pct() -> None:
    out = check_fs39_license(license_max_concurrent=200, fs_cli_runner=lambda _: _ok("160"))
    assert out["status"] == "warn"


def test_fs39_crit_above_90_pct() -> None:
    out = check_fs39_license(license_max_concurrent=200, fs_cli_runner=lambda _: _ok("195"))
    assert out["status"] == "crit"


def test_fs39_handles_license_unlimited() -> None:
    out = check_fs39_license(license_max_concurrent=0, fs_cli_runner=lambda _: _ok("500"))
    assert out["status"] == "ok"


def test_fs39_handles_unparseable() -> None:
    out = check_fs39_license(license_max_concurrent=100, fs_cli_runner=lambda _: _ok("garbage"))
    assert out["status"] == "warn"


# ============= FS-40 trunk TPS ============================================

def test_fs40_ok_under_50_tps(tmp_path: Path) -> None:
    log = tmp_path / "freeswitch.log"
    # Write 30 SIP method lines (~6 TPS over 5s)
    log.write_text("INVITE sip:user@host\n" * 30)
    out = check_fs40_trunk_tps(log_path=str(log), window_seconds=5)
    assert out["status"] == "ok"


def test_fs40_warn_50_to_200(tmp_path: Path) -> None:
    log = tmp_path / "freeswitch.log"
    log.write_text("INVITE sip:user@host\n" * 350)  # 70 TPS over 5s
    out = check_fs40_trunk_tps(log_path=str(log), window_seconds=5)
    assert out["status"] == "warn"


def test_fs40_crit_above_200(tmp_path: Path) -> None:
    log = tmp_path / "freeswitch.log"
    log.write_text("INVITE sip:user@host\n" * 1500)  # 300 TPS over 5s
    out = check_fs40_trunk_tps(log_path=str(log), window_seconds=5)
    assert out["status"] == "crit"


def test_fs40_zero_tps_ok(tmp_path: Path) -> None:
    log = tmp_path / "freeswitch.log"
    log.write_text("INFO nothing\n")
    out = check_fs40_trunk_tps(log_path=str(log), window_seconds=5)
    assert out["status"] == "ok"


def test_fs40_handles_log_missing(tmp_path: Path) -> None:
    out = check_fs40_trunk_tps(log_path=str(tmp_path / "no.log"))
    assert out["status"] == "warn"


def test_fs40_counts_per_method(tmp_path: Path) -> None:
    log = tmp_path / "freeswitch.log"
    log.write_text(
        "INVITE sip:a\n" * 10 +
        "REGISTER sip:b\n" * 5 +
        "BYE sip:c\n" * 3 +
        "OPTIONS sip:d\n" * 2
    )
    out = check_fs40_trunk_tps(log_path=str(log), window_seconds=5)
    # 20 SIP methods / 5s = 4 TPS
    assert out["values"]["fs40_sip_methods_total"] == 20