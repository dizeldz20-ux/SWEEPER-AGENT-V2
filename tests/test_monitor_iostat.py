"""Tests for iostat I/O latency collector."""
from __future__ import annotations
from ipracticom_sweeper.monitor.iostat import parse_iostat_output, IostatDevice


# Sample iostat -dx 1 2 output (we parse the 2nd sample, which is averaged)
SAMPLE_IOSTAT = """\
Linux 5.15.0-91-generic (host) 	06/29/2026 	_x86_64_	(6 CPU)

Device            r/s     w/s     rkB/s    wkB/s   rrqm/s   wrqm/s  %rrqm  %wrqm r_await w_await aqu-sz rarez-sz warez-sz  svctm  %util
loop0            0.00    0.00      0.00      0.00     0.00     0.00   0.00   0.00    0.00    0.00   0.00     0.00     0.00   0.00   0.00
sda              0.50    2.00     12.00     8.00     0.00     0.50   0.00  20.00    1.20   50.00   0.10     0.00     0.00   0.50   1.50
nvme0n1         10.00   5.00    500.00   200.00     0.00     0.00   0.00   0.00    0.30    0.20   0.01     0.00     0.00   0.10   0.50
"""


def test_parse_extracts_devices():
    """Should return one IostatDevice per device line."""
    devices = parse_iostat_output(SAMPLE_IOSTAT)
    device_names = [d.device for d in devices]
    assert "sda" in device_names
    assert "nvme0n1" in device_names
    # loop devices should be filtered (all-zero, no real I/O)
    assert "loop0" not in device_names


def test_parse_extracts_await():
    """r_await + w_await are summed into await_ms."""
    devices = parse_iostat_output(SAMPLE_IOSTAT)
    sda = next(d for d in devices if d.device == "sda")
    # r_await=1.20, w_await=50.00 → 51.20 (we store as ms*100, see parser)
    assert sda.r_await_ms is not None
    assert sda.w_await_ms is not None
    assert abs(sda.r_await_ms - 1.2) < 0.01
    assert abs(sda.w_await_ms - 50.0) < 0.01


def test_parse_extracts_util_percent():
    """svctm or %util is captured."""
    devices = parse_iostat_output(SAMPLE_IOSTAT)
    sda = next(d for d in devices if d.device == "sda")
    assert sda.util_percent is not None
    assert abs(sda.util_percent - 1.5) < 0.01


def test_parse_handles_empty():
    """Empty input = no devices."""
    devices = parse_iostat_output("")
    assert devices == []


def test_parse_filters_zero_activity():
    """Devices with r/s=0 and w/s=0 are excluded (loop, idle, etc.)."""
    devices = parse_iostat_output(SAMPLE_IOSTAT)
    # loop0 is excluded; sda and nvme0n1 are included (have non-zero r/s or w/s)
    assert all(d.rps > 0 or d.wps > 0 for d in devices)
