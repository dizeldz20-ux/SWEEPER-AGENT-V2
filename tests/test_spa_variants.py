"""Tests for the two dashboard SPA variants (A + B) and their shared
view-model shaping.

Structure mirrors test_dashboard.py:
  - Pure-function tests for spa_context.shape_spa_context (no Flask)
  - Route tests using the Flask test client with a mocked snapshot
  - Both variants must render REAL snapshot data, not mock fixtures
"""

from unittest.mock import patch

import pytest

from ipracticom_sweeper.dashboard import app
from ipracticom_sweeper.spa_context import (
    shape_modules,
    shape_problems,
    shape_spa_context,
)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def snapshot():
    """A realistic snapshot mirroring the live /api/snapshot shape."""
    return {
        "defcon": 4,
        "defcon_label": "yellow",
        "duration_ms": 423,
        "problems_found": 2,
        "repairs_attempted": 0,
        "repairs_succeeded": 0,
        "repairs_failed": 0,
        "needs_human": 2,
        "server": "sweeper-test",
        "started_at": "2026-07-01T09:00:00+00:00",
        "finished_at": "2026-07-01T09:00:07+00:00",
        "diagnosis": {
            "defcon": 4,
            "defcon_label": "yellow",
            "summary": "2 issue(s) detected",
            "problem_count": 2,
            "problems": [
                {
                    "module": "security_baseline",
                    "kind": "baseline_drift",
                    "severity": "crit",
                    "detail": "Security baseline drift detected",
                },
                {
                    "module": "disk",
                    "kind": "disk_expected_ro_missing",
                    "severity": "warn",
                    "detail": "Expected read-only mounts not read-only: ['/']",
                },
            ],
            "modules": {
                "cpu": {"status": "ok", "values": {"load_5min": 0.71}},
                "disk": {"status": "warn", "values": {"mount_count": 8}},
                "memory": {"status": "ok", "values": {}},
                "security_baseline": {"status": "crit", "values": {}},
                "logs": {"status": "warn", "values": {}},
                "network": {"status": "ok", "values": {}},
            },
        },
    }


# --- Pure-function tests: shape_spa_context ----------------------------------


def test_shape_modules_sorts_worst_first(snapshot):
    rows = shape_modules(snapshot["diagnosis"])
    assert rows[0]["status"] == "crit"
    assert rows[0]["name"] == "security_baseline"
    # ok modules come last
    assert rows[-1]["status"] == "ok"


def test_shape_problems_sorts_crit_before_warn(snapshot):
    probs = shape_problems(snapshot["diagnosis"])
    assert [p["severity"] for p in probs] == ["crit", "warn"]
    assert probs[0]["module"] == "security_baseline"
    assert probs[0]["severity_he"] == "קריטי"


def test_shape_spa_context_counts(snapshot):
    ctx = shape_spa_context(snapshot)
    assert ctx["total_modules"] == 6
    assert ctx["counts"]["crit"] == 1
    assert ctx["counts"]["warn"] == 2
    assert ctx["counts"]["ok"] == 3
    assert ctx["defcon"] == 4
    assert ctx["defcon_label_he"] == "צהוב"
    assert ctx["problems_found"] == 2


def test_shape_spa_context_handles_none():
    ctx = shape_spa_context(None)
    assert ctx["total_modules"] == 0
    assert ctx["counts"] == {"crit": 0, "warn": 0, "ok": 0}
    assert ctx["problems"] == []
    assert ctx["has_data"] is False


def test_shape_spa_context_handles_empty_diagnosis():
    ctx = shape_spa_context({"defcon": 5})
    assert ctx["total_modules"] == 0
    assert ctx["defcon"] == 5


def test_normalizes_critical_to_crit():
    diag = {"modules": {"x": {"status": "critical"}}}
    rows = shape_modules(diag)
    assert rows[0]["status"] == "crit"


# --- Route tests: chooser + both variants ------------------------------------


def test_spa_chooser_renders(client):
    """A won the A/B, so /spa now redirects to / (the unified shell)."""
    resp = client.get("/spa", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_spa_variant_a_renders_real_data(client, snapshot):
    with patch("ipracticom_sweeper.dashboard._fetch_snapshot", return_value=snapshot):
        resp = client.get("/spa/a")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-variant="a"' in body
    # real modules from the snapshot appear
    assert "security_baseline" in body
    assert "disk" in body
    # real problem detail rendered
    assert "Security baseline drift detected" in body
    # counts reflect real data (1 crit)
    assert "מבט על המערכת" in body


def test_spa_variant_b_renders_real_data(client, snapshot):
    with patch("ipracticom_sweeper.dashboard._fetch_snapshot", return_value=snapshot):
        resp = client.get("/spa/b")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-variant="b"' in body
    assert "security_baseline" in body
    assert "Expected read-only mounts" in body
    # OKLCH tokens present (impeccable polish signature)
    assert "oklch(" in body
    # Hebrew font stack
    assert "Heebo" in body


def test_both_variants_survive_empty_snapshot(client):
    with patch("ipracticom_sweeper.dashboard._fetch_snapshot", return_value=None):
        ra = client.get("/spa/a")
        rb = client.get("/spa/b")
    assert ra.status_code == 200
    assert rb.status_code == 200
    # empty-state message in variant B problems panel
    assert "אין בעיות פעילות" in rb.get_data(as_text=True)


def test_variant_b_respects_reduced_motion(client, snapshot):
    with patch("ipracticom_sweeper.dashboard._fetch_snapshot", return_value=snapshot):
        resp = client.get("/spa/b")
    body = resp.get_data(as_text=True)
    assert "prefers-reduced-motion" in body


# --- Top nav (links to all legacy routes from the SPA variants) -----------
# Matches the legacy top-nav from templates/base.html exactly so the operator
# and the peer can reach any surface (history, approvals, settings, fleet,
# inspector, catalogue, chat) without going through the sidebar.

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
]


# Top-nav is provided by the unified shell (base_spa.html), tested in
# test_spa_shell.py. The /spa/a and /spa/b variant templates are now
# content-only — they don't carry their own top-nav. To inspect the
# shell wrapping a variant, see test_spa_shell.py::test_spa_a_and_b_still_render.


def test_top_nav_routes_all_reachable(client, snapshot):
    """Every nav link must point to a route the dashboard actually serves."""
    with patch("ipracticom_sweeper.dashboard._fetch_snapshot", return_value=snapshot):
        # /chat requires a registered chat blueprint — exercised by registering
        # in setUp; if it's not registered the test for that link would 404.
        for href, _ in NAV_LINKS:
            resp = client.get(href)
            assert resp.status_code in (200, 302), (
                f"nav link {href} returned {resp.status_code}"
            )
