"""Tests for the end-to-end pipeline."""

from unittest.mock import patch

import pytest

from ipracticom_sweeper.pipeline import _extract_repair_kwargs, run_pipeline


@pytest.fixture
def default_rules():
    return {
        "cpu": {
            "load_avg_5min_warn": 2.0,
            "load_avg_5min_crit": 5.0,
            "iowait_percent_warn": 20.0,
        },
        "memory": {
            "used_percent_warn": 80.0,
            "used_percent_crit": 95.0,
            "swap_used_percent_warn": 50.0,
        },
        "disk": {
            "used_percent_warn": 80.0,
            "used_percent_crit": 95.0,
            "read_only_mounts": [],
        },
        "services": {"critical_list": []},
        "security": {
            "failed_ssh_per_min_warn": 5,
            "sudo_failures_per_hour_warn": 3,
        },
    }


@pytest.fixture
def green_snapshot():
    """A snapshot that should produce DEFCON 5 (all good)."""
    return {
        "modules": {
            "cpu": {"values": {"load_5min": 0.5, "iowait_percent": 1.0}, "status": "ok"},
            "memory": {"values": {"ram_used_percent": 30.0, "swap_used_percent": 0.0}, "status": "ok"},
            "disk": {"values": {"mounts": [{"mount": "/", "used_percent": 50.0, "read_only": False}]}, "status": "ok"},
            "services": {"values": {"failed_units": [], "failed_count": 0}, "status": "ok"},
            "security": {"values": {"failed_ssh_per_minute": 0.0, "sudo_failures": 0}, "status": "ok"},
        },
        "overall_status": "ok",
    }


@pytest.fixture
def warn_memory_snapshot():
    """A snapshot where memory hits the warn threshold — triggers drop_caches (GUARDED)."""
    return {
        "modules": {
            "cpu": {"values": {"load_5min": 0.5, "iowait_percent": 1.0}, "status": "ok"},
            "memory": {"values": {"ram_used_percent": 85.0, "swap_used_percent": 0.0}, "status": "warn"},
            "disk": {"values": {"mounts": [{"mount": "/", "used_percent": 50.0, "read_only": False}]}, "status": "ok"},
            "services": {"values": {"failed_units": [], "failed_count": 0}, "status": "ok"},
            "security": {"values": {"failed_ssh_per_minute": 0.0, "sudo_failures": 0}, "status": "ok"},
        },
        "overall_status": "warn",
    }


# --- Dry-run pipeline (no actual repair) ------------------------------------


def test_pipeline_dry_run_no_repairs(default_rules, warn_memory_snapshot):
    """In dry-run mode, we should see suggested repairs but no execution."""
    with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=warn_memory_snapshot):
        result = run_pipeline(default_rules, auto_repair=True, dry_run=True)

    assert result.monitor_overall == "warn"
    assert result.defcon == 4
    assert result.problems_found >= 1
    assert result.repairs_attempted == 0  # dry-run skips
    assert len(result.repair_results) >= 1
    assert all(r.get("dry_run") for r in result.repair_results)


def test_pipeline_auto_repair_off(default_rules, warn_memory_snapshot):
    """With auto_repair=False, no repairs attempted even if suggested."""
    with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=warn_memory_snapshot):
        result = run_pipeline(default_rules, auto_repair=False, dry_run=False)

    assert result.defcon == 4
    assert result.repairs_attempted == 0


# --- Auto-repair path --------------------------------------------------------


