"""Monitor orchestrator: runs all monitor modules, aggregates status.

This is the entry point for the monitor phase. Each module's collect() +
evaluate() pair is wrapped in ``_safe_module()`` so a single module
failure (disk I/O error, broken permissions, etc.) cannot abort the
rest of the pipeline. v1.5.16 finding #15.
"""

from __future__ import annotations

from typing import Any, Callable
import shutil
import time
from pathlib import Path

import structlog

from ipracticom_sweeper import audit
from ipracticom_sweeper.config import load_rules
from ipracticom_sweeper.monitor import aide_check, aws, cpu, disk, fd_check, freeswitch, http_check, iostat, kernel_errors, logs, memory, network, process_tracker, processes, security, security_baseline, services, smart_check, ssl_check, uptime, health

logger = structlog.get_logger()


def _safe_module(name: str, fn: Callable[[], tuple[dict, str]]) -> dict[str, Any]:
    """Run a module's (collect, evaluate) pair; catch all exceptions.

    Returns the module's slot for the snapshot. On failure, the slot
    contains the error message and ``status="warn"`` so it shows up in
    the snapshot but doesn't break the overall pipeline.
    """
    try:
        values, status = fn()
        return {"values": values, "status": status, "error": None}
    except Exception as e:
        logger.warning("monitor_module_failed", module=name, error=str(e))
        return {"values": {"error": str(e)}, "status": "warn", "module_error": True, "error": str(e)}


