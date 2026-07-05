"""Tests for file descriptor monitor."""
from __future__ import annotations
from ipracticom_sweeper.monitor.fd_check import (
    parse_proc_fs_filenr,
    FdSystemStats,
)


def test_parse_proc_fs_filenr_basic():
    """Parse /proc/sys/fs/file-nr: 'alloc unused max'."""
    stats = parse_proc_fs_filenr("100 0 5000")
    assert stats.allocated == 100
    assert stats.unused == 0
    assert stats.max == 5000
    # used = allocated - unused (from kernel semantics)
    # used% = 100 / 5000 * 100 = 2.0
    assert abs(stats.used_percent - 2.0) < 0.01


def test_parse_proc_fs_filenr_handles_max_zero():
    """Edge case: max=0 should not crash (divide by zero protection)."""
    stats = parse_proc_fs_filenr("0 0 0")
    assert stats.allocated == 0
    assert stats.max == 0
    assert stats.used_percent == 0.0


def test_parse_handles_high_allocation():
    """If allocated > max (rare), used% is capped at 100."""
    stats = parse_proc_fs_filenr("5500 100 5000")
    assert stats.used_percent == 100.0


def test_parse_handles_empty():
    """Empty input = zeros."""
    stats = parse_proc_fs_filenr("")
    assert stats.allocated == 0
    assert stats.max == 0
    assert stats.used_percent == 0.0
