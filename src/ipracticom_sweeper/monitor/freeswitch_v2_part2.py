"""FreeSWITCH Tier 5 — FS-29..FS-40 deep checks.

Continues from freeswitch_v2.py (FS-26..FS-28). Same shape:
each check returns `{status, values}` for the snapshot pipeline.
"""
from __future__ import annotations

import gzip
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from ipracticom_sweeper._log import log_suppressed

from ipracticom_sweeper.monitor.freeswitch import (
    _run,
    _run_fscli,
    DEFAULT_CLI_TIMEOUT,
)
from ipracticom_sweeper.monitor.freeswitch_v2 import FS35_REQUIRED_MODS


# --- FS-29 RTP silence detection ---------------------------------------------

FS29_SILENCE_WARN_PCT = 5.0
FS29_SILENCE_CRIT_PCT = 15.0


def _parse_sofia_rtp_silence(stdout: str, silence_threshold_pct: float = FS29_SILENCE_WARN_PCT) -> dict[str, Any]:
    """Parse `sofia status profile internal` output for per-leg silence %.

    Returns dict {call_id: silence_pct} — empty dict if unparseable.
    """
    legs: dict[str, float] = {}
    if not stdout:
        return legs
    # Lines like: "Call-ID: abcdef, RTP: in=1200 lost=24 jitter=2 silence=85 (5.2%)"
    pattern = re.compile(
        r"Call-ID:\s*([\w\-]+).*?silence=(\d+)\s*\((\d+(?:\.\d+)?)%\)",
        re.I,
    )
    for m in pattern.finditer(stdout):
        call_id = m.group(1)
        silence_pct = float(m.group(3))
        legs[call_id] = silence_pct
    return legs


def check_fs29_rtp_silence(
    fs_cli_runner=_run_fscli,
    silence_threshold_pct: float = FS29_SILENCE_WARN_PCT,
) -> dict[str, Any]:
    """FS-29: RTP silence detection (per-call-leg)."""
    res = fs_cli_runner("sofia status profile internal")
    if res.get("rc") != 0:
        return {
            "status": "warn",
            "values": {
                "fs29_legs": {},
                "fs29_max_silence_pct": None,
                "fs29_silent_legs": 0,
                "fs29_reason": "cli failed",
            },
        }
    legs = _parse_sofia_rtp_silence(res.get("stdout", ""))
    silent_legs = sum(1 for pct in legs.values() if pct >= FS29_SILENCE_CRIT_PCT)
    max_silence = max(legs.values()) if legs else None

    if silent_legs > 0:
        status = "crit"
    elif max_silence is not None and max_silence >= FS29_SILENCE_WARN_PCT:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "values": {
            "fs29_legs": legs,
            "fs29_max_silence_pct": max_silence,
            "fs29_silent_legs": silent_legs,
            "fs29_threshold_pct": silence_threshold_pct,
        },
    }


# --- FS-30 SIP OPTIONS keepalive ---------------------------------------------

FS30_PROBE_TIMEOUT = 3