def run_all(rules: dict | None = None, *, run_freeswitch: bool = False) -> dict[str, Any]:
    """Run every monitor module; return aggregated snapshot.

    This runs on the agent's OWN machine (the operator box). FreeSWITCH checks
    are OFF by default: the agent box is not a PBX, so probing ``fs_cli`` there
    only yields false crits. Monitored PBX machines opt into FreeSWITCH per-host
    via their connector's ``freeswitch_enabled`` flag (see fleet SSM collection)
    — never on the agent's self-scan. ``run_freeswitch=True`` is kept for the
    rare case the agent box itself is a FreeSWITCH host.

    A ``self`` section (agent-machine resilience + notification-bot connectivity)
    is always attached, independent of any monitored machine.
    """
    rules = rules or load_rules()
    snapshot: dict[str, Any] = {"modules": {}}

    snapshot["modules"]["cpu"] = _safe_module("cpu", lambda: _run_module(
        cpu, rules, lambda: cpu.collect(),
        lambda v: cpu.evaluate(v, rules),
    ))
    if not snapshot["modules"]["cpu"].get("module_error"):
        audit.monitor_event("cpu", snapshot["modules"]["cpu"]["values"], snapshot["modules"]["cpu"]["status"])
    snapshot["modules"]["memory"] = _safe_module("memory", lambda: _run_module(
        memory, rules, lambda: memory.collect(),
        lambda v: memory.evaluate(v, rules),
    ))
    if not snapshot["modules"]["memory"].get("module_error"):
        audit.monitor_event("memory", snapshot["modules"]["memory"]["values"], snapshot["modules"]["memory"]["status"])
    snapshot["modules"]["disk"] = _safe_module("disk", lambda: _run_module(
        disk, rules, lambda: disk.collect(),
        lambda v: disk.evaluate(v, rules),
    ))
    if not snapshot["modules"]["disk"].get("module_error"):
        audit.monitor_event("disk", snapshot["modules"]["disk"]["values"], snapshot["modules"]["disk"]["status"])
    snapshot["modules"]["network"] = _safe_module("network", lambda: _run_module(
        network, rules, lambda: network.collect(),
        lambda v: network.evaluate(v, rules),
    ))
    if not snapshot["modules"]["network"].get("module_error"):
        audit.monitor_event("network", snapshot["modules"]["network"]["values"], snapshot["modules"]["network"]["status"])
    snapshot["modules"]["services"] = _safe_module("services", lambda: _run_module(
        services, rules,
        lambda: services.collect(rules["services"].get("critical_list", [])),
        lambda v: services.evaluate(v, rules),
    ))
    if not snapshot["modules"]["services"].get("module_error"):
        audit.monitor_event("services", snapshot["modules"]["services"]["values"], snapshot["modules"]["services"]["status"])
    snapshot["modules"]["logs"] = _safe_module("logs", lambda: _run_module(
        logs, rules,
        lambda: logs.collect(rules),
        lambda v: logs.evaluate(v, rules),
    ))
    if not snapshot["modules"]["logs"].get("module_error"):
        audit.monitor_event("logs", snapshot["modules"]["logs"]["values"], snapshot["modules"]["logs"]["status"])
    snapshot["modules"]["processes"] = _safe_module("processes", lambda: _run_module(
        processes, rules, lambda: processes.collect(),
        lambda v: processes.evaluate(v, rules),
    ))
    if not snapshot["modules"]["processes"].get("module_error"):
        audit.monitor_event("processes", snapshot["modules"]["processes"]["values"], snapshot["modules"]["processes"]["status"])
    snapshot["modules"]["security"] = _safe_module("security", lambda: _run_module(
        security, rules,
        lambda: security.collect(rules),
        lambda v: security.evaluate(v, rules),
    ))
    if not snapshot["modules"]["security"].get("module_error"):
        audit.monitor_event("security", snapshot["modules"]["security"]["values"], snapshot["modules"]["security"]["status"])
    snapshot["modules"]["aws"] = _safe_module("aws", lambda: _run_module(
        aws, rules, lambda: aws.collect(),
        lambda v: aws.evaluate(v, rules),
    ))
    # Only audit AWS if data is available
    if snapshot["modules"]["aws"]["values"].get("available"):
        if not snapshot["modules"]["aws"].get("module_error"):
            audit.monitor_event("aws", snapshot["modules"]["aws"]["values"], snapshot["modules"]["aws"]["status"])
    # HTTP endpoints (graceful if no endpoints configured)
    http_endpoints = rules.get("http", {}).get("endpoints", [])
    if http_endpoints:
        snapshot["modules"]["http"] = _safe_module("http", lambda: _run_module(
            http_check, rules,
            lambda: {"endpoints": [r.to_dict() for r in http_check.collect_http_endpoints(http_endpoints)]},
            lambda v: http_check.evaluate(v, rules),
        ))
        if not snapshot["modules"]["http"].get("module_error"):
            audit.monitor_event("http", snapshot["modules"]["http"]["values"], snapshot["modules"]["http"]["status"])
    # SSL cert expiry (graceful if no hosts configured)
    ssl_hosts = rules.get("ssl", {}).get("hosts", [])
    if ssl_hosts:
        snapshot["modules"]["ssl"] = _safe_module("ssl", lambda: _run_module(
            ssl_check, rules,
            lambda: {"certificates": [r.to_dict() for r in ssl_check.collect_ssl_certs(ssl_hosts)]},
            lambda v: ssl_check.evaluate(v, rules),
        ))
        if not snapshot["modules"]["ssl"].get("module_error"):
            audit.monitor_event("ssl", snapshot["modules"]["ssl"]["values"], snapshot["modules"]["ssl"]["status"])
    # SMART disk health (graceful if smartctl missing or no devices)
    smart_devices = rules.get("smart", {}).get("devices", [])
    if smart_devices:
        snapshot["modules"]["smart"] = _safe_module("smart", lambda: _run_module(
            smart_check, rules,
            lambda: {"disks": [r.to_dict() for r in smart_check.collect_smart_health(smart_devices)]},
            lambda v: smart_check.evaluate(v, rules),
        ))
        if snapshot["modules"]["smart"]["values"].get("disks"):
            if not snapshot["modules"]["smart"].get("module_error"):
                audit.monitor_event("smart", snapshot["modules"]["smart"]["values"], snapshot["modules"]["smart"]["status"])
    # Kernel errors (Oops, MCE, segfaults) — always on, low cost
    snapshot["modules"]["kernel"] = _safe_module("kernel", lambda: _run_module(
        kernel_errors, rules,
        lambda: kernel_errors.collect_kernel_errors(window_minutes=rules.get("kernel", {}).get("window_minutes", 5)),
        lambda v: kernel_errors.evaluate(v, rules),
    ))
    if not snapshot["modules"]["kernel"].get("module_error"):
        audit.monitor_event("kernel", snapshot["modules"]["kernel"]["values"], snapshot["modules"]["kernel"]["status"])
    # I/O latency per device (iostat) — graceful if binary missing
    if shutil.which("iostat"):
        snapshot["modules"]["iostat"] = _safe_module("iostat", lambda: _run_module(
            iostat, rules,
            lambda: {"devices": [d.to_dict() for d in iostat.collect_iostat()]},
            lambda v: iostat.evaluate(v, rules),
        ))
        if not snapshot["modules"]["iostat"].get("module_error"):
            audit.monitor_event("iostat", snapshot["modules"]["iostat"]["values"], snapshot["modules"]["iostat"]["status"])
    # Process tracker: top-N resource hogs + service restart counter
    pt_window = rules.get("process_tracker", {}).get("window_minutes", 60)
    pt_top_n = rules.get("process_tracker", {}).get("top_n", 10)
    snapshot["modules"]["process_tracker"] = _safe_module("process_tracker", lambda: _run_module(
        process_tracker, rules,
        lambda: {
            "top_processes": [p.to_dict() for p in process_tracker.get_top_processes(top_n=pt_top_n)],
            "service_restarts": [r.to_dict() for r in process_tracker.collect_service_restarts(window_minutes=pt_window)],
            "window_minutes": pt_window,
        },
        lambda v: process_tracker.evaluate(v, rules),
    ))
    if not snapshot["modules"]["process_tracker"].get("module_error"):
        audit.monitor_event("process_tracker", snapshot["modules"]["process_tracker"]["values"], snapshot["modules"]["process_tracker"]["status"])
    # File descriptor monitor — system-wide + top-N consumers
    fd_top_n = rules.get("fd_check", {}).get("top_n", 5)
    snapshot["modules"]["fd_check"] = _safe_module("fd_check", lambda: _run_module(
        fd_check, rules,
        lambda: {
            "system": fd_check.collect_fd_system().to_dict(),
            "top_processes": fd_check.collect_top_fd_processes(top_n=fd_top_n),
        },
        lambda v: fd_check.evaluate(v, rules),
    ))
    if not snapshot["modules"]["fd_check"].get("module_error"):
        audit.monitor_event("fd_check", snapshot["modules"]["fd_check"]["values"], snapshot["modules"]["fd_check"]["status"])
    # AIDE file integrity (graceful if not installed or no baseline)
    if shutil.which("aide"):
        snapshot["modules"]["aide"] = _safe_module("aide", lambda: _run_module(
            aide_check, rules,
            lambda: aide_check.collect_aide_report().to_dict(),
            lambda v: aide_check.evaluate(v, rules),
        ))
        if not snapshot["modules"]["aide"].get("module_error"):
            audit.monitor_event("aide", snapshot["modules"]["aide"]["values"], snapshot["modules"]["aide"]["status"])
    # Security baseline (SSH config + SUID binaries + listening ports)
    snapshot["modules"]["security_baseline"] = _safe_module("security_baseline", lambda: _run_module(
        security_baseline, rules,
        lambda: {
            "sshd_config": security_baseline.collect_sshd_config(),
            "suid_binaries": security_baseline.scan_suid_binaries(),
            "listening_ports": security_baseline.collect_listening_ports(),
        },
        lambda v: security_baseline.evaluate(v, rules),
    ))
    if not snapshot["modules"]["security_baseline"].get("module_error"):
        audit.monitor_event("security_baseline", snapshot["modules"]["security_baseline"]["values"], snapshot["modules"]["security_baseline"]["status"])
    # Uptime / boot time
    snapshot["modules"]["uptime"] = _safe_module("uptime", lambda: _run_module(
        uptime, rules, lambda: uptime.collect(),
        lambda v: uptime.evaluate(v, rules),
    ))
    if not snapshot["modules"]["uptime"].get("module_error"):
        audit.monitor_event("uptime", snapshot["modules"]["uptime"]["values"], snapshot["modules"]["uptime"]["status"])
    # Agent self-health (heartbeat)
    # The heartbeat is written AFTER this run completes, so on the first run
    # we'll see "missing" — that's expected and we record it but don't alert.
    snapshot["modules"]["health"] = _safe_module("health", lambda: _run_module(
        health, rules, lambda: health.collect(),
        lambda v: health.evaluate(v, rules),
    ))
    # Don't alert "missing" on the first ever run — that's the agent itself.
    if (snapshot["modules"]["health"]["values"].get("state") == "missing"
            and snapshot["modules"]["health"]["values"].get("last_run_ts") is None):
        # Suppress first-run noise; record as ok so overall isn't degraded
        snapshot["modules"]["health"]["status"] = "ok"
    if not snapshot["modules"]["health"].get("module_error"):
        audit.monitor_event("health", snapshot["modules"]["health"]["values"], snapshot["modules"]["health"]["status"])
    # FreeSWITCH tiers (FS-01..25) — OFF by default. The agent box is not a PBX,
    # so probing fs_cli here produces false crits. Monitored PBX machines opt in
    # per-host via their connector's freeswitch_enabled flag (fleet SSM), never
    # on the agent's own self-scan. run_freeswitch=True re-enables all four tiers
    # for the rare case the agent box itself runs FreeSWITCH.
    if run_freeswitch:
        _run_freeswitch_tiers(snapshot, rules)

    # --- Self-monitoring: agent-machine resilience + notification-bot
    # connectivity. Always attached, independent of any monitored machine, and
    # never includes FreeSWITCH. Lives in snapshot["self"] (NOT ["modules"]) so
    # the diagnose adapter — which only reads ["modules"] — leaves it untouched;
    # self-repair is handled separately (repair/self_repair.py).
    snapshot["self"] = _run_self_checks(rules)

    # Compute overall status (worst wins) across monitored modules + self.
    rank = {"ok": 0, "warn": 1, "crit": 2}
    worst = "ok"
    for mod_data in snapshot["modules"].values():
        status = mod_data.get("status", "ok")
        if status in rank and rank[status] > rank[worst]:
            worst = status
    self_status = _defcon_to_status(snapshot["self"].get("self_defcon", 5))
    if rank.get(self_status, 0) > rank[worst]:
        worst = self_status
    snapshot["overall_status"] = worst

    # Persist numeric metrics to local time-series DB (graceful if disabled)
    _persist_to_timeseries(snapshot, rules)

    # Run predictions on collected time-series data
    _run_predictions(snapshot, rules)

    logger.info(
        "monitor_complete",
        overall=worst,
        modules=list(snapshot["modules"].keys()),
    )

    return snapshot


