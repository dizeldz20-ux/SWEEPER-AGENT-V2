"""v1.4.0 Slice 4 — Dashboard routes for per-host config and modules.

Tests the REST endpoints that surface the slice 1+2+3 work
(``host_config``, ``module_registry``) over the agent API. All tests
run in OPEN mode (no ``AGENT_API_TOKEN``) so they exercise the real
``create_app()`` without auth dance; auth behaviour is covered in
``test_agent_api_endpoints.py``.
"""
from __future__ import annotations

import pytest

from ipracticom_sweeper.agent_api import create_app
from ipracticom_sweeper.config import host_config as hc
from ipracticom_sweeper.config import module_registry as mr
from ipracticom_sweeper.config.paths import ROOT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """A Flask test client with state isolated to tmp_path.

    Also forces ``discover_modules`` to read from the freshly-written
    module catalog (avoids cross-test drift accumulation from earlier
    test runs that may have left catalog edits).
    """
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    ROOT.cache_clear()
    hc._DB_PATH = None
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    ROOT.cache_clear()
    hc._DB_PATH = None


# ---------------------------------------------------------------------------
# /api/hosts
# ---------------------------------------------------------------------------

def test_30_4_api_hosts_empty_returns_empty_list(client):
    """No hosts yet -> []."""
    r = client.get("/api/hosts")
    assert r.status_code == 200
    assert r.get_json() == []


def test_30_4_api_hosts_lists_seeded_hosts(client):
    """Seed two hosts via the engine, then list them via the API."""
    hc.add_suppression("alpha", "rule_x", reason="x")
    hc.add_suppression("beta", "rule_y", reason="y")
    r = client.get("/api/hosts")
    assert r.status_code == 200
    names = {h["name"] for h in r.get_json()}
    assert names == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# /api/hosts/<name>
# ---------------------------------------------------------------------------

def test_30_4_api_hosts_detail_returns_full_config(client):
    """GET on a known host returns monitors / repairs / runbooks /
    suppressions / enabled / description."""
    defaults = mr.default_host_config("gamma")
    cfg = hc.HostConfig(
        name="gamma",
        description=defaults.get("description", ""),
        enabled=defaults.get("enabled", True),
        monitors=[
            hc.MonitorConfig(name=m["name"],
                             enabled=m.get("enabled", True),
                             interval_sec=m.get("interval_sec", 60),
                             settings={k: v for k, v in m.items()
                                       if k not in ("name", "enabled", "interval_sec")})
            for m in defaults.get("monitors", [])
        ],
        repairs=[
            hc.RepairConfig(name=r["name"],
                            enabled=r.get("enabled", True),
                            require_approval=r.get("require_approval", True),
                            settings={k: v for k, v in r.items()
                                      if k not in ("name", "enabled", "require_approval")})
            for r in defaults.get("repairs", [])
        ],
    )
    hc.save_host(cfg)
    r = client.get("/api/hosts/gamma")
    assert r.status_code == 200
    body = r.get_json()
    assert body["name"] == "gamma"
    assert "monitors" in body
    assert "repairs" in body
    assert "runbooks" in body
    assert "suppressions" in body
    assert isinstance(body["enabled"], bool)


def test_30_4_api_hosts_detail_404_for_unknown(client):
    r = client.get("/api/hosts/never-existed")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"


