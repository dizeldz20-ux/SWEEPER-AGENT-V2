"""FreeSWITCH Tier 5 — FS-26..FS-28 tests.

Mocked: fs_cli, cdr file. No real FreeSWITCH or CDRs touched.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ipracticom_sweeper.monitor.freeswitch_v2 import (
    check_fs26_invite_auth_failures,
    check_fs27_call_drop_rate,
    check_fs28_nat_binding_failures,
)


def _fs_cli_ok(stdout: str = "") -> dict:
    return {"rc": 0, "stdout": stdout, "stderr": ""}


def _fs_cli_fail(rc: int = 1) -> dict:
    return {"rc": rc, "stdout": "", "stderr": "fail"}


# --- FS-26 -------------------------------------------------------------------

def test_fs26_ok_below_5_per_min() -> None:
    out = check_fs26_invite_auth_failures(fs_cli_runner=lambda _: _fs_cli_ok(
        "Call-ID: abc, status: OK\nCall-ID: def, status: OK\n"
    ))
    assert out["status"] == "ok"
    assert out["values"]["fs26_auth_failures"] == 0


def test_fs26_warn_5_to_20_per_min() -> None:
    # 7 lines with 'fail auth' to trigger warn
    body = "\n".join(f"line {i}: fail auth from 10.0.0.{i}" for i in range(7))
    out = check_fs26_invite_auth_failures(fs_cli_runner=lambda _: _fs_cli_ok(body))
    assert out["status"] == "warn"
    assert out["values"]["fs26_auth_failures"] >= 5
    # IPs are capped at 5 entries
    assert "10.0.0.0" in out["values"]["fs26_source_ips"]


def test_fs26_crit_above_20_per_min() -> None:
    body = "\n".join(f"401 unauthorized from 10.0.0.{i}" for i in range(25))
    out = check_fs26_invite_auth_failures(fs_cli_runner=lambda _: _fs_cli_ok(body))
    assert out["status"] == "crit"


def test_fs26_parses_401_in_sofia_output() -> None:
    body = "endpoint 1.2.3.4: 401 unauthorized in INVITE"
    out = check_fs26_invite_auth_failures(fs_cli_runner=lambda _: _fs_cli_ok(body))
    assert out["values"]["fs26_auth_failures"] >= 1
    assert "1.2.3.4" in out["values"]["fs26_source_ips"]


def test_fs26_handles_empty_sofia_output() -> None:
    out = check_fs26_invite_auth_failures(fs_cli_runner=lambda _: _fs_cli_ok(""))
    assert out["status"] == "ok"
    assert out["values"]["fs26_auth_failures"] == 0


def test_fs26_handles_fs_cli_timeout() -> None:
    out = check_fs26_invite_auth_failures(fs_cli_runner=lambda _: _fs_cli_fail(124))
    assert out["status"] == "warn"
    assert out["values"]["fs26_reason"] == "cli failed"


def test_fs26_metadata_source_ip_capture() -> None:
    body = "fail auth from 192.168.1.42"
    out = check_fs26_invite_auth_failures(fs_cli_runner=lambda _: _fs_cli_ok(body))
    assert "192.168.1.42" in out["values"]["fs26_source_ips"]


def test_fs26_window_default_60s() -> None:
    out = check_fs26_invite_auth_failures(fs_cli_runner=lambda _: _fs_cli_ok(""))
    assert out["values"]["fs26_window_seconds"] == 60


# --- FS-27 -------------------------------------------------------------------

def _write_cdr(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def test_fs27_ok_drop_rate_under_2pct(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr-csv.csv"
    # Real fixture: 100 calls, 1 drop = 1%
    rows = ["a,b,NORMAL_CLEARING"] * 99 + ["a,b,CALL_REJECTED"]
    _write_cdr(cdr, rows)
    out = check_fs27_call_drop_rate(cdr_path=str(cdr))
    assert out["status"] == "ok"
    assert out["values"]["fs27_total"] == 100
    assert out["values"]["fs27_dropped"] == 1


def test_fs27_warn_2_to_5_pct(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr-csv.csv"
    rows = ["a,b,NORMAL_CLEARING"] * 96 + ["a,b,CALL_REJECTED"] * 4
    _write_cdr(cdr, rows)
    out = check_fs27_call_drop_rate(cdr_path=str(cdr))
    assert out["status"] == "warn"
    assert 2.0 <= out["values"]["fs27_drop_pct"] < 5.0


def test_fs27_crit_above_5pct(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr-csv.csv"
    rows = ["a,b,NORMAL_CLEARING"] * 92 + ["a,b,CALL_REJECTED"] * 8
    _write_cdr(cdr, rows)
    out = check_fs27_call_drop_rate(cdr_path=str(cdr))
    assert out["status"] == "crit"


def test_fs27_no_calls_returns_ok(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr-csv.csv"
    _write_cdr(cdr, [])
    out = check_fs27_call_drop_rate(cdr_path=str(cdr))
    assert out["status"] == "ok"
    assert out["values"]["fs27_total"] == 0


def test_fs27_parses_cdr_csv(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr-csv.csv"
    _write_cdr(cdr, [
        "+15551234567,2001,NORMAL_CLEARING",
        "+15551234568,2002,NO_ANSWER",
        "+15551234569,2003,CALL_REJECTED",
    ])
    out = check_fs27_call_drop_rate(cdr_path=str(cdr))
    assert out["values"]["fs27_total"] == 3
    # Drop = NO_ANSWER (1), CALL_REJECTED (1) — but NO_ANSWER is in our list
    assert out["values"]["fs27_dropped"] >= 1


def test_fs27_excludes_incomplete_calls(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr-csv.csv"
    # Rows with empty cause are skipped
    _write_cdr(cdr, [
        "a,b,NORMAL_CLEARING",
        "a,b,",
        "a,b,0",
        "a,b,CALL_REJECTED",
    ])
    out = check_fs27_call_drop_rate(cdr_path=str(cdr))
    # Only 2 rows counted (NORMAL_CLEARING + CALL_REJECTED)
    assert out["values"]["fs27_total"] == 2


def test_fs27_metadata_total_vs_dropped(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr-csv.csv"
    _write_cdr(cdr, ["a,b,NORMAL_CLEARING", "a,b,CALL_REJECTED"])
    out = check_fs27_call_drop_rate(cdr_path=str(cdr))
    assert "fs27_total" in out["values"]
    assert "fs27_dropped" in out["values"]
    assert "fs27_drop_pct" in out["values"]


def test_fs27_handles_missing_cdr_dir(tmp_path: Path) -> None:
    out = check_fs27_call_drop_rate(cdr_path=str(tmp_path / "no" / "cdr.csv"))
    assert out["status"] == "warn"
    assert out["values"]["fs27_reason"] == "cdr missing"


# --- FS-28 -------------------------------------------------------------------

def test_fs28_ok_no_cause_codes_in_cdr(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr.csv"
    _write_cdr(cdr, ["a,b,NORMAL_CLEARING"] * 10)
    out = check_fs28_nat_binding_failures(cdr_path=str(cdr))
    assert out["status"] == "ok"
    assert out["values"]["fs28_total"] == 0


def test_fs28_warn_3_to_10_nat_failures(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr.csv"
    rows = ["a,b,41 from 10.0.0.1"] * 5
    _write_cdr(cdr, rows)
    out = check_fs28_nat_binding_failures(cdr_path=str(cdr))
    assert out["status"] == "warn"
    assert out["values"]["fs28_total"] == 5


def test_fs28_crit_above_10(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr.csv"
    rows = ["a,b,48 from 10.0.0.1"] * 15
    _write_cdr(cdr, rows)
    out = check_fs28_nat_binding_failures(cdr_path=str(cdr))
    assert out["status"] == "crit"


def test_fs28_recognizes_cause_41(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr.csv"
    _write_cdr(cdr, ["caller,callee,41 from 10.0.0.1"])
    out = check_fs28_nat_binding_failures(cdr_path=str(cdr))
    assert out["values"]["fs28_total"] == 1


def test_fs28_recognizes_cause_48(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr.csv"
    _write_cdr(cdr, ["caller,callee,48 from 10.0.0.1"])
    out = check_fs28_nat_binding_failures(cdr_path=str(cdr))
    assert out["values"]["fs28_total"] == 1


def test_fs28_recognizes_cause_49(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr.csv"
    _write_cdr(cdr, ["caller,callee,49 from 10.0.0.1"])
    out = check_fs28_nat_binding_failures(cdr_path=str(cdr))
    assert out["values"]["fs28_total"] == 1


def test_fs28_ignores_other_cause_codes(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr.csv"
    _write_cdr(cdr, [
        "caller,callee,16 from 10.0.0.1",  # NORMAL_CLEARING
        "caller,callee,127 from 10.0.0.1",  # not NAT
        "caller,callee,99 from 10.0.0.1",
    ])
    out = check_fs28_nat_binding_failures(cdr_path=str(cdr))
    assert out["values"]["fs28_total"] == 0


def test_fs28_groups_by_remote_ip(tmp_path: Path) -> None:
    cdr = tmp_path / "cdr.csv"
    _write_cdr(cdr, [
        "caller,callee,41 from 10.0.0.1",
        "caller,callee,41 from 10.0.0.1",
        "caller,callee,41 from 10.0.0.2",
    ])
    out = check_fs28_nat_binding_failures(cdr_path=str(cdr))
    by_ip = out["values"]["fs28_by_ip"]
    assert by_ip["10.0.0.1"] == 2
    assert by_ip["10.0.0.2"] == 1


def test_fs28_handles_missing_cdr_dir(tmp_path: Path) -> None:
    out = check_fs28_nat_binding_failures(cdr_path=str(tmp_path / "missing.csv"))
    assert out["status"] == "warn"
    assert out["values"]["fs28_reason"] == "cdr missing"