def _run_module(_mod: Any, _rules: dict, collect_fn: Callable[[], dict], evaluate_fn: Callable[[dict], str]) -> tuple[dict, str]:
    """Run a module's (collect, evaluate) pair and return (values, status).

    Pulled out so ``_safe_module`` can wrap it in a single try/except.
    """
    values = collect_fn()
    status = evaluate_fn(values)
    return values, status


def _run_freeswitch_tiers(snapshot: dict[str, Any], rules: dict) -> None:
    """Run all four FreeSWITCH monitor tiers (FS-01..25) into the snapshot.

    Only invoked when the host is a PBX (``run_freeswitch=True``). Extracted from
    run_all so the agent's self-scan can skip FreeSWITCH cleanly, and so a
    monitored PBX machine can opt in without duplicating this wiring.
    """
    # FreeSWITCH Tier 1 (FS-01..05)
    snapshot["modules"]["freeswitch"] = _safe_module("freeswitch", lambda: _run_module(
        freeswitch, rules,
        lambda: freeswitch.collect_all(),
        lambda v: freeswitch.evaluate(v, rules),
    ))
    if not snapshot["modules"]["freeswitch"].get("module_error"):
        audit.monitor_event("freeswitch", snapshot["modules"]["freeswitch"]["values"], snapshot["modules"]["freeswitch"]["status"])
    # FreeSWITCH Tier 2 (FS-06..09)
    snapshot["modules"]["freeswitch_network"] = _safe_module("freeswitch_network", lambda: _run_module(
        freeswitch, rules,
        lambda: freeswitch.collect_network(),
        lambda v: freeswitch.evaluate_network(v, rules),
    ))
    if not snapshot["modules"]["freeswitch_network"].get("module_error"):
        audit.monitor_event("freeswitch_network", snapshot["modules"]["freeswitch_network"]["values"], snapshot["modules"]["freeswitch_network"]["status"])
    # FreeSWITCH Tier 3 (FS-10..15)
    snapshot["modules"]["freeswitch_operational"] = _safe_module("freeswitch_operational", lambda: _run_module(
        freeswitch, rules,
        lambda: freeswitch.collect_operational(),
        lambda v: freeswitch.evaluate_operational(v, rules),
    ))
    if not snapshot["modules"]["freeswitch_operational"].get("module_error"):
        audit.monitor_event("freeswitch_operational", snapshot["modules"]["freeswitch_operational"]["values"], snapshot["modules"]["freeswitch_operational"]["status"])
    # FreeSWITCH Tier 4 (FS-16..25)
    snapshot["modules"]["freeswitch_edge"] = _safe_module("freeswitch_edge", lambda: _run_module(
        freeswitch, rules,
        lambda: freeswitch.collect_edge_cases(),
        lambda v: freeswitch.evaluate_edge_cases(v, rules),
    ))
    if not snapshot["modules"]["freeswitch_edge"].get("module_error"):
        audit.monitor_event("freeswitch_edge", snapshot["modules"]["freeswitch_edge"]["values"], snapshot["modules"]["freeswitch_edge"]["status"])


