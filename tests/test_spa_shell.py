"""Tests for the unified SPA shell that wraps every dashboard page.

The redesign wraps every legacy route (/, /history, /settings, /approvals,
/fleet, /inspector, /catalogue, /chat) in a shared shell defined by
``base_spa.html``: a left sidebar carrying all 9 nav items (slice 3,
2026-07-01) + the page content. The shell must be present on EVERY page
so the operator never feels a visual jump when clicking between sections.

These tests verify:
  - Every legacy route renders the shared shell (spa-nav, spa-sidebar,
    data-shell="spa" body attribute).
  - Every page has all 9 sidebar nav links to other legacy routes.
  - The /spa chooser redirects to / (the design of record won).
  - Settings/approvals in remote mode still render the shell even
    though they return 403 (the operator sees a styled error card).
"""

from unittest.mock import patch

import pytest

from ipracticom_sweeper.dashboard import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


ALL_PAGES = [
    ("/", 200),
    ("/history", 200),
    ("/settings", 200),  # 200 in local mode (test client default)
    ("/approvals", 200),
    ("/fleet", 200),
    ("/inspector", 200),
    ("/catalogue", 200),
    ("/chat", 200),
]

REMOTE_PAGES = [
    ("/settings", 403),  # 403 in remote mode is the correct security gate
    ("/approvals", 403),
]


# All 9 sidebar nav links (must match base_spa.html).
# Slice 3: migrated from .spa-topnav (removed) into <nav class="spa-nav">
# inside the sidebar, with SVG icons.
NAV_LINKS = [
    ("/", "לוח בקרה"),
    ("/history", "היסטוריה"),
    ("/approvals", "אישורים"),
    ("/settings", "הגדרות"),
    ("/settings/connectors", "מחברים"),
    ("/fleet", "צי"),
    ("/inspector", "מפקח בדיקות"),
    ("/catalogue", "קטלוג"),
    ("/chat", "צ'אט"),
]  # 9 entries — sidebar widened from 256px→288px in slice 3.


@pytest.mark.parametrize("path,expected_status", ALL_PAGES)
def test_every_legacy_page_renders_unified_shell(client, path, expected_status):
    """Each page in local mode must be wrapped in the new shell."""
    resp = client.get(path)
    assert resp.status_code == expected_status, (
        f"{path} returned {resp.status_code}, expected {expected_status}"
    )
    body = resp.get_data(as_text=True)
    assert 'data-shell="spa"' in body, f"{path} missing data-shell=spa"
    assert 'spa-nav' in body, f"{path} missing spa-nav"
    assert 'spa-sidebar' in body, f"{path} missing spa-sidebar"


@pytest.mark.parametrize("path,expected_status", REMOTE_PAGES)
def test_remote_mode_renders_shell_around_error(client, path, expected_status):
    """In remote mode, settings/approvals return 403 — but the 403 page
    must STILL be wrapped in the unified shell so the operator has nav."""
    with patch("ipracticom_sweeper.dashboard._is_remote_mode", return_value=True):
        resp = client.get(path)
    assert resp.status_code == expected_status
    body = resp.get_data(as_text=True)
    assert 'data-shell="spa"' in body, f"{path} 403 body missing shell"
    assert 'spa-nav' in body, f"{path} 403 body missing sidebar-nav"


@pytest.mark.parametrize("path,expected_status", ALL_PAGES)
def test_every_page_has_all_9_nav_links(client, path, expected_status):
    """Every page must carry the full 9-item sidebar nav to other legacy routes.

    Slice 3: links live inside <nav class="spa-nav"> (formerly .spa-topnav)."""
    resp = client.get(path)
    body = resp.get_data(as_text=True)
    for href, label in NAV_LINKS:
        assert f'href="{href}"' in body, (
            f"{path} missing nav link to {href}"
        )


def test_spa_chooser_redirects_to_root(client):
    """/spa was the A/B chooser — A won, so it now redirects to /."""
    resp = client.get("/spa", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_no_legacy_template_still_extends_base_html():
    """The 11 templates that wrapped with base.html must all be on base_spa.html."""
    import os
    from pathlib import Path
    templates_dir = Path(__file__).resolve().parent.parent / "src" / "ipracticom_sweeper" / "templates"
    targets = [
        "approval_detail.html",
        "approvals.html",
        "catalogue.html",
        "chat.html",
        "connectors.html",
        "dashboard.html",
        "error.html",
        "fleet.html",
        "history.html",
        "inspector.html",
        "settings.html",
    ]
    for name in targets:
        path = templates_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        first_extends = next(
            (line for line in text.splitlines() if "extends" in line), None
        )
        assert first_extends is not None, f"{name} has no extends"
        assert 'extends "base_spa.html"' in first_extends, (
            f"{name} still uses legacy base: {first_extends!r}"
        )


def test_spa_a_and_b_still_render(client):
    """/spa/a and /spa/b are kept for visual A/B review."""
    snapshot = {
        "defcon": 4, "defcon_label": "yellow", "duration_ms": 423,
        "problems_found": 0, "repairs_attempted": 0, "repairs_succeeded": 0,
        "repairs_failed": 0, "needs_human": 0,
        "diagnosis": {
            "defcon": 4, "defcon_label": "yellow", "summary": "all ok",
            "problems": [], "modules": {"cpu": {"status": "ok", "values": {}}},
        },
    }
    with patch("ipracticom_sweeper.dashboard._fetch_snapshot", return_value=snapshot):
        a = client.get("/spa/a")
        b = client.get("/spa/b")
    assert a.status_code == 200
    assert b.status_code == 200
    assert 'data-variant="a"' in a.get_data(as_text=True)
    assert 'data-variant="b"' in b.get_data(as_text=True)
