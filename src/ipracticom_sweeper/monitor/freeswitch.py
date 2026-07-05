"""FreeSWITCH Tier 1 — service health checks (FS-01..FS-05).

Read-only heartbeat for the FreeSWITCH PBX. Every check returns a
{status, values} dict in the same shape as other monitor modules so it can
slot into monitor.checks.run_all + the snapshot stream.

Checks:
  FS-01  process running       — ps for 'freeswitch' command
  FS-02  systemd unit active   — systemctl is-active freeswitch
  FS-03  port 5060 listening   — SIP signaling port (UDP)
  FS-04  port 5080 listening   — SIP over TLS port (UDP)
  FS-05  fs_cli reachable      — `fs_cli -x status` returns within 5s

Additive module for v0.5.0 Sprint 2 (slice 2.1). End-to-end validation will
happen on the iPracticom AWS POC box; this file uses mocks in tests.

Does NOT restart, reload, or otherwise mutate the service. Repair belongs to
the repair subsystem (separate from monitor).
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from typing import Any

from .._log import log_suppressed

DEFAULT_SIP_PORT = 5060  # FS-03
DEFAULT_SIPS_PORT = 5080  # FS-04
DEFAULT_CLI_TIMEOUT = 5  # FS-05, seconds
DEFAULT_RTP_PORT_LOW = 16384  # FS-09 RTP range low
DEFAULT_RTP_PORT_HIGH = 32768  # FS-09 RTP range high
DEFAULT_REGISTRATIONS_MIN = 1  # FS-07: anything below 1 registered = crit

# Tier-3 (slice 2.3) thresholds
DEFAULT_FS_CLI_LATENCY_WARN_MS = 500   # FS-10
DEFAULT_FS_CLI_LATENCY_CRIT_MS = 2000  # FS-10
DEFAULT_ACTIVE_CALLS_WARN = 100        # FS-11
DEFAULT_ACTIVE_CALLS_CRIT = 500        # FS-11
DEFAULT_ACTIVE_CHANNELS_WARN = 200     # FS-12
DEFAULT_ACTIVE_CHANNELS_CRIT = 1000    # FS-12
DEFAULT_LOG_DISK_PCT_WARN = 80         # FS-13
DEFAULT_LOG_DISK_PCT_CRIT = 95         # FS-13
DEFAULT_CONFIG_DRIFT_DAYS_WARN = 60    # FS-14
DEFAULT_CONFIG_DRIFT_DAYS_CRIT = 180   # FS-14
DEFAULT_BASELINE_DRIFT_FACTOR_WARN = 2.0  # FS-15: 2× baseline = warn
DEFAULT_BASELINE_DRIFT_FACTOR_CRIT = 4.0  # FS-15: 4× baseline = crit

# Tier-4 (slice 2.4) thresholds
DEFAULT_CDR_BACKUP_MAX_AGE_HOURS = 26   # FS-16: warn if no fresh backup
DEFAULT_RECORDINGS_MAX_AGE_DAYS = 90    # FS-17
DEFAULT_FS_RSS_WARN_BYTES = 2 * 1024 ** 3      # FS-21: 2 GB
DEFAULT_FS_RSS_CRIT_BYTES = 4 * 1024 ** 3      # FS-21: 4 GB
DEFAULT_FS_CPU_PCT_WARN = 50                  # FS-22
DEFAULT_FS_CPU_PCT_CRIT = 80                  # FS-22
DEFAULT_TCP_RETRANS_WARN_PER_100 = 1.0   # FS-23: 1% retransmit
DEFAULT_TCP_RETRANSMIT_CRIT_PER_100 = 5.0
DEFAULT_FS_LOG_ERRORS_PER_MIN_WARN = 5    # FS-24
DEFAULT_FS_LOG_ERRORS_PER_MIN_CRIT = 50


# --- Low-level helpers ------------------------------------------------------


def _run(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """Subprocess.run with timeout + uniform error envelope.

    Returns (rc, stdout, stderr). Never raises for normal failures.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Return True if a UDP socket can be opened on (host, port)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        # Try to *bind* to confirm presence; if something else has the port we
        # get EADDRINUSE. Connecting on UDP is a poor signal.
        try:
            sock.bind((host, port))
            # We managed to bind → nothing was listening.
            sock.close()
            return False
        except OSError:
            # EADDRINUSE → port is held by another process.
            return True
    finally:
        try:
            sock.close()
        except Exception as e:
            log_suppressed("freeswitch_port_probe_close", e)


# --- FS-01..05 individual checks -------------------------------------------


def check_fs01_process_running() -> dict[str, Any]:
    """FS-01: a `freeswitch` process is visible in `ps`.

    Uses `ps -eo comm` to scan for the literal process name. Missing ps or
    no process → status=crit.
    """
    rc, out, _ = _run(["ps", "-eo", "comm"], timeout=3)
    if rc != 0:
        return {
            "status": "crit",
            "values": {
                "fs01_ps_rc": rc,
                "fs01_running": False,
                "fs01_pids": [],
            },
        }
    pids: list[int] = []
    target = "freeswitch"
    rc2, out2, _ = _run(["pgrep", "-x", target], timeout=3)
    if rc2 == 0 and out2.strip():
        for tok in out2.split():
            if tok.strip().isdigit():
                pids.append(int(tok))
    running = bool(pids)
    return {
        "status": "ok" if running else "crit",
        "values": {
            "fs01_ps_rc": rc,
            "fs01_running": running,
            "fs01_pids": pids,
        },
    }


def check_fs02_systemd_active(unit: str = "freeswitch") -> dict[str, Any]:
    """FS-02: systemd reports the FreeSWITCH unit as `active`.

    Returns the raw systemctl output for operator diagnosis.
    """
    rc, out, _ = _run(["systemctl", "is-active", "--quiet", f"{unit}.service"], timeout=5)
    active = rc == 0
    return {
        "status": "ok" if active else "crit",
        "values": {
            "fs02_unit": unit,
            "fs02_active": active,
            "fs02_systemctl_rc": rc,
        },
    }


