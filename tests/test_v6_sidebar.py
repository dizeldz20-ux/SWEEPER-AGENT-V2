"""v0.6.0 — slice 5.2: sidebar layout present and renders.

Additive: does NOT touch `/`. The new `/v6` preview route renders the
sidebar + 4-card placeholder grid + agent status block.
"""
from pathlib import Path

from ipracticom_sweeper.dashboard import app


TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates"
CSS_PATH = TEMPLATES.parent / "static" / "style.css"


def _client():
    app.config["TESTING"] = True
    return app.test_client()


def test_v6_sidebar_partial_exists():
    """`templates/_v6_sidebar.html` exists with sidebar markup."""
    p = TEMPLATES / "_v6_sidebar.html"
    assert p.exists()
    body = p.read_text(encoding="utf-8")
    for marker in ("v6-sidebar", "v6-sidebar-nav", "v6-sidebar-brand", "v6-sidebar-status"):
        assert marker in body, f"sidebar partial missing {marker}"


def test_v6_layout_extends_base():
    """`templates/v6_layout.html` extends base.html (no broken layout chain)."""
    p = TEMPLATES / "v6_layout.html"
    assert p.exists()
    body = p.read_text(encoding="utf-8")
    assert '{% extends "base.html" %}' in body
    assert '{% include "_v6_sidebar.html" %}' in body
    assert "v6-main" in body
    assert "{% block v6_content %}" in body


def test_v6_index_template_uses_stats_bar_partial():
    """The /v6 template now includes the stats bar partial (slice 5.3+)."""
    p = TEMPLATES / "v6_index.html"
    assert p.exists()
    body = p.read_text(encoding="utf-8")
    # Slice 5.3: stats bar partial replaces the placeholder grid.
    assert '{% include "_v6_stats_bar.html" %}' in body
    # Legacy placeholder grid from slice 5.2 should be gone now.
    assert "v6-placeholder-grid" not in body


def test_v6_route_returns_200_and_sidebar_markup():
    """GET /v6 returns 200 and includes sidebar nav + section heading."""
    c = _client()
    r = c.get("/v6")
    assert r.status_code == 200, f"/v6 returned {r.status_code}"
    html = r.get_data(as_text=True)
    assert "v6-sidebar" in html
    assert "v6-nav-item" in html
    assert "לוח בקרה" in html
    assert "v6-shell" in html
    assert "v6-main" in html
    # Active link for the current page should be marked.
    assert 'class="v6-nav-item active"' in html


def test_v6_route_does_not_break_legacy_index():
    """`/` renders the unified SPA shell (v6 sidebar is on /v6 only)."""
    c = _client()
    r = c.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # Unified shell markers (slice 3, 2026-07-01: top-nav removed; sidebar carries nav)
    assert "spa-nav" in html, "unified sidebar-nav missing on /"
    assert "spa-sidebar" in html, "unified sidebar missing on /"
    # v6 sidebar must NOT appear on / — it lives on /v6 only
    assert "v6-sidebar" not in html, "/ should NOT show v6 sidebar"


def test_css_contains_v6_shell_and_sidebar_rules():
    """Style file ships the v6-shell + .v6-sidebar + responsive rules."""
    css = CSS_PATH.read_text(encoding="utf-8")
    for marker in (
        ".v6-shell",
        ".v6-sidebar",
        ".v6-sidebar-brand",
        ".v6-nav-item",
        ".v6-nav-item.active",
        ".v6-sidebar-status",
        ".v6-status-on",
        "@media (max-width: 900px)",
    ):
        assert marker in css, f"missing css rule {marker}"


def test_sidebar_uses_only_heebo_and_jetbrains_mono_fonts():
    """No new font dependency introduced (Heebo stays, per plan)."""
    css = CSS_PATH.read_text(encoding="utf-8")
    body = (TEMPLATES / "_v6_sidebar.html").read_text(encoding="utf-8")
    # Sidebar markup itself contains no inline font overrides.
    assert "font-family" not in body
    # CSS uses existing fonts only — no "Inter" snuck in.
    assert "Inter" not in css, "Inter not allowed (Heebo stays)"
    # Heebo still wired up.
    assert "Heebo" in css