def test_pipeline_auto_repair_executes_drop_caches(default_rules, warn_memory_snapshot):
    """v0.4.6 (Daniel #4): With default=needs_approval, drop_caches creates a
    pending proposal instead of executing. The operator must approve via /approvals.
    """
    from ipracticom_sweeper.repair import pending as pending_mod
    pending_dir = default_rules  # dummy to keep signature; not used
    # Use a temp pending dir to capture the proposal.
    import tempfile, json
    from pathlib import Path as P
    with tempfile.TemporaryDirectory() as tmp:
        from ipracticom_sweeper.repair import pending as pm
        original_pending = pm.PENDING_DIR
        original_approved = pm.APPROVED_DIR
        original_rejected = pm.REJECTED_DIR
        original_audit = pm.AUDIT_LOG
        pm.PENDING_DIR = P(tmp) / "pending"
        pm.APPROVED_DIR = P(tmp) / "approved"
        pm.REJECTED_DIR = P(tmp) / "rejected"
        pm.AUDIT_LOG = P(tmp) / "audit" / "repairs.jsonl"
        pm.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=warn_memory_snapshot):
                result = run_pipeline(default_rules, auto_repair=True, dry_run=False)

            # No auto-execution — the repair is queued for approval.
            assert result.repairs_attempted == 0
            assert result.repairs_succeeded == 0
            # A proposal was created on disk.
            proposals = list(pm.PENDING_DIR.glob("*.json"))
            assert len(proposals) == 1
            proposal = json.loads(proposals[0].read_text())
            assert proposal["action"] == "drop_caches"
            assert proposal["status"] == "pending"
            assert "reason" in proposal and proposal["reason"]
            assert "proposed_command" in proposal
            # Pipeline result marks the repair as awaiting_approval.
            assert any(r.get("status") == "awaiting_approval" for r in result.repair_results)
        finally:
            pm.PENDING_DIR = original_pending
            pm.APPROVED_DIR = original_approved
            pm.REJECTED_DIR = original_rejected
            pm.AUDIT_LOG = original_audit


def test_pipeline_repair_failure_counted(default_rules, warn_memory_snapshot):
    """v0.4.6 (Daniel #4): With default=needs_approval, failures are no longer
    applicable for auto-execution. Instead verify the proposal contains the
    problem details so the operator can decide.
    """
    import tempfile, json
    from pathlib import Path as P
    from ipracticom_sweeper.repair import pending as pm
    with tempfile.TemporaryDirectory() as tmp:
        original_pending = pm.PENDING_DIR
        original_approved = pm.APPROVED_DIR
        original_rejected = pm.REJECTED_DIR
        original_audit = pm.AUDIT_LOG
        pm.PENDING_DIR = P(tmp) / "pending"
        pm.APPROVED_DIR = P(tmp) / "approved"
        pm.REJECTED_DIR = P(tmp) / "rejected"
        pm.AUDIT_LOG = P(tmp) / "audit" / "repairs.jsonl"
        pm.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=warn_memory_snapshot):
                result = run_pipeline(default_rules, auto_repair=True, dry_run=False)

            # No execution attempted → no failed counter.
            assert result.repairs_attempted == 0
            # But the proposal exists with full problem context for operator review.
            proposals = list(pm.PENDING_DIR.glob("*.json"))
            assert len(proposals) == 1
            proposal = json.loads(proposals[0].read_text())
            assert proposal["problem"]["kind"] == "memory_warn"
            assert proposal["problem"]["severity"] == "warn"
            assert "metrics" in proposal["problem"]
        finally:
            pm.PENDING_DIR = original_pending
            pm.APPROVED_DIR = original_approved
            pm.REJECTED_DIR = original_rejected
            pm.AUDIT_LOG = original_audit


def test_pipeline_repair_exception_caught(default_rules, warn_memory_snapshot):
    """v0.4.6 (Daniel #4): With default=needs_approval, drop_caches never calls
    execute_repair at all, so it can't raise. The proposal is created cleanly.
    """
    import tempfile, json
    from pathlib import Path as P
    from ipracticom_sweeper.repair import pending as pm
    with tempfile.TemporaryDirectory() as tmp:
        original_pending = pm.PENDING_DIR
        original_approved = pm.APPROVED_DIR
        original_rejected = pm.REJECTED_DIR
        original_audit = pm.AUDIT_LOG
        pm.PENDING_DIR = P(tmp) / "pending"
        pm.APPROVED_DIR = P(tmp) / "approved"
        pm.REJECTED_DIR = P(tmp) / "rejected"
        pm.AUDIT_LOG = P(tmp) / "audit" / "repairs.jsonl"
        pm.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=warn_memory_snapshot):
                with patch("ipracticom_sweeper.pipeline.execute_repair", side_effect=RuntimeError("boom")):
                    # execute_repair should NOT be called when policy=needs_approval.
                    # If it raises here, the test would fail — which is what we want,
                    # because the whole point of the gate is that this code path is
                    # unreachable for drop_caches now.
                    result = run_pipeline(default_rules, auto_repair=True, dry_run=False)

            # No execution attempted → no failures.
            assert result.repairs_attempted == 0
            proposals = list(pm.PENDING_DIR.glob("*.json"))
            assert len(proposals) == 1
        finally:
            pm.PENDING_DIR = original_pending
            pm.APPROVED_DIR = original_approved
            pm.REJECTED_DIR = original_rejected
            pm.AUDIT_LOG = original_audit


