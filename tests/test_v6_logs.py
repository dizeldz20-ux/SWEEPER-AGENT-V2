"""v0.6.0 — slice 7.2: read-only log tail."""
from pathlib import Path

from ipracticom_sweeper.dashboard import (
    _pick_v6_log_target,
    _tail_log_file,
    app,
)


def _client():
    app.config["TESTING"] = True
    return app.test_client()


def test_log_routes_registered():
    rules = {r.rule for r in app.url_map.iter_rules()}
    for r in ("/v6/logs", "/v6/logs/page"):
        assert r in rules, f"missing route {r}"


def test_pick_v6_log_target_returns_none_when_no_logs(monkeypatch, tmp_path):
    """No log files anywhere → None, never crash."""
    import ipracticom_sweeper.dashboard as d
    monkeypatch.setattr(d, "Path", lambda *a, **kw: tmp_path / "_never_")
    assert _pick_v6_log_target() is None


def test_pick_v6_log_target_prefers_freeswitch(monkeypatch, tmp_path):
    """If the FS log exists, that's what we tail."""
    fs_log = tmp_path / "freeswitch.log"
    fs_log.write_text("2026-06-30 FS started\n")
    audit = tmp_path / "audit" / "monitor.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text('{"module":"cpu","status":"ok"}\n')

    import ipracticom_sweeper.dashboard as d
    real_path = d.Path
    def fake(p):
        pp = str(p)
        if pp.endswith("freeswitch.log") or pp.endswith("freeswitch.log.1"):
            return fs_log
        if pp.endswith("monitor.jsonl"):
            return audit
        return real_path(p)
    monkeypatch.setattr(d, "Path", fake)
    chosen = _pick_v6_log_target()
    assert chosen == fs_log


def test_pick_v6_log_target_falls_back_to_audit(monkeypatch, tmp_path):
    """No FS log → fall back to the sweeper monitor audit."""
    audit = tmp_path / "audit" / "monitor.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text('{"module":"cpu","status":"ok"}\n')
    import ipracticom_sweeper.dashboard as d
    real_path = d.Path
    def fake(p):
        pp = str(p)
        if pp.endswith("freeswitch.log") or pp.endswith("freeswitch.log.1"):
            return tmp_path / "_missing_"
        if pp.endswith("monitor.jsonl"):
            return audit
        return real_path(p)
    monkeypatch.setattr(d, "Path", fake)
    assert _pick_v6_log_target() == audit


def test_tail_log_file_returns_last_n_lines(tmp_path):
    p = tmp_path / "x.log"
    lines = [f"line {i}\n" for i in range(300)]
    p.write_text("".join(lines))
    out = _tail_log_file(p, max_lines=50)
    assert len(out) == 50
    assert out[-1] == "line 299"
    assert out[0] == "line 250"


def test_tail_log_file_handles_missing(tmp_path):
    assert _tail_log_file(None, 50) == []
    assert _tail_log_file(tmp_path / "_missing.log", 50) == []


def test_tail_log_file_handles_small_files(tmp_path):
    """File smaller than the 64KB seek window should still return all lines."""
    p = tmp_path / "small.log"
    p.write_text("a\nb\nc\n")
    assert _tail_log_file(p, 50) == ["a", "b", "c"]


def test_v6_logs_route_returns_json(tmp_path, monkeypatch):
    fs_log = tmp_path / "freeswitch.log"
    fs_log.write_text("FOO\nBAR\nBAZ\n")
    import ipracticom_sweeper.dashboard as d
    real_path = d.Path
    def fake(p):
        if str(p).endswith("freeswitch.log") or str(p).endswith("freeswitch.log.1"):
            return fs_log
        return real_path(p)
    monkeypatch.setattr(d, "Path", fake)
    c = _client()
    r = c.get("/v6/logs")
    assert r.status_code == 200
    j = r.get_json()
    assert j["log"] == "freeswitch.log"
    assert j["lines"] == ["FOO", "BAR", "BAZ"]


def test_v6_logs_returns_empty_when_no_log(monkeypatch, tmp_path):
    """No log file → 200 + empty list (UI shows '— אין לוג זמין —')."""
    import ipracticom_sweeper.dashboard as d
    monkeypatch.setattr(d, "Path", lambda *a, **kw: tmp_path / "_nope_")
    c = _client()
    r = c.get("/v6/logs")
    assert r.status_code == 200
    j = r.get_json()
    assert j["log"] is None
    assert j["lines"] == []


def test_v6_logs_page_template():
    body = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "v6_logs.html").read_text(encoding="utf-8")
    for marker in (
        "v6-logs-pause",
        "v6-logs-clear",
        "v6-logs-autoscroll",
        "v6-log-pre",
        "/v6/logs",
        "setInterval(tick, 3000)",
        "v6-log-wrap",
    ):
        assert marker in body, f"template missing {marker!r}"


def test_sidebar_includes_logs_link():
    body = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "_v6_sidebar.html").read_text(encoding="utf-8")
    assert "/v6/logs/page" in body
    assert "לוג חי" in body


def test_css_supports_log_widget():
    css = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "static" / "style.css").read_text(encoding="utf-8")
    for marker in (".v6-logs-toolbar", ".v6-log-pre", ".v6-log-wrap"):
        assert marker in css


def test_logs_endpoint_is_read_only(monkeypatch):
    """Ensure there is no POST / PUT / DELETE on /v6/logs — read-only by design."""
    c = _client()
    for verb in ("POST", "PUT", "DELETE", "PATCH"):
        r = getattr(c, verb.lower())("/v6/logs")
        assert r.status_code in (404, 405), (
            f"{verb} /v6/logs should be rejected, got {r.status_code}"
        )
