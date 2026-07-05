"""Tests for the dashboard /fleet and /fleet/host/<name> routes."""
from __future__ import annotations

import json

import pytest

from ipracticom_sweeper.config import add_connector
from ipracticom_sweeper.config.connectors import Connector
from ipracticom_sweeper import dashboard as dash


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client with isolated state dir."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    dash.app.config["TESTING"] = True
    return dash.app.test_client()


def test_fleet_view_empty_state_links_to_connectors(client):
    resp = client.get("/fleet")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "צי שרתים" in body
    assert "הוסף connector" in body or "/settings/connectors" in body


def test_fleet_view_renders_host_card_per_connector(client):
    add_connector(Connector(name="prod-web-1", instance_id="i-111"))
    add_connector(Connector(name="prod-db-1", instance_id="i-222"))
    resp = client.get("/fleet")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "prod-web-1" in body
    assert "prod-db-1" in body
    assert "data-host=" in body  # card markup


def test_fleet_view_marks_uncollected_host_as_unavailable(client):
    add_connector(Connector(name="prod-web-1", instance_id="i-111"))
    resp = client.get("/fleet")
    body = resp.get_data(as_text=True)
    assert "defcon-1" in body  # unavailable → DEFCON 1 red


def test_fleet_view_renders_successful_collector_snapshot(client):
    add_connector(Connector(name="prod-web-1", instance_id="i-111"))
    # Simulate the collector writing a snapshot
    from ipracticom_sweeper.fleet.collector import write_snapshot
    write_snapshot("prod-web-1", {
        "available": True,
        "data": {
            "host": "prod-web-1",
            "load": {"5m": 0.3},
            "memory": {"used_percent": 30, "total_kb": 8 * 1024 * 1024},
            "disk": {"used_percent": 40},
            "failed_units": [],
            "uptime_seconds": 86400,
            "kernel": "5.15.0",
        },
    })
    resp = client.get("/fleet")
    body = resp.get_data(as_text=True)
    assert "prod-web-1" in body
    assert "defcon-5" in body  # all-healthy → green


def test_fleet_host_detail_404_when_missing(client):
    resp = client.get("/fleet/host/nonexistent")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_fleet_host_detail_returns_connector_and_snapshot(client):
    add_connector(Connector(name="prod-web-1", instance_id="i-111",
                            region="il-central-1", tags={"env": "prod"}))
    from ipracticom_sweeper.fleet.collector import write_snapshot
    write_snapshot("prod-web-1", {
        "available": True,
        "data": {
            "host": "prod-web-1",
            "load": {"5m": 0.5},
            "memory": {"used_percent": 30},
            "disk": {"used_percent": 40},
            "failed_units": [],
            "top_processes": [{"pid": 123, "name": "nginx", "cpu_percent": 2.0, "mem_percent": 1.0}],
        },
    })
    resp = client.get("/fleet/host/prod-web-1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "prod-web-1"
    assert data["connector"]["instance_id"] == "i-111"
    assert data["connector"]["tags"] == {"env": "prod"}
    assert data["snapshot"]["data"]["load"]["5m"] == 0.5
    assert data["snapshot_age_seconds"] is not None
    assert data["snapshot_age_seconds"] < 60
    # repairs and pending start empty
    assert data["repairs"] == []
    assert data["pending_approvals"] == []


def test_fleet_host_detail_includes_repair_history_field(client, tmp_path):
    """Verify the response includes the repairs field (empty when audit log missing)."""
    add_connector(Connector(name="prod-web-1", instance_id="i-111"))
    from ipracticom_sweeper.fleet.collector import write_snapshot
    write_snapshot("prod-web-1", {
        "available": True,
        "data": {"load": {"5m": 0.5}, "memory": {"used_percent": 30}, "disk": {"used_percent": 40}, "failed_units": []},
    })
    resp = client.get("/fleet/host/prod-web-1")
    data = resp.get_json()
    assert "repairs" in data
    assert isinstance(data["repairs"], list)
    assert "pending_approvals" in data
    assert isinstance(data["pending_approvals"], list)