# --- Multi-channel push: server, dedup, blocked ------------------------------


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Pin pending + block dirs to a private tmp dir so these tests are immune
    to state leaked by other tests in the full suite."""
    from ipracticom_sweeper.repair import block
    from ipracticom_sweeper.repair import pending as pm
    monkeypatch.setattr(pm, "PENDING_DIR", tmp_path / "pending")
    monkeypatch.setattr(pm, "APPROVED_DIR", tmp_path / "pending" / "approved")
    monkeypatch.setattr(pm, "REJECTED_DIR", tmp_path / "pending" / "rejected")
    monkeypatch.setattr(pm, "AUDIT_LOG", tmp_path / "audit" / "repairs.jsonl")
    monkeypatch.setattr(block, "BLOCKED_DIR", tmp_path / "pending" / "blocked")


def test_pipeline_proposal_carries_server_and_pushes(isolated_state, default_rules, warn_memory_snapshot, monkeypatch):
    """A needs-approval repair stamps the host on the proposal and fires an
    approval-request push exactly once."""
    from ipracticom_sweeper.repair import pending as pm

    calls = []

    async def fake_push(proposal):
        calls.append(proposal)
        return {"telegram": True}

    monkeypatch.setattr("ipracticom_sweeper.notify.approvals.notify_approval_request", fake_push)
    monkeypatch.setattr("ipracticom_sweeper.pipeline.get_server_id", lambda: "test-host")

    with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=warn_memory_snapshot):
        run_pipeline(default_rules, auto_repair=True, dry_run=False)

    proposals = pm.list_pending()
    assert len(proposals) == 1
    assert proposals[0].action == "drop_caches"
    assert proposals[0].server == "test-host"
    assert len(calls) == 1
    assert calls[0]["action"] == "drop_caches"
    assert calls[0]["server"] == "test-host"


def test_pipeline_dedup_skips_existing_pending(isolated_state, default_rules, warn_memory_snapshot, monkeypatch):
    """A problem already awaiting a decision does not create a second proposal
    or re-push."""
    from ipracticom_sweeper.repair import pending as pm

    calls = []

    async def fake_push(proposal):
        calls.append(proposal)
        return {}

    monkeypatch.setattr("ipracticom_sweeper.notify.approvals.notify_approval_request", fake_push)
    monkeypatch.setattr("ipracticom_sweeper.pipeline.get_server_id", lambda: "test-host")

    pm.create_proposal(
        action="drop_caches", kwargs={"level": 3}, reason="prev",
        proposed_command="c", server="test-host",
    )

    with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=warn_memory_snapshot):
        run_pipeline(default_rules, auto_repair=True, dry_run=False)

    assert len(pm.list_pending()) == 1     # no duplicate
    assert calls == []                     # no re-push


def test_pipeline_blocked_skips_proposal(isolated_state, default_rules, warn_memory_snapshot, monkeypatch):
    """A blocked (action, server) — a prior approved repair that failed — does
    not get re-proposed until a human unblocks it."""
    from ipracticom_sweeper.repair import pending as pm
    from ipracticom_sweeper.repair import block

    calls = []

    async def fake_push(proposal):
        calls.append(proposal)
        return {}

    monkeypatch.setattr("ipracticom_sweeper.notify.approvals.notify_approval_request", fake_push)
    monkeypatch.setattr("ipracticom_sweeper.pipeline.get_server_id", lambda: "test-host")

    block.block("drop_caches", "test-host", reason="prior failure")

    with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=warn_memory_snapshot):
        run_pipeline(default_rules, auto_repair=True, dry_run=False)

    assert len(pm.list_pending()) == 0     # blocked → nothing proposed
    assert calls == []                     # no push


# --- Green path --------------------------------------------------------------


def test_pipeline_all_green(default_rules, green_snapshot):
    """When everything is healthy, DEFCON 5 and no repairs."""
    with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=green_snapshot):
        result = run_pipeline(default_rules, auto_repair=True, dry_run=False)

    assert result.defcon == 5
    assert result.defcon_label == "green"
    assert result.problems_found == 0
    assert result.repairs_attempted == 0


# --- Monitor failure ---------------------------------------------------------


def test_pipeline_monitor_failure_returns_defcon_1(default_rules):
    """If monitor itself crashes, we return DEFCON 1 with errors."""
    with patch("ipracticom_sweeper.pipeline.run_monitor", side_effect=RuntimeError("monitor down")):
        result = run_pipeline(default_rules)

    assert result.monitor_overall == "error"
    assert result.defcon == 1
    assert result.defcon_label == "black"
    assert len(result.errors) >= 1


# --- Needs-human path --------------------------------------------------------


def test_pipeline_security_triggers_needs_human(default_rules):
    """SSH brute force = defcon 2, never auto-repair, needs_human > 0."""
    snapshot = {
        "modules": {
            "cpu": {"values": {"load_5min": 0.5, "iowait_percent": 1.0}, "status": "ok"},
            "memory": {"values": {"ram_used_percent": 30.0, "swap_used_percent": 0.0}, "status": "ok"},
            "disk": {"values": {"mounts": [{"mount": "/", "used_percent": 50.0, "read_only": False}]}, "status": "ok"},
            "services": {"values": {"failed_units": [], "failed_count": 0}, "status": "ok"},
            "security": {"values": {"failed_ssh_per_minute": 50.0, "sudo_failures": 0}, "status": "crit"},
        },
        "overall_status": "crit",
    }
    with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=snapshot):
        result = run_pipeline(default_rules, auto_repair=True, dry_run=False)

    assert result.defcon == 2
    assert result.needs_human >= 1
    assert result.repairs_attempted == 0  # security never auto-repair


# --- to_dict -----------------------------------------------------------------


def test_pipeline_result_to_dict(default_rules, green_snapshot):
    with patch("ipracticom_sweeper.pipeline.run_monitor", return_value=green_snapshot):
        result = run_pipeline(default_rules)

    d = result.to_dict()
    assert isinstance(d, dict)
    assert "started_at" in d
    assert "duration_ms" in d
    assert "defcon" in d
    assert "diagnosis" in d
    assert isinstance(d["repair_results"], list)


# --- _extract_repair_kwargs --------------------------------------------------


def test_extract_repair_kwargs_drop_caches():
    kwargs = _extract_repair_kwargs("drop_caches", None)
    assert kwargs == {"level": 3}


def test_extract_repair_kwargs_log_truncate():
    kwargs = _extract_repair_kwargs("log_truncate_journald", None)
    assert kwargs == {"max_age_days": 7}


def test_extract_repair_kwargs_service_restart_with_problem():
    from ipracticom_sweeper.diagnose.engine import Problem, RepairSafety
    problem = Problem(
        module="services",
        kind="service_failed",
        severity="crit",
        detail="nginx failed",
        metrics={"unit": "nginx"},
        suggested_repair="service_restart",
        repair_safety=RepairSafety.GUARDED,
        defcon_at_least=2,
    )
    kwargs = _extract_repair_kwargs("service_restart", problem)
    assert kwargs == {"unit": "nginx"}


def test_extract_repair_kwargs_notify_human():
    from ipracticom_sweeper.diagnose.engine import Problem, RepairSafety
    problem = Problem(
        module="memory",
        kind="memory_critical",
        severity="crit",
        detail="Memory at 96%",
        metrics={"used_percent": 96.0},
        suggested_repair="notify_human",
        repair_safety=RepairSafety.DANGEROUS,
        defcon_at_least=3,
    )
    kwargs = _extract_repair_kwargs("notify_human", problem)
    assert kwargs["channel"] == "all"
    assert kwargs["defcon"] == 3
    assert "Memory" in kwargs["summary"]