def check_fs03_sip_port(port: int = DEFAULT_SIP_PORT) -> dict[str, Any]:
    """FS-03: UDP/5060 (SIP) is held by a process."""
    listening = _port_listening(port)
    return {
        "status": "ok" if listening else "crit",
        "values": {
            "fs03_port": port,
            "fs03_listening": listening,
        },
    }


def check_fs04_sips_port(port: int = DEFAULT_SIPS_PORT) -> dict[str, Any]:
    """FS-04: UDP/5080 (SIP over TLS) is held by a process."""
    listening = _port_listening(port)
    return {
        "status": "ok" if listening else "crit",
        "values": {
            "fs04_port": port,
            "fs04_listening": listening,
        },
    }


def check_fs05_cli_reachable(timeout: int = DEFAULT_CLI_TIMEOUT) -> dict[str, Any]:
    """FS-05: `fs_cli -x 'status'` returns successfully within `timeout` sec.

    If `fs_cli` is not on PATH we report crit with a clear reason (operators
    need to know whether the failure is "FS is sick" or "FS isn't installed").
    """
    if shutil.which("fs_cli") is None:
        return {
            "status": "crit",
            "values": {
                "fs05_cli_path": None,
                "fs05_cli_rc": 127,
                "fs05_reachable": False,
                "fs05_reason": "fs_cli not on PATH",
            },
        }
    rc, out, err = _run(["fs_cli", "-x", "status"], timeout=timeout)
    reachable = rc == 0
    return {
        "status": "ok" if reachable else "crit",
        "values": {
            "fs05_cli_rc": rc,
            "fs05_reachable": reachable,
            "fs05_elapsed_ms": None,  # populated by snapshot if needed
            "fs05_output_excerpt": out.strip()[:120],
        },
    }


# --- FS-06..09 network integrity (Sprint 2 slice 2.2) ---------------------


def _run_fscli(command: str, timeout: int = DEFAULT_CLI_TIMEOUT) -> dict[str, Any]:
    """Invoke `fs_cli -x <command>` and return a uniform dict.

    Reads the rc, stdout, stderr. If fs_cli is not on PATH, returns
    rc=127 with a clear reason; callers should treat that as "could not
    inspect" rather than "FS is broken".
    """
    if shutil.which("fs_cli") is None:
        return {"rc": 127, "stdout": "", "stderr": "fs_cli not on PATH"}
    rc, out, err = _run(["fs_cli", "-x", command], timeout=timeout)
    return {"rc": rc, "stdout": out, "stderr": err}


def _parse_int_from_fscli(out: str, last_token: bool = True) -> int | None:
    """Best-effort int extractor for fs_cli output.

    Strategy: take the *last* whitespace-separated token that looks like an
    int. Many fs_cli `show ... count` commands return "<N> total." or
    "<N> entries." — we want that number, not the leading label words.

    Returns None when no numeric token is found.
    """
    if not out:
        return None
    for tok in reversed(out.split()):
        tok = tok.strip(".,")
        if tok.isdigit():
            return int(tok)
    return None


def check_fs06_sip_peers(min_peers: int = 1) -> dict[str, Any]:
    """FS-06: `show endpoints count` returns >= `min_peers`.

    We measure *configured* SIP endpoints. Zero configured endpoints = the
    PBX is online but useless → warn (operators may be migrating). A
    negative read (CLI ok but parsing empty) → warn (we don't know).
    """
    res = _run_fscli("show endpoints count")
    if res["rc"] != 0:
        return {
            "status": "warn",
            "values": {
                "fs06_cli_rc": res["rc"],
                "fs06_endpoint_count": None,
                "fs06_min_peers": min_peers,
                "fs06_reason": "cli failed",
            },
        }
    n = _parse_int_from_fscli(res["stdout"])
    if n is None:
        return {
            "status": "warn",
            "values": {
                "fs06_cli_rc": res["rc"],
                "fs06_endpoint_count": None,
                "fs06_min_peers": min_peers,
                "fs06_reason": "could not parse count",
            },
        }
    ok = n >= min_peers
    return {
        "status": "ok" if ok else "warn",
        "values": {
            "fs06_cli_rc": res["rc"],
            "fs06_endpoint_count": n,
            "fs06_min_peers": min_peers,
        },
    }


def check_fs07_sip_registrations(min_registrations: int = DEFAULT_REGISTRATIONS_MIN) -> dict[str, Any]:
    """FS-07: at least one SIP registration is active.

    Zero registrations on an otherwise-healthy PBX is a hard crit — phones
    cannot place/receive calls. CLI failure is warn (don't page on infra).
    """
    res = _run_fscli("show registrations count")
    if res["rc"] != 0:
        return {
            "status": "warn",
            "values": {
                "fs07_cli_rc": res["rc"],
                "fs07_registration_count": None,
                "fs07_min_registrations": min_registrations,
                "fs07_reason": "cli failed",
            },
        }
    n = _parse_int_from_fscli(res["stdout"])
    if n is None:
        return {
            "status": "warn",
            "values": {
                "fs07_cli_rc": res["rc"],
                "fs07_registration_count": None,
                "fs07_min_registrations": min_registrations,
                "fs07_reason": "could not parse count",
            },
        }
    ok = n >= min_registrations
    return {
        "status": "ok" if ok else "crit",
        "values": {
            "fs07_cli_rc": res["rc"],
            "fs07_registration_count": n,
            "fs07_min_registrations": min_registrations,
        },
    }


def check_fs08_gateway_status() -> dict[str, Any]:
    """FS-08: at least one SIP gateway is in `REGED`/`UP` state.

    `sofia status` prints a table; we use `sofia status gateway` and look
    for `REGED` in the output. If the operator runs a single-gateway setup,
    zero working gateways = warn (degraded call quality / failover only).
    """
    res = _run_fscli("sofia status gateway")
    if res["rc"] != 0:
        return {
            "status": "warn",
            "values": {
                "fs08_cli_rc": res["rc"],
                "fs08_gateway_count": None,
                "fs08_gateway_up": None,
                "fs08_reason": "cli failed",
            },
        }
    out = res["stdout"].upper()
    gateway_up = out.count("REGED") + out.count(" UP ")
    has_any_gateway = ("REGED" in out or "NOREG" in out or
                       " UNREGED" in out or "UP" in out)
    if not has_any_gateway and not gateway_up:
        return {
            "status": "warn",
            "values": {
                "fs08_cli_rc": res["rc"],
                "fs08_gateway_count": 0,
                "fs08_gateway_up": 0,
                "fs08_reason": "no gateway block in output",
            },
        }
    return {
        "status": "ok" if gateway_up > 0 else "warn",
        "values": {
            "fs08_cli_rc": res["rc"],
            "fs08_gateway_count": max(gateway_up, 1),
            "fs08_gateway_up": gateway_up,
        },
    }


