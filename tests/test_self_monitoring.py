"""Tests for the agent self-monitoring feature set:

- Slack bot-token health probe (auth.test)
- Bot-connectivity aggregation + persistence (Telegram + Slack)
- Self-repair (no-approval auto-repair + alert only on failure/needs-human)
- Connector freeswitch_enabled flag + remote-collect script injection
- run_all self section + FreeSWITCH gating
- Catalog exclude_self filtering
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# --- Slack health probe ------------------------------------------------------

class TestSlackProbe:
    def test_no_token_is_disabled(self):
        from ipracticom_sweeper.notify.slack_health import probe_slack_token
        r = probe_slack_token("")
        assert r.status == "disabled"

    def test_ok_response(self):
        from ipracticom_sweeper.notify import slack_health
        with patch.object(slack_health, "_http_auth_test",
                          return_value=(200, {"ok": True, "user": "sweeper-bot", "team": "iPracticom"})):
            r = slack_health.probe_slack_token("xoxb-abc")
        assert r.status == "ok"
        assert r.bot_username == "sweeper-bot"
        assert r.team == "iPracticom"

    def test_invalid_auth_is_crit(self):
        from ipracticom_sweeper.notify import slack_health
        with patch.object(slack_health, "_http_auth_test",
                          return_value=(200, {"ok": False, "error": "invalid_auth"})):
            r = slack_health.probe_slack_token("xoxb-dead")
        assert r.status == "crit"
        assert r.error_code == "invalid_auth"

    def test_transient_error_is_warn(self):
        from ipracticom_sweeper.notify import slack_health
        with patch.object(slack_health, "_http_auth_test",
                          return_value=(200, {"ok": False, "error": "ratelimited"})):
            r = slack_health.probe_slack_token("xoxb-abc")
        assert r.status == "warn"

    def test_tracker_counts_consecutive_failures(self, tmp_path: Path):
        from ipracticom_sweeper.notify import slack_health
        tracker = slack_health.SlackTokenHealthTracker(state_dir=tmp_path)
        with patch.object(slack_health, "probe_slack_token",
                          return_value=slack_health.SlackBotHealthResult(
                              status="crit", error_code="invalid_auth", bot_username=None)):
            tracker.probe_if_configured("xoxb-dead")
            tracker.probe_if_configured("xoxb-dead")
        assert tracker.consecutive_failures == 2
        assert slack_health.should_alert_admin(tracker, threshold=2) is True


# --- Bot connectivity aggregation --------------------------------------------

class TestBotConnectivity:
    def test_empty_store_is_disabled(self, monkeypatch):
        from ipracticom_sweeper.monitor import bot_connectivity as bc
        monkeypatch.setattr("ipracticom_sweeper.notify.store.telegram_bots", lambda: [])
        monkeypatch.setattr("ipracticom_sweeper.notify.store.slack_bots", lambda: [])
        monkeypatch.setattr(bc, "_legacy_telegram_token", lambda: None)
        result = bc.check_all_bots()
        assert result["status"] == "disabled"
        assert result["configured"] == 0

    def test_worst_wins_across_platforms(self, monkeypatch):
        from ipracticom_sweeper.monitor import bot_connectivity as bc
        monkeypatch.setattr("ipracticom_sweeper.notify.store.telegram_bots",
                            lambda: [{"id": "t1", "name": "TG", "bot_token": "123:abc", "chat_id": "1"}])
        monkeypatch.setattr("ipracticom_sweeper.notify.store.slack_bots",
                            lambda: [{"id": "s1", "name": "SL", "bot_token": "xoxb-dead", "channel": "#ops"}])
        monkeypatch.setattr(bc, "_legacy_telegram_token", lambda: None)
        monkeypatch.setattr(bc, "_probe_telegram",
                            lambda t: {"status": "ok", "identity": "tgbot", "error": None, "error_code": None, "latency_ms": 5})
        monkeypatch.setattr(bc, "_probe_slack",
                            lambda t: {"status": "crit", "identity": None, "error": "invalid_auth", "error_code": "invalid_auth", "latency_ms": 5})
        result = bc.check_all_bots()
        assert result["status"] == "crit"  # worst wins
        assert result["defcon"] == 2
        assert len(result["bots"]) == 2

    def test_persistence_round_trip(self, tmp_path: Path):
        from ipracticom_sweeper.monitor import bot_connectivity as bc
        payload = {"status": "ok", "defcon": 5, "summary": "telegram: 1 ok", "bots": []}
        bc.save_result(payload, tmp_path)
        loaded = bc.load_result(tmp_path)
        assert loaded is not None
        assert loaded["status"] == "ok"
        assert "checked_at" in loaded

    def test_load_missing_is_none(self, tmp_path: Path):
        from ipracticom_sweeper.monitor import bot_connectivity as bc
        assert bc.load_result(tmp_path / "nope") is None


# --- Self-repair -------------------------------------------------------------

class TestSelfRepair:
    def test_healthy_does_nothing(self, tmp_path: Path):
        from ipracticom_sweeper.repair import self_repair
        section = {"state_dir_pct": 20.0, "self_defcon": 5,
                   "bots": {"status": "ok"}, "watchdog_restart_count": 0}
        out = self_repair.run_self_repairs(section, state_dir=tmp_path)
        assert out["repairs"] == []
        assert out["needs_human"] is False
        assert out["alert_sent"] is False

    def test_disk_pressure_auto_repairs_without_approval(self, tmp_path: Path):
        from ipracticom_sweeper.repair import self_repair
        calls = []
        with patch.object(self_repair, "_do",
                          side_effect=lambda a, k, d: calls.append(a) or {"action": a, "success": True}):
            out = self_repair.run_self_repairs(
                {"state_dir_pct": 97.0, "self_defcon": 2, "bots": {"status": "ok"}},
                state_dir=tmp_path,
            )
        # Space-freeing repairs ran, no approval involved.
        assert "log_truncate_journald" in calls
        assert "drop_caches" in calls
        assert out["needs_human"] is False  # all succeeded

    def test_stuck_agent_triggers_self_restart(self, tmp_path: Path):
        from ipracticom_sweeper.repair import self_repair
        calls = []
        with patch.object(self_repair, "_do",
                          side_effect=lambda a, k, d: calls.append(a) or {"action": a, "success": True}):
            self_repair.run_self_repairs(
                {"healthz": {"status": "crit"}, "self_defcon": 2, "bots": {"status": "ok"}},
                state_dir=tmp_path,
            )
        assert "self_agent_restart" in calls

    def test_dead_bot_alerts_human_no_autofix(self, tmp_path: Path):
        from ipracticom_sweeper.repair import self_repair
        with patch.object(self_repair, "_send_admin_alert", return_value=True) as alert:
            out = self_repair.run_self_repairs(
                {"state_dir_pct": 10.0, "self_defcon": 2,
                 "bots": {"status": "crit", "summary": "slack: 1 crit"}},
                state_dir=tmp_path,
            )
        assert out["needs_human"] is True
        assert out["alert_sent"] is True
        alert.assert_called_once()

    def test_failed_repair_alerts(self, tmp_path: Path):
        from ipracticom_sweeper.repair import self_repair
        with patch.object(self_repair, "_do",
                          side_effect=lambda a, k, d: {"action": a, "success": False, "message": "boom"}), \
             patch.object(self_repair, "_send_admin_alert", return_value=True) as alert:
            out = self_repair.run_self_repairs(
                {"state_dir_pct": 99.0, "self_defcon": 2, "bots": {"status": "ok"}},
                state_dir=tmp_path,
            )
        assert out["needs_human"] is True
        alert.assert_called_once()

    def test_alert_deduplicated(self, tmp_path: Path):
        from ipracticom_sweeper.repair import self_repair
        section = {"state_dir_pct": 10.0, "self_defcon": 2,
                   "bots": {"status": "crit", "summary": "slack: 1 crit"}}
        with patch.object(self_repair, "_send_admin_alert", return_value=True) as alert:
            self_repair.run_self_repairs(section, state_dir=tmp_path)   # alerts
            self_repair.run_self_repairs(section, state_dir=tmp_path)   # same → suppressed
        assert alert.call_count == 1

    def test_dry_run_never_alerts_or_repairs(self, tmp_path: Path):
        from ipracticom_sweeper.repair import self_repair
        with patch.object(self_repair, "_send_admin_alert", return_value=True) as alert:
            out = self_repair.run_self_repairs(
                {"state_dir_pct": 99.0, "self_defcon": 2, "bots": {"status": "crit"}},
                state_dir=tmp_path, dry_run=True,
            )
        assert out["alert_sent"] is False
        alert.assert_not_called()


# --- Connector FreeSWITCH flag + collect-script injection --------------------

class TestConnectorFreeswitch:
    def test_flag_defaults_false_and_round_trips(self):
        from ipracticom_sweeper.config.connectors import Connector
        c = Connector(name="m1", instance_id="i-0")
        assert c.freeswitch_enabled is False
        c2 = Connector.from_dict({"name": "m2", "instance_id": "i-1", "freeswitch_enabled": True})
        assert c2.freeswitch_enabled is True
        assert c2.to_dict()["freeswitch_enabled"] is True

    def test_collect_script_injects_flag_and_is_valid_python(self):
        import ast
        from ipracticom_sweeper.fleet.aws_connector import _build_collect_script
        off = _build_collect_script(False)
        on = _build_collect_script(True)
        assert "FS_ENABLED = False" in off
        assert "FS_ENABLED = True" in on
        assert "__FS_ENABLED__" not in on  # placeholder fully replaced
        ast.parse(on)   # both variants must be syntactically valid Python
        ast.parse(off)

    def test_collect_all_passes_freeswitch_ids(self):
        from ipracticom_sweeper.fleet.aws_connector import AwsSsmConnector, HostSnapshot
        with patch("boto3.client"):
            conn = AwsSsmConnector(region="il-central-1")
        seen = {}

        def fake_one(iid, *, freeswitch_enabled=False):
            seen[iid] = freeswitch_enabled
            return HostSnapshot(instance_id=iid, available=True)

        with patch.object(conn, "collect_one", side_effect=fake_one):
            conn.collect_all(["i-fs", "i-plain"], freeswitch_ids={"i-fs"})
        assert seen["i-fs"] is True
        assert seen["i-plain"] is False


# --- run_all self section + catalog filter -----------------------------------

class TestRunAllSelfSection:
    def test_self_section_always_present(self):
        from ipracticom_sweeper.monitor.checks import run_all
        snap = run_all({})
        assert "self" in snap
        assert "self_defcon" in snap["self"]

    def test_self_section_has_bots_key(self):
        from ipracticom_sweeper.monitor.checks import run_all
        snap = run_all({})
        # bots is either the aggregate dict or None (no cached probe yet) — the
        # key must exist so the dashboard can render it.
        assert "bots" in snap["self"]


class TestCatalogExcludeSelf:
    def test_exclude_self_drops_self_tagged_modules(self):
        from ipracticom_sweeper.config.module_registry import discover_modules, filter_modules, SELF_TAG
        all_mods = discover_modules()
        kept = filter_modules(all_mods, exclude_self=True)
        assert all(SELF_TAG not in m.tags for m in kept)
        # And at least one self module exists in the full set (sanity).
        assert any(SELF_TAG in m.tags for m in all_mods)

    def test_default_host_config_has_no_self_modules(self):
        from ipracticom_sweeper.config.module_registry import default_host_config
        cfg = default_host_config("newhost")
        names = {m["name"] for m in cfg["monitors"]}
        assert "self_disk" not in names
        assert "self_snapshot" not in names
        assert "health" not in names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
