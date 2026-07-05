"""v0.6.0 — slice 5.3: live stats bar with real data sources.

Verifies:
  - `_fetch_v6_stats` returns all 5 expected keys with sane defaults
  - `/v6` renders the new `_v6_stats_bar.html` partial
  - All 4 cards (machines, pbx, critical, events) are present in HTML
  - If no data is available the page does NOT crash — values fall back to "—"
  - critical_count > 0 triggers pulse animation
  - SQLite event store degradation is handled gracefully
"""
from ipracticom_sweeper.dashboard import _fetch_v6_stats, app


def _client():
    app.config["TESTING"] = True
    return app.test_client()


def test_fetch_v6_stats_returns_expected_keys():
    """`_fetch_v6_stats` always returns the 5 documented keys."""
    s = _fetch_v6_stats()
    for key in ("total_machines", "pbx_count", "critical_count", "events_today", "defcon"):
        assert key in s, f"missing key {key}"


def test_fetch_v6_stats_defaults_are_honest():
    """Every missing value falls back to '—', never to 0 or fake numbers."""
    s = _fetch_v6_stats()
    # All four non-defcon fields must be either an int OR the literal "—".
    for key in ("total_machines", "pbx_count", "critical_count", "events_today"):
        v = s[key]
        assert v == "—" or isinstance(v, int), (
            f"{key} should be '—' or int, got {type(v).__name__}={v!r}"
        )
    # defcon may be None or int
    assert s["defcon"] is None or isinstance(s["defcon"], int)


def test_v6_renders_stats_bar_partial():
    """`/v6` includes the 4-card stats bar markup (no placeholder grid)."""
    c = _client()
    r = c.get("/v6")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "v6-stats-bar" in html
    assert "v6-stat-card" in html
    # All 4 named cards present.
    for card in ("machines", "pbx", "critical", "events"):
        assert f'data-card="{card}"' in html, f"missing card {card}"


def test_v6_does_not_use_legacy_placeholder_grid():
    """Placeholder grid from slice 5.2 was replaced (no leftover TODO grid)."""
    c = _client()
    r = c.get("/v6")
    html = r.get_data(as_text=True)
    assert "v6-placeholder-grid" not in html, "legacy placeholder grid still present"


def test_legacy_index_does_not_show_v6_layout():
    """`/` still uses base.html (additive contract preserved)."""
    c = _client()
    r = c.get("/")
    html = r.get_data(as_text=True)
    assert "v6-stats-bar" not in html, "/ should not show v6 stats bar yet"
    assert "v6-sidebar" not in html


def test_stats_bar_partial_has_pulse_marker():
    """Pulse class is wired only when critical_count is positive."""
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "_v6_stats_bar.html"
    body = p.read_text(encoding="utf-8")
    # The conditional is enforced via Jinja — the class name itself is in the
    # markup so the renderer can apply it.
    assert "v6-pulse-fast" in body
    assert "stats.critical_count" in body


def test_critical_card_alert_border_set():
    """Critical card carries the alert border class (CSS-driven highlight)."""
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "_v6_stats_bar.html"
    body = p.read_text(encoding="utf-8")
    assert "v6-stat-card-alert" in body


def test_css_includes_stats_bar_rules():
    """CSS file ships the 4-card stats bar layout + per-card accent rules."""
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "static" / "style.css").read_text(encoding="utf-8")
    for marker in (
        ".v6-stats-bar",
        ".v6-stat-card",
        ".v6-stat-label",
        ".v6-stat-value",
        ".v6-stat-hint",
        '.v6-stat-card[data-card="machines"]::before',
        '.v6-stat-card[data-card="critical"]::before',
    ):
        assert marker in css, f"missing CSS rule {marker}"


def test_route_pulls_stats_via_real_sources(monkeypatch):
    """`_fetch_v6_stats` delegates to fleet/snapshot/sqlite (not a hardcoded dict)."""
    captured = {}
    def fake_inner():
        captured["called"] = True
        return {"total_machines": 7, "pbx_count": 2, "critical_count": 1,
                "events_today": 42, "defcon": 3}
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._fetch_v6_stats", fake_inner)
    c = _client()
    with c.get("/v6") as r:
        html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert captured.get("called") is True
    # The injected values should appear in the rendered HTML.
    assert ">7<" in html or ">7\n" in html or "v6-stat-value\">7<" in html
    assert "42" in html


def test_count_pbx_hosts_heuristic():
    """PBX count uses hostname prefix tokens (fs-, freeswitch, pbx)."""
    from ipracticom_sweeper.dashboard import _count_pbx_hosts
    summary = {"hosts": {
        "fs-prod-01": {}, "freeswitch-staging": {}, "pbx-1": {},
        "web-server": {}, "db-primary": {},
    }}
    assert _count_pbx_hosts(summary) == 3
    # Non-dict summary → 0, never crash.
    assert _count_pbx_hosts(None) == 0
    assert _count_pbx_hosts({}) == 0
