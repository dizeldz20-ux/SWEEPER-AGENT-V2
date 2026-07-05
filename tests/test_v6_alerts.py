"""v0.6.0 — slice 7.1: live alerts feed + tabs + queued actions."""
import json
from pathlib import Path

import pytest

from ipracticom_sweeper.dashboard import app


def _client():
    app.config["TESTING"] = True
    return app.test_client()


def _seed_audit(tmp_path, lines):
    """Write a JSONL file to a fake monitor audit log."""
    p = Path(tmp_path) / "monitor.jsonl"
    with p.open("w") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")
    return p


def _patch_audit_path(monkeypatch, audit_path):
    """Redirect `_load_history_runs` to use a tmp audit log path."""
    import ipracticom_sweeper.dashboard as d
    monkeypatch.setattr(d, "Path", lambda *args, **kw: (
        audit_path if (args and "monitor.jsonl" in str(args[-1])) else
        Path(*args, **kw)
    ) if False else Path(*args, **kw))
    # Direct route — monkeypatch the function body to use our tmp dir.
    from ipracticom_sweeper import dashboard as _d
    orig = _d._load_history_runs

    def wrapper():
        if not audit_path.exists():
            return []
        out = []
        for line in audit_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                out.append({
                    "ts": ev.get("ts", ""),
                    "module": ev.get("module", ""),
                    "status": ev.get("status", ""),
                })
            except json.JSONDecodeError:
                continue
        return out

    monkeypatch.setattr(_d, "_load_history_runs", wrapper)
    return wrapper


def test_alerts_route_registered():
    rules = {r.rule for r in app.url_map.iter_rules()}
    for r in ("/v6/alerts", "/v6/alerts/page"):
        assert r in rules, f"missing route {r}"


def test_alerts_json_groups_events_by_tab(tmp_path, monkeypatch):
    """Non-ok events appear; ?tab=network filters them by module name heuristic."""
    audit = _seed_audit(tmp_path, [
        {"ts": "2026-06-30T12:00:00", "module": "tcp_check",    "status": "crit"},
        {"ts": "2026-06-30T12:01:00", "module": "cpu",          "status": "warn"},
        {"ts": "2026-06-30T12:02:00", "module": "freeswitch",   "status": "crit"},
        {"ts": "2026-06-30T12:03:00", "module": "ok_module",    "status": "ok"},  # excluded
        {"ts": "2026-06-30T12:04:00", "module": "auth_log",     "status": "red"},
    ])
    _patch_audit_path(monkeypatch, audit)

    c = _client()
    r = c.get("/v6/alerts")
    assert r.status_code == 200
    body = r.get_json()
    assert body["tab"] == "all"
    assert body["count"] == 4   # ok excluded
    mods = [a["module"] for a in body["alerts"]]
    assert "ok_module" not in mods
    assert "tcp_check" in mods
    assert "cpu" in mods
    assert "freeswitch" in mods
    assert "auth_log" in mods
    tabs = {a["module"]: a["tab"] for a in body["alerts"]}
    assert tabs["tcp_check"] == "network"
    assert tabs["cpu"] == "performance"
    assert tabs["freeswitch"] == "system"
    assert tabs["auth_log"] == "security"
    assert body["crit_count"] == 3


def test_alerts_tab_filters_results(tmp_path, monkeypatch):
    """?tab=performance keeps only performance alerts."""
    audit = _seed_audit(tmp_path, [
        {"ts": "2026-06-30T12:00:00", "module": "tcp_check", "status": "crit"},
        {"ts": "2026-06-30T12:01:00", "module": "cpu",       "status": "warn"},
        {"ts": "2026-06-30T12:02:00", "module": "memory",    "status": "warn"},
    ])
    _patch_audit_path(monkeypatch, audit)

    c = _client()
    r = c.get("/v6/alerts?tab=performance")
    assert r.status_code == 200
    body = r.get_json()
    assert body["tab"] == "performance"
    mods = [a["module"] for a in body["alerts"]]
    assert mods == ["cpu", "memory"]
    assert body["count"] == 2


