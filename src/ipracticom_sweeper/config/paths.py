"""Centralized filesystem paths for the iPracticom Sweeper.

Every state/log/audit directory the agent touches is derived from a
single environment variable — ``IPRACTICOM_SWEEPER_STATE_DIR`` — and
exposed through this module. Code MUST NOT call ``os.environ.get`` for
``IPRACTICOM_SWEEPER_STATE_DIR`` directly; use one of the helpers here
so the layout can be changed in one place if needed.

Why centralize:
    - 17 inline ``os.environ.get("IPRACTICOM_SWEEPER_STATE_DIR", ...)``
      calls across 7 files made the layout effectively read-only at
      runtime. Adding a new subdir meant editing every file.
    - Tests can monkeypatch ``paths.ROOT`` once instead of setting an
      env var and rebuilding every Path.
    - The default ``/var/lib/ipracticom-sweeper`` is now defined once
      and surfaced in the docs.

Public API:
    ROOT              → base state directory (env-overridable)
    maintenance_dir   → persistent maintenance flags / locks
    fleet_snapshots   → per-host collector snapshots
    connectors_file   → AWS SSM connector config (YAML)
    pending_repairs   → approval workflow in-flight proposals
    approved_repairs  → approved, awaiting execution
    rejected_repairs  → rejected archive
    audit_log         → repairs.jsonl audit trail
    ntp_history       → NTP offset time-series
    token_health      → Telegram token health tracker state

All paths are returned as :class:`pathlib.Path` and are created lazily
on access (no side effects at import time).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from .._log import log_suppressed

# ---------------------------------------------------------------------------
# Environment handling
# ---------------------------------------------------------------------------

_DEFAULT_ROOT = Path("/var/lib/ipracticom-sweeper")
_ENV_VAR = "IPRACTICOM_SWEEPER_STATE_DIR"


@lru_cache(maxsize=1)
def ROOT() -> Path:
    """Return the agent's root state directory.

    Read once per process from ``$IPRACTICOM_SWEEPER_STATE_DIR`` (falls
    back to ``/var/lib/ipracticom-sweeper``). The result is cached so
    monkeypatching the env var at runtime has no effect — but tests can
    call ``ROOT.cache_clear()`` to reset.
    """
    return Path(os.environ.get(_ENV_VAR, str(_DEFAULT_ROOT)))


# Expose the env-var name so tests + tools can refer to it symbolically.
ENV_STATE_DIR = _ENV_VAR
DEFAULT_ROOT = _DEFAULT_ROOT


# ---------------------------------------------------------------------------
# Subdirectory helpers
# ---------------------------------------------------------------------------

def _ensure(p: Path) -> Path:
    """Return ``p`` with its parents created (best-effort, ignore EEXIST)."""
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # If we can't create (e.g. read-only FS in a test), the caller will
        # surface the real error on write. Don't swallow it here.
        log_suppressed("paths_ensure_mkdir", e)
    return p


def maintenance_dir() -> Path:
    return _ensure(ROOT() / "maintenance")


def fleet_snapshots() -> Path:
    return _ensure(ROOT() / "fleet" / "snapshots")


def connectors_file() -> Path:
    return ROOT() / "connectors.yaml"


def pending_repairs() -> Path:
    return _ensure(ROOT() / "pending_repairs")


def approved_repairs() -> Path:
    return _ensure(ROOT() / "approved_repairs")


def rejected_repairs() -> Path:
    return _ensure(ROOT() / "rejected_repairs")


def audit_log() -> Path:
    return _ensure(ROOT() / "audit")


def ntp_history() -> Path:
    return _ensure(ROOT() / "ntp_history")


def token_health() -> Path:
    return _ensure(ROOT() / "token_health")


# Backwards-compatible aliases (the old `state_dir` symbol from connectors.py
# was actually a function in some call sites and a constant in others).
def state_dir() -> Path:
    """Deprecated alias for :func:`ROOT`. Prefer ``paths.ROOT()``."""
    return ROOT()


__all__ = [
    "ROOT",
    "ENV_STATE_DIR",
    "DEFAULT_ROOT",
    "maintenance_dir",
    "fleet_snapshots",
    "connectors_file",
    "pending_repairs",
    "approved_repairs",
    "rejected_repairs",
    "audit_log",
    "ntp_history",
    "token_health",
    "state_dir",  # deprecated
]