def _defcon_to_status(defcon: int) -> str:
    """Map a self DEFCON (1-5) to a monitor status string for the worst-wins fold."""
    if defcon <= 2:
        return "crit"
    if defcon <= 4:
        return "warn"
    return "ok"


def _run_self_checks(rules: dict) -> dict[str, Any]:
    """Agent self-monitoring: machine resilience + notification-bot connectivity.

    Always runs, independent of any monitored machine, and never includes
    FreeSWITCH (the agent box has no PBX). Never raises — a probe failure
    degrades the section rather than aborting the sweep.
    """
    import os
    from ipracticom_sweeper.monitor import bot_connectivity, self_snapshot

    state_dir = Path(os.environ.get(
        "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper",
    ))

    # Live-probe every configured bot (Telegram + Slack) and cache the result.
    # The read-path (build_self_section / /api/self-health) reads the cache, so
    # this is the single place per sweep that does live network probing.
    try:
        bot_connectivity.check_and_persist(state_dir)
    except Exception as e:
        logger.warning("self_bot_probe_failed", error=str(e))

    # Aggregate all self signals (state-dir disk, audit size, bots, watchdog,
    # uptime) into one section with a self_defcon.
    try:
        section = self_snapshot.build_self_section(state_dir)
    except Exception as e:
        logger.warning("self_section_failed", error=str(e))
        section = {"degraded": True, "self_defcon": 3, "error": str(e)}

    # Optional active self /healthz ping (detects a deadlocked agent that still
    # answers the socket). Configured under rules["self"]["healthz_url"].
    healthz_url = (rules.get("self", {}) or {}).get("healthz_url")
    if healthz_url:
        from ipracticom_sweeper.monitor import healthz_probe
        try:
            hz = healthz_probe.probe_healthz(healthz_url)
            section["healthz"] = {
                "status": hz.status,
                "status_code": hz.status_code,
                "latency_ms": hz.latency_ms,
                "url": hz.url,
                "error": hz.error,
            }
            if hz.status == "crit":
                section["self_defcon"] = min(section.get("self_defcon", 5), 2)
        except Exception as e:
            logger.warning("self_healthz_failed", error=str(e))

    return section