def test_alerts_returns_empty_when_no_log(tmp_path, monkeypatch):
    """No audit log → empty list, count=0, never 500."""
    fake = tmp_path / "monitor.jsonl"  # doesn't exist
    _patch_audit_path(monkeypatch, fake)
    c = _client()
    r = c.get("/v6/alerts")
    assert r.status_code == 200
    body = r.get_json()
    assert body["count"] == 0
    assert body["alerts"] == []


def test_alerts_page_renders_dark_tabs_and_list():
    """HTML page wires tabs + empty/list region + JS polling."""
    body = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "v6_alerts.html").read_text(encoding="utf-8")
    for marker in (
        "v6-tabs",
        'href="?tab=all"',
        'href="?tab=network"',
        'href="?tab=performance"',
        'href="?tab=security"',
        'href="?tab=system"',
        "v6-alerts-list",
        "v6-alerts-empty",
        "v6-crit-count",
        "/v6/alerts?tab=",
        "setInterval(tick, 5000)",
        "v6-pulse-fast",
    ):
        assert marker in body, f"template missing {marker!r}"


def test_resolve_writes_proposal(tmp_path, monkeypatch):
    """POST /v6/alerts/<id>/resolve enqueues a mark_resolved proposal."""
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._is_remote_mode",
        lambda: False,
    )
    import ipracticom_sweeper.repair.pending as _pmod
    new_pending = tmp_path / "pending_repairs"
    new_pending.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_pmod, "PENDING_DIR", new_pending)

    c = _client()
    r = c.post(
        "/v6/alerts/2026-06-30T12:00:00:freeswitch/resolve",
        data={"note": "reboot solved it"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["queued"] is True
    assert body["proposal"]["action"] == "mark_resolved"
    files = list(new_pending.glob("*.json"))
    assert len(files) == 1


def test_snooze_writes_proposal_with_valid_duration(tmp_path, monkeypatch):
    """snooze 15/60/1440 all accepted and enqueued."""
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._is_remote_mode",
        lambda: False,
    )
    import ipracticom_sweeper.repair.pending as _pmod
    new_pending = tmp_path / "pending_repairs"
    new_pending.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_pmod, "PENDING_DIR", new_pending)

    c = _client()
    for d in ("15", "60", "1440"):
        r = c.post(
            "/v6/alerts/A/snooze",
            data={"duration_min": d},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["proposal"]["action"] == "snooze"
        assert body["proposal"]["kwargs"]["duration_min"] == int(d)


@pytest.mark.parametrize("bad", ["10", "30", "999", "abc", ""])
def test_snooze_rejects_invalid_duration(bad, tmp_path, monkeypatch):
    """Anything not in {15, 60, 1440} is 400."""
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._is_remote_mode",
        lambda: False,
    )
    c = _client()
    r = c.post("/v6/alerts/A/snooze", data={"duration_min": bad})
    assert r.status_code == 400, f"d={bad!r} should be 400, got {r.status_code}"


def test_remote_mode_blocks_alert_actions(monkeypatch):
    """Remote mode refuses local alert actions."""
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._is_remote_mode",
        lambda: True,
    )
    c = _client()
    assert c.post("/v6/alerts/A/resolve").status_code == 400
    assert c.post("/v6/alerts/A/snooze", data={"duration_min": "60"}).status_code == 400


def test_alert_actions_require_basic_auth(tmp_path, monkeypatch):
    """Basic Auth gates alert destructive endpoints."""
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._is_remote_mode",
        lambda: False,
    )
    monkeypatch.setattr("ipracticom_sweeper.dashboard._DASHBOARD_USER", "u")
    monkeypatch.setattr("ipracticom_sweeper.dashboard._DASHBOARD_PASS", "p")
    c = _client()
    r = c.post("/v6/alerts/A/resolve")
    assert r.status_code == 401


def test_css_supports_alert_tabs():
    css = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "static" / "style.css").read_text(encoding="utf-8")
    for marker in (".v6-tabs", ".v6-tab", ".v6-tab.active", ".v6-alert-row",
                   ".v6-actions-inline", ".v6-actions-inline button.v6-danger"):
        assert marker in css, f"missing CSS {marker}"


def test_sidebar_includes_alerts_link():
    body = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "_v6_sidebar.html").read_text(encoding="utf-8")
    assert "/v6/alerts/page" in body
    assert "התראות" in body
