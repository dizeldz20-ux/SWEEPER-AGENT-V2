"""Log health: error rate from journalctl.

Detects:
- High error rate in last N minutes
- OOM killer activations
- Kernel panics / hardware errors
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from .._log import log_suppressed


def _run_journalctl(args: list[str]) -> tuple[int, str]:
    """Run journalctl, return (rc, stdout)."""
    try:
        result = subprocess.run(
            ["journalctl"] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, result.stdout
    except FileNotFoundError:
        return 127, ""
    except Exception as e:
        return 1, str(e)


def count_by_priority(since_minutes: int = 5) -> dict[str, int]:
    """Count log lines by syslog priority in the last N minutes.

    Uses journald's own PRIORITY metadata via ``-p warning`` (priorities
    0..4) with JSON output, instead of scanning message text for words like
    "error". The old keyword approach could not work: ``-o short`` never
    prints the priority, and ``\\berr\\b`` does not even match "error"/"ERROR"
    (a word char follows), so a real error storm counted as zero — a
    silent-OK. We now count what journald actually recorded.
    """
    counts = {"emerg": 0, "alert": 0, "crit": 0, "err": 0, "warning": 0}
    rc, out = _run_journalctl([
        f"--since={since_minutes} minutes ago",
        "--no-pager",
        "-p", "warning",            # warning (4) and more severe: 0..4
        "-o", "json",
        "--output-fields=PRIORITY",
    ])
    if rc != 0 or not out:
        return counts

    prio_names = {0: "emerg", 1: "alert", 2: "crit", 3: "err", 4: "warning"}
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            priority = int(entry.get("PRIORITY", 6))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            log_suppressed("monitor.logs.count_by_severity.parse", exc)
            continue
        name = prio_names.get(priority)
        if name:
            counts[name] += 1
    return counts


def find_oom_events(window_minutes: int = 60) -> list[str]:
    """Find OOM killer activations in the last N minutes."""
    rc, out = _run_journalctl([
        f"--since={window_minutes} minutes ago",
        "--no-pager",
        "-k",   # kernel messages only
        "-g", "killed process",
    ])
    if rc != 0 or not out:
        return []
    return [line for line in out.split("\n") if "Out of memory" in line or "killed process" in line]


def collect(rules: dict) -> dict[str, Any]:
    """Collect log health snapshot."""
    # Was reading `failed_units_window_min` — a *services* key that does not
    # exist under `logs`, so the window was always the 5-minute default and
    # ignored operator config. Use a dedicated `error_window_min`.
    window = rules.get("logs", {}).get("error_window_min", 5)
    oom_window = rules.get("logs", {}).get("oom_events_window_min", 60)

    by_priority = count_by_priority(window)
    oom_events = find_oom_events(oom_window)
    total_errors = by_priority["emerg"] + by_priority["alert"] + by_priority["crit"] + by_priority["err"]
    error_rate_per_min = total_errors / max(window, 1)

    return {
        "window_minutes": window,
        "by_priority": by_priority,
        "error_rate_per_minute": round(error_rate_per_min, 2),
        "oom_events": oom_events[:10],  # truncate
        "oom_count": len(oom_events),
    }


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Apply rules; return 'ok' | 'warn' | 'crit'."""
    if values["oom_count"] > 0:
        return "crit"
    rate = values["error_rate_per_minute"]
    if rate >= rules["logs"]["error_rate_per_min_warn"] * 5:
        return "crit"
    if rate >= rules["logs"]["error_rate_per_min_warn"]:
        return "warn"
    if values["by_priority"]["crit"] > 0 or values["by_priority"]["alert"] > 0:
        return "warn"
    return "ok"