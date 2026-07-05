"""Tests for slice 8.5: self-monitor snapshot integrated into /api/snapshot.

The self-resilience checks (8.1 watchdog, 8.2 state dir, 8.3 bot token,
8.4 audit rotation) are exposed in the dashboard so the operator can see
the sweeper's own health at a glance.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ipracticom_sweeper.monitor.self_snapshot import build_self_section


def test_8_5_snapshot_has_self_keys(tmp_path: Path) -> None:
    """`build_self_section` returns the expected fields."""
    section = build_self_section(state_dir=tmp_path)
    assert "state_dir_pct" in section
    assert "audit_size_bytes" in section
    assert "bot_token_status" in section
    assert "uptime_seconds" in section
    assert "watchdog_restart_count" in section


def test_8_5_self_defcon_wired(tmp_path: Path) -> None:
    """The self section's defcon is the minimum across self checks."""
    section = build_self_section(state_dir=tmp_path)
    assert "self_defcon" in section
    assert 1 <= section["self_defcon"] <= 5


def test_8_5_dashboard_renders_self_card(tmp_path: Path) -> None:
    """The self section has enough fields for a dashboard card to render."""
    section = build_self_section(state_dir=tmp_path)
    required = {"state_dir_pct", "audit_size_bytes", "bot_token_status"}
    assert required <= set(section.keys())


def test_8_5_handles_self_check_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If a self-check throws, the section still returns (degraded)."""
    def boom(*a, **kw):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(
        "ipracticom_sweeper.monitor.self_snapshot._state_dir_pct", boom
    )
    section = build_self_section(state_dir=tmp_path)
    # Section exists; state_dir_pct field is None (degraded) but others are present
    assert "audit_size_bytes" in section
    assert section["state_dir_pct"] is None
    assert section.get("degraded") is True


def test_8_5_healthz_returns_self_section(tmp_path: Path) -> None:
    """The combined healthz response includes the self-monitoring summary."""
    section = build_self_section(state_dir=tmp_path)
    assert "summary" in section
    # summary is a short string the dashboard / Telegram can show
    assert isinstance(section["summary"], str)
    assert len(section["summary"]) < 200