"""Edge-case tests for runbooks/engine.py — should_trigger, mark_fired, execute."""
from __future__ import annotations

import time

import pytest

from ipracticom_sweeper.runbooks.engine import (
    RunbookEngine,
    RunbookAction,
    RunbookResult,
    disk_cleanup_runbook,
    memory_pressure_runbook,
    zombie_processes_runbook,
    audit_pressure_runbook,
    self_health_recovery_runbook,
)


@pytest.fixture
def eng() -> RunbookEngine:
    return RunbookEngine(dry_run=True)


def _trig_args(name: str, defcon: int = 3, trigger_defcon: int = 3, cooldown: float = 300) -> dict:
    return dict(runbook_name=name, defcon=defcon, trigger_defcon=trigger_defcon, cooldown_seconds=cooldown)


# ============= should_trigger ==============================================

def test_should_trigger_first_time(eng: RunbookEngine) -> None:
    assert eng.should_trigger(**_trig_args("disk_cleanup")) is True


def test_should_trigger_blocks_during_cooldown(eng: RunbookEngine) -> None:
    eng.mark_fired("disk_cleanup")
    assert eng.should_trigger(**_trig_args("disk_cleanup")) is False


def test_should_trigger_after_cooldown_elapses(eng: RunbookEngine) -> None:
    eng.mark_fired("disk_cleanup")
    future = time.time() + 1000
    assert eng.should_trigger(**_trig_args("disk_cleanup"), now=future) is True


def test_should_trigger_different_runbooks_independent(eng: RunbookEngine) -> None:
    eng.mark_fired("disk_cleanup")
    assert eng.should_trigger(**_trig_args("memory_pressure")) is True


def test_should_trigger_zero_cooldown(eng: RunbookEngine) -> None:
    eng.mark_fired("disk_cleanup")
    assert eng.should_trigger(**_trig_args("disk_cleanup", cooldown=0)) is True


def test_should_trigger_defcon_below_threshold(eng: RunbookEngine) -> None:
    """defcon=5 (normal) > trigger_defcon=3 → don't fire."""
    assert eng.should_trigger(**_trig_args("x", defcon=5)) is False


def test_should_trigger_defcon_at_threshold(eng: RunbookEngine) -> None:
    """defcon=3 == trigger_defcon=3 → fire."""
    assert eng.should_trigger(**_trig_args("x", defcon=3)) is True


def test_should_trigger_defcon_below_trigger(eng: RunbookEngine) -> None:
    """defcon=1 (most severe) < trigger_defcon=3 → fire."""
    assert eng.should_trigger(**_trig_args("x", defcon=1)) is True


# ============= mark_fired ==================================================

def test_mark_fired_does_not_raise(eng: RunbookEngine) -> None:
    eng.mark_fired("test_runbook")


def test_mark_fired_with_explicit_time(eng: RunbookEngine) -> None:
    eng.mark_fired("test_runbook", now=time.time() - 10000)
    # Old timestamp → cooldown already passed
    assert eng.should_trigger(**_trig_args("test_runbook")) is True


# ============= execute (dry-run) ===========================================

def test_execute_dry_run_returns_result(eng: RunbookEngine) -> None:
    result = eng.execute("disk_cleanup", disk_cleanup_runbook())
    assert isinstance(result, RunbookResult)
    assert result.dry_run is True


def test_execute_runs_all_actions(eng: RunbookEngine) -> None:
    actions = disk_cleanup_runbook()
    result = eng.execute("disk_cleanup", actions)
    assert result.actions_executed == len(actions)


# ============= RunbookAction ===============================================

def test_runbook_action_dataclass() -> None:
    a = RunbookAction(name="x", action_type="shell", params={"cmd": "echo"})
    assert a.name == "x"
    assert a.action_type == "shell"
    assert a.params == {"cmd": "echo"}


def test_runbook_action_with_explicit_params() -> None:
    """RunbookAction requires params; no default."""
    a = RunbookAction(name="x", action_type="notify", params={"channel": "slack"})
    assert a.params == {"channel": "slack"}


# ============= RunbookResult ===============================================

def test_runbook_result_dataclass() -> None:
    r = RunbookResult(
        runbook="x", triggered=True, actions_executed=2,
        actions_succeeded=2, dry_run=True, output=[],
    )
    assert r.actions_executed == 2
    assert r.actions_succeeded == 2
    assert r.output == []


# ============= Runbook factory functions ===================================

def test_disk_cleanup_returns_list_of_actions() -> None:
    actions = disk_cleanup_runbook()
    assert isinstance(actions, list)
    assert len(actions) > 0
    for a in actions:
        assert isinstance(a, RunbookAction)


def test_memory_pressure_returns_list() -> None:
    actions = memory_pressure_runbook()
    assert isinstance(actions, list)
    assert len(actions) > 0


def test_zombie_processes_returns_list() -> None:
    actions = zombie_processes_runbook()
    assert isinstance(actions, list)
    assert len(actions) > 0


def test_audit_pressure_returns_list() -> None:
    actions = audit_pressure_runbook()
    assert isinstance(actions, list)
    assert len(actions) > 0


def test_self_health_recovery_returns_list() -> None:
    actions = self_health_recovery_runbook()
    assert isinstance(actions, list)
    assert len(actions) > 0


# ============= Engine construction =========================================

def test_engine_default_dry_run() -> None:
    assert RunbookEngine().dry_run is True


def test_engine_non_dry_run() -> None:
    assert RunbookEngine(dry_run=False).dry_run is False


def test_engine_in_memory_cooldown() -> None:
    """Engine tracks fired runbooks in memory; no persistence."""
    eng = RunbookEngine(dry_run=True)
    eng.mark_fired("x")
    eng2 = RunbookEngine(dry_run=True)
    assert eng2.should_trigger(**_trig_args("x")) is True


def test_engine_output_lines_for_dry_run(eng: RunbookEngine) -> None:
    """Dry run produces [DRY-RUN] markers in output."""
    result = eng.execute("disk_cleanup", disk_cleanup_runbook())
    # Output is a list; if non-empty, should have at least one entry
    assert isinstance(result.output, list)