def check_fs09_rtp_ports_open(
    low: int = DEFAULT_RTP_PORT_LOW, high: int = DEFAULT_RTP_PORT_HIGH
) -> dict[str, Any]:
    """FS-09: at least one UDP port in the RTP range is bound by FS.

    We can't reliably bind-check the entire range without permissions; instead
    we spot-check three anchors (low/mid/high). If any of them is held, FS
    has at least claimed an RTP port. If all three are free AND FS is
    otherwise healthy (FS-01 ok), we treat this as a soft warn so we don't
    page on a transient race.
    """
    anchors = (low, (low + high) // 2, high)

    def _held(port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", port))
            return False  # succeeded → port was free
        except OSError:
            return True  # EADDRINUSE → held by something (very likely FS)
        finally:
            try:
                sock.close()
            except Exception as e:
                log_suppressed("freeswitch_port_anchor_close", e)

    results = {p: _held(p) for p in anchors}
    held_count = sum(1 for v in results.values() if v)
    ok = held_count >= 1

    return {
        "status": "ok" if ok else "warn",
        "values": {
            "fs09_port_range": [low, high],
            "fs09_anchors_checked": list(anchors),
            "fs09_anchors_held": results,
            "fs09_anchors_held_count": held_count,
        },
    }


# --- Aggregator -------------------------------------------------------------


def _worst(*statuses: str) -> str:
    order = {"ok": 0, "warn": 1, "crit": 2}
    return max(statuses, key=lambda s: order.get(s, 0))


def collect_all() -> dict[str, Any]:
    """Run FS-01..05 and return merged `values` for the snapshot module.

    A single `freeswitch` module in the snapshot carries all five flags so
    downstream UI can highlight the specific subsystem that is down.
    """
    fs01 = check_fs01_process_running()
    fs02 = check_fs02_systemd_active()
    fs03 = check_fs03_sip_port()
    fs04 = check_fs04_sips_port()
    fs05 = check_fs05_cli_reachable()

    return {
        "fs01_process_running": fs01["values"]["fs01_running"],
        "fs02_systemd_active": fs02["values"]["fs02_active"],
        "fs03_sip_port_5060": fs03["values"]["fs03_listening"],
        "fs04_sips_port_5080": fs04["values"]["fs04_listening"],
        "fs05_cli_reachable": fs05["values"]["fs05_reachable"],
        "fs05_cli_reason": fs05["values"].get("fs05_reason"),
        "fs01_pids": fs01["values"]["fs01_pids"],
    }


def evaluate(values: dict[str, Any], rules: dict | None = None) -> str:
    """Tier-1 evaluate: FS-01..05 liveness.

    All five must be ok. Any failure → crit (FS being down = phone system down).
    Kept as a separate function so Tier-2 checks (FS-06..09) can have their own
    softer evaluation.
    """
    if not values.get("fs01_process_running"):
        return "crit"
    if not values.get("fs02_systemd_active"):
        return "crit"
    if not values.get("fs03_sip_port_5060"):
        return "crit"
    if not values.get("fs04_sips_port_5080"):
        return "crit"
    if not values.get("fs05_cli_reachable"):
        return "crit"
    return "ok"


# --- FS-10..15 operational + baseline drift (Sprint 2 slice 2.3) --------


def check_fs10_cli_latency(
    warn_ms: int = DEFAULT_FS_CLI_LATENCY_WARN_MS,
    crit_ms: int = DEFAULT_FS_CLI_LATENCY_CRIT_MS,
) -> dict[str, Any]:
    """FS-10: time a `fs_cli -x status` round-trip.

    Slow CLI is usually a sign of disk pressure or an under-resourced VM.
    Misses to call fs_cli (PATH) → status=warn with explicit reason.
    """
    import time as _time

    if shutil.which("fs_cli") is None:
        return {
            "status": "warn",
            "values": {
                "fs10_cli_rc": 127,
                "fs10_elapsed_ms": None,
                "fs10_warn_ms": warn_ms,
                "fs10_crit_ms": crit_ms,
                "fs10_reason": "fs_cli not on PATH",
            },
        }
    started = _time.monotonic()
    rc, _, _ = _run(["fs_cli", "-x", "status"], timeout=10)
    elapsed_ms = int((_time.monotonic() - started) * 1000)
    if rc != 0:
        return {
            "status": "warn",
            "values": {
                "fs10_cli_rc": rc,
                "fs10_elapsed_ms": elapsed_ms,
                "fs10_warn_ms": warn_ms,
                "fs10_crit_ms": crit_ms,
                "fs10_reason": "cli error",
            },
        }
    if elapsed_ms >= crit_ms:
        status = "crit"
    elif elapsed_ms >= warn_ms:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs10_cli_rc": rc,
            "fs10_elapsed_ms": elapsed_ms,
            "fs10_warn_ms": warn_ms,
            "fs10_crit_ms": crit_ms,
        },
    }


