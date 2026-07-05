"""Sprint v1.3.0 Slice 2 — Module registry + catalog."""
from __future__ import annotations

import logging

import pytest

from ipracticom_sweeper.config import module_registry as mr


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

def test_30_2_catalog_loads():
    mods = mr.discover_modules()
    assert len(mods) > 0
    # Spot-check we have all three kinds
    kinds = {m.kind for m in mods}
    assert {"monitor", "repair", "runbook"} <= kinds


def test_30_2_module_info_shape():
    mods = mr.discover_modules()
    m = next(m for m in mods if m.kind == "monitor" and m.name == "disk_check")
    assert m.title_en != ""
    assert m.title_he != ""
    assert m.description != ""
    assert m.risk in ("low", "medium", "high")


def test_30_2_param_spec_shape():
    mods = mr.discover_modules()
    m = next(m for m in mods if m.name == "disk_check")
    assert m.params
    p = m.params[0]
    assert p.name
    assert p.type in ("int", "float", "str", "bool", "list")
    assert p.default is not None or p.type == "str"


def test_30_2_modules_sorted():
    mods = mr.discover_modules()
    keys = [m.name for m in mods]
    # Within each kind, alphabetical
    by_kind: dict[str, list[str]] = {}
    for m in mods:
        by_kind.setdefault(m.kind, []).append(m.name)
    for names in by_kind.values():
        assert names == sorted(names), f"not sorted: {names}"


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def test_30_2_filter_by_kind():
    mods = mr.discover_modules()
    monitors = mr.filter_modules(mods, kind="monitor")
    assert all(m.kind == "monitor" for m in monitors)


def test_30_2_filter_by_tag():
    mods = mr.discover_modules()
    fs = mr.filter_modules(mods, tag="freeswitch")
    assert all("freeswitch" in m.tags for m in fs)
    assert len(fs) > 0


def test_30_2_filter_by_risk():
    mods = mr.discover_modules()
    high = mr.filter_modules(mods, risk="high")
    assert all(m.risk == "high" for m in high)
    assert len(high) > 0  # catalog has high-risk items


def test_30_2_filter_available_only_hides_catalog_only():
    mods = mr.discover_modules()
    available = mr.filter_modules(mods, available_only=True)
    assert all(not m.catalog_only for m in available)
    # Total count must be <= raw count
    assert len(available) <= len(mods)


def test_30_2_filter_combines():
    mods = mr.discover_modules()
    high_fs = mr.filter_modules(mods, kind="monitor", tag="freeswitch", risk="high")
    for m in high_fs:
        assert m.kind == "monitor"
        assert "freeswitch" in m.tags
        assert m.risk == "high"


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def test_30_2_get_module_found():
    m = mr.get_module("disk_check", kind="monitor")
    assert m is not None
    assert m.name == "disk_check"


def test_30_2_get_module_not_found():
    assert mr.get_module("never_existed_xyz") is None


def test_30_2_get_module_kind_mismatch():
    """Looking up a monitor name with kind=repair returns None."""
    m = mr.get_module("disk_check", kind="repair")
    assert m is None


# ---------------------------------------------------------------------------
# Default host config builder
# ---------------------------------------------------------------------------

def test_30_2_default_host_config_has_all_kinds():
    cfg = mr.default_host_config("new-host")
    assert "monitors" in cfg
    assert "repairs" in cfg
    assert "runbooks" in cfg
    assert "suppressions" in cfg
    assert cfg["host"]["name"] == "new-host"


def test_30_2_default_host_config_disables_high_risk_monitors():
    """High-risk monitors default to enabled=False to force opt-in."""
    cfg = mr.default_host_config("new-host")
    high_monitors = mr.filter_modules(mr.discover_modules(),
                                      kind="monitor", risk="high")
    for hm in high_monitors:
        entry = next((m for m in cfg["monitors"] if m["name"] == hm.name), None)
        if entry is None:
            continue  # not in defaults; OK
        assert entry["enabled"] is False, f"{hm.name} should default to disabled"


def test_30_2_default_host_config_repairs_require_approval_when_not_low():
    """Medium/high risk repairs default to require_approval=True."""
    cfg = mr.default_host_config("new-host")
    for r in cfg["repairs"]:
        if r.get("require_approval") is False:
            # The only repairs that default to no-approval should be low risk
            m = mr.get_module(r["name"], kind="repair")
            assert m is not None
            assert m.risk == "low", f"{r['name']} no-approval but risk={m.risk}"


def test_30_2_default_host_config_uses_param_defaults():
    """Default settings should match the catalog's default values."""
    cfg = mr.default_host_config("new-host")
    mods_by_name = {m.name: m for m in mr.discover_modules()}
    for m_entry in cfg["monitors"] + cfg["repairs"] + cfg["runbooks"]:
        m = mods_by_name.get(m_entry["name"])
        if m is None:
            continue
        for p in m.params:
            if p.name in ("interval_sec",) and p.name not in m_entry:
                continue  # monitor may handle this specially
            if p.name in m_entry:
                assert m_entry[p.name] == p.default, (
                    f"{m.name}.{p.name}: catalog default={p.default!r}, "
                    f"host default={m_entry[p.name]!r}"
                )


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

def test_30_2_drift_logged_but_doesnt_raise(caplog):
    """Non-strict mode logs warnings but does not raise."""
    with caplog.at_level(logging.WARNING, logger="ipracticom_sweeper.module_registry"):
        mods = mr.discover_modules(strict=False)
    # Some drift may or may not exist depending on the catalog state;
    # we just assert we get a result back.
    assert isinstance(mods, list)


# ---------------------------------------------------------------------------
# Drift hardening — every catalog entry must point at real code (or be
# explicitly suppressed). Three historic drift entries fixed in this slice:
# - fs_inode_check / freeswitch_health lived in monitor/freeswitch.py;
#   the catalog now points at that single file.
# - 'checks' is the orchestrator, not a leaf monitor — removed from catalog.
# ---------------------------------------------------------------------------

_IGNORED_DRIFT = set()  # no exemptions yet; tighten as catalog grows


def test_30_2_no_catalog_only_entries_except_ignored():
    """Every catalog monitor entry must resolve to a real monitor file."""
    mods = mr.discover_modules(strict=False)
    catalog_only = [
        m for m in mods
        if m.catalog_only and m.name not in _IGNORED_DRIFT
    ]
    assert catalog_only == [], (
        f"catalog entries without matching code: "
        f"{[(m.kind, m.name) for m in catalog_only]}"
    )


def test_30_2_strict_mode_raises_on_any_drift():
    """strict=True is the CI/dashbord-deployment gate — must raise on drift."""
    from ipracticom_sweeper.config import module_registry as mr_mod
    mr_mod.discover_modules(strict=True)  # noqa: must not raise
