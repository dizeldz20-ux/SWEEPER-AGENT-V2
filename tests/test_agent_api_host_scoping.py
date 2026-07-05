"""Per-machine scoping for /api/predictions and /api/evidence/export.

The machine console opens one floating panel per machine and passes ?host=<id>
so predictions/evidence reflect THAT machine, not the whole fleet. These tests
lock in that the endpoints honour the query host and fall back to the env host
id for backward compatibility.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ipracticom_sweeper.agent_api import create_app


@pytest.fixture
def client():
    """A Flask test client with auth disabled (token empty = OPEN mode)."""
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _seed_disk_trend(db_path: Path, host: str) -> None:
    """Write a rising disk-usage series that yields a crossing prediction."""
    from ipracticom_sweeper.storage import TimeSeriesDB

    db = TimeSeriesDB(db_path)
    # 5 days of disk growing 50% -> 70% (mirrors test_predict_integration).
    for day in range(5):
        ts = int(time.time()) - (5 - day) * 86400
        db.write(host=host, metric="disk.used_percent./", value=50.0 + day * 5, ts=ts)
    db.close()


# ---------------------------- /api/predictions ----------------------------

def test_predictions_scoped_to_query_host(client, tmp_path, monkeypatch):
    """?host= selects the machine; a host with no samples gets no predictions."""
    db_path = tmp_path / "metrics.db"
    _seed_disk_trend(db_path, host="host-a")
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))

    # host-a has a rising disk trend -> at least one prediction, echoed host.
    r = client.get("/api/predictions?host=host-a")
    assert r.status_code == 200
    body = r.get_json()
    assert body["host"] == "host-a"
    assert body["count"] >= 1
    assert any("disk" in (p.get("metric") or "") for p in body["predictions"])

    # host-b has no samples -> scoped query returns an empty prediction set.
    r = client.get("/api/predictions?host=host-b")
    assert r.status_code == 200
    body = r.get_json()
    assert body["host"] == "host-b"
    assert body["predictions"] == []


def test_predictions_falls_back_to_env_host(client, tmp_path, monkeypatch):
    """Omitting ?host= keeps the legacy behaviour: use the env host id."""
    db_path = tmp_path / "metrics.db"
    _seed_disk_trend(db_path, host="envhost")
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("IPRACTICOM_SWEEPER_HOST_ID", "envhost")

    r = client.get("/api/predictions")
    assert r.status_code == 200
    body = r.get_json()
    assert body["host"] == "envhost"
    assert body["count"] >= 1


# ---------------------------- /api/evidence/export ----------------------------

def test_evidence_export_filters_audit_by_host(client, tmp_path, monkeypatch):
    """Audit entries are scoped by host; untagged entries belong to the local host."""
    audit_log = tmp_path / "audit" / "repairs.jsonl"
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    lines = [
        {"ts": now, "host": "host-a", "kind": "repair_executed", "module": "disk"},
        {"ts": now, "host": "host-b", "kind": "repair_executed", "module": "mem"},
        {"ts": now, "kind": "repair_executed", "module": "legacy-untagged"},
    ]
    audit_log.write_text("\n".join(json.dumps(x) for x in lines) + "\n")

    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    # The local agent is host-a; untagged legacy entries belong to it.
    monkeypatch.setenv("IPRACTICOM_SWEEPER_HOST_ID", "host-a")

    # host-a: its own tagged entry + the untagged legacy one.
    r = client.get("/api/evidence/export?host=host-a&format=json")
    assert r.status_code == 200
    body = r.get_json()
    assert body["host"] == "host-a"
    modules = {e.get("module") for e in body["audit_entries"]}
    assert modules == {"disk", "legacy-untagged"}

    # host-b: only its own tagged entry; the untagged one is NOT the local host.
    r = client.get("/api/evidence/export?host=host-b&format=json")
    assert r.status_code == 200
    body = r.get_json()
    assert body["host"] == "host-b"
    modules = {e.get("module") for e in body["audit_entries"]}
    assert modules == {"mem"}
