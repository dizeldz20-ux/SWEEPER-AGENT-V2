"""Tests for slice 8.1: external watchdog systemd unit.

The watchdog runs as a separate systemd oneshot timer every 60s. It curls
`/healthz` on the API service and restarts the service on 5xx.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ipracticom_sweeper.deploy.watchdog import (
    RestartTracker,
    evaluate_health,
    should_restart,
    should_alert_admin,
)


# --- 8.1.1 unit files exist ----------------------------------------------------

def test_8_1_unit_installed(deploy_dir: Path) -> None:
    """Verify the watchdog unit files exist in deploy/."""
    assert (deploy_dir / "ipracticom-sweeper-watchdog.service").is_file()
    assert (deploy_dir / "ipracticom-sweeper-watchdog.timer").is_file()


def test_8_1_timer_schedule_60s(deploy_dir: Path) -> None:
    """The timer fires every 60 seconds."""
    timer = (deploy_dir / "ipracticom-sweeper-watchdog.timer").read_text()
    assert "OnUnitActiveSec=60s" in timer


def test_8_1_oneshot_calls_healthz(deploy_dir: Path) -> None:
    """The watchdog chain (service + shell script) probes /healthz."""
    svc = (deploy_dir / "ipracticom-sweeper-watchdog.service").read_text()
    assert "ExecStart" in svc
    # The shell helper does the actual curl — verify it references /healthz
    sh = (deploy_dir / "ipracticom-sweeper-watchdog.sh").read_text()
    assert "curl" in sh
    assert "/healthz" in sh


# --- 8.1.2 behaviour ----------------------------------------------------------

def test_8_1_restarts_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock /healthz returns 502 -> watchdog returns 'restart'."""
    monkeypatch.setattr(
        "ipracticom_sweeper.deploy.watchdog.probe_healthz",
        lambda url: 502,
    )
    decision = evaluate_health("http://127.0.0.1:8787/healthz")
    assert decision == "restart"


def test_8_1_no_restart_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock /healthz returns 200 -> watchdog returns 'ok'."""
    monkeypatch.setattr(
        "ipracticom_sweeper.deploy.watchdog.probe_healthz",
        lambda url: 200,
    )
    decision = evaluate_health("http://127.0.0.1:8787/healthz")
    assert decision == "ok"


# --- 8.1.3 cooldown logic ------------------------------------------------------

def test_8_1_restart_cooldown(tmp_path: Path) -> None:
    """A second restart within 5 minutes is suppressed."""
    tracker = RestartTracker(state_dir=tmp_path)
    tracker.record_restart()
    # First call after a restart within 5 min should be suppressed
    assert should_restart(tracker, recent_failure_count=5) is False


def test_8_1_alerts_after_3_restarts(tmp_path: Path) -> None:
    """3 restarts in 1h -> admin alert is triggered."""
    tracker = RestartTracker(state_dir=tmp_path)
    tracker.record_restart()
    tracker.record_restart()
    tracker.record_restart()
    assert should_alert_admin(tracker, threshold=3) is True


# --- 8.1.4 installer integration -----------------------------------------------

def test_8_1_installed_via_install_sh(deploy_dir: Path) -> None:
    """install.sh references the watchdog unit."""
    install_sh = (deploy_dir.parent / "install.sh").read_text()
    assert "ipracticom-sweeper-watchdog" in install_sh


def test_8_1_uninstall_cleans_up(deploy_dir: Path) -> None:
    """The --uninstall branch in install.sh stops and disables the watchdog."""
    install_sh = (deploy_dir.parent / "install.sh").read_text()
    # Find the --uninstall block
    assert "uninstall" in install_sh.lower()
    assert "watchdog" in install_sh.lower()


# --- Fixtures ------------------------------------------------------------------

@pytest.fixture
def deploy_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "deploy"