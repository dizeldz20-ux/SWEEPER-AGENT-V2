"""Sprint 15 — 2 new runbooks (audit_pressure, self_health_recovery) + policy tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ipracticom_sweeper.runbooks import (
    RunbookEngine,
    RunbookAction,
    audit_pressure_runbook,
    self_health_recovery_runbook,
    disk_cleanup_runbook,
    memory_pressure_runbook,
    zombie_processes_runbook,
)
from ipracticom_sweeper.repair import policy


# ============= audit_pressure_runbook =======================================

def test_audit_pressure_runbook_has_3_actions() -> None:
    actions = audit_pressure_runbook()
    assert len(actions) == 3


def test_audit_pressure_first_action_is_rotate_audit() -> None:
    actions = audit_pressure_runbook()
    assert actions[0].name == "rotate-audit"
    assert actions[0].action_type == "repair"
    assert actions[0].params["action"] == "rotate_audit_now"


def test_audit_pressure_second_action_is_vacuum() -> None:
    actions = audit_pressure_runbook()
    assert actions[1].name == "vacuum-journald"
    assert actions[1].params["max_age_days"] == 14


def test_audit_pressure_third_is_notify_telegram() -> None:
    actions = audit_pressure_runbook()
    assert actions[2].name == "notify-admin"
    assert actions[2].action_type == "notify"
    assert actions[2].params["channel"] == "telegram"


# ============= self_health_recovery_runbook =================================

def test_self_health_recovery_has_3_actions() -> None:
    actions = self_health_recovery_runbook()
    assert len(actions) == 3


def test_self_health_first_action_is_healthz() -> None:
    actions = self_health_recovery_runbook()
    assert actions[0].name == "healthz-ping"
    assert actions[0].params["action"] == "self_healthz_ping"


def test_self_health_second_is_revalidate_telegram() -> None:
    actions = self_health_recovery_runbook()
    assert actions[1].name == "revalidate-telegram"
    assert actions[1].params["action"] == "telegram_token_revalidate"


def test_self_health_third_notifies_all_channels() -> None:
    actions = self_health_recovery_runbook()
    assert actions[2].params["channel"] == "all"
    assert actions[2].params["defcon"] == 4


# ============= RunbookEngine integration with new runbooks =================

def test_engine_dry_run_runs_audit_pressure_actions() -> None:
    eng = RunbookEngine(dry_run=True)
    result = eng.execute("audit_pressure", audit_pressure_runbook())
    assert result.dry_run is True
    assert result.actions_executed == 3
    # dry_run: actions are not actually executed, so succeeded=0
    assert result.actions_succeeded == 0


def test_engine_dry_run_emits_dry_run_markers() -> None:
    eng = RunbookEngine(dry_run=True)
    result = eng.execute("audit_pressure", audit_pressure_runbook())
    assert all("[DRY-RUN]" in line for line in result.output)


def test_engine_real_run_uses_action_runner() -> None:
    """When dry_run=False, action_runner is invoked."""
    eng = RunbookEngine(dry_run=False)
    called = []

    def runner(action):
        called.append(action.name)
        return True

    result = eng.execute("audit_pressure", audit_pressure_runbook(),
                         action_runner=runner)
    assert result.dry_run is False
    assert called == ["rotate-audit", "vacuum-journald", "notify-admin"]


def test_engine_real_run_handles_runner_exception() -> None:
    eng = RunbookEngine(dry_run=False)

    def runner(action):
        raise RuntimeError("boom")

    result = eng.execute("audit_pressure", audit_pressure_runbook(),
                         action_runner=runner)
    # The action was attempted but failed; output should contain [ERROR]
    assert any("[ERROR]" in line for line in result.output)
    assert result.actions_succeeded == 0


# ============= Cooldown semantics (existing engine) =========================

def test_should_trigger_respects_defcon() -> None:
    eng = RunbookEngine(dry_run=True)
    # trigger_defcon=2, defcon=3 → no trigger
    assert not eng.should_trigger("rb", defcon=3, trigger_defcon=2, cooldown_seconds=60)
    # trigger_defcon=2, defcon=2 → trigger
    assert eng.should_trigger("rb", defcon=2, trigger_defcon=2, cooldown_seconds=60)


def test_should_trigger_respects_cooldown() -> None:
    eng = RunbookEngine(dry_run=True)
    # First trigger fires
    assert eng.should_trigger("rb", defcon=2, trigger_defcon=3, cooldown_seconds=60)
    eng.mark_fired("rb", now=1000.0)
    # 30s later → still in cooldown
    assert not eng.should_trigger("rb", defcon=2, trigger_defcon=3, cooldown_seconds=60, now=1030.0)
    # 90s later → cooldown over
    assert eng.should_trigger("rb", defcon=2, trigger_defcon=3, cooldown_seconds=60, now=1090.0)


def test_cooldown_is_per_runbook() -> None:
    eng = RunbookEngine(dry_run=True)
    eng.mark_fired("rb1", now=1000.0)
    # rb2 is independent
    assert eng.should_trigger("rb2", defcon=2, trigger_defcon=3, cooldown_seconds=60, now=1001.0)


# ============= Built-in runbooks unchanged =================================

def test_legacy_runbooks_still_defined() -> None:
    assert disk_cleanup_runbook() is not None
    assert memory_pressure_runbook() is not None
    assert zombie_processes_runbook() is not None


def test_audit_pressure_runbook_does_not_share_actions() -> None:
    """Each call to the runbook fn should return fresh action objects."""
    a1 = audit_pressure_runbook()
    a2 = audit_pressure_runbook()
    assert a1 is not a2  # different list instances


# ============= Policy engine =================================================

def test_policy_loads_auto_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text("default: auto\n")
    monkeypatch.setattr(policy, "POLICY_FILE", p)
    # auto default → does NOT need approval
    assert policy.needs_approval("any_action") is False


def test_policy_loads_needs_approval_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text("default: needs_approval\n")
    monkeypatch.setattr(policy, "POLICY_FILE", p)
    # needs_approval default → requires approval
    assert policy.needs_approval("any_action") is True


def test_policy_overrides_per_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text("default: needs_approval\nrepairs:\n  drop_caches: auto\n")
    monkeypatch.setattr(policy, "POLICY_FILE", p)
    # Override: drop_caches is auto
    assert policy.needs_approval("drop_caches") is False
    # Other actions follow default: needs_approval
    assert policy.needs_approval("service_restart") is True


def test_policy_handles_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "missing.yaml"
    monkeypatch.setattr(policy, "POLICY_FILE", p)
    # Missing file → safe default: needs_approval
    assert policy.needs_approval("any_action") is True


def test_policy_handles_comments_and_blanks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text("# a comment\n\ndefault: auto\n# another\n")
    monkeypatch.setattr(policy, "POLICY_FILE", p)
    assert policy.needs_approval("x") is False


# ============= Runbook metadata preservation ================================

def test_engine_preserves_action_metadata() -> None:
    """Each dry-run line mentions the action name and type."""
    eng = RunbookEngine(dry_run=True)
    result = eng.execute("audit_pressure", audit_pressure_runbook())
    rotate_line = next(l for l in result.output if "rotate-audit" in l)
    assert "repair" in rotate_line


def test_engine_returns_succeeded_count_in_dry_run() -> None:
    eng = RunbookEngine(dry_run=True)
    result = eng.execute("audit_pressure", audit_pressure_runbook())
    # dry_run means actions are not actually executed; succeeded stays 0
    # while executed reflects everything that would have run
    assert result.actions_executed == 3
    assert result.actions_succeeded == 0


def test_engine_records_result_metadata() -> None:
    eng = RunbookEngine(dry_run=True)
    result = eng.execute("self_health_recovery", self_health_recovery_runbook())
    assert result.runbook == "self_health_recovery"
    assert result.actions_executed == 3
    assert isinstance(result.output, list)
    assert all(isinstance(line, str) for line in result.output)


def test_audit_pressure_action_types_valid() -> None:
    """Each action's type must be in {shell, repair, notify}."""
    valid = {"shell", "repair", "notify"}
    for action in audit_pressure_runbook():
        assert action.action_type in valid