def _persist_to_timeseries(snapshot: dict, rules: dict) -> None:
    """Write key numeric metrics to the local time-series DB.

    Extracts a small set of high-signal scalars (defcon, CPU%, memory%,
    disk% per mount, FD%, overall_status as numeric) and appends them
    to the SQLite store. The agent_api /api/history endpoint reads
    from the same store.

    Storage path comes from IPRACTICOM_SWEEPER_STATE_DIR env var
    (default /var/lib/ipracticom-sweeper), consistent with other state.
    """
    storage_cfg = rules.get("storage", {})
    if not storage_cfg.get("enabled", True):
        return
    retention_days = storage_cfg.get("retention_days", 30)

    import os
    from ipracticom_sweeper.storage import TimeSeriesDB
    state_dir = Path(os.environ.get(
        "IPRACTICOM_SWEEPER_STATE_DIR",
        "/var/lib/ipracticom-sweeper",
    ))
    try:
        db = TimeSeriesDB(state_dir / "metrics.db", retention_days=retention_days)
    except (OSError, PermissionError) as e:
        # No write permission (e.g. dev env) — skip silently
        logger.debug("timeseries_init_skipped", error=str(e))
        return

    host = os.environ.get("IPRACTICOM_SWEEPER_HOST_ID", "localhost")
    now = int(time.time())

    # Overall defcon → store as int 1-5
    defcon = _defcon_to_int(snapshot.get("overall_status", "ok"))
    try:
        db.write(host=host, metric="agent.defcon", value=defcon, ts=now)
    except Exception as e:
        logger.debug("timeseries_write_skipped", metric="agent.defcon", error=str(e))

    # Per-module numeric metrics
    metrics_to_persist = [
        ("cpu", "cpu.idle_percent"),
        ("cpu", "cpu.load_5min"),
        ("memory", "memory.used_percent"),
        ("disk", "disk.used_percent"),
        ("fd_check", "fd_check.used_percent"),
        ("process_tracker", "process_tracker.cpu_top"),
        ("process_tracker", "process_tracker.mem_top"),
    ]
    for module_key, metric_name in metrics_to_persist:
        mod_data = snapshot.get("modules", {}).get(module_key)
        if not mod_data:
            continue
        value = _extract_scalar_metric(mod_data.get("values", {}), metric_name)
        if value is None:
            continue
        try:
            db.write(host=host, metric=metric_name, value=float(value), ts=now)
        except Exception as e:
            logger.debug("timeseries_write_skipped", metric=metric_name, error=str(e))

    # Per-mount disk% (one row per mount)
    disk_data = snapshot.get("modules", {}).get("disk", {}).get("values", {})
    for mount in disk_data.get("mounts", []) or []:
        mountpoint = mount.get("mountpoint") or mount.get("target")
        used = mount.get("used_percent")
        if not mountpoint or used is None:
            continue
        try:
            db.write(
                host=host,
                metric=f"disk.used_percent.{mountpoint}",
                value=float(used),
                ts=now,
            )
        except Exception as e:
            logger.debug("timeseries_write_skipped", metric=f"disk.{mountpoint}", error=str(e))

    # Enforce retention every sweep — prune_old_data was never called, so
    # metrics.db grew without bound on an agent whose whole job is warning
    # about disks filling up. The DELETE is indexed on ts and cheap.
    try:
        removed = db.prune_old_data()
        if removed:
            logger.debug("timeseries_pruned", rows=removed)
    except Exception as e:
        logger.debug("timeseries_prune_skipped", error=str(e))
    finally:
        db.close()


