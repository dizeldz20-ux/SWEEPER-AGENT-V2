"""Pure-Python helpers for the dashboard — split out of dashboard.py.

What's here:
  * `read_last_result` / `write_last_result` — on-disk cache of the
    most recent pipeline result (atomic tmp+rename).
  * `trigger_pipeline_run` — runs the pipeline in-process and
    caches the result.

What's NOT here (and intentionally stays in dashboard.py):
  * Anything that touches `app.logger`, `_is_remote_mode`,
    `_fetch_*`, or the Flask request context. Pure functions only,
    so they can be unit-tested without a request/app fixture.

This module is re-exported from `dashboard` for backwards-compat:
`from ipracticom_sweeper.dashboard import read_last_result` still works.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from ipracticom_sweeper._log import log_suppressed
from ipracticom_sweeper.config import get_server_id, load_rules
from ipracticom_sweeper.pipeline import run_pipeline

# Paths lifted from dashboard.py. Kept in sync via this module.
CACHE_DIR = Path("/var/lib/ipracticom-sweeper/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LAST_RESULT_FILE = CACHE_DIR / "last-result.json"

# Serializes concurrent writers (multiple /run/now clicks, cron + manual).
# Process-local — a single agent process never races against itself.
_write_lock = threading.Lock()


def read_last_result() -> dict[str, Any] | None:
    """Read the most recent pipeline result from disk cache."""
    if not LAST_RESULT_FILE.exists():
        return None
    try:
        with open(LAST_RESULT_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def write_last_result(result_dict: dict[str, Any]) -> None:
    """Write a pipeline result to disk cache (atomic + fsync'd).

    v1.5.15 (C-3 fix):
      - unique tmp filename (UUID suffix) so concurrent writers don't truncate
      - fsync the file before rename so a power-cut can't leave a half-written file
      - threading.Lock so two /run/now POSTs serialize rather than collide
    """
    # Build unique tmp in the same directory so rename stays atomic on
    # the same filesystem (os.replace would silently fall back to copy+delete
    # across filesystems, losing atomicity).
    tmp = LAST_RESULT_FILE.with_suffix(f".{uuid.uuid4().hex[:8]}.json.tmp")
    fd = None
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            fd = None  # os.fdopen took ownership
            json.dump(result_dict, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        with _write_lock:
            os.replace(tmp, LAST_RESULT_FILE)
    except Exception:
        # Best-effort cleanup of the orphan tmp
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as exc:
            log_suppressed("dashboard_helpers.write_last_result.tmp_cleanup", exc)
        raise


def trigger_pipeline_run(force_notify: bool = False) -> dict[str, Any]:
    """Execute the pipeline in-process and cache the result.

    We invoke the pipeline function directly (not via subprocess) so we get
    a fresh, in-process result. For long-running cron-style execution use
    the sweeper CLI via systemd timer instead.
    """
    rules = load_rules()
    result = run_pipeline(
        rules,
        auto_repair=True,
        dry_run=False,
    )
    d = result.to_dict()
    d["server"] = get_server_id()
    d["notified"] = force_notify
    write_last_result(d)
    return d