def test_30_4_api_hosts_detail_invalid_name_does_not_500(client):
    """Path-traversal / invalid host names must NOT 500. Acceptable
    responses are 400 (we reject), 404 (route doesn't match), or
    405 (Flask routing rejects because the path is not the route)."""
    r = client.get("/api/hosts/..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404, 405), f"got {r.status_code}, expected a 4xx"


# ---------------------------------------------------------------------------
# /api/hosts/<name>/suppressions  (POST)
# ---------------------------------------------------------------------------

def test_30_4_api_add_suppression_creates_host(client):
    """POST to a host that does not exist auto-creates it."""
    body = {"rule": "fs_inode_check", "until": "2099-01-01T00:00:00+00:00",
            "reason": "known issue"}
    r = client.post("/api/hosts/zzz/suppressions", json=body)
    assert r.status_code == 201
    out = r.get_json()
    assert out["rule"] == "fs_inode_check"
    assert out["reason"] == "known issue"
    # host is now persisted
    assert hc.load_host("zzz").suppressions[0].rule == "fs_inode_check"


def test_30_4_api_add_suppression_rejects_empty_rule(client):
    r = client.post("/api/hosts/aaa/suppressions",
                    json={"rule": "  ", "reason": "x"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_rule"


def test_30_4_api_add_suppression_rejects_bad_host(client):
    r = client.post("/api/hosts/has%20space/suppressions",
                    json={"rule": "r", "reason": "x"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_host"


def test_30_4_api_list_suppressions_filters_expired(client):
    """GET returns only currently-active entries (expired filtered)."""
    hc.add_suppression("delta", "perm", until=None, reason="p")
    hc.add_suppression("delta", "future",
                       until="2099-12-31T00:00:00+00:00", reason="f")
    hc.add_suppression("delta", "past",
                       until="2020-01-01T00:00:00+00:00", reason="x")
    r = client.get("/api/hosts/delta/suppressions")
    assert r.status_code == 200
    rules = {s["rule"] for s in r.get_json()}
    assert rules == {"perm", "future"}


# ---------------------------------------------------------------------------
# /api/hosts/<name>/suppressions/<rule>  (DELETE)
# ---------------------------------------------------------------------------

def test_30_4_api_delete_suppression_returns_204(client):
    hc.add_suppression("epsilon", "rule_z", reason="r")
    r = client.delete("/api/hosts/epsilon/suppressions/rule_z")
    assert r.status_code == 204
    assert hc.load_host("epsilon").suppressions == []


def test_30_4_api_delete_suppression_unknown_returns_404(client):
    r = client.delete("/api/hosts/epsilon/suppressions/no-such-rule")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/hosts/_cleanup-suppressions  (POST)
# ---------------------------------------------------------------------------

def test_30_4_api_cleanup_returns_count(client):
    hc.add_suppression("h1", "past",
                       until="2020-01-01T00:00:00+00:00", reason="x")
    hc.add_suppression("h2", "past2",
                       until="2020-01-01T00:00:00+00:00", reason="x")
    r = client.post("/api/hosts/_cleanup-suppressions")
    assert r.status_code == 200
    body = r.get_json()
    assert body["removed"] == 2


# ---------------------------------------------------------------------------
# /api/modules
# ---------------------------------------------------------------------------

def test_30_4_api_modules_returns_catalog(client):
    r = client.get("/api/modules")
    assert r.status_code == 200
    body = r.get_json()
    assert isinstance(body, list)
    assert body, "catalog should be non-empty"
    # every entry has the documented shape
    sample = body[0]
    for k in ("kind", "name", "title_en", "title_he",
              "description", "tags", "risk"):
        assert k in sample


def test_30_4_api_modules_filter_by_kind(client):
    r = client.get("/api/modules?kind=monitor")
    assert r.status_code == 200
    body = r.get_json()
    assert all(m["kind"] == "monitor" for m in body)
    assert body, "should be at least one monitor in the catalog"


def test_30_4_api_modules_filter_by_risk(client):
    r = client.get("/api/modules?risk=high")
    assert r.status_code == 200
    body = r.get_json()
    assert all(m["risk"] == "high" for m in body)


def test_30_4_api_modules_filter_available_only(client):
    """?available_only=1 strips catalog-only entries from the result."""
    r = client.get("/api/modules?available_only=1")
    assert r.status_code == 200
    body = r.get_json()
    assert all(not m["catalog_only"] for m in body), (
        f"catalog_only entries leaked: "
        f"{[m for m in body if m['catalog_only']]}"
    )


# ---------------------------------------------------------------------------
# /api/modules/<kind>/<name>
# ---------------------------------------------------------------------------

def test_30_4_api_module_detail_found(client):
    """Pick a real monitor from the catalog and look it up by URL."""
    catalog = mr.discover_modules()
    monitor = next(m for m in catalog
                   if m.kind == "monitor" and not m.catalog_only)
    r = client.get(f"/api/modules/monitor/{monitor.name}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["name"] == monitor.name
    assert body["kind"] == "monitor"


def test_30_4_api_module_detail_404_when_not_found(client):
    r = client.get("/api/modules/monitor/no-such-monitor-12345")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"
