"""Process tracker: top-N resource hogs + service restart counter.

Reads /proc/[pid]/stat to get CPU%, MEM%, and runtime for the top
N processes. Also parses journalctl to count service restarts in a
given window.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import os
import re
import shutil
import subprocess
import time

from .._log import log_suppressed


@dataclass
class TopProcess:
    """A single top resource-consuming process."""

    pid: int
    name: str
    cpu_percent: float
    mem_percent: float
    runtime_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "cpu_percent": self.cpu_percent,
            "mem_percent": self.mem_percent,
            "runtime_seconds": self.runtime_seconds,
        }


@dataclass
class ServiceRestart:
    """A systemd service that has been restarted N times in a window."""

    service: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {"service": self.service, "count": self.count}


# Pattern: "Jun 29 10:00:01 host systemd[1]: Started nginx.service."
SERVICE_STARTED_RE = re.compile(
    r"systemd\[\d+\]:\s+Started\s+([\w\-@.]+\.service)"
)


def parse_journalctl_restarts(output: str, window_minutes: int = 60) -> list[ServiceRestart]:
    """Parse journalctl output for service-start events.

    Multiple 'Started X.service' lines for the same service are aggregated
    into a single ServiceRestart with a count.
    """
    counts: dict[str, int] = {}
    for line in output.splitlines():
        m = SERVICE_STARTED_RE.search(line)
        if m:
            svc = m.group(1)
            counts[svc] = counts.get(svc, 0) + 1
    return [ServiceRestart(service=s, count=c) for s, c in sorted(counts.items())]


def _scan_processes() -> list[dict]:
    """Scan /proc and return cpu/mem/runtime for each process.

    Cheap heuristic: read /proc/[pid]/stat + /proc/[pid]/statm.
    CPU% and MEM% are approximations (no two-sample delta) — sufficient
    for "who's eating resources" ranking, not for accurate billing.
    """
    procs: list[dict] = []
    now_jiffies = os.sysconf("SC_CLK_TCK")
    if not now_jiffies:
        now_jiffies = 100
    uptime_jiffies = 0
    try:
        with open("/proc/uptime") as f:
            uptime_seconds = float(f.read().split()[0])
    except (OSError, ValueError):
        uptime_seconds = 1.0

    total_mem = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_mem = int(line.split()[1])  # kB
                    break
    except OSError as e:
        log_suppressed("process_tracker_meminfo", e)

    for pid_dir in os.listdir("/proc"):
        if not pid_dir.isdigit():
            continue
        pid = int(pid_dir)
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read().split()
            # Field 1 (comm) is the process name in parens; the comm field
            # can contain spaces so we split on the LAST ')'.
            # fields[1] after split by ' ' is everything after the last ')'
            # Easier: read the file and find '(' and ')'
            with open(f"/proc/{pid}/stat") as f:
                raw = f.read()
            rpar = raw.rfind(")")
            lpar = raw.find("(")
            name = raw[lpar + 1:rpar]
            fields = raw[rpar + 2:].split()
            # After dropping "pid (comm) ", fields[0]=state (proc field 3), so
            # proc field N maps to fields[N-3]: utime=14→[11], stime=15→[12],
            # starttime=22→[19]. (Was [21] = field 24 = rss pages → bogus
            # runtime/cpu%; matches processes.py:32.)
            utime = int(fields[11])
            stime = int(fields[12])
            starttime = int(fields[19])
            total_time = utime + stime
            runtime_ticks = (uptime_seconds * now_jiffies) - starttime
            runtime_seconds = int(runtime_ticks / now_jiffies)

            with open(f"/proc/{pid}/statm") as f:
                rss_pages = int(f.read().split()[1])
            rss_kb = rss_pages * 4  # PAGE_SIZE = 4kB on most systems
            mem_pct = (rss_kb / total_mem * 100) if total_mem > 0 else 0.0

            # CPU% approximation: total_time / runtime
            # This is the "fraction of one core" the process has used
            # since it started. Not per-second, but good enough for ranking.
            cpu_pct = (total_time / now_jiffies / max(runtime_seconds, 1)) * 100

            procs.append({
                "pid": pid,
                "name": name,
                "cpu_percent": round(min(cpu_pct, 100.0), 2),
                "mem_percent": round(mem_pct, 2),
                "runtime_seconds": max(runtime_seconds, 0),
            })
        except (OSError, ValueError, IndexError) as e:
            log_suppressed("process_tracker_proc_read", e)
            continue
    return procs


def get_top_processes(top_n: int = 10) -> list[TopProcess]:
    """Return top N processes by combined CPU+MEM score."""
    procs = _scan_processes()
    procs.sort(key=lambda p: p["cpu_percent"] + p["mem_percent"], reverse=True)
    return [TopProcess(**p) for p in procs[:top_n]]


def collect_service_restarts(window_minutes: int = 60) -> list[ServiceRestart]:
    """Collect systemd service restart counts in the last window_minutes."""
    journalctl = shutil.which("journalctl")
    if not journalctl:
        return []
    try:
        proc = subprocess.run(
            [journalctl, "--since", f"{window_minutes} min ago",
             "-u", "*.service", "--no-pager", "-q", "--no-hostname"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return []
        return parse_journalctl_restarts(proc.stdout, window_minutes=window_minutes)
    except (subprocess.TimeoutExpired, OSError):
        return []


def evaluate(values: dict, rules: dict) -> str:
    """Return 'ok' | 'warn' | 'crit'.

    crit if a single proc > 95% CPU or MEM, or a service restarted > 10x in window.
    warn if > 80%, or a service restarted > 3x in window.
    """
    top = values.get("top_processes", [])
    restarts = values.get("service_restarts", [])

    cpu_crit = rules.get("process_tracker", {}).get("cpu_crit_percent", 95)
    cpu_warn = rules.get("process_tracker", {}).get("cpu_warn_percent", 80)
    restart_crit = rules.get("process_tracker", {}).get("restart_crit", 10)
    restart_warn = rules.get("process_tracker", {}).get("restart_warn", 3)

    has_crit = any(
        (p.get("cpu_percent") or 0) > cpu_crit
        or (p.get("mem_percent") or 0) > cpu_crit
        for p in top
    ) or any(r.get("count", 0) > restart_crit for r in restarts)

    has_warn = (
        any(
            cpu_warn < (p.get("cpu_percent") or 0) <= cpu_crit
            or cpu_warn < (p.get("mem_percent") or 0) <= cpu_crit
            for p in top
        )
        or any(restart_warn < r.get("count", 0) <= restart_crit for r in restarts)
    )

    if has_crit:
        return "crit"
    if has_warn:
        return "warn"
    return "ok"
