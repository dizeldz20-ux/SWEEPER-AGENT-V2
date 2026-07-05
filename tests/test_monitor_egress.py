"""Sprint 13.2 — egress detector tests."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ipracticom_sweeper.monitor.egress import (
    parse_ss_output,
    check_egress,
    EgressConnection,
    EgressResult,
    _ip_in_cidrs,
)


def _ss_output(lines: list[str]) -> str:
    """Build a fake `ss -tn` output."""
    header = "State  Recv-Q  Send-Q  Local Address:Port  Peer Address:Port"
    return header + "\n" + "\n".join(lines) + "\n"


# ============= parse_ss_output ==============================================

def test_parse_ss_empty() -> None:
    assert parse_ss_output("") == []


def test_parse_ss_header_only() -> None:
    text = "State  Recv-Q  Send-Q  Local Address:Port  Peer Address:Port\n"
    assert parse_ss_output(text) == []


def test_parse_ss_one_established() -> None:
    text = _ss_output(["ESTAB  0  0  10.0.0.5:443  1.2.3.4:443"])
    conns = parse_ss_output(text)
    assert len(conns) == 1
    assert conns[0].remote_ip == "1.2.3.4"
    assert conns[0].remote_port == 443
    assert conns[0].state == "ESTAB"


def test_parse_ss_skips_listen_state() -> None:
    text = _ss_output(["LISTEN 0 128 0.0.0.0:80  0.0.0.0:*"])
    assert parse_ss_output(text) == []


def test_parse_ss_handles_multiple_connections() -> None:
    text = _ss_output([
        "ESTAB  0  0  10.0.0.5:443  1.2.3.4:443",
        "ESTAB  0  0  10.0.0.5:80   5.6.7.8:54321",
    ])
    conns = parse_ss_output(text)
    assert len(conns) == 2


def test_parse_ss_skips_unparseable_lines() -> None:
    text = _ss_output(["garbage line", "ESTAB  0  0  10.0.0.5:443  1.2.3.4:443"])
    conns = parse_ss_output(text)
    assert len(conns) == 1


def test_parse_ss_skips_ipv6() -> None:
    """Simplification: only IPv4 for now."""
    text = _ss_output(["ESTAB  0  0  [::1]:443  [2606:4700::1]:443"])
    assert parse_ss_output(text) == []


def test_parse_ss_handles_empty_remote_port() -> None:
    text = _ss_output(["ESTAB  0  0  10.0.0.5:443  1.2.3.4:"])
    # rpartition gives ("1.2.3.4", ":", "") → port_str = "" → int() fails → skip
    assert parse_ss_output(text) == []


# ============= _ip_in_cidrs =================================================

def test_ip_in_cidrs_match() -> None:
    assert _ip_in_cidrs("10.0.0.5", ["10.0.0.0/24"])


def test_ip_in_cidrs_no_match() -> None:
    assert not _ip_in_cidrs("192.168.1.1", ["10.0.0.0/24"])


def test_ip_in_cidrs_handles_invalid_ip() -> None:
    assert not _ip_in_cidrs("not-an-ip", ["10.0.0.0/24"])


def test_ip_in_cidrs_handles_invalid_cidr() -> None:
    # Invalid CIDR is skipped, not raised
    assert not _ip_in_cidrs("10.0.0.5", ["not-a-cidr"])


def test_ip_in_cidrs_empty_list() -> None:
    assert not _ip_in_cidrs("10.0.0.5", [])


# ============= check_egress ==================================================

def test_egress_ok_no_connections() -> None:
    out = check_egress(ss_runner=lambda: "")
    assert out.status == "ok"
    assert out.total == 0


def test_egress_ok_only_allowed() -> None:
    def runner():
        return _ss_output(["ESTAB  0  0  10.0.0.5:443  1.2.3.4:443"])
    out = check_egress(allowlist=["1.2.3.0/24"], ss_runner=runner)
    assert out.status == "ok"


def test_egress_warn_new_remote_ip() -> None:
    def runner():
        return _ss_output(["ESTAB  0  0  10.0.0.5:443  8.8.8.8:443"])
    out = check_egress(allowlist=["1.2.3.0/24"], ss_runner=runner)
    assert out.status == "warn"
    assert any(c.remote_ip == "8.8.8.8" for c in out.unknown_ips)


def test_egress_crit_connection_to_blocked() -> None:
    def runner():
        return _ss_output(["ESTAB  0  0  10.0.0.5:443  6.6.6.6:443"])
    out = check_egress(denylist=["6.6.6.0/24"], ss_runner=runner)
    assert out.status == "crit"
    assert any(c.remote_ip == "6.6.6.6" for c in out.blocked_ips)


def test_egress_no_allowlist_means_no_warnings() -> None:
    """With no allowlist, all IPs are 'allowed' (only denylist applies)."""
    def runner():
        return _ss_output(["ESTAB  0  0  10.0.0.5:443  8.8.8.8:443"])
    out = check_egress(ss_runner=runner)
    assert out.status == "ok"


def test_egress_metadata_remote_ips() -> None:
    def runner():
        return _ss_output([
            "ESTAB  0  0  10.0.0.5:443  1.2.3.4:443",
            "ESTAB  0  0  10.0.0.5:443  5.6.7.8:443",
        ])
    out = check_egress(allowlist=["1.2.3.0/24"], ss_runner=runner)
    assert any(c.remote_ip == "5.6.7.8" for c in out.unknown_ips)


def test_egress_returns_dataclass() -> None:
    out = check_egress(ss_runner=lambda: "")
    assert isinstance(out, EgressResult)
    assert hasattr(out, "status")
    assert hasattr(out, "unknown_ips")
    assert hasattr(out, "blocked_ips")
    assert hasattr(out, "total")


def test_egress_uses_allowlist_from_config() -> None:
    """allowlist provided as parameter (not hardcoded)."""
    def runner():
        return _ss_output(["ESTAB  0  0  10.0.0.5:443  8.8.8.8:443"])
    # Different allowlist, different result
    out_a = check_egress(allowlist=["8.8.8.0/24"], ss_runner=runner)
    out_b = check_egress(allowlist=["1.0.0.0/8"], ss_runner=runner)
    assert out_a.status == "ok"
    assert out_b.status == "warn"


def test_egress_blocked_takes_precedence() -> None:
    """An IP in both allowlist and denylist → crit."""
    def runner():
        return _ss_output(["ESTAB  0  0  10.0.0.5:443  6.6.6.6:443"])
    out = check_egress(
        allowlist=["6.6.6.0/24"],
        denylist=["6.6.6.0/24"],
        ss_runner=runner,
    )
    assert out.status == "crit"


def test_egress_handles_empty_ss() -> None:
    out = check_egress(ss_runner=lambda: "")
    assert out.total == 0
    assert out.unknown_ips == []
    assert out.blocked_ips == []


def test_egress_counts_total() -> None:
    def runner():
        return _ss_output([
            "ESTAB  0  0  10.0.0.5:443  1.2.3.4:443",
            "ESTAB  0  0  10.0.0.5:443  5.6.7.8:443",
            "ESTAB  0  0  10.0.0.5:443  9.9.9.9:443",
        ])
    out = check_egress(ss_runner=runner)
    assert out.total == 3