"""Fleet collector — periodic loop that pulls SSM snapshots from all configured
remote hosts and writes them to disk for the dashboard /fleet view.

Lifecycle
---------
1. Operator configures connectors via /settings/connectors (or directly via
   config.connectors.add()).
2. The collector runs in a background thread (started by start_collector_loop()).
3. Every COLLECT_INTERVAL_SEC seconds, it:
   a. Reads enabled connectors
   b. Calls AwsSsmConnector.collect_one() for each (in parallel, bounded)
   c. Writes a HostSnapshot JSON to state_dir/fleet/snapshots/<name>.json
   d. Updates the connector's last_collected_at / last_error
4. The dashboard /fleet view reads these JSON files (no SSM round-trip per page load).

Why a thread, not systemd timer
-------------------------------
A timer-based approach would need a separate service for fleet collection,
duplicating the existing ipracticom-sweeper.service. A thread inside the
agent_api process keeps deployment simple — one service, one state dir.
Trade-off: if agent_api crashes, fleet collection pauses. Acceptable for v1
because the dashboard would also be down, and the next pipeline tick on the
local box still runs via the timer.

Why per-host JSON files (not one big fleet.json)
-------------------------------------------------
- Atomic per-host writes: partial fleet never breaks the /fleet view
- Easy to delete by removing one file when a connector is deleted
- Future-proof: per-host detail pages can read one file
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import structlog

from .._log import log_suppressed

from ipracticom_sweeper.config import (
    load_connectors,
    mark_connector_collected,
    mark_connector_error,
)
from ipracticom_sweeper.fleet import AwsSsmConnector, SsmError

logger = structlog.get_logger("ipracticom.fleet.collector")


# Defaults; can be overridden via env if needed.
COLLECT_INTERVAL_SEC = int(os.environ.get("FLEET_COLLECT_INTERVAL_SEC", "300"))  # 5 min
STARTUP_DELAY_SEC = int(os.environ.get("FLEET_STARTUP_DELAY_SEC", "10"))  # let agent_api boot first


def snapshots_dir() -> Path:
    """Directory where per-host snapshot JSON files live."""
    from ipracticom_sweeper.config.connectors import state_dir
    d = state_dir() / "fleet" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_snapshot(name: str, snapshot: dict) -> Path:
    """Atomic per-host write — write to .tmp, then rename.

    Returns the path to the final file.
    """
    path = snapshots_dir() / f"{name}.json"
    tmp = path.with_suffix(".json.tmp")
    payload = {"name": name, "collected_at": time.time(), "snapshot": snapshot}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_snapshot(name: str) -> dict | None:
    """Read one host's last snapshot. None if missing."""
    path = snapshots_dir() / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_all_snapshots() -> list[dict]:
    """Read every host's last snapshot, sorted by host name."""
    d = snapshots_dir()
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            log_suppressed("fleet_snapshot_read", e)
            continue
    return out


# --- Single-shot collection (used by the loop + by /api/connectors/<n>/test) ---

def collect_once() -> dict[str, dict]:
    """Run one collection cycle. Returns {connector_name: snapshot_dict}.

    Side effects:
      - Writes per-host snapshot JSON files
      - Updates connector last_collected_at / last_error

    No-op (returns {}) if there are no enabled connectors.
    Errors are recorded per-connector, not raised — one bad host doesn't
    poison the rest of the fleet.
    """
    connectors = [c for c in load_connectors() if c.enabled]
    if not connectors:
        return {}

    # Group by region so we instantiate one AwsSsmConnector per region
    by_region: dict[str, list] = {}
    for c in connectors:
        by_region.setdefault(c.region, []).append(c)

    results: dict[str, dict] = {}
    for region, conns in by_region.items():
        try:
            ssm = AwsSsmConnector(region=region)
        except SsmError as e:
            for c in conns:
                mark_connector_error(c.name, f"region {region}: {e}")
                results[c.name] = {"available": False, "reason": str(e)}
            continue

        instance_ids = [c.instance_id for c in conns]
        # Only machines the operator marked as FreeSWITCH hosts get the FS probe.
        fs_ids = {c.instance_id for c in conns if getattr(c, "freeswitch_enabled", False)}
        snapshots = ssm.collect_all(instance_ids, freeswitch_ids=fs_ids)
        for c, snap in zip(conns, snapshots):
            snap_dict = {
                "instance_id": snap.instance_id,
                "available": snap.available,
                "reason": getattr(snap, "reason", None),
                "data": getattr(snap, "data", None),
                "duration_ms": getattr(snap, "duration_ms", None),
            }
            results[c.name] = snap_dict
            if snap.available:
                write_snapshot(c.name, snap_dict)
                mark_connector_collected(c.name)
            else:
                mark_connector_error(c.name, snap.reason or "unknown")
    return results


# --- Background loop -----------------------------------------------------

_loop_thread: threading.Thread | None = None
_loop_stop = threading.Event()


def _loop_main() -> None:
    """Background thread body. Runs forever until _loop_stop is set."""
    logger.info("fleet collector loop starting", interval_sec=COLLECT_INTERVAL_SEC)
    time.sleep(STARTUP_DELAY_SEC)
    while not _loop_stop.is_set():
        try:
            collect_once()
        except Exception as e:
            logger.exception("fleet collector cycle crashed", error=str(e))
        # Sleep in small chunks so we respond to stop quickly
        for _ in range(COLLECT_INTERVAL_SEC):
            if _loop_stop.is_set():
                break
            time.sleep(1)
    logger.info("fleet collector loop stopped")


def start_collector_loop() -> None:
    """Start the background collection thread. Idempotent — safe to call twice.

    Intended to be called once during agent_api startup. If called from
    the dashboard process, also fine (each process owns its own thread).
    """
    global _loop_thread
    if _loop_thread is not None and _loop_thread.is_alive():
        return
    _loop_stop.clear()
    _loop_thread = threading.Thread(
        target=_loop_main, name="fleet-collector", daemon=True
    )
    _loop_thread.start()


def stop_collector_loop(timeout: float = 5.0) -> None:
    """Signal the loop to stop. Used in tests and at shutdown."""
    _loop_stop.set()
    if _loop_thread is not None and _loop_thread.is_alive():
        _loop_thread.join(timeout=timeout)