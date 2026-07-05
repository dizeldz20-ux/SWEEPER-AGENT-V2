"""Sprint 20.1 — Centralized state paths (config.paths)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def fresh_paths(monkeypatch, tmp_path):
    """Force a clean ROOT pointing at a tmp dir for the duration of the test."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    # The module uses lru_cache on ROOT() — must clear so the new env is seen.
    from ipracticom_sweeper.config import paths
    paths.ROOT.cache_clear()
    yield tmp_path
    paths.ROOT.cache_clear()


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def test_20_1_root_reads_env_var(fresh_paths):
    from ipracticom_sweeper.config.paths import ROOT
    assert ROOT() == fresh_paths


def test_20_1_root_falls_back_to_default(tmp_path):
    from ipracticom_sweeper.config import paths
    paths.ROOT.cache_clear()
    with mock.patch.dict(os.environ, {}, clear=True):
        # Remove the var explicitly
        os.environ.pop("IPRACTICOM_SWEEPER_STATE_DIR", None)
        paths.ROOT.cache_clear()
        assert paths.ROOT() == paths.DEFAULT_ROOT


def test_20_1_root_is_cached_per_process():
    from ipracticom_sweeper.config.paths import ROOT
    a = ROOT()
    b = ROOT()
    assert a is b or a == b  # lru_cache returns same object, but tolerate str


# ---------------------------------------------------------------------------
# Subdirectory helpers
# ---------------------------------------------------------------------------

def test_20_1_maintenance_dir_creates_parent(fresh_paths):
    from ipracticom_sweeper.config.paths import maintenance_dir
    p = maintenance_dir()
    assert p == fresh_paths / "maintenance"
    assert p.exists()
    assert p.is_dir()


def test_20_1_fleet_snapshots_creates_parent(fresh_paths):
    from ipracticom_sweeper.config.paths import fleet_snapshots
    p = fleet_snapshots()
    assert p == fresh_paths / "fleet" / "snapshots"
    assert p.is_dir()


def test_20_1_connectors_file_is_a_file_path(fresh_paths):
    """connectors_file should NOT create the file — only return the path."""
    from ipracticom_sweeper.config.paths import connectors_file
    p = connectors_file()
    assert p == fresh_paths / "connectors.yaml"
    assert not p.exists()  # explicit: helper does not touch disk


def test_20_1_pending_repairs_creates_parent(fresh_paths):
    from ipracticom_sweeper.config.paths import pending_repairs
    p = pending_repairs()
    assert p == fresh_paths / "pending_repairs"
    assert p.is_dir()


def test_20_1_audit_log_creates_parent(fresh_paths):
    from ipracticom_sweeper.config.paths import audit_log
    p = audit_log()
    assert p == fresh_paths / "audit"
    assert p.is_dir()


# ---------------------------------------------------------------------------
# Backwards compatibility — the `state_dir` alias
# ---------------------------------------------------------------------------

def test_20_1_state_dir_alias_matches_root(fresh_paths):
    from ipracticom_sweeper.config.paths import state_dir, ROOT
    assert state_dir() == ROOT()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_20_1_env_var_name_constant():
    from ipracticom_sweeper.config.paths import ENV_STATE_DIR
    assert ENV_STATE_DIR == "IPRACTICOM_SWEEPER_STATE_DIR"


def test_20_1_default_root_is_var_lib_ipracticom():
    from ipracticom_sweeper.config.paths import DEFAULT_ROOT
    assert DEFAULT_ROOT == Path("/var/lib/ipracticom-sweeper")


# ---------------------------------------------------------------------------
# Module surface — must be importable from config directly
# ---------------------------------------------------------------------------

def test_20_1_paths_exported_from_config_package():
    from ipracticom_sweeper.config import paths
    # Public API
    for name in (
        "ROOT", "maintenance_dir", "fleet_snapshots", "connectors_file",
        "pending_repairs", "approved_repairs", "rejected_repairs",
        "audit_log", "ntp_history", "token_health", "state_dir",
    ):
        assert hasattr(paths, name), f"missing public symbol: {name}"