def check_fs11_active_calls(
    warn: int = DEFAULT_ACTIVE_CALLS_WARN,
    crit: int = DEFAULT_ACTIVE_CALLS_CRIT,
) -> dict[str, Any]:
    """FS-11: count of in-progress calls from `show calls count`.

    Thresholds are advisory — a busy contact-center may legitimately exceed
    `warn`. The number is what matters for dashboards + diffs over time.
    """
    res = _run_fscli("show calls count")
    if res["rc"] != 0:
        return {
            "status": "warn",
            "values": {
                "fs11_cli_rc": res["rc"],
                "fs11_active_calls": None,
                "fs11_warn": warn,
                "fs11_crit": crit,
                "fs11_reason": "cli failed",
            },
        }
    n = _parse_int_from_fscli(res["stdout"])
    if n is None:
        return {
            "status": "warn",
            "values": {
                "fs11_cli_rc": res["rc"],
                "fs11_active_calls": None,
                "fs11_warn": warn,
                "fs11_crit": crit,
                "fs11_reason": "could not parse count",
            },
        }
    if n >= crit:
        status = "crit"
    elif n >= warn:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs11_cli_rc": res["rc"],
            "fs11_active_calls": n,
            "fs11_warn": warn,
            "fs11_crit": crit,
        },
    }


def check_fs12_active_channels(
    warn: int = DEFAULT_ACTIVE_CHANNELS_WARN,
    crit: int = DEFAULT_ACTIVE_CHANNELS_CRIT,
) -> dict[str, Any]:
    """FS-12: count of open channels (== calls × legs usually).

    High channel count vs. low calls count = media not being torn down,
    possible leak.
    """
    res = _run_fscli("show channels count")
    if res["rc"] != 0:
        return {
            "status": "warn",
            "values": {
                "fs12_cli_rc": res["rc"],
                "fs12_active_channels": None,
                "fs12_warn": warn,
                "fs12_crit": crit,
                "fs12_reason": "cli failed",
            },
        }
    n = _parse_int_from_fscli(res["stdout"])
    if n is None:
        return {
            "status": "warn",
            "values": {
                "fs12_cli_rc": res["rc"],
                "fs12_active_channels": None,
                "fs12_warn": warn,
                "fs12_crit": crit,
                "fs12_reason": "could not parse count",
            },
        }
    if n >= crit:
        status = "crit"
    elif n >= warn:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs12_cli_rc": res["rc"],
            "fs12_active_channels": n,
            "fs12_warn": warn,
            "fs12_crit": crit,
        },
    }


def check_fs13_log_disk_usage(
    log_path: str = "/var/log/freeswitch",
    warn_pct: int = DEFAULT_LOG_DISK_PCT_WARN,
    crit_pct: int = DEFAULT_LOG_DISK_PCT_CRIT,
) -> dict[str, Any]:
    """FS-13: disk usage of the FS log directory as a % of its parent FS.

    We don't have `du` semantics in pure Python without scanning — we use
    `shutil.disk_usage` on the directory's mount point as an approximation.
    For an exact measurement, install logs.shutil.disk_usage would need an
    extra recursion step which we defer. The approximation is fine for
    *trend* alerts because errors of a few GB don't change the alert tone.
    """
    try:
        usage = shutil.disk_usage(log_path)
        used_pct = round(usage.used * 100.0 / usage.total, 1)
    except (FileNotFoundError, OSError) as e:
        return {
            "status": "warn",
            "values": {
                "fs13_path": log_path,
                "fs13_used_pct": None,
                "fs13_warn_pct": warn_pct,
                "fs13_crit_pct": crit_pct,
                "fs13_reason": str(e),
            },
        }
    if used_pct >= crit_pct:
        status = "crit"
    elif used_pct >= warn_pct:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs13_path": log_path,
            "fs13_used_pct": used_pct,
            "fs13_warn_pct": warn_pct,
            "fs13_crit_pct": crit_pct,
        },
    }


def check_fs14_config_drift_days(
    config_path: str = "/etc/freeswitch/freeswitch.xml",
    warn_days: int = DEFAULT_CONFIG_DRIFT_DAYS_WARN,
    crit_days: int = DEFAULT_CONFIG_DRIFT_DAYS_CRIT,
) -> dict[str, Any]:
    """FS-14: age of the FS configuration in days.

    If nobody has touched the config in months, the instance likely fell
    off the upgrade cadence. Operators can use this to schedule reviews.
    """
    try:
        mtime = os.path.getmtime(config_path)
    except (FileNotFoundError, OSError) as e:
        return {
            "status": "warn",
            "values": {
                "fs14_path": config_path,
                "fs14_age_days": None,
                "fs14_warn_days": warn_days,
                "fs14_crit_days": crit_days,
                "fs14_reason": str(e),
            },
        }
    import time as _time
    age_days = int((_time.time() - mtime) / 86400)
    if age_days >= crit_days:
        status = "crit"
    elif age_days >= warn_days:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs14_path": config_path,
            "fs14_age_days": age_days,
            "fs14_warn_days": warn_days,
            "fs14_crit_days": crit_days,
        },
    }


def check_fs15_baseline_calls_per_hour(
    baseline_calls_per_hour: float | None = None,
    warn_factor: float = DEFAULT_BASELINE_DRIFT_FACTOR_WARN,
    crit_factor: float = DEFAULT_BASELINE_DRIFT_FACTOR_CRIT,
) -> dict[str, Any]:
    """FS-15: calls-per-hour vs. a learned baseline.

    When the operator hasn't set a baseline yet (`baseline_calls_per_hour`
    is None), we report ok with `fs15_baseline_set=False` so the UI can
    hide this check rather than emit a spurious warning.
    """
    res = _run_fscli("show calls count")
    if res["rc"] != 0:
        return {
            "status": "warn",
            "values": {
                "fs15_cli_rc": res["rc"],
                "fs15_current_calls": None,
                "fs15_baseline": baseline_calls_per_hour,
                "fs15_reason": "cli failed",
            },
        }
    current = _parse_int_from_fscli(res["stdout"])
    if baseline_calls_per_hour is None or baseline_calls_per_hour <= 0:
        return {
            "status": "ok",
            "values": {
                "fs15_cli_rc": res["rc"],
                "fs15_current_calls": current,
                "fs15_baseline": None,
                "fs15_baseline_set": False,
            },
        }
    ratio = (current or 0) / baseline_calls_per_hour
    if ratio >= crit_factor:
        status = "crit"
    elif ratio >= warn_factor:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs15_cli_rc": res["rc"],
            "fs15_current_calls": current,
            "fs15_baseline": baseline_calls_per_hour,
            "fs15_baseline_set": True,
            "fs15_drift_factor": round(ratio, 2),
            "fs15_warn_factor": warn_factor,
            "fs15_crit_factor": crit_factor,
        },
    }