def _probe_sipsak(host: str, port: int, timeout: int) -> tuple[bool, float]:
    """Probe a SIP provider via sipsak. Returns (success, response_ms)."""
    start = time.time()
    try:
        # sipsak -s sip:user@host -c 1 --timeout=2s
        r = subprocess.run(
            ["sipsak", "-s", f"sip:ping@{host}", "-c", "1",
             "--timeout", f"{timeout}s", "-p", str(port)],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        elapsed_ms = (time.time() - start) * 1000
        # sipsak returns 0 on 200 OK from peer
        return r.returncode == 0, elapsed_ms
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        elapsed_ms = (time.time() - start) * 1000
        return False, elapsed_ms


def check_fs30_options_keepalive(
    providers: list[dict] | None = None,
    timeout: int = FS30_PROBE_TIMEOUT,
    retries: int = 1,
    probe_runner=_probe_sipsak,
) -> dict[str, Any]:
    """FS-30: SIP OPTIONS keepalive probe to each provider.

    `providers` is a list of {name, host, port} dicts.
    """
    if not providers:
        return {
            "status": "disabled",
            "values": {
                "fs30_providers": [],
                "fs30_results": {},
                "fs30_reason": "no providers configured",
            },
        }

    results: dict[str, dict] = {}
    failed_count = 0
    for p in providers:
        name = p.get("name", "?")
        host = p.get("host", "")
        port = p.get("port", 5060)

        success, response_ms = probe_runner(host, port, timeout)
        if not success and retries > 0:
            # retry once
            success, response_ms = probe_runner(host, port, timeout)

        results[name] = {
            "host": host,
            "port": port,
            "ok": success,
            "response_ms": response_ms,
        }
        if not success:
            failed_count += 1

    total = len(providers)
    if failed_count >= 2 and total <= 3:
        # Most/all providers down (when there are few) → crit
        status = "crit"
    elif failed_count == total:
        status = "crit"
    elif failed_count > 0:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "values": {
            "fs30_providers": [p.get("name") for p in providers],
            "fs30_results": results,
            "fs30_failed_count": failed_count,
            "fs30_total_count": total,
        },
    }


# --- FS-31 SIP message parse errors ------------------------------------------

FS31_PARSE_ERRORS_WARN = 1
FS31_PARSE_ERRORS_CRIT = 5


_PARSE_ERROR_PATTERNS = (
    re.compile(r"Failed to parse SIP", re.I),
    re.compile(r"SIP parse error", re.I),
    re.compile(r"malformed.*message", re.I),
)


def _count_parse_errors_in_window(log_path: Path, window_seconds: int = 300) -> int:
    """Count parse-error lines in `log_path` mtime-filtered to `window_seconds`."""
    if not log_path.exists():
        return 0
    cutoff = time.time() - window_seconds
    count = 0
    try:
        with log_path.open("r", errors="replace") as f:
            for line in f:
                if any(p.search(line) for p in _PARSE_ERROR_PATTERNS):
                    # Check mtime approximation — only count if file was modified recently
                    # (this is a coarse check; finer grained would need log timestamps)
                    if log_path.stat().st_mtime >= cutoff:
                        count += 1
    except OSError:
        return 0
    return count


def check_fs31_sip_parse_errors(
    log_path: str = "/var/log/freeswitch/freeswitch.log",
    window_seconds: int = 300,
) -> dict[str, Any]:
    """FS-31: SIP message parse errors over rolling window."""
    log = Path(log_path)
    if not log.exists():
        return {
            "status": "ok",
            "values": {
                "fs31_log_path": log_path,
                "fs31_parse_errors": 0,
                "fs31_window_seconds": window_seconds,
                "fs31_reason": "log missing (no signal = good)",
            },
        }
    count = _count_parse_errors_in_window(log, window_seconds)
    if count >= FS31_PARSE_ERRORS_CRIT:
        status = "crit"
    elif count >= FS31_PARSE_ERRORS_WARN:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs31_log_path": log_path,
            "fs31_parse_errors": count,
            "fs31_window_seconds": window_seconds,
        },
    }


# --- FS-32 dialplan execution time outliers ----------------------------------

FS32_DIALPLAN_WARN_MS = 500
FS32_DIALPLAN_CRIT_MS = 2000
FS32_MIN_SAMPLES = 10


def _parse_show_calls_latencies(stdout: str) -> list[int]:
    """Parse `show calls` detail output for dialplan execution times (ms)."""
    latencies: list[int] = []
    if not stdout:
        return latencies
    # Lines like: "uuid=abc, dialplan_time=350ms, ..."
    pattern = re.compile(r"dialplan[_\- ]?time[=:]\s*(\d+)\s*ms", re.I)
    for m in pattern.finditer(stdout):
        try:
            latencies.append(int(m.group(1)))
        except ValueError as exc:
            log_suppressed("monitor.fs.parse_dialplan_time", exc,
                           extras={"match": m.group(0)[:80]})
    return latencies


