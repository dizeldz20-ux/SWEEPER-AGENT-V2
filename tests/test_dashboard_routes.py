"""Tests for dashboard (Flask test_client, no real HTTP)."""
import pytest
from unittest.mock import patch, MagicMock
from ipracticom_sweeper.dashboard import app, _read_last_result, _write_last_result


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert "server_id" in data


def test_index_loads(client):
    r = client.get("/")
    assert r.status_code == 200


def test_history_loads(client):
    r = client.get("/history")
    assert r.status_code == 200


def test_run_view_get(client):
    fake = {"defcon": 5, "server": "h1", "defcon_label": "green"}
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=fake):
        r = client.get("/run")
    assert r.status_code == 200
    assert r.get_json()["defcon"] == 5


def test_run_view_get_no_cache(client):
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=None):
        r = client.get("/run")
    assert r.status_code == 404


def test_api_snapshot_no_data(client):
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=None):
        r = client.get("/api/snapshot")
    assert r.status_code == 404


def test_api_snapshot_with_data(client):
    fake = {"defcon": 4, "server": "h1", "defcon_label": "yellow"}
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=fake):
        r = client.get("/api/snapshot")
    assert r.status_code == 200
    data = r.get_json()
    assert data["defcon"] == 4


def test_index_with_result(client):
    fake = {
        "defcon": 3,
        "defcon_label": "orange",
        "server": "h1",
        "modules": {"cpu": {"status": "warn"}},
        "thresholds": {},
        "diagnosis": {"summary": "test", "problems": []},
        "problems_found": 0,
        "repairs_attempted": 0,
        "repairs_succeeded": 0,
    }
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=fake):
        r = client.get("/")
    assert r.status_code == 200
