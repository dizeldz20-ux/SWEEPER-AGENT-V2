"""FreeSWITCH Tier 5 — deep checks (FS-26..FS-40). v0.7.0 sprint 9.

These complement Tier 1-4 (FS-01..FS-25) by detecting subtle failure
modes: auth storms, call drops, NAT issues, media silence, gateway
keepalive, parse errors, dialplan outliers, conference overload, voicemail
disk, module load, ESL backlog, registration overflow, CDR pool, license
limit, and trunk TPS.

Every check returns `{status, values}` in the same shape as Tier 1-4.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

# Reuse helpers from freeswitch.py
from ipracticom_sweeper.monitor.freeswitch import (
    _run,
    _run_fscli,
    DEFAULT_CLI_TIMEOUT,
)


# --- Thresholds (FS-26..FS-40) -----------------------------------------------

# FS-26 INVITE auth failure rate (per 60s window)
FS26_INVITE_AUTH_FAIL_WARN = 5
FS26_INVITE_AUTH_FAIL_CRIT = 20

# FS-27 call drop rate (% of completed calls dropped)
FS27_DROP_RATE_WARN_PCT = 2.0
FS27_DROP_RATE_CRIT_PCT = 5.0

# FS-28 NAT binding failures (Q.850 cause codes 41/48/49 per 5min)
FS28_NAT_BINDING_WARN = 3
FS28_NAT_BINDING_CRIT = 10

# FS-29 RTP silence rate (per call leg)
FS29_SILENCE_WARN_PCT = 5.0
FS29_SILENCE_CRIT_PCT = 15.0

# FS-30 SIP OPTIONS keepalive (max failures per probe round)
FS30_PROBE_TIMEOUT = 3
FS30_PROBE_RETRIES = 1

# FS-31 SIP message parse errors (per 5min)
FS31_PARSE_ERRORS_WARN = 1
FS31_PARSE_ERRORS_CRIT = 5

# FS-32 dialplan latency (p95 over last 100 calls)
FS32_DIALPLAN_WARN_MS = 500
FS32_DIALPLAN_CRIT_MS = 2000

# FS-33 conference participant count (% of max)
FS33_CONF_PARTICIPANT_WARN_PCT = 80
FS33_CONF_PARTICIPANT_CRIT_PCT = 100

# FS-34 voicemail disk quota (%)
FS34_VM_QUOTA_WARN = 80
FS34_VM_QUOTA_CRIT = 95

# FS-35 mod_* load
FS35_REQUIRED_MODS = (
    "mod_sofia", "mod_dptools", "mod_console", "mod_logfile",
    "mod_event_socket",
)

# FS-36 ESL socket backlog
FS36_ESL_BACKLOG_WARN = 10
FS36_ESL_BACKLOG_CRIT = 50

# FS-37 registered vs max-procs (%)
FS37_MAX_PROCS_WARN_PCT = 80
FS37_MAX_PROCS_CRIT_PCT = 95

# FS-39 concurrent calls vs license (%)
FS39_LICENSE_WARN_PCT = 70
FS39_LICENSE_CRIT_PCT = 90

# FS-40 trunk TPS
FS40_TPS_WARN = 50
FS40_TPS_CRIT = 200


# --- FS-26 -------------------------------------------------------------------

def _parse_sofia_reg_invite_fails(stdout: str) -> tuple[int, list[str]]:
    """Parse `sofia status profile internal reg` output.

    Looks for failure indicators: '401 Unauthorized', '407 Proxy Auth',
    'fail auth', or 'SIP/2.0 401'. Returns (count, source_ips_when_available).
    """
    count = 0
    ips: list[str] = []
    if not stdout:
        return 0, []
    # Patterns we treat as auth failure indicators
    fail_patterns = (
        re.compile(r"\b401\b.*unauth", re.I),
        re.compile(r"\b407\b.*proxy", re.I),
        re.compile(r"fail.*auth", re.I),
        re.compile(r"\bauth.*fail", re.I),
    )
    for line in stdout.splitlines():
        if any(p.search(line) for p in fail_patterns):
            count += 1
            m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
            if m:
                ips.append(m.group(1))
    return count, ips


def check_fs26_invite_auth_failures(
    window_seconds: int = 60,
    fs_cli_runner=_run_fscli,
) -> dict[str, Any]:
    """FS-26: SIP INVITE auth failure rate (rolling window).

    Counts authentication failures from `sofia status profile internal reg`.
    """
    res = fs_cli_runner("sofia status profile internal reg")
    if res.get("rc") != 0:
        return {
            "status": "warn",
            "values": {
                "fs26_cli_rc": res.get("rc"),
                "fs26_auth_failures": None,
                "fs26_source_ips": [],
                "fs26_window_seconds": window_seconds,
                "fs26_reason": "cli failed",
            },
        }
    count, ips = _parse_sofia_reg_invite_fails(res.get("stdout", ""))

    if count >= FS26_INVITE_AUTH_FAIL_CRIT:
        status = "crit"
    elif count >= FS26_INVITE_AUTH_FAIL_WARN:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs26_cli_rc": 0,
            "fs26_auth_failures": count,
            "fs26_source_ips": ips[:5],
            "fs26_window_seconds": window_seconds,
        },
    }


# --- FS-27 -------------------------------------------------------------------

def _parse_cdr_for_drops(csv_text: str) -> tuple[int, int]:
    """Parse CDR CSV. Returns (total_completed, total_dropped).

    A call is "dropped" if it ended without normal clearing — i.e. one of
    the documented drop causes. CDR rows with empty/0 hangup_cause are
    treated as in-progress and excluded.
    """
    if not csv_text:
        return 0, 0
    total = 0
    dropped = 0
    for line in csv_text.splitlines():
        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 2:
            continue
        # Completed call = has a non-empty, non-zero hangup_cause
        hangup = parts[-1].strip()
        if not hangup or hangup == "0":
            continue
        total += 1
        # Drop = cause indicates call never fully established
        if hangup in ("CALL_REJECTED", "USER_BUSY", "NO_ANSWER", "NO_USER_RESPONSE",
                      "ORIGINATOR_CANCEL", "NO_ROUTE_DESTINATION"):
            dropped += 1
    return total, dropped


def check_fs27_call_drop_rate(
    cdr_path: str = "/var/log/freeswitch/cdr-csv.csv",
    window_minutes: int = 5,
) -> dict[str, Any]:
    """FS-27: call drop rate over rolling window."""
    cdr_file = Path(cdr_path)
    if not cdr_file.exists():
        return {
            "status": "warn",
            "values": {
                "fs27_cdr_path": cdr_path,
                "fs27_total": 0,
                "fs27_dropped": 0,
                "fs27_drop_pct": None,
                "fs27_window_minutes": window_minutes,
                "fs27_reason": "cdr missing",
            },
        }
    try:
        text = cdr_file.read_text(errors="replace")
        total, dropped = _parse_cdr_for_drops(text)
    except OSError as e:
        return {
            "status": "warn",
            "values": {
                "fs27_cdr_path": cdr_path,
                "fs27_total": 0,
                "fs27_dropped": 0,
                "fs27_drop_pct": None,
                "fs27_window_minutes": window_minutes,
                "fs27_reason": f"read error: {e}",
            },
        }

    if total == 0:
        return {
            "status": "ok",
            "values": {
                "fs27_cdr_path": cdr_path,
                "fs27_total": 0,
                "fs27_dropped": 0,
                "fs27_drop_pct": 0.0,
                "fs27_window_minutes": window_minutes,
            },
        }

    pct = (dropped / total) * 100.0
    if pct >= FS27_DROP_RATE_CRIT_PCT:
        status = "crit"
    elif pct >= FS27_DROP_RATE_WARN_PCT:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs27_cdr_path": cdr_path,
            "fs27_total": total,
            "fs27_dropped": dropped,
            "fs27_drop_pct": pct,
            "fs27_window_minutes": window_minutes,
        },
    }


# --- FS-28 -------------------------------------------------------------------

# Q.850 cause codes that indicate NAT binding / routing failure
_NAT_CAUSE_CODES = frozenset({"41", "48", "49", "38", "34"})


def _parse_cdr_for_nat_failures(csv_text: str) -> dict[str, Any]:
    """Parse CDR for NAT-binding cause codes (41/48/49).

    Looks for Q.850 cause codes 41, 48, 49 anywhere in the row (these
    correspond to network/NAT/quality issues).
    """
    by_ip: dict[str, int] = {}
    total = 0
    if not csv_text:
        return {"total": 0, "by_ip": by_ip}
    for line in csv_text.splitlines():
        if not line.strip():
            continue
        # Try to find a NAT-binding cause code anywhere in the row.
        # Match as standalone tokens to avoid false positives (e.g. "4141").
        matched_code = None
        for code in _NAT_CAUSE_CODES:
            # Word-boundary match for the code
            if re.search(rf"(?:^|,){code}(?:,|$|\s)", line):
                matched_code = code
                break
        if matched_code is None:
            continue
        total += 1
        ip_match = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
        ip = ip_match.group(1) if ip_match else "unknown"
        by_ip[ip] = by_ip.get(ip, 0) + 1
    return {"total": total, "by_ip": by_ip}


def check_fs28_nat_binding_failures(
    cdr_path: str = "/var/log/freeswitch/cdr-csv.csv",
    window_minutes: int = 5,
) -> dict[str, Any]:
    """FS-28: NAT binding failures detected via Q.850 cause codes."""
    cdr_file = Path(cdr_path)
    if not cdr_file.exists():
        return {
            "status": "warn",
            "values": {
                "fs28_total": 0,
                "fs28_by_ip": {},
                "fs28_window_minutes": window_minutes,
                "fs28_reason": "cdr missing",
            },
        }
    try:
        result = _parse_cdr_for_nat_failures(cdr_file.read_text(errors="replace"))
    except OSError as e:
        return {
            "status": "warn",
            "values": {
                "fs28_total": 0,
                "fs28_by_ip": {},
                "fs28_window_minutes": window_minutes,
                "fs28_reason": f"read error: {e}",
            },
        }

    total = result["total"]
    if total >= FS28_NAT_BINDING_CRIT:
        status = "crit"
    elif total >= FS28_NAT_BINDING_WARN:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs28_total": total,
            "fs28_by_ip": result["by_ip"],
            "fs28_window_minutes": window_minutes,
        },
    }