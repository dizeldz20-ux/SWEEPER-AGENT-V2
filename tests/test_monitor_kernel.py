"""Tests for kernel error detector (Oops, MCE, segfaults)."""
from __future__ import annotations
from ipracticom_sweeper.monitor.kernel_errors import (
    parse_dmesg_output,
    KernelError,
)


SAMPLE_DMESG = """\
[Mon Jun 29 12:00:01 2026] systemd[1]: Started Daily Cleanup.
[Mon Jun 29 12:00:05 2026] kernel: BUG: unable to handle kernel paging request at 00000000deadbeef
[Mon Jun 29 12:00:06 2026] kernel: Oops: 0002 [#1] SMP NOPTI
[Mon Jun 29 12:00:10 2026] kernel: Machine Check Exception: 5
[Mon Jun 29 12:00:15 2026] php-fpm[1234]: segfault at 0 ip 0x7f8b8c0d2e3f sp 0x7ffd4e2c1a00
[Mon Jun 29 12:00:20 2026] cron[5678]: (root) CMD (test)
"""


def test_parse_dmesg_finds_oops():
    """Oops lines are captured as CRIT-severity kernel errors."""
    errors = parse_dmesg_output(SAMPLE_DMESG, window_minutes=60)
    kinds = [e.kind for e in errors]
    assert "kernel_oops" in kinds


def test_parse_dmesg_finds_mce():
    """Machine Check Exceptions are CRIT."""
    errors = parse_dmesg_output(SAMPLE_DMESG, window_minutes=60)
    kinds = [e.kind for e in errors]
    assert "machine_check_exception" in kinds


def test_parse_dmesg_finds_segfaults():
    """Segfaults are WARN (less critical than Oops/MCE)."""
    errors = parse_dmesg_output(SAMPLE_DMESG, window_minutes=60)
    kinds = [e.kind for e in errors]
    assert "segfault" in kinds


def test_parse_dmesg_ignores_normal_lines():
    """systemd, cron etc. should NOT be captured as errors."""
    errors = parse_dmesg_output(SAMPLE_DMESG, window_minutes=60)
    messages = [e.message for e in errors]
    assert not any("systemd" in m for m in messages)
    assert not any("cron" in m for m in messages)


def test_parse_handles_empty_output():
    """Empty dmesg = no errors."""
    errors = parse_dmesg_output("", window_minutes=60)
    assert errors == []