def _percentile(values: list[int], pct: float) -> float:
    """Compute percentile (0-100) over a non-empty sorted list."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = int((pct / 100.0) * (len(s) - 1))
    return float(s[idx])


def check_fs32_dialplan_latency(
    fs_cli_runner=_run_fscli,
    window: int = 100,
) -> dict[str, Any]:
    """FS-32: dialplan execution time outliers (p95 over recent calls)."""
    res = fs_cli_runner("show calls")
    if res.get("rc") != 0:
        return {
            "status": "warn",
            "values": {
                "fs32_samples": 0,
                "fs32_p95_ms": None,
                "fs32_reason": "cli failed",
            },
        }
    latencies = _parse_show_calls_latencies(res.get("stdout", ""))[:window]
    if len(latencies) < FS32_MIN_SAMPLES:
        return {
            "status": "ok",
            "values": {
                "fs32_samples": len(latencies),
                "fs32_p95_ms": None,
                "fs32_reason": "insufficient data",
            },
        }
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)

    if p95 >= FS32_DIALPLAN_CRIT_MS:
        status = "crit"
    elif p95 >= FS32_DIALPLAN_WARN_MS:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "values": {
            "fs32_samples": len(latencies),
            "fs32_p50_ms": p50,
            "fs32_p95_ms": p95,
            "fs32_p99_ms": p99,
        },
    }


# --- FS-33 conference participant count --------------------------------------

FS33_CONF_WARN_PCT = 80
FS33_CONF_CRIT_PCT = 100


def _parse_conference_list(stdout: str) -> dict[str, int]:
    """Parse `conference list` output. Returns {conf_name: member_count}."""
    confs: dict[str, int] = {}
    if not stdout:
        return confs
    # Format: "Conference 3000 (members: 5)"
    pattern = re.compile(r"Conference\s+(\S+)\s+\(members:\s*(\d+)\)", re.I)
    for m in pattern.finditer(stdout):
        try:
            confs[m.group(1)] = int(m.group(2))
        except ValueError as exc:
            log_suppressed("monitor.fs.parse_conf_members", exc,
                           extras={"match": m.group(0)[:80]})
    return confs


def check_fs33_conference_participants(
    max_participants: int = 100,
    fs_cli_runner=_run_fscli,
) -> dict[str, Any]:
    """FS-33: conference participant count vs configured max."""
    res = fs_cli_runner("conference list")
    if res.get("rc") != 0:
        return {
            "status": "warn",
            "values": {
                "fs33_conferences": {},
                "fs33_max_per_conf": max_participants,
                "fs33_reason": "cli failed",
            },
        }
    confs = _parse_conference_list(res.get("stdout", ""))

    status = "ok"
    over_conf: dict[str, int] = {}
    for name, count in confs.items():
        if count > max_participants:
            over_conf[name] = count
            status = "crit"
        elif (count / max_participants * 100) >= FS33_CONF_WARN_PCT:
            if status != "crit":
                status = "warn"

    return {
        "status": status,
        "values": {
            "fs33_conferences": confs,
            "fs33_max_per_conf": max_participants,
            "fs33_over_limit": over_conf,
        },
    }


# --- FS-34 voicemail disk quota ----------------------------------------------

FS34_VM_QUOTA_WARN = 80
FS34_VM_QUOTA_CRIT = 95


def _get_voicemail_quota(fs_xml: Path) -> int:
    """Read voicemail quota from freeswitch.xml (in MB). Returns 0 if unset."""
    if not fs_xml.exists():
        return 0
    try:
        text = fs_xml.read_text(errors="replace")
        m = re.search(r'<param\s+name="quota"\s+value="(\d+)"', text)
        if m:
            return int(m.group(1))
    except OSError as exc:
        log_suppressed("monitor.fs.read_quota", exc,
                       extras={"path": str(fs_xml)})
    return 0


def check_fs34_voicemail_quota(
    vm_dir: str = "/var/lib/freeswitch/voicemail",
    fs_xml: str = "/etc/freeswitch/freeswitch.xml",
) -> dict[str, Any]:
    """FS-34: voicemail disk usage vs configured quota."""
    dir_path = Path(vm_dir)
    if not dir_path.exists():
        return {
            "status": "disabled",
            "values": {
                "fs34_vm_dir": vm_dir,
                "fs34_used_bytes": 0,
                "fs34_quota_bytes": 0,
                "fs34_used_pct": None,
                "fs34_reason": "voicemail dir missing",
            },
        }
    quota_mb = _get_voicemail_quota(Path(fs_xml))
    quota_bytes = quota_mb * 1024 * 1024 if quota_mb > 0 else 0

    # Walk the dir to compute used bytes
    used_bytes = 0
    try:
        for p in dir_path.rglob("*"):
            if p.is_file():
                try:
                    used_bytes += p.stat().st_size
                except OSError as exc:
                    log_suppressed("monitor.fs.dir_size_stat", exc,
                                   extras={"path": str(p)})
    except OSError as exc:
        log_suppressed("monitor.fs.dir_size_rglob", exc,
                       extras={"dir": str(dir_path)})

    if quota_bytes == 0:
        # Treat as unlimited
        return {
            "status": "ok",
            "values": {
                "fs34_vm_dir": vm_dir,
                "fs34_used_bytes": used_bytes,
                "fs34_quota_bytes": 0,
                "fs34_used_pct": None,
                "fs34_reason": "no quota configured",
            },
        }

    pct = (used_bytes / quota_bytes) * 100.0
    if pct >= FS34_VM_QUOTA_CRIT:
        status = "crit"
    elif pct >= FS34_VM_QUOTA_WARN:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs34_vm_dir": vm_dir,
            "fs34_used_bytes": used_bytes,
            "fs34_quota_bytes": quota_bytes,
            "fs34_used_pct": pct,
        },
    }


# --- FS-35 mod_* load health -------------------------------------------------


def _parse_show_modules(stdout: str) -> set[str]:
    """Parse `show modules` output. Returns set of loaded module names."""
    loaded: set[str] = set()
    if not stdout:
        return loaded
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Lines may be: "mod_sofia,type,api" or "mod_sofia type api" — split on both
        first = line.replace(",", " ").split()[0]
        if first.startswith("mod_"):
            loaded.add(first)
    return loaded


def check_fs35_mod_load(
    required_modules: tuple = FS35_REQUIRED_MODS,
    fs_cli_runner=_run_fscli,
) -> dict[str, Any]:
    """FS-35: required modules loaded."""
    res = fs_cli_runner("show modules")
    if res.get("rc") != 0:
        return {
            "status": "warn",
            "values": {
                "fs35_loaded_count": 0,
                "fs35_required": list(required_modules),
                "fs35_missing": list(required_modules),
                "fs35_reason": "cli failed",
            },
        }
    loaded = _parse_show_modules(res.get("stdout", ""))
    missing = [m for m in required_modules if m not in loaded]

    if not missing:
        status = "ok"
    elif any(m in {"mod_sofia", "mod_dptools"} for m in missing):
        # Core modules missing → crit
        status = "crit"
    else:
        status = "warn"

    return {
        "status": status,
        "values": {
            "fs35_loaded_count": len(loaded),
            "fs35_required": list(required_modules),
            "fs35_missing": missing,
        },
    }


# --- FS-36 ESL socket backlog -----------------------------------------------

FS36_ESL_BACKLOG_WARN = 10
FS36_ESL_BACKLOG_CRIT = 50


def _parse_event_queue_depth(stdout: str) -> Optional[int]:
    """Parse `status` output for the event queue depth."""
    if not stdout:
        return None
    m = re.search(r"event[_\s]queue[_\s]depth[=:\s]+(\d+)", stdout, re.I)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def check_fs36_esl_backlog(
    fs_cli_runner=_run_fscli,
) -> dict[str, Any]:
    """FS-36: ESL event queue backlog."""
    res = fs_cli_runner("status")
    if res.get("rc") != 0:
        return {
            "status": "warn",
            "values": {
                "fs36_queue_depth": None,
                "fs36_reason": "cli failed",
            },
        }
    depth = _parse_event_queue_depth(res.get("stdout", ""))
    if depth is None:
        return {
            "status": "warn",
            "values": {
                "fs36_queue_depth": None,
                "fs36_reason": "could not parse queue depth",
            },
        }
    if depth >= FS36_ESL_BACKLOG_CRIT:
        status = "crit"
    elif depth >= FS36_ESL_BACKLOG_WARN:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs36_queue_depth": depth,
            "fs36_warn_threshold": FS36_ESL_BACKLOG_WARN,
            "fs36_crit_threshold": FS36_ESL_BACKLOG_CRIT,
        },
    }


# --- FS-37 registered count vs max-procs ------------------------------------

FS37_MAX_PROCS_WARN_PCT = 80
FS37_MAX_PROCS_CRIT_PCT = 95


def _parse_sofia_profile_procs(stdout: str) -> dict[str, dict[str, int]]:
    """Parse `sofia status profile` output.

    Returns {profile_name: {"registered": N, "max_procs": M}}.
    """
    profiles: dict[str, dict[str, int]] = {}
    if not stdout:
        return profiles
    # Multi-line parsing — accept either 'Name' or 'profile name'
    current_name: Optional[str] = None
    for line in stdout.splitlines():
        nm = re.search(r"(?:profile\s+name|Name)[:=]\s*(\S+)", line, re.I)
        if nm:
            current_name = nm.group(1)
            assert current_name is not None
            profiles.setdefault(current_name, {"registered": 0, "max_procs": 0})
        if current_name is None:
            continue
        reg = re.search(r"registered[:=]\s*(\d+)", line, re.I)
        if reg:
            profiles[current_name]["registered"] = int(reg.group(1))
        mx = re.search(r"max[_\- ]?procs[:=]\s*(\d+)", line, re.I)
        if mx:
            profiles[current_name]["max_procs"] = int(mx.group(1))
    return profiles


def check_fs37_max_procs(
    fs_cli_runner=_run_fscli,
) -> dict[str, Any]:
    """FS-37: registered vs max-procs per profile."""
    res = fs_cli_runner("sofia status profile")
    if res.get("rc") != 0:
        return {
            "status": "warn",
            "values": {
                "fs37_profiles": {},
                "fs37_reason": "cli failed",
            },
        }
    profiles = _parse_sofia_profile_procs(res.get("stdout", ""))

    # Compute worst per-profile ratio
    worst_pct = 0.0
    over: dict[str, dict] = {}
    for name, p in profiles.items():
        max_procs = p.get("max_procs", 0)
        reg = p.get("registered", 0)
        if max_procs > 0:
            pct = (reg / max_procs) * 100.0
            if pct > worst_pct:
                worst_pct = pct
            if pct >= FS37_MAX_PROCS_CRIT_PCT:
                over[name] = {"registered": reg, "max_procs": max_procs, "pct": pct}

    if over:
        status = "crit"
    elif worst_pct >= FS37_MAX_PROCS_WARN_PCT:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "values": {
            "fs37_profiles": profiles,
            "fs37_worst_pct": worst_pct,
            "fs37_over_limit": over,
        },
    }


# --- FS-38 CDR DB connection pool --------------------------------------------


def check_fs38_cdr_db_pool(
    psql_runner=None,
) -> dict[str, Any]:
    """FS-38: PostgreSQL connection pool used by CDR writer.

    Reuses collectors.pg.collect_pg_stats (delegated via psql_runner for
    testability).
    """
    if psql_runner is None:
        # Production path — defer to collectors
        from ipracticom_sweeper.collectors.pg import collect_pg_stats
        try:
            stats = collect_pg_stats()
        except Exception as e:
            return {
                "status": "warn",
                "values": {
                    "fs38_active": None,
                    "fs38_max": None,
                    "fs38_pct": None,
                    "fs38_reason": f"pg collect failed: {e}",
                },
            }
        active = stats.active_connections
        max_conn = stats.max_connections
    else:
        # Test path
        active, max_conn = psql_runner()

    if max_conn == 0:
        return {
            "status": "warn",
            "values": {
                "fs38_active": active,
                "fs38_max": None,
                "fs38_pct": None,
                "fs38_reason": "max_connections unknown",
            },
        }

    pct = (active / max_conn) * 100.0
    if pct >= 90:
        status = "crit"
    elif pct >= 70:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "values": {
            "fs38_active": active,
            "fs38_max": max_conn,
            "fs38_pct": pct,
        },
    }


# --- FS-39 concurrent calls vs license ---------------------------------------

FS39_LICENSE_WARN_PCT = 70
FS39_LICENSE_CRIT_PCT = 90


def check_fs39_license(
    license_max_concurrent: int = 0,
    fs_cli_runner=_run_fscli,
) -> dict[str, Any]:
    """FS-39: total concurrent calls vs license limit."""
    res = fs_cli_runner("show calls count")
    if res.get("rc") != 0:
        return {
            "status": "warn",
            "values": {
                "fs39_active_calls": None,
                "fs39_license": license_max_concurrent,
                "fs39_reason": "cli failed",
            },
        }
    try:
        n = int(res.get("stdout", "").strip().split()[0])
    except (ValueError, IndexError):
        return {
            "status": "warn",
            "values": {
                "fs39_active_calls": None,
                "fs39_license": license_max_concurrent,
                "fs39_reason": "could not parse calls count",
            },
        }
    if license_max_concurrent <= 0:
        return {
            "status": "ok",
            "values": {
                "fs39_active_calls": n,
                "fs39_license": 0,
                "fs39_pct": None,
                "fs39_reason": "license unlimited",
            },
        }
    pct = (n / license_max_concurrent) * 100.0
    if pct >= FS39_LICENSE_CRIT_PCT:
        status = "crit"
    elif pct >= FS39_LICENSE_WARN_PCT:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs39_active_calls": n,
            "fs39_license": license_max_concurrent,
            "fs39_pct": pct,
        },
    }


# --- FS-40 SIP trunk TPS -----------------------------------------------------

FS40_TPS_WARN = 50
FS40_TPS_CRIT = 200


def check_fs40_trunk_tps(
    log_path: str = "/var/log/freeswitch/freeswitch.log",
    window_seconds: int = 5,
) -> dict[str, Any]:
    """FS-40: SIP trunk transactions per second (rolling 5s window)."""
    log = Path(log_path)
    if not log.exists():
        return {
            "status": "warn",
            "values": {
                "fs40_tps": 0,
                "fs40_window_seconds": window_seconds,
                "fs40_reason": "log missing",
            },
        }
    cutoff = time.time() - window_seconds
    if log.stat().st_mtime < cutoff:
        # Log file is older than the window — assume zero activity
        return {
            "status": "ok",
            "values": {
                "fs40_tps": 0,
                "fs40_window_seconds": window_seconds,
                "fs40_reason": "log older than window",
            },
        }
    # Count SIP methods (INVITE/REGISTER/BYE/OPTIONS) in the log
    sip_methods = re.compile(r"\b(INVITE|REGISTER|BYE|OPTIONS|CANCEL|ACK)\b")
    count = 0
    try:
        with log.open("r", errors="replace") as f:
            for line in f:
                if sip_methods.search(line):
                    count += 1
    except OSError:
        return {
            "status": "warn",
            "values": {
                "fs40_tps": None,
                "fs40_window_seconds": window_seconds,
                "fs40_reason": "read error",
            },
        }
    tps = count / window_seconds

    if tps >= FS40_TPS_CRIT:
        status = "crit"
    elif tps >= FS40_TPS_WARN:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "values": {
            "fs40_tps": tps,
            "fs40_window_seconds": window_seconds,
            "fs40_sip_methods_total": count,
        },
    }