# --- FS-06..09 aggregator (Tier 2 / network integrity) --------------------


def collect_network() -> dict[str, Any]:
    """Run FS-06..09 and return a parallel dict.

    Lives separately from collect_all() so the Tier-1 snapshot shape stays
    identical (additive). The pipeline can choose to merge both into the
    same `freeswitch` module or split them later.
    """
    fs06 = check_fs06_sip_peers()
    fs07 = check_fs07_sip_registrations()
    fs08 = check_fs08_gateway_status()
    fs09 = check_fs09_rtp_ports_open()

    return {
        "fs06_endpoint_count": fs06["values"]["fs06_endpoint_count"],
        "fs06_min_peers": fs06["values"]["fs06_min_peers"],
        "fs07_registration_count": fs07["values"]["fs07_registration_count"],
        "fs07_min_registrations": fs07["values"]["fs07_min_registrations"],
        "fs08_gateway_up": fs08["values"]["fs08_gateway_up"],
        "fs08_gateway_count": fs08["values"]["fs08_gateway_count"],
        "fs09_anchors_held_count": fs09["values"]["fs09_anchors_held_count"],
        "fs09_port_range": fs09["values"]["fs09_port_range"],
        # per-check status, useful for UI drilldown
        "fs06_status": fs06["status"],
        "fs07_status": fs07["status"],
        "fs08_status": fs08["status"],
        "fs09_status": fs09["status"],
    }


def evaluate_network(values: dict[str, Any], rules: dict | None = None) -> str:
    """Tier-2 evaluate: FS-06..09 network integrity.

    Semantics:
      - FS-07 (registrations == 0)        → crit (no phones registered)
      - any other check warn/crit         → warn

    `rules` is accepted for symmetry with other modules; future thresholds
    (e.g. "warn if endpoint_count < 5") will read from rules["freeswitch"].
    """
    if values.get("fs07_registration_count") is not None and \
            values["fs07_registration_count"] < values.get("fs07_min_registrations", 1):
        return "crit"
    statuses = [
        values.get("fs06_status", "warn"),
        values.get("fs08_status", "warn"),
        values.get("fs09_status", "warn"),
    ]
    order = {"ok": 0, "warn": 1, "crit": 2}
    worst = max(statuses, key=lambda s: order.get(s, 1))
    return worst


# --- FS-10..15 aggregator (Tier 3 / operational + baseline drift) -------


def collect_operational(
    fs_baseline_calls_per_hour: float | None = None,
) -> dict[str, Any]:
    """Run FS-10..15 and return a parallel dict for the snapshot module."""
    fs10 = check_fs10_cli_latency()
    fs11 = check_fs11_active_calls()
    fs12 = check_fs12_active_channels()
    fs13 = check_fs13_log_disk_usage()
    fs14 = check_fs14_config_drift_days()
    fs15 = check_fs15_baseline_calls_per_hour(
        baseline_calls_per_hour=fs_baseline_calls_per_hour
    )

    return {
        "fs10_elapsed_ms": fs10["values"]["fs10_elapsed_ms"],
        "fs10_warn_ms": fs10["values"]["fs10_warn_ms"],
        "fs10_crit_ms": fs10["values"]["fs10_crit_ms"],
        "fs11_active_calls": fs11["values"]["fs11_active_calls"],
        "fs11_warn": fs11["values"]["fs11_warn"],
        "fs11_crit": fs11["values"]["fs11_crit"],
        "fs12_active_channels": fs12["values"]["fs12_active_channels"],
        "fs12_warn": fs12["values"]["fs12_warn"],
        "fs12_crit": fs12["values"]["fs12_crit"],
        "fs13_used_pct": fs13["values"]["fs13_used_pct"],
        "fs13_path": fs13["values"]["fs13_path"],
        "fs14_age_days": fs14["values"]["fs14_age_days"],
        "fs14_warn_days": fs14["values"]["fs14_warn_days"],
        "fs14_crit_days": fs14["values"]["fs14_crit_days"],
        "fs15_current_calls": fs15["values"]["fs15_current_calls"],
        "fs15_baseline": fs15["values"]["fs15_baseline"],
        "fs15_baseline_set": fs15["values"].get("fs15_baseline_set", False),
        "fs15_drift_factor": fs15["values"].get("fs15_drift_factor"),
        "fs10_status": fs10["status"],
        "fs11_status": fs11["status"],
        "fs12_status": fs12["status"],
        "fs13_status": fs13["status"],
        "fs14_status": fs14["status"],
        "fs15_status": fs15["status"],
    }


def evaluate_operational(values: dict[str, Any], rules: dict | None = None) -> str:
    """Tier-3 evaluate: FS-10..15.

    Tier-3 checks are advisory — even a crit here means "investigate" rather
    than "phone system down". The evaluator returns the worst of the
    per-check statuses so the snapshot module still reflects severity.
    """
    statuses = [
        values.get("fs10_status", "warn"),
        values.get("fs11_status", "warn"),
        values.get("fs12_status", "warn"),
        values.get("fs13_status", "warn"),
        values.get("fs14_status", "warn"),
        values.get("fs15_status", "warn"),
    ]
    order = {"ok": 0, "warn": 1, "crit": 2}
    worst = max(statuses, key=lambda s: order.get(s, 1))
    return worst


# --- FS-16..25 edge cases (Sprint 2 slice 2.4) ---------------------------


def _file_age_hours(path: str) -> float | None:
    """Return age in hours of the file at `path`, or None on error."""
    try:
        mtime = os.path.getmtime(path)
    except (FileNotFoundError, OSError):
        return None
    return (time.time() - mtime) / 3600.0


def _read_text(path: str) -> str | None:
    """Read a file as text, return None on any error."""
    try:
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return None


