"""Regression guard: every module keys monitor writes into snapshot.modules MUST have a corresponding catalogue entry.

History: before slice 2 (2026-07-01) the catalogue exposed only 19 of the
24 modules that run_all() actually publishes — `aide`, `http`, `iostat`,
`smart`, `ssl` were run but not editable / discoverable in the UI.

This test reads `monitor.checks.run_all()` and asserts that every key
written into ``snapshot["modules"]`` has a matching ``CHECK_REGISTRY``
entry.  Future commits that add a collector without a catalogue row will
fail loudly here instead of silently drifting again.
"""
from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from ipracticom_sweeper.catalogue import CHECK_REGISTRY
from ipracticom_sweeper.monitor import checks as monitor_checks


CATALOGUE_KEYS = {e["key"] for e in CHECK_REGISTRY}


def _extract_monitor_module_keys() -> set[str]:
    """Return every ``snapshot["modules"]["<key>"] =`` key written by run_all()."""
    src = monitor_checks.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    return set(re.findall(r'snapshot\["modules"\]\["([^"]+)"\]', text))


def test_all_monitor_modules_have_catalogue_entries():
    """Every module run_all() registers MUST also exist in the catalogue."""
    monitor_keys = _extract_monitor_module_keys()
    missing = monitor_keys - CATALOGUE_KEYS
    assert not missing, (
        f"monitor publishes {len(monitor_keys)} modules but catalogue is "
        f"missing entries for: {sorted(missing)}. Add them to CHECK_REGISTRY."
    )


def test_catalogue_has_no_stale_entries():
    """Catalogue keys must not contain entries with no backing monitor module.

    This is the reverse direction: stops catalogue drift in the other way
    (a key removed from monitor but left dangling in catalogue)."""
    monitor_keys = _extract_monitor_module_keys()
    stale = CATALOGUE_KEYS - monitor_keys
    assert not stale, (
        f"catalogue contains entries with no run_all() collector: "
        f"{sorted(stale)}. Remove them or wire a collector."
    )


def test_catalogue_keys_are_unique():
    """CHECK_REGISTRY must not contain duplicate keys."""
    keys = [e["key"] for e in CHECK_REGISTRY]
    dups = {k for k in keys if keys.count(k) > 1}
    assert not dups, f"duplicate keys in catalogue: {sorted(dups)}"


def test_live_run_all_succeeds_with_required_modules():
    """Smoke: run_all() executes without error and writes at least the
    required (always-on) module keys (cpu/memory/disk/network/services
    /logs/processes/security/kernel/uptime/health).

    Note: aide/http/iostat/smart/ssl/freeswitch* are gracefully skipped
    when their binaries/systemd unit are missing — that is documented
    intentional behaviour in checks.py. The static AST check above
    is the authoritative guard for catalogue drift."""
    snap = monitor_checks.run_all()
    produced = set(snap["modules"].keys())
    REQUIRED_ALWAYS_ON = {
        "cpu", "memory", "disk", "network", "services", "logs",
        "processes", "security", "kernel", "uptime", "health",
    }
    missing = REQUIRED_ALWAYS_ON - produced
    assert not missing, (
        f"run_all() failed to write required always-on module keys: "
        f"{sorted(missing)} — these have no graceful-degradation path."
    )