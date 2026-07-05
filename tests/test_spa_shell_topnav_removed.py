"""Slice 3 — spa-topnav must be entirely absent; sidebar carries all 9.

This guards the design decision (2026-07-01): the 9 nav items live in
the sidebar (``spa-nav`` inside ``spa-sidebar``), not in a full-width
top strip. We assert:

  * No ``spa-topnav`` class appears anywhere in the rendered shell.
  * No CSS rule for ``.spa-topnav`` lives in the inline ``<style>``.
  * The sidebar ``<nav class="spa-nav">`` carries exactly 9 anchor children.
  * The sidebar width has been widened past the legacy 256px.
"""
from __future__ import annotations

import re

import pytest

from ipracticom_sweeper.dashboard import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


PAGES_TO_PROBE = [
    "/", "/history", "/settings", "/approvals",
    "/settings/connectors", "/fleet", "/inspector",
    "/catalogue", "/chat",
]


def _render(client, path: str) -> str:
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    return resp.get_data(as_text=True)


@pytest.mark.parametrize("path", PAGES_TO_PROBE)
def test_spa_topnav_class_is_gone(client, path):
    body = _render(client, path)
    # The class `spa-topnav` (in markup AND in CSS) must be fully removed.
    assert "spa-topnav" not in body, (
        f"{path} still references spa-topnav — slice 3 should have removed it"
    )


def test_sidebar_nav_has_nine_anchors(client):
    body = _render(client, "/")
    # Pull the sidebar nav block — first <nav class="spa-nav"> after the brand.
    m = re.search(
        r'<nav\s+class="spa-nav">(.*?)</nav>', body, re.DOTALL
    )
    assert m, "spa-nav block not found"
    nav_inner = m.group(1)
    # Each entry is <a href="..."><svg>...</svg><span>...</span></a>
    anchors = re.findall(r'<a\s+href="[^"]+"', nav_inner)
    assert len(anchors) == 9, (
        f"sidebar nav must carry exactly 9 anchors, found {len(anchors)}"
    )


def test_sidebar_widened_past_256px(client):
    body = _render(client, "/")
    # The inline style contains either the literal .spa-sidebar { width: 288px ... }
    # — accept any width >= 280px (post-slice 3 the value is 288px).
    m = re.search(r"\.spa-sidebar\s*\{[^}]*width:\s*(\d+)px", body)
    assert m, "spa-sidebar width rule not found in inline style"
    width = int(m.group(1))
    assert width >= 280, (
        f"sidebar should be widened to >=280px in slice 3, got {width}"
    )


def test_no_legacy_min_height_compensation(client):
    """Topnav was 41px tall; removing it must remove the calc(100vh - 41px) trick."""
    body = _render(client, "/")
    assert "calc(100vh - 41px)" not in body, (
        "stale topnav compensation still in CSS — slice 3 should have removed it"
    )