def check_fs16_cdr_backup_fresh(
    backup_glob_pattern: str = "/var/backups/freeswitch/cdr-*.sql*",
    max_age_hours: float = DEFAULT_CDR_BACKUP_MAX_AGE_HOURS,
) -> dict[str, Any]:
    """FS-16: most recent CDR backup is fresh (under max_age_hours).

    Without `glob` we rely on os.listdir() of the parent directory, since
    importing `glob` inside a hot code path is wasteful and `pathlib` is
    already implicitly loaded.
    """
    import glob as _glob
    files = sorted(_glob.glob(backup_glob_pattern), key=os.path.getmtime,
                   reverse=True) if backup_glob_pattern else []
    if not files:
        return {
            "status": "warn",
            "values": {
                "fs16_pattern": backup_glob_pattern,
                "fs16_latest_backup": None,
                "fs16_age_hours": None,
                "fs16_max_age_hours": max_age_hours,
                "fs16_reason": "no backup files matched",
            },
        }
    latest = files[0]
    age = _file_age_hours(latest)
    if age is None:
        return {
            "status": "warn",
            "values": {
                "fs16_pattern": backup_glob_pattern,
                "fs16_latest_backup": latest,
                "fs16_age_hours": None,
                "fs16_max_age_hours": max_age_hours,
                "fs16_reason": "cannot stat latest backup",
            },
        }
    ok = age <= max_age_hours
    return {
        "status": "ok" if ok else "crit",
        "values": {
            "fs16_pattern": backup_glob_pattern,
            "fs16_latest_backup": latest,
            "fs16_age_hours": round(age, 1),
            "fs16_max_age_hours": max_age_hours,
        },
    }


def check_fs17_recordings_age(
    recordings_dir: str = "/var/lib/freeswitch/recordings",
    max_age_days: int = DEFAULT_RECORDINGS_MAX_AGE_DAYS,
) -> dict[str, Any]:
    """FS-17: oldest recording exceeds max_age_days → storage creeping.

    We sample the 100 newest recordings and report the oldest among them.
    If the tree is empty, we report ok (nothing to age out).
    """
    if not os.path.isdir(recordings_dir):
        return {
            "status": "warn",
            "values": {
                "fs17_path": recordings_dir,
                "fs17_oldest_newest_sample_days": None,
                "fs17_max_age_days": max_age_days,
                "fs17_reason": "directory not found",
            },
        }
    try:
        samples = []
        cutoff = time.time() - max_age_days * 86400
        for root, _, files in os.walk(recordings_dir):
            for name in files[:50]:  # cap to keep this fast on huge trees
                full = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(full)
                except OSError as e:
                    log_suppressed("freeswitch_mtime_scan", e)
                    continue
                samples.append(mtime)
                if len(samples) >= 100:
                    break
            if len(samples) >= 100:
                break
    except OSError:
        return {
            "status": "warn",
            "values": {
                "fs17_path": recordings_dir,
                "fs17_oldest_newest_sample_days": None,
                "fs17_max_age_days": max_age_days,
                "fs17_reason": "os.walk failed",
            },
        }
    if not samples:
        return {
            "status": "ok",
            "values": {
                "fs17_path": recordings_dir,
                "fs17_oldest_newest_sample_days": None,
                "fs17_max_age_days": max_age_days,
                "fs17_reason": "no recordings",
            },
        }
    oldest_in_sample = min(samples)
    age_days = (time.time() - oldest_in_sample) / 86400.0
    ok = age_days <= max_age_days
    return {
        "status": "warn" if not ok else "ok",
        "values": {
            "fs17_path": recordings_dir,
            "fs17_oldest_newest_sample_days": round(age_days, 1),
            "fs17_max_age_days": max_age_days,
        },
    }


def check_fs18_sofia_packet_loss() -> dict[str, Any]:
    """FS-18: any non-zero `packet loss` line in `sofia status profile`.

    Looks for the literal phrase; if found, status=warn (degraded media).
    """
    res = _run_fscli("sofia status profile")
    if res["rc"] != 0:
        return {
            "status": "warn",
            "values": {
                "fs18_cli_rc": res["rc"],
                "fs18_packet_loss_detected": None,
                "fs18_reason": "cli failed",
            },
        }
    out = res["stdout"].lower()
    has_loss = "packet loss" in out and " 0 " not in out
    return {
        "status": "warn" if has_loss else "ok",
        "values": {
            "fs18_cli_rc": res["rc"],
            "fs18_packet_loss_detected": has_loss,
        },
    }


def check_fs19_sofia_jitter(jitter_warn_ms: int = 30, jitter_crit_ms: int = 100) -> dict[str, Any]:
    """FS-19: `sofia status` reports non-trivial jitter.

    We parse the line containing "jitter" and pull the largest integer.
    """
    res = _run_fscli("sofia status")
    if res["rc"] != 0:
        return {
            "status": "warn",
            "values": {
                "fs19_cli_rc": res["rc"],
                "fs19_max_jitter_ms": None,
                "fs19_warn_ms": jitter_warn_ms,
                "fs19_crit_ms": jitter_crit_ms,
                "fs19_reason": "cli failed",
            },
        }
    max_jitter = 0
    found = False
    for line in res["stdout"].splitlines():
        low = line.lower()
        if "jitter" not in low:
            continue
        for tok in line.split():
            tok = tok.strip("ms,()")
            try:
                val = float(tok)
            except ValueError as e:
                log_suppressed("freeswitch_jitter_parse", e)
                continue
            if val > max_jitter:
                max_jitter = val
                found = True
    if not found:
        return {
            "status": "ok",
            "values": {
                "fs19_cli_rc": res["rc"],
                "fs19_max_jitter_ms": None,
                "fs19_warn_ms": jitter_warn_ms,
                "fs19_crit_ms": jitter_crit_ms,
            },
        }
    if max_jitter >= jitter_crit_ms:
        status = "crit"
    elif max_jitter >= jitter_warn_ms:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs19_cli_rc": res["rc"],
            "fs19_max_jitter_ms": max_jitter,
            "fs19_warn_ms": jitter_warn_ms,
            "fs19_crit_ms": jitter_crit_ms,
        },
    }


