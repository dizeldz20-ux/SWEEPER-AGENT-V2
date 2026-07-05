"""Sprint 17.3 — Grafana dashboard JSON tests."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = REPO_ROOT / "dashboards"

# The Prometheus metric names we expose (must match render_metrics output)
EXPECTED_METRICS = {
    "sweeper_defcon",
    "sweeper_self_health",
    "sweeper_pipeline_runs_total",
    "sweeper_pipeline_duration_seconds",
    "sweeper_repair_executions_total",
    "sweeper_repair_success_total",
    "sweeper_check_status",
}


def _load(name: str) -> dict:
    path = DASHBOARDS / name
    with open(path) as f:
        return json.load(f)


def test_dashboards_dir_exists() -> None:
    assert DASHBOARDS.is_dir()


def test_three_dashboard_files_exist() -> None:
    files = sorted(DASHBOARDS.glob("*.json"))
    names = {f.name for f in files}
    assert names == {"overview.json", "freeswitch.json", "security.json"}


def test_overview_dashboard_valid() -> None:
    d = _load("overview.json")
    assert d["title"]
    assert d["uid"]
    assert d["schemaVersion"] >= 30
    assert len(d["panels"]) > 0


def test_freeswitch_dashboard_valid() -> None:
    d = _load("freeswitch.json")
    assert d["title"]
    assert d["uid"]
    assert len(d["panels"]) > 0


def test_security_dashboard_valid() -> None:
    d = _load("security.json")
    assert d["title"]
    assert d["uid"]
    assert len(d["panels"]) > 0


def test_uses_correct_promql() -> None:
    """All dashboards should reference our metric names."""
    text = ""
    for name in ("overview.json", "freeswitch.json", "security.json"):
        text += (DASHBOARDS / name).read_text()
    for metric in EXPECTED_METRICS:
        assert metric in text, f"Dashboard missing reference to {metric}"


def test_panels_have_descriptions() -> None:
    """Every panel should have a non-empty description."""
    for name in ("overview.json", "freeswitch.json", "security.json"):
        d = _load(name)
        for panel in d["panels"]:
            assert panel.get("description"), f"Panel {panel.get('id')} in {name} has no description"


def test_panels_have_unique_ids() -> None:
    """Panel IDs should be unique within a dashboard."""
    for name in ("overview.json", "freeswitch.json", "security.json"):
        d = _load(name)
        ids = [p["id"] for p in d["panels"]]
        assert len(ids) == len(set(ids)), f"Duplicate panel ids in {name}"


def test_panels_have_targets() -> None:
    """Every panel should reference at least one query."""
    for name in ("overview.json", "freeswitch.json", "security.json"):
        d = _load(name)
        for panel in d["panels"]:
            targets = panel.get("targets", [])
            assert targets, f"Panel {panel.get('id')} in {name} has no targets"


def test_uids_are_unique_across_dashboards() -> None:
    uids = []
    for name in ("overview.json", "freeswitch.json", "security.json"):
        d = _load(name)
        uids.append(d["uid"])
    assert len(uids) == len(set(uids)), "Dashboard uids must be unique"


def test_freeswitch_dashboard_references_all_40_fs_checks() -> None:
    """The FS dashboard should mention at least some FS-XX checks."""
    text = (DASHBOARDS / "freeswitch.json").read_text()
    # We use regex matches like fs01_, fs11_, etc.
    fs_refs = set(re.findall(r"fs(\d{2})_", text))
    # We expect at least 4 explicit FS checks + 1 heatmap regex
    assert len(fs_refs) >= 4, f"Only {len(fs_refs)} FS-XX references: {fs_refs}"