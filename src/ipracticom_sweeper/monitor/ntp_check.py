"""Sprint 12.3 — NTP clock-skew check.

Parses chronyc tracking (preferred) or ntpq -p output, extracts the
last offset, and classifies it:
  |offset| < 100ms → ok
  100ms..1s → warn
  > 1s → crit
If neither chrony nor ntp is available, returns disabled.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from .._log import log_suppressed


CHRONY_OFFSET_RE = re.compile(
    r"Last\s+offset\s*:\s*([+-]?\d+(?:\.\d+)?)\s*(\w+)",
    re.IGNORECASE,
)
NTPQ_OFFSET_RE = re.compile(
    r"^[\s\*+#o-]?\S+\s+\S+\s+\d+\s+\S+\s+\d+\s+\d+\s+\d+\s+"
    r"[+-]?\d+(?:\.\d+)?\s+([+-]?\d+(?:\.\d+)?)\s*$"
)


@dataclass
class NtpResult:
    status: str                # ok | warn | crit | disabled | unknown
    offset_seconds: Optional[float]
    source: str                # "chrony" | "ntpq" | "none"
    error: str = ""


def _parse_offset_seconds(value: float, unit: str) -> float:
    """Convert value+unit to seconds."""
    u = unit.lower()
    if u.startswith("nsec"):
        return value * 1e-9
    if u.startswith("usec") or u.startswith("microsec") or u == "µs":
        return value * 1e-6
    if u.startswith("msec") or u in ("ms", "millisec", "millisecs", "millisecond", "milliseconds"):
        return value * 1e-3
    if u in ("s", "sec", "secs", "second", "seconds"):
        return float(value)
    # Unknown unit — return as seconds (best effort)
    return float(value)


def _run_chronyc() -> Optional[str]:
    try:
        r = subprocess.run(
            ["chronyc", "tracking"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _run_ntpq() -> Optional[str]:
    try:
        r = subprocess.run(
            ["ntpq", "-p"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _parse_chronyc(stdout: str) -> Optional[float]:
    for line in stdout.splitlines():
        m = CHRONY_OFFSET_RE.search(line)
        if m:
            try:
                return _parse_offset_seconds(float(m.group(1)), m.group(2))
            except ValueError as e:
                log_suppressed("ntp_parse_chronyc", e)
                continue
    return None


def _parse_ntpq(stdout: str) -> Optional[float]:
    for line in stdout.splitlines():
        # Skip header line (contains "refid") and dashed separators (start with "=")
        if "refid" in line.lower() or line.startswith("="):
            continue
        # For lines with a tally code (* + # o - space), the offset is in the
        # 9th column. Try split-based parsing first (more robust than regex).
        stripped = line.lstrip()
        if stripped and stripped[0] in "*+#o- ":
            parts = stripped.split()
            # Strip the tally code if present
            if parts and parts[0] in ("*", "+", "#", "o", "-"):
                parts = parts[1:]
            # Expected: remote refid st t when poll reach delay offset jitter
            if len(parts) >= 9:
                try:
                    # ntpq -p offset column is in seconds (with decimal)
                    return float(parts[8])
                except ValueError as e:
                    log_suppressed("ntp_parse_ntpq_offset", e)
        # Fallback: regex match
        m = NTPQ_OFFSET_RE.match(line)
        if m:
            try:
                # ntpq -p offset column is in seconds
                return float(m.group(1))
            except ValueError as e:
                log_suppressed("ntp_parse_regex_offset", e)
                continue
    return None


def check_ntp(
    warn_threshold_s: float = 0.1,
    crit_threshold_s: float = 1.0,
    chrony_runner=None,
    ntpq_runner=None,
) -> NtpResult:
    """Check NTP clock skew."""
    if chrony_runner is None:
        chrony_runner = _run_chronyc
    if ntpq_runner is None:
        ntpq_runner = _run_ntpq

    # Try chrony first
    chrony_out = chrony_runner()
    if chrony_out:
        off = _parse_chronyc(chrony_out)
        if off is not None:
            return _classify(off, "chrony", warn_threshold_s, crit_threshold_s)

    # Fall back to ntpq
    ntpq_out = ntpq_runner()
    if ntpq_out:
        off = _parse_ntpq(ntpq_out)
        if off is not None:
            return _classify(off, "ntpq", warn_threshold_s, crit_threshold_s)

    return NtpResult(
        status="disabled",
        offset_seconds=None,
        source="none",
        error="no_ntp_daemon",
    )


def _classify(offset: float, source: str, warn: float, crit: float) -> NtpResult:
    abs_off = abs(offset)
    if abs_off < warn:
        status = "ok"
    elif abs_off < crit:
        status = "warn"
    else:
        status = "crit"
    return NtpResult(
        status=status,
        offset_seconds=offset,
        source=source,
    )