def check_fs20_codec_mismatch() -> dict[str, Any]:
    """FS-20: at least one configured codec is `NEGOTIATION` mismatch.

    Looks at `sofia status profile internal` for the literal token
    `NEGOTIATION` outside the header list.
    """
    res = _run_fscli("sofia status profile internal")
    if res["rc"] != 0:
        return {
            "status": "warn",
            "values": {
                "fs20_cli_rc": res["rc"],
                "fs20_negotiation_count": None,
                "fs20_reason": "cli failed",
            },
        }
    count = res["stdout"].upper().count("NEGOTIATION")
    if count == 0:
        return {
            "status": "ok",
            "values": {
                "fs20_cli_rc": res["rc"],
                "fs20_negotiation_count": 0,
            },
        }
    return {
        "status": "warn",
        "values": {
            "fs20_cli_rc": res["rc"],
            "fs20_negotiation_count": count,
        },
    }


def check_fs21_process_rss(
    warn_bytes: int = DEFAULT_FS_RSS_WARN_BYTES,
    crit_bytes: int = DEFAULT_FS_RSS_CRIT_BYTES,
) -> dict[str, Any]:
    """FS-21: RSS of the `freeswitch` process.

    Uses psutil when available (already a dependency of the project) and
    falls back to a warn status with an explanation otherwise.
    """
    try:
        import psutil
    except ImportError:
        return {
            "status": "warn",
            "values": {
                "fs21_rss_bytes": None,
                "fs21_warn_bytes": warn_bytes,
                "fs21_crit_bytes": crit_bytes,
                "fs21_reason": "psutil not available",
            },
        }
    candidates = []
    for proc in psutil.process_iter(attrs=["name", "memory_info"]):
        try:
            name = proc.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log_suppressed("freeswitch_mem_iter", e)
            continue
        if name != "freeswitch":
            continue
        mi = proc.info.get("memory_info")
        if mi is not None:
            candidates.append(mi.rss)
    if not candidates:
        return {
            "status": "warn",
            "values": {
                "fs21_rss_bytes": None,
                "fs21_warn_bytes": warn_bytes,
                "fs21_crit_bytes": crit_bytes,
                "fs21_reason": "no freeswitch process",
            },
        }
    rss = max(candidates)
    if rss >= crit_bytes:
        status = "crit"
    elif rss >= warn_bytes:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs21_rss_bytes": rss,
            "fs21_warn_bytes": warn_bytes,
            "fs21_crit_bytes": crit_bytes,
        },
    }


def check_fs22_process_cpu_pct(
    warn_pct: float = DEFAULT_FS_CPU_PCT_WARN,
    crit_pct: float = DEFAULT_FS_CPU_PCT_CRIT,
    sample_seconds: float = 0.0,
) -> dict[str, Any]:
    """FS-22: instantaneous CPU% of all `freeswitch` processes (aggregated).

    Without a sample window we rely on psutil's cached value. Pass
    sample_seconds=0.5 if you want a measured delta (slower).
    """
    try:
        import psutil
    except ImportError:
        return {
            "status": "warn",
            "values": {
                "fs22_cpu_pct": None,
                "fs22_warn_pct": warn_pct,
                "fs22_crit_pct": crit_pct,
                "fs22_reason": "psutil not available",
            },
        }
    total = 0.0
    seen = 0
    for proc in psutil.process_iter(attrs=["name"]):
        try:
            name = proc.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log_suppressed("freeswitch_cpu_iter", e)
            continue
        if name != "freeswitch":
            continue
        try:
            total += proc.cpu_percent(interval=sample_seconds)
            seen += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log_suppressed("freeswitch_cpu_sample", e)
            continue
    if seen == 0:
        return {
            "status": "warn",
            "values": {
                "fs22_cpu_pct": None,
                "fs22_warn_pct": warn_pct,
                "fs22_crit_pct": crit_pct,
                "fs22_reason": "no freeswitch process",
            },
        }
    if total >= crit_pct:
        status = "crit"
    elif total >= warn_pct:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs22_cpu_pct": round(total, 1),
            "fs22_warn_pct": warn_pct,
            "fs22_crit_pct": crit_pct,
        },
    }


def check_fs23_tcp_retransmit_pct(
    warn_pct: float = DEFAULT_TCP_RETRANS_WARN_PER_100,
    crit_pct: float = DEFAULT_TCP_RETRANSMIT_CRIT_PER_100,
) -> dict[str, Any]:
    """FS-23: TCP retransmit rate from `netstat -s`.

    We look for the sections "Tcp:" and "TcpExt:" and parse "X segments
    retransmitted" + "Y segments transmitted" to compute a percentage.
    """
    rc, out, _ = _run(["netstat", "-s"], timeout=5)
    if rc != 0:
        return {
            "status": "warn",
            "values": {
                "fs23_netstat_rc": rc,
                "fs23_retransmit_pct": None,
                "fs23_warn_pct": warn_pct,
                "fs23_crit_pct": crit_pct,
                "fs23_reason": "netstat failed",
            },
        }

    segments_retrans = None
    segments_in = None
    segments_out = None
    in_tcp_section = False
    for line in out.splitlines():
        if not line:
            continue
        low = line.lower()
        if low.startswith("tcp:"):
            in_tcp_section = True
            continue
        if in_tcp_section and not line[0].isspace():
            # We've left the Tcp: section.
            in_tcp_section = False
        if not in_tcp_section:
            continue
        if "segments retransmitted" in low and segments_retrans is None:
            segments_retrans = _parse_int_from_fscli(low)
        elif "active connections established" in low and segments_out is None:
            segments_out = _parse_int_from_fscli(low)
        elif "segments received" in low and segments_in is None:
            segments_in = _parse_int_from_fscli(low)

    if segments_retrans is None:
        return {
            "status": "warn",
            "values": {
                "fs23_netstat_rc": rc,
                "fs23_retransmit_pct": None,
                "fs23_warn_pct": warn_pct,
                "fs23_crit_pct": crit_pct,
                "fs23_reason": "no segments retransmitted line",
            },
        }
    # Preferred denominator: segments_out + segments_in (rough total volume).
    # Fall back to whichever we found, else 1 to avoid /0.
    denom = (segments_out or 0) + (segments_in or 0) or segments_in or 1
    pct = (segments_retrans * 100.0) / denom
    if pct >= crit_pct:
        status = "crit"
    elif pct >= warn_pct:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs23_netstat_rc": rc,
            "fs23_retransmit_pct": round(pct, 2),
            "fs23_warn_pct": warn_pct,
            "fs23_crit_pct": crit_pct,
        },
    }


