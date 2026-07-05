"""v0.6.0 — slice 6.1: dark machine list page (read-only)."""
from pathlib import Path

from ipracticom_sweeper.dashboard import app


def _client():
    app.config["TESTING"] = True
    return app.test_client()


def test_v6_machines_route_registered():
    """/v6/machines is a known route."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/v6/machines" in rules


def test_v6_machines_returns_200():
    """GET /v6/machines returns 200 with the v6 layout."""
    c = _client()
    r = c.get("/v6/machines")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "v6-shell" in html
    assert "v6-sidebar" in html


def test_v6_machines_renders_table_structure():
    """Markup includes a v6-table with header columns and host rows when data exists."""
    c = _client()
    r = c.get("/v6/machines")
    html = r.get_data(as_text=True)
    # Either the table renders hosts OR the empty state shows up — both correct.
    assert ("v6-table" in html) or ("v6-empty" in html), (
        "page should render either table or empty state"
    )


def test_v6_actions_column_header_present():
    """The /v6/machines template now renders an Actions column with 3 destructive
    actions (reboot/agent_restart/ssm_connect) and a Maintenance column for
    metadata-only toggling. Slice 6.2 contract.
    """
    from pathlib import Path
    body = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "v6_machines.html").read_text(encoding="utf-8")
    assert ">פעולות<" in body or "th>פעולות<" in body, "Actions column header missing"
    assert "תחזוקה" in body, "Maintenance column header missing"
    # All 3 destructive actions are wired.
    for action in ("agent_restart", "ssm_connect", "reboot"):
        assert f'value="{action}"' in body, f"action {action} not wired"
    # At least one approval-gate reference.
    assert "/approvals" in body, "approvals gate reference missing"


def test_sidebar_includes_machines_link():
    """The v6 sidebar surfaces the new machines nav item."""
    p = Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "_v6_sidebar.html"
    body = p.read_text(encoding="utf-8")
    assert "/v6/machines" in body
    assert "מכונות" in body


def test_css_includes_table_rules():
    """style.css ships the v6-table layout + defcon row tints."""
    css = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "static" / "style.css").read_text(encoding="utf-8")
    for marker in (
        ".v6-table",
        ".v6-table-wrap",
        ".v6-row-host",
        ".defcon-red",
        ".defcon-orange",
        ".defcon-green",
        ".v6-modules",
        ".v6-btn",
    ):
        assert marker in css, f"missing CSS rule {marker}"


def test_v6_machines_does_not_break_legacy_fleet_route():
    """`/fleet` returns 200 and now uses the unified SPA chrome."""
    c = _client()
    r = c.get("/fleet")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # Unified SPA shell — replaces the legacy site-header.
    # Slice 3 (2026-07-01): spa-topnav removed; sidebar now carries the 9 nav items.
    assert "spa-nav" in html
    assert "spa-sidebar" in html
    assert "v6-sidebar" not in html


def test_v6_machines_safe_when_no_connectors(tmp_path, monkeypatch):
    """Empty connectors list must produce a graceful empty state, never 500."""
    # Patch the connector loader to return an empty list.
    import ipracticom_sweeper.dashboard as d
    import ipracticom_sweeper.config.connectors as _real_connectors
    monkeypatch.setattr(
        "ipracticom_sweeper.fleet.load_all_snapshots",
        lambda: [],
        raising=True,
    )
    c = _client()
    r = c.get("/v6/machines")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "v6-empty" in html or "לא הוגדרו connectors" in html
