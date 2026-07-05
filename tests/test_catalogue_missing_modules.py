"""Slice 2 — Catalogue must expose all 5 missing check modules.

The five modules were imported into `monitor/checks.py` but never
registered in CHECK_REGISTRY:
  - aide       (AideCheck)
  - http       (HTTP probes)
  - iostat     (per-device I/O latency)
  - smart      (SMART disk health)
  - ssl        (TLS certificate expiry)

Each entry must:
  - exist in CHECK_REGISTRY under the exact module key
  - carry label_he + description_he
  - carry rule_keys matching the keys read by the module's evaluate()
"""
from __future__ import annotations

from ipracticom_sweeper.catalogue import CHECK_REGISTRY


REQUIRED_KEYS = {"aide", "http", "iostat", "smart", "ssl"}

# rule_keys actually consumed by each module's evaluate() — must surface here
EXPECTED_RULE_KEYS = {
    "aide": ["critical_paths"],
    "http": ["slow_response_ms"],
    "iostat": ["await_warn_ms", "await_crit_ms", "util_warn_percent", "util_crit_percent"],
    "smart": ["reallocated_warn", "reallocated_crit", "temp_warn_c"],
    "ssl": ["warn_days", "crit_days"],
}


def test_all_five_missing_modules_present():
    keys = {e["key"] for e in CHECK_REGISTRY}
    missing = REQUIRED_KEYS - keys
    assert not missing, f"catalogue missing modules: {sorted(missing)}"


def test_each_entry_has_hebrew_label_and_description():
    by_key = {e["key"]: e for e in CHECK_REGISTRY}
    for k in REQUIRED_KEYS:
        assert k in by_key, f"{k} not in registry"
        entry = by_key[k]
        assert entry.get("label_he"), f"{k} missing label_he"
        assert entry.get("description_he"), f"{k} missing description_he"


def test_each_entry_exposes_consumed_rule_keys():
    by_key = {e["key"]: e for e in CHECK_REGISTRY}
    for k, expected in EXPECTED_RULE_KEYS.items():
        assert k in by_key, f"{k} not in registry"
        declared = {rk["name"] for rk in by_key[k]["rule_keys"]}
        missing = set(expected) - declared
        assert not missing, f"{k} missing rule_keys: {sorted(missing)}"


# ============= v1.5.16 RED: monitor all-or-nothing =========================
# Code review finding #15 (HEAD v1.5.15): monitor/checks.py run_all() calls
# every module's collect() / evaluate() without per-module exception
# handling. If disk.collect() raises (FS unmounted, perms broken),
# the entire monitor phase aborts and the pipeline loses ALL modules —
# cpu, memory, services, everything after the failure.

def test_run_all_continues_when_module_raises(monkeypatch):
    """A module that throws must not stop the entire monitor phase.

    Strategy: monkeypatch cpu.collect() to raise. The remaining modules
    (memory, disk, etc.) must still appear in the snapshot.
    """
    from ipracticom_sweeper.monitor import checks
    from ipracticom_sweeper.monitor import cpu as cpu_mod

    def boom(*args, **kwargs):
        raise RuntimeError("simulated disk I/O failure")
    monkeypatch.setattr(cpu_mod, "collect", boom)

    snap = checks.run_all({})  # pass empty rules; we only test failure mode
    # The snapshot must still contain other modules
    assert "memory" in snap.get("modules", {}), (
        f"cpu.collect() raised but memory module is missing from snapshot. "
        f"Modules present: {list(snap.get('modules', {}).keys())}"
    )
    # The failed module should be flagged in the snapshot
    assert snap.get("modules", {}).get("cpu", {}).get("error"), (
        "failed module should record its error in the snapshot"
    )


def test_run_all_returns_partial_when_late_module_raises(monkeypatch):
    """Failure mid-pipeline must not lose prior modules' data."""
    from ipracticom_sweeper.monitor import checks
    from ipracticom_sweeper.monitor import disk as disk_mod

    def boom(*args, **kwargs):
        raise RuntimeError("disk died")
    monkeypatch.setattr(disk_mod, "collect", boom)

    snap = checks.run_all({})
    modules = snap.get("modules", {})
    # cpu and memory (before disk) must still be present
    assert "cpu" in modules, "cpu module lost after disk failure"
    assert "memory" in modules, "memory module lost after disk failure"
    # disk should be flagged with error
    assert modules.get("disk", {}).get("error"), "failed module should record its error"
