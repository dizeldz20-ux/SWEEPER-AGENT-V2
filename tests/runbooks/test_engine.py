"""Tests for RunbookEngine."""
import time
import pytest
from ipracticom_sweeper.runbooks import (
    RunbookEngine,
    RunbookAction,
    disk_cleanup_runbook,
    memory_pressure_runbook,
    zombie_processes_runbook,
)


def test_should_trigger_defcon_too_high():
    e = RunbookEngine()
    assert e.should_trigger("rb1", defcon=5, trigger_defcon=4, cooldown_seconds=60) is False


def test_should_trigger_first_time():
    e = RunbookEngine()
    assert e.should_trigger("rb1", defcon=4, trigger_defcon=4, cooldown_seconds=60) is True


def test_should_trigger_cooldown():
    e = RunbookEngine()
    e.mark_fired("rb1", now=1000.0)
    # 30s later — within cooldown
    assert e.should_trigger("rb1", defcon=4, trigger_defcon=4, cooldown_seconds=60, now=1030.0) is False
    # 120s later — past cooldown
    assert e.should_trigger("rb1", defcon=4, trigger_defcon=4, cooldown_seconds=60, now=1120.0) is True


def test_execute_dry_run():
    e = RunbookEngine(dry_run=True)
    actions = [RunbookAction("a1", "shell", {"command": "ls"})]
    result = e.execute("rb1", actions)
    assert result.dry_run is True
    assert result.actions_executed == 1
    assert result.actions_succeeded == 0  # dry-run doesn't count as success
    assert "DRY-RUN" in result.output[0]


def test_execute_real_with_runner():
    e = RunbookEngine(dry_run=False)
    actions = [
        RunbookAction("a1", "shell", {}),
        RunbookAction("a2", "shell", {}),
    ]
    runner = lambda a: True  # noqa: E731
    result = e.execute("rb1", actions, action_runner=runner)
    assert result.actions_succeeded == 2


def test_execute_real_with_failing_runner():
    e = RunbookEngine(dry_run=False)
    actions = [RunbookAction("a1", "shell", {})]
    runner = lambda a: False  # noqa: E731
    result = e.execute("rb1", actions, action_runner=runner)
    assert result.actions_succeeded == 0


def test_execute_handles_exception():
    e = RunbookEngine(dry_run=False)
    actions = [RunbookAction("a1", "shell", {})]
    def bad_runner(a):
        raise RuntimeError("boom")
    result = e.execute("rb1", actions, action_runner=bad_runner)
    assert result.actions_succeeded == 0
    assert "ERROR" in result.output[0]


def test_disk_cleanup_runbook_has_actions():
    actions = disk_cleanup_runbook()
    assert len(actions) >= 1
    assert any(a.name == "journalctl-vacuum" for a in actions)


def test_memory_pressure_runbook_has_actions():
    actions = memory_pressure_runbook()
    assert len(actions) == 1
    assert actions[0].name == "drop-caches"


def test_zombie_processes_runbook_has_actions():
    actions = zombie_processes_runbook()
    assert len(actions) == 2
    names = {a.name for a in actions}
    assert "list-zombies" in names
    assert "reap-zombies" in names


def test_zombie_processes_runbook_uses_sigchld():
    actions = zombie_processes_runbook()
    reap = next(a for a in actions if a.name == "reap-zombies")
    # The reap action must send SIGCHLD (kernel's standard reap signal)
    assert "CHLD" in reap.params["command"] or "SIGCHLD" in reap.params["command"]


def test_zombie_processes_runbook_executes_in_dry_run():
    e = RunbookEngine(dry_run=True)
    result = e.execute("zombies", zombie_processes_runbook())
    assert result.actions_executed == 2
    assert all("DRY-RUN" in line for line in result.output)