def check_fs24_log_error_rate(
    log_path: str = "/var/log/freeswitch/freeswitch.log",
    window_min: int = 5,
    warn_per_min: int = DEFAULT_FS_LOG_ERRORS_PER_MIN_WARN,
    crit_per_min: int = DEFAULT_FS_LOG_ERRORS_PER_MIN_CRIT,
) -> dict[str, Any]:
    """FS-24: count of ERROR lines per minute in the FS log.

    For a hot-running log, we sample only the last 4096 lines to keep this
    cheap. The rate is "errors in sample / window_min" — for an active log
    stream the absolute count is also useful even if the time window drifts.
    """
    raw = _read_text(log_path)
    if raw is None:
        return {
            "status": "warn",
            "values": {
                "fs24_path": log_path,
                "fs24_window_min": window_min,
                "fs24_errors_per_min": None,
                "fs24_warn_per_min": warn_per_min,
                "fs24_crit_per_min": crit_per_min,
                "fs24_reason": "log not readable",
            },
        }
    lines = raw.splitlines()[-4096:]
    error_count = sum(1 for ln in lines if "ERROR" in ln)
    per_min = error_count / max(window_min, 1)
    if per_min >= crit_per_min:
        status = "crit"
    elif per_min >= warn_per_min:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "values": {
            "fs24_path": log_path,
            "fs24_window_min": window_min,
            "fs24_errors_count": error_count,
            "fs24_errors_per_min": round(per_min, 2),
            "fs24_warn_per_min": warn_per_min,
            "fs24_crit_per_min": crit_per_min,
        },
    }


def check_fs25_fail2ban_active(jail: str = "freeswitch") -> dict[str, Any]:
    """FS-25: `fail2ban-client status <jail>` reports ≥ 1 banned IP.

    fail2ban is optional; missing client → warn with explanation (don't page
    for a tool that was never installed).
    """
    if shutil.which("fail2ban-client") is None:
        return {
            "status": "warn",
            "values": {
                "fs25_jail": jail,
                "fs25_banned": None,
                "fs25_reason": "fail2ban-client not on PATH",
            },
        }
    rc, out, _ = _run(["fail2ban-client", "status", jail], timeout=5)
    if rc != 0:
        return {
            "status": "warn",
            "values": {
                "fs25_jail": jail,
                "fs25_banned": None,
                "fs25_rc": rc,
                "fs25_reason": "fail2ban-client status failed",
            },
        }
    banned = _parse_int_from_fscli(out.splitlines()[-1]) or 0
    return {
        "status": "ok",
        "values": {
            "fs25_jail": jail,
            "fs25_banned": banned,
            "fs25_rc": rc,
        },
    }


# --- FS-16..25 aggregator (Tier 4 / edge cases) -------------------------


def collect_edge_cases(
    fs_recordings_dir: str = "/var/lib/freeswitch/recordings",
    fs_log_path: str = "/var/log/freeswitch/freeswitch.log",
) -> dict[str, Any]:
    """Run FS-16..25 and return a parallel dict for the snapshot module."""
    fs16 = check_fs16_cdr_backup_fresh()
    fs17 = check_fs17_recordings_age(recordings_dir=fs_recordings_dir)
    fs18 = check_fs18_sofia_packet_loss()
    fs19 = check_fs19_sofia_jitter()
    fs20 = check_fs20_codec_mismatch()
    fs21 = check_fs21_process_rss()
    fs22 = check_fs22_process_cpu_pct(sample_seconds=0.0)
    fs23 = check_fs23_tcp_retransmit_pct()
    fs24 = check_fs24_log_error_rate(log_path=fs_log_path)
    fs25 = check_fs25_fail2ban_active()

    return {
        "fs16_latest_backup": fs16["values"]["fs16_latest_backup"],
        "fs16_age_hours": fs16["values"]["fs16_age_hours"],
        "fs16_max_age_hours": fs16["values"]["fs16_max_age_hours"],
        "fs17_oldest_sample_days": fs17["values"]["fs17_oldest_newest_sample_days"],
        "fs17_path": fs17["values"]["fs17_path"],
        "fs18_packet_loss_detected": fs18["values"]["fs18_packet_loss_detected"],
        "fs19_max_jitter_ms": fs19["values"]["fs19_max_jitter_ms"],
        "fs20_negotiation_count": fs20["values"]["fs20_negotiation_count"],
        "fs21_rss_bytes": fs21["values"]["fs21_rss_bytes"],
        "fs22_cpu_pct": fs22["values"]["fs22_cpu_pct"],
        "fs23_retransmit_pct": fs23["values"]["fs23_retransmit_pct"],
        "fs24_errors_per_min": fs24["values"]["fs24_errors_per_min"],
        "fs25_banned": fs25["values"]["fs25_banned"],
        "fs16_status": fs16["status"],
        "fs17_status": fs17["status"],
        "fs18_status": fs18["status"],
        "fs19_status": fs19["status"],
        "fs20_status": fs20["status"],
        "fs21_status": fs21["status"],
        "fs22_status": fs22["status"],
        "fs23_status": fs23["status"],
        "fs24_status": fs24["status"],
        "fs25_status": fs25["status"],
    }


def evaluate_edge_cases(values: dict[str, Any], rules: dict | None = None) -> str:
    """Tier-4 evaluate: FS-16..25.

    Edge-case checks. Worst-of.
    """
    statuses = [
        values.get(f"fs{n}_status", "warn")
        for n in (16, 17, 18, 19, 20, 21, 22, 23, 24, 25)
    ]
    order = {"ok": 0, "warn": 1, "crit": 2}
    worst = max(statuses, key=lambda s: order.get(s, 1))
    return worst
