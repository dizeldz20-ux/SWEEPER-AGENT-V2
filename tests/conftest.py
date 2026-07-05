"""Pytest configuration — sandbox tests into a tmp state dir.

The production code reads /var/lib/ipracticom-sweeper for audit logs,
pending proposals, and snapshots. If tests write there, fake entries
("boom", "snap-123", etc.) leak into the production dashboard and confuse
operators reviewing real history.

This conftest points IPRACTICOM_SWEEPER_STATE_DIR at a per-session tmp dir
BEFORE any production module imports its paths, so tests never touch the
real /var/lib store.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest


# --- Session-scope sandbox ---------------------------------------------------
# IMPORTANT: this runs at conftest import time, BEFORE pytest collects tests
# and BEFORE test modules (or anything they import) capture production paths.
_SESSION_STATE_DIR = tempfile.mkdtemp(prefix="ipracticom-sweeper-tests-")
os.environ["IPRACTICOM_SWEEPER_STATE_DIR"] = _SESSION_STATE_DIR

# If a host shell pre-loaded /tmp/sweeper_tunnel.env (Cloudflare tunnel creds),
# those env vars would gate the dashboard test client with HTTP Basic auth
# and break every request. Strip them so tests run as the open local mode.
for _k in ("DASHBOARD_USER", "DASHBOARD_PASS", "AGENT_API_TOKEN"):
    os.environ.pop(_k, None)

# If a previous test session left production modules cached with the real
# /var/lib path, reload them now so they re-read the env var.
for _mod in ("ipracticom_sweeper.repair.pending", "ipracticom_sweeper.repair.actions"):
    if _mod in sys.modules:
        importlib.reload(sys.modules[_mod])


def pytest_configure(config):
    """Sanity check — fail loud if the sandbox isn't wired up correctly."""
    from ipracticom_sweeper.repair import pending as p

    expected = Path(_SESSION_STATE_DIR) / "audit" / "repairs.jsonl"
    assert str(p.AUDIT_LOG) == str(expected), (
        f"Test sandbox not active: AUDIT_LOG={p.AUDIT_LOG} "
        f"(expected {expected}). Production audit would be polluted."
    )


@pytest.fixture(autouse=True)
def _clean_state_between_tests():
    """Wipe audit/pending/snapshot state between tests so they don't bleed.

    autouse=True so every test starts with an empty slate without having to
    remember to ask for the fixture.
    """
    base = Path(_SESSION_STATE_DIR)
    for sub in ("audit", "pending_repairs", "snapshots"):
        d = base / sub
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    f.unlink()
            for f in sorted(d.glob("**/*"), reverse=True):
                if f.is_dir():
                    try:
                        f.rmdir()
                    except OSError:
                        pass
    yield
