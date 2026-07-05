"""Per-machine manual scan: scoping + diagnose-only, at the pipeline level.

The console's "run scan" calls ``POST /api/run`` with ``host`` + ``repair=0`` so
the run is (a) scoped to the checks ENABLED for that host and (b) diagnose-only
(no auto-repair). This exercises the ``only_modules`` / ``auto_repair`` contract
of ``run_pipeline`` directly; the monitor/adapt/diagnose layers are stubbed so
the test is platform-neutral and runs native on Windows.

See also ``test_host_config.py`` for ``enabled_monitor_modules`` (catalog-name →
canonical-stem mapping + the None-vs-empty safety).
"""
from __future__ import annotations

import pytest

import ipracticom_sweeper.pipeline as pipeline
from ipracticom_sweeper.config.paths import ROOT
from ipracticom_sweeper.diagnose.engine import Diagnosis


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    """Point state-dir writes (heartbeat/audit) at a tmp dir."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    ROOT.cache_clear()
    yield tmp_path
    ROOT.cache_clear()


def _stub_monitor(monkeypatch, findings):
    """Stub the read-only monitor + adapt layers with fixed findings."""
    monkeypatch.setattr(
        pipeline, "run_monitor",
        lambda rules: {"modules": {}, "overall_status": "ok", "self": {}},
    )
    monkeypatch.setattr(pipeline, "adapt_for_diagnose", lambda snap: dict(findings))


def test_only_modules_filters_findings_before_diagnose(monkeypatch):
    """Scoped run: diagnose sees ONLY the enabled module's findings."""
    _stub_monitor(monkeypatch, {"cpu": {"v": 1}, "disk": {"v": 2}})
    seen: dict = {}

    def fake_diagnose(findings, rules):
        seen["keys"] = set(findings)
        return Diagnosis(defcon=5, defcon_label="green", summary="ok")

    monkeypatch.setattr(pipeline, "diagnose", fake_diagnose)
    pipeline.run_pipeline({}, auto_repair=False, only_modules={"cpu"})
    assert seen["keys"] == {"cpu"}


def test_only_modules_none_diagnoses_everything(monkeypatch):
    """Back-compat: no scoping → every module reaches diagnose (agent-wide)."""
    _stub_monitor(monkeypatch, {"cpu": {"v": 1}, "disk": {"v": 2}})
    seen: dict = {}

    def fake_diagnose(findings, rules):
        seen["keys"] = set(findings)
        return Diagnosis(defcon=5, defcon_label="green", summary="ok")

    monkeypatch.setattr(pipeline, "diagnose", fake_diagnose)
    pipeline.run_pipeline({}, auto_repair=False, only_modules=None)
    assert seen["keys"] == {"cpu", "disk"}


def test_diagnose_only_never_executes_repairs(monkeypatch):
    """auto_repair=False: even with a safe repair on the table, nothing runs."""
    _stub_monitor(monkeypatch, {"cpu": {"v": 1}})
    monkeypatch.setattr(
        pipeline, "diagnose",
        lambda findings, rules: Diagnosis(
            defcon=3, defcon_label="orange", summary="x",
            safe_repairs=["drop_caches"],
        ),
    )

    def boom(*a, **k):
        raise AssertionError("execute_repair must not run in a diagnose-only scan")

    monkeypatch.setattr(pipeline, "execute_repair", boom)
    result = pipeline.run_pipeline({}, auto_repair=False, only_modules={"cpu"})
    assert result.repairs_attempted == 0
