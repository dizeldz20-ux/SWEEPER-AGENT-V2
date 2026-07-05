"""Sprint 12 — Network + Service Probes tests (24 tests)."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from ipracticom_sweeper.monitor.healthz_probe import (
    probe_healthz,
    probe_healthz_list,
    HealthzResult,
)
from ipracticom_sweeper.monitor.systemd_state import (
    check_unit_state,
    SystemdStateResult,
)
from ipracticom_sweeper.monitor.ntp_check import (
    check_ntp,
    NtpResult,
    _parse_chronyc,
    _parse_ntpq,
)


# ============= healthz_probe ===============================================

def test_healthz_ok_2xx() -> None:
    fake = MagicMock()
    fake.__enter__ = lambda s: s
    fake.__exit__ = lambda s, *a: False
    fake.getcode.return_value = 200
    with patch("urllib.request.urlopen", return_value=fake):
        r = probe_healthz("http://example.com/healthz")
    assert r.status == "ok"
    assert r.status_code == 200


def test_healthz_warn_5xx() -> None:
    import urllib.error
    err = urllib.error.HTTPError("http://x", 503, "Unavailable", {}, None)
    with patch("urllib.request.urlopen", side_effect=err):
        r = probe_healthz("http://example.com/healthz")
    assert r.status == "warn"
    assert r.status_code == 503


def test_healthz_crit_on_4xx() -> None:
    import urllib.error
    err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
    with patch("urllib.request.urlopen", side_effect=err):
        r = probe_healthz("http://example.com/healthz")
    assert r.status == "crit"


def test_healthz_crit_on_connection_refused() -> None:
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError()):
        r = probe_healthz("http://example.com/healthz")
    assert r.status == "crit"
    assert r.status_code is None


def test_healthz_crit_on_timeout() -> None:
    with patch("urllib.request.urlopen", side_effect=TimeoutError()):
        r = probe_healthz("http://example.com/healthz")
    assert r.status == "crit"


def test_healthz_disabled_no_url() -> None:
    r = probe_healthz("")
    assert r.status == "disabled"


def test_healthz_latency_in_metadata() -> None:
    fake = MagicMock()
    fake.__enter__ = lambda s: s
    fake.__exit__ = lambda s, *a: False
    fake.getcode.return_value = 200
    with patch("urllib.request.urlopen", return_value=fake):
        r = probe_healthz("http://x")
    assert r.latency_ms is not None
    assert r.latency_ms >= 0


def test_healthz_list_probes_each() -> None:
    fake = MagicMock()
    fake.__enter__ = lambda s: s
    fake.__exit__ = lambda s, *a: False
    fake.getcode.return_value = 200
    with patch("urllib.request.urlopen", return_value=fake):
        results = probe_healthz_list([
            {"name": "a", "url": "http://a"},
            {"name": "b", "url": "http://b"},
        ])
    assert len(results) == 2
    assert all(r.status == "ok" for r in results)


def test_healthz_list_skips_disabled() -> None:
    results = probe_healthz_list([
        {"name": "a", "url": ""},
        {"name": "b"},  # no url at all
    ])
    assert all(r.status == "disabled" for r in results)


# ============= systemd_state ===============================================

def test_systemd_ok_active() -> None:
    def runner(unit):
        return (0, "ActiveState=active\nLoadState=loaded\nUnitFileState=enabled\n")
    r = check_unit_state("x.service", systemctl_runner=runner)
    assert r.status == "ok"
    assert r.active_state == "active"


def test_systemd_warn_inactive() -> None:
    def runner(unit):
        return (0, "ActiveState=inactive\nLoadState=loaded\nUnitFileState=enabled\n")
    r = check_unit_state("x.service", systemctl_runner=runner)
    assert r.status == "warn"


def test_systemd_crit_failed() -> None:
    def runner(unit):
        return (0, "ActiveState=failed\nLoadState=loaded\nUnitFileState=enabled\n")
    r = check_unit_state("x.service", systemctl_runner=runner)
    assert r.status == "crit"


def test_systemd_crit_masked() -> None:
    def runner(unit):
        return (0, "ActiveState=inactive\nLoadState=masked\nUnitFileState=masked\n")
    r = check_unit_state("x.service", systemctl_runner=runner)
    assert r.status == "crit"


def test_systemd_warn_disabled_at_boot() -> None:
    def runner(unit):
        return (0, "ActiveState=inactive\nLoadState=loaded\nUnitFileState=disabled\n")
    r = check_unit_state("x.service", systemctl_runner=runner)
    assert r.status == "warn"


def test_systemd_unknown_when_not_found() -> None:
    def runner(unit):
        return (0, "ActiveState=inactive\nLoadState=not-found\nUnitFileState=invalid\n")
    r = check_unit_state("x.service", systemctl_runner=runner)
    assert r.status == "unknown"


def test_systemd_disabled_no_unit() -> None:
    r = check_unit_state("")
    assert r.status == "disabled"


def test_systemd_handles_systemctl_failure() -> None:
    def runner(unit):
        return (-1, "")
    r = check_unit_state("x.service", systemctl_runner=runner)
    assert r.status == "unknown"


# ============= ntp_check ===================================================

def test_ntp_ok_under_100ms() -> None:
    r = check_ntp(
        chrony_runner=lambda: "Reference ID    : C0A80101 (...)\nLast offset     : +0.045 seconds\n",
        ntpq_runner=lambda: None,
    )
    assert r.status == "ok"
    assert r.offset_seconds is not None
    assert abs(r.offset_seconds - 0.045) < 1e-6


def test_ntp_warn_100ms_to_1s() -> None:
    r = check_ntp(
        chrony_runner=lambda: "Last offset     : +0.500 seconds\n",
        ntpq_runner=lambda: None,
    )
    assert r.status == "warn"


def test_ntp_crit_above_1s() -> None:
    r = check_ntp(
        chrony_runner=lambda: "Last offset     : +2.300 seconds\n",
        ntpq_runner=lambda: None,
    )
    assert r.status == "crit"


def test_ntp_uses_chrony_when_available() -> None:
    r = check_ntp(
        chrony_runner=lambda: "Last offset     : +0.001 seconds\n",
        ntpq_runner=lambda: "should not be used",
    )
    assert r.source == "chrony"


def test_ntp_falls_back_to_ntpq() -> None:
    r = check_ntp(
        chrony_runner=lambda: None,
        ntpq_runner=lambda: "*time.cloudflare  10.0.0.1  2 u 100 256 377  10.123  0.456  1.234\n",
    )
    assert r.source == "ntpq"


def test_ntp_handles_no_daemon() -> None:
    r = check_ntp(chrony_runner=lambda: None, ntpq_runner=lambda: None)
    assert r.status == "disabled"


def test_ntp_negative_offset() -> None:
    r = check_ntp(
        chrony_runner=lambda: "Last offset     : -0.500 seconds\n",
        ntpq_runner=lambda: None,
    )
    assert r.status == "warn"
    assert r.offset_seconds < 0


def test_ntp_ms_units() -> None:
    """NTP can report offset in milliseconds."""
    r = check_ntp(
        chrony_runner=lambda: "Last offset     : +50 milliseconds\n",
        ntpq_runner=lambda: None,
    )
    assert r.status == "ok"
    assert abs(r.offset_seconds - 0.05) < 1e-6


def test_ntp_parses_chronyc_units() -> None:
    assert _parse_chronyc("Last offset     : +0.100 seconds\n") == 0.1
    assert _parse_chronyc("Last offset     : +500 milliseconds\n") == 0.5
    assert _parse_chronyc("Last offset     : +1000 microseconds\n") == 1e-3
    assert _parse_chronyc("garbage") is None
    assert _parse_chronyc("") is None


def test_ntp_parses_ntpq() -> None:
    # Standard ntpq -p output: remote refid st t when poll reach delay offset jitter
    out = "*time.cloudflare  10.0.0.1  2 u 100 256 377  10.123  0.456  1.234\n"
    assert _parse_ntpq(out) == 0.456


def test_ntp_parses_ntpq_skips_header() -> None:
    out = "remote           refid      st t when poll reach   delay   offset  jitter\n"
    out += "*time.cloudflare  10.0.0.1  2 u 100 256 377  10.123  0.456  1.234\n"
    assert _parse_ntpq(out) == 0.456


# ============= helpers =====================================================

def test_healthz_result_dataclass() -> None:
    r = HealthzResult(status="ok", status_code=200, latency_ms=5.0, url="x")
    assert r.status == "ok"


def test_systemd_state_result_dataclass() -> None:
    r = SystemdStateResult(
        status="ok", active_state="active", load_state="loaded",
        unit_file_state="enabled", unit="x",
    )
    assert r.unit == "x"


def test_ntp_result_dataclass() -> None:
    r = NtpResult(status="ok", offset_seconds=0.001, source="chrony")
    assert r.source == "chrony"