def test_self_health_action_types_valid() -> None:
    valid = {"shell", "repair", "notify"}
    for action in self_health_recovery_runbook():
        assert action.action_type in valid


def test_runbook_actions_have_names() -> None:
    """Every RunbookAction must have a non-empty name."""
    for action in audit_pressure_runbook() + self_health_recovery_runbook():
        assert action.name
        assert isinstance(action.name, str)


def test_runbook_actions_have_params() -> None:
    """Every RunbookAction must have a params dict (can be empty)."""
    for action in audit_pressure_runbook() + self_health_recovery_runbook():
        assert isinstance(action.params, dict)


def test_runbook_dedupes_action_names() -> None:
    """Within one runbook, action names should be unique."""
    for runbook_fn in (audit_pressure_runbook, self_health_recovery_runbook):
        names = [a.name for a in runbook_fn()]
        assert len(names) == len(set(names)), f"duplicate names in {runbook_fn.__name__}"


def test_engine_handles_zero_actions() -> None:
    eng = RunbookEngine(dry_run=True)
    result = eng.execute("empty", [])
    assert result.actions_executed == 0
    assert result.actions_succeeded == 0
    assert result.output == []


def test_engine_calls_runner_once_per_action() -> None:
    """action_runner receives each action exactly once, in order."""
    eng = RunbookEngine(dry_run=False)
    received = []

    def runner(action):
        received.append(action.name)
        return True

    eng.execute("self_health_recovery", self_health_recovery_runbook(),
                action_runner=runner)
    assert received == ["healthz-ping", "revalidate-telegram", "notify-admin"]


def test_runbook_action_count_consistent() -> None:
    """New runbooks should have 3 actions each (matches the pattern)."""
    for runbook_fn in (audit_pressure_runbook, self_health_recovery_runbook):
        actions = runbook_fn()
        assert len(actions) == 3, f"{runbook_fn.__name__} should have 3 actions"


def test_engine_runbook_name_in_result() -> None:
    eng = RunbookEngine(dry_run=True)
    result = eng.execute("audit_pressure", audit_pressure_runbook())
    assert result.runbook == "audit_pressure"
    assert result.triggered is True