def _defcon_to_int(overall: str) -> int:
    """Map overall status string to a numeric 1-5 (lower = worse)."""
    return {"ok": 5, "warn": 4, "crit": 2}.get(overall, 3)


def _run_predictions(snapshot: dict, rules: dict) -> None:
    """Run predict layer on time-series data, attach to snapshot.

    Reads the local metrics.db, runs linear regression to predict
    threshold crossings, and writes the predictions to snapshot["predictions"].
    Graceful: no-op if storage is disabled or db missing.
    """
    import os
    from ipracticom_sweeper.predict.integration import collect_predictions

    state_dir = Path(os.environ.get(
        "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
    ))
    db_path = state_dir / "metrics.db"
    if not db_path.exists():
        snapshot["predictions"] = []
        return

    host = os.environ.get("IPRACTICOM_SWEEPER_HOST_ID", "localhost")
    thresholds = rules.get("predict", {}).get("thresholds", None)
    try:
        preds = collect_predictions(db_path, host=host, thresholds=thresholds)
        snapshot["predictions"] = [p.to_dict() for p in preds]
    except Exception as e:
        logger.debug("predict_skipped", error=str(e))
        snapshot["predictions"] = []


def _extract_scalar_metric(values: dict, dotted_key: str) -> float | None:
    """Pull a scalar numeric value out of a module's values dict.

    dotted_key uses dots for nested access. Returns None if not found
    or not numeric.
    """
    cur = values
    for part in dotted_key.split(".")[1:]:  # skip module prefix
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    import json

    snap = run_all()
    print(json.dumps(snap, indent=2, default=str))
