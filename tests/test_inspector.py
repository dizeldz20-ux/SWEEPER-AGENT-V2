"""Tests for inspector routes (v0.5.0 slice 1.1 — additive)."""
import pytest
from unittest.mock import patch
from ipracticom_sweeper.dashboard import app, _summarize_module


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_inspector_view_loads_with_no_data(client):
    """Local inspector renders 200 even with no snapshot present."""
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=None):
        r = client.get("/inspector")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "מפקח בדיקות" in body
    assert "localhost" in body


def test_inspector_view_renders_modules(client):
    """Modules from local snapshot are rendered as table rows."""
    snap = {
        "modules": {
            "cpu": {"status": "ok", "values": {"cpu.idle_percent": 85.0}},
            "memory": {"status": "warn", "values": {"memory.used_percent": 72.0}},
            "disk": {"status": "crit", "values": {"disk.used_percent": 95.0}},
        }
    }
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=snap):
        r = client.get("/inspector")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # All three modules present
    assert "<code>cpu</code>" in body
    assert "<code>memory</code>" in body
    assert "<code>disk</code>" in body
    # Status pills rendered
    assert "status-ok" in body
    assert "status-warn" in body
    assert "status-crit" in body
    # Summaries include human-readable label
    assert "idle" in body and "used" in body


def test_inspector_view_empty_state(client):
    """Empty snapshot still renders a clean page, not a 500."""
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value={"modules": {}}):
        r = client.get("/inspector")
    assert r.status_code == 200
    assert "אין נתוני בדיקות" in r.get_data(as_text=True)


def test_inspector_host_404_when_unknown(client):
    """Unknown host returns JSON 404, not a crash."""
    r = client.get("/inspector/host/does-not-exist")
    assert r.status_code == 404
    data = r.get_json()
    assert data["error"] == "not_found"


def test_inspector_host_renders_known_host(client):
    """Known host returns 200 with module table."""
    from ipracticom_sweeper.config import Connector

    snap_entry = {
        "name": "test-host",
        "collected_at": 1000000000,
        "snapshot": {
            "modules": {
                "cpu": {"status": "ok", "values": {"cpu.idle_percent": 90.0}},
                "services": {"status": "warn", "values": {"freeswitch.running": False}},
            }
        },
    }
    connectors = [Connector(name="test-host", instance_id="i-abc", region="il-central-1")]
    with patch("ipracticom_sweeper.config.get_connector") as gc, \
         patch("ipracticom_sweeper.fleet.load_snapshot", return_value=snap_entry), \
         patch("ipracticom_sweeper.config.load_connectors", return_value=connectors):
        gc.return_value = connectors[0]
        r = client.get("/inspector/host/test-host")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "test-host" in body
    assert "cpu" in body
    assert "services" in body


def test_summarize_module_prefers_known_metrics():
    """Summarizer uses known scalar metric labels."""
    m = {"status": "ok", "values": {"cpu.idle_percent": 87.3}}
    assert "87" in _summarize_module(m) or "88" in _summarize_module(m)


def test_summarize_module_falls_back_to_first_value():
    """Unknown metric → uses first numeric or string value."""
    m = {"status": "ok", "values": {"weird.metric": 42}}
    assert "weird.metric" in _summarize_module(m)


def test_summarize_module_handles_empty_values():
    """Empty values dict → returns module status string."""
    m = {"status": "crit", "values": {}}
    assert _summarize_module(m) == "crit"


def test_base_html_links_to_inspector(client):
    """Nav menu exposes inspector link."""
    r = client.get("/")
    body = r.get_data(as_text=True)
    assert url_for_inspector_in_nav(body) is True


def url_for_inspector_in_nav(body: str) -> bool:
    return 'href="/inspector"' in body or "url_for('inspector_view')" in body
