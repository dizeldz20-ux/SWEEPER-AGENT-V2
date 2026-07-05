"""v0.6.0 — slice 7.3: heatmap + uptime endpoints + inline SVG rendering."""
import json
from pathlib import Path

from ipracticom_sweeper.dashboard import app


def _client():
    app.config["TESTING"] = True
    return app.test_client()


def _seed_audit(tmp_path, lines):
    p = Path(tmp_path) / "monitor.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return p


def _patch_audit(monkeypatch, audit_path):
    """Make the metrics endpoints read from `audit_path` instead of the
    hardcoded /var/lib location.
    """
    import ipracticom_sweeper.dashboard as d
    real_path = d.Path

    def fake(p):
        sp = str(p)
        if sp.endswith("/var/lib/ipracticom-sweeper/audit/monitor.jsonl"):
            return audit_path
        return real_path(p)

    monkeypatch.setattr(d, "Path", fake)


def test_heatmap_routes_registered():
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/v6/metrics/events_heatmap" in rules
    assert "/v6/metrics/uptime_30d" in rules
    assert "/v6/metrics/page" in rules


def test_heatmap_returns_correct_grid_shape(tmp_path, monkeypatch):
    """Always returns 7x24 grid even when no audit log present."""
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard.Path",
        lambda p, *a, **kw: tmp_path / "_nope_" if "_nope_" in str(p) else Path(p, *a, **kw),
        raising=False,
    )
    c = _client()
    r = c.get("/v6/metrics/events_heatmap")
    assert r.status_code == 200
    j = r.get_json()
    assert j["days"] == 7 and j["hours"] == 24
    assert len(j["grid"]) == 7
    assert all(len(row) == 24 for row in j["grid"])


def test_heatmap_counts_events_per_hour(tmp_path, monkeypatch):
    """Events are bucketed by day-of-week window × hour-of-day."""
    from datetime import datetime as _dt, timezone, timedelta

    now = _dt.now(timezone.utc)
    # Bucket: 2 events today at hour=14, 1 event 2 days ago at hour=8.
    audit = _seed_audit(tmp_path, [
        {"ts": (now - timedelta(hours=1)).isoformat(),  "module": "x", "status": "crit"},
        {"ts": (now - timedelta(hours=2)).isoformat(),  "module": "x", "status": "warn"},
        {"ts": (now - timedelta(days=2, hours=4)).isoformat(), "module": "y", "status": "ok"},
    ])
    _patch_audit(monkeypatch, audit)

    c = _client()
    r = c.get("/v6/metrics/events_heatmap")
    j = r.get_json()
    grid = j["grid"]
    # Sum across grid == number of in-window events.
    total = sum(sum(row) for row in grid)
    assert total == 3, f"expected 3 in-window events, got {total}"
    # Today's row should have at least 2 events (the two recent ones).
    today_row = grid[-1]
    today_total = sum(today_row)
    assert today_total >= 2, f"expected at least 2 today, got {today_total}"


def test_uptime_returns_30_points(tmp_path, monkeypatch):
    """Each day in the 30-day window emits one {date, ratio} entry."""
    audit = _seed_audit(tmp_path, [])
    _patch_audit(monkeypatch, audit)
    c = _client()
    r = c.get("/v6/metrics/uptime_30d")
    assert r.status_code == 200
    j = r.get_json()
    assert j["days"] == 30
    assert len(j["points"]) == 30
    for i, p in enumerate(j["points"]):
        assert "date" in p and "ratio" in p
        assert 0.0 <= p["ratio"] <= 1.0
        # No data = ratio=1.0 (no failures).
        assert p["ratio"] == 1.0


def test_uptime_counts_critical_events_as_failures(tmp_path, monkeypatch):
    """Days with 100% crit events should produce ratio=0.0."""
    from datetime import datetime as _dt, timezone, timedelta
    now = _dt.now(timezone.utc)
    today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    audit = _seed_audit(tmp_path, [
        {"ts": today_noon.isoformat(), "module": "x", "status": "crit"},
        {"ts": today_noon.isoformat(), "module": "x", "status": "red"},
        {"ts": today_noon.isoformat(), "module": "x", "status": "ok"},
    ])
    _patch_audit(monkeypatch, audit)

    c = _client()
    r = c.get("/v6/metrics/uptime_30d")
    j = r.get_json()
    points = j["points"]
    # Today is the LAST entry (newest last).
    assert points[-1]["ratio"] < 1.0, "crit events today should drop ratio"
    assert points[-1]["date"] == __import__("datetime").datetime.now().date().isoformat()


def test_metrics_page_renders_heatmap_and_svg():
    body = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "v6_metrics.html").read_text(encoding="utf-8")
    for marker in (
        "v6-heatmap",
        "v6-uptime",
        "/v6/metrics/events_heatmap",
        "/v6/metrics/uptime_30d",
        "<svg",
        "setInterval(tick, 60000)",
        "colorFor",
    ):
        assert marker in body, f"missing {marker!r}"


def test_metrics_zero_data_returns_valid_empties(tmp_path, monkeypatch):
    """No audit log → heatmap empty grid, uptime 30 ratio=1.0 days."""
    # Use the same _patch_audit helper the other tests use so the endpoints
    # read from an isolated (non-existent) path. The previous inline
    # monkeypatch keyed on "_never_" never matched the real audit path, so
    # the test read whatever audit data existed on the host and failed
    # non-deterministically on any machine that had run the agent.
    _patch_audit(monkeypatch, tmp_path / "nonexistent.jsonl")
    c = _client()
    r1 = c.get("/v6/metrics/events_heatmap")
    r2 = c.get("/v6/metrics/uptime_30d")
    assert r1.status_code == r2.status_code == 200
    j1 = r1.get_json()
    j2 = r2.get_json()
    assert j1["grid"] == [[0]*24 for _ in range(7)]
    assert j2["points"] == []
    # Note: when no audit exists, we return empty points list (caller renders
    # 'אין נתוני uptime עדיין'), and days=30 is preserved.


def test_css_supports_heatmap_and_svg():
    css = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "static" / "style.css").read_text(encoding="utf-8")
    for marker in (
        ".v6-heatmap",
        ".v6-heatmap-legend",
        ".v6-legend-cell",
        ".v6-uptime-svg",
        "v6-uptime-wrap",
    ):
        assert marker in css, f"missing CSS {marker}"


def test_sidebar_includes_metrics_link():
    body = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "_v6_sidebar.html").read_text(encoding="utf-8")
    assert "/v6/metrics/page" in body
    assert "מדדים" in body
