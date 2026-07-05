"""Tests for the v0.4.2 agent_api endpoints: /api/history, /api/approvals, /api/fleet."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ipracticom_sweeper.agent_api import create_app


@pytest.fixture
def client():
    """A Flask test client with auth disabled (token empty = OPEN mode)."""
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def pending_tmp(tmp_path, monkeypatch):
    """Redirect pending_repairs into a tmp dir."""
    pending_dir = tmp_path / "pending"
    approved = pending_dir / "approved"
    rejected = pending_dir / "rejected"
    audit = tmp_path / "audit" / "repairs.jsonl"
    from ipracticom_sweeper.repair import pending as pending_mod
    monkeypatch.setattr(pending_mod, "PENDING_DIR", pending_dir)
    monkeypatch.setattr(pending_mod, "APPROVED_DIR", approved)
    monkeypatch.setattr(pending_mod, "REJECTED_DIR", rejected)
    monkeypatch.setattr(pending_mod, "AUDIT_LOG", audit)
    return {"pending": pending_dir, "approved": approved, "rejected": rejected, "audit": audit}


# ---------------------------- /api/history ----------------------------

def test_history_catalog_empty_db_returns_empty_lists(client, tmp_path, monkeypatch):
    """With no metrics.db, /api/history returns empty catalogs + a note."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    r = client.get("/api/history")
    assert r.status_code == 200
    body = r.get_json()
    assert "metrics" in body
    assert "hosts" in body
    assert body["metrics"] == []
    assert body["hosts"] == []


def test_history_catalog_returns_distinct_metrics_and_hosts(client, tmp_path, monkeypatch):
    """Catalog deduplicates and counts samples per metric."""
    import sqlite3

    db_path = tmp_path / "metrics.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        # Match the real TimeSeriesDB schema (storage/timeseries.py): the
        # table is named `metrics`, not `samples` — the old test mirrored a
        # bug in the catalog endpoint that queried a nonexistent table.
        conn.execute(
            "CREATE TABLE metrics (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " host TEXT NOT NULL, metric TEXT NOT NULL,"
            " ts INTEGER NOT NULL, value REAL NOT NULL)"
        )
        rows = [
            ("host-a", "cpu_percent", 1000, 12.0),
            ("host-a", "cpu_percent", 2000, 22.0),
            ("host-a", "memory_percent", 2000, 50.0),
            ("host-b", "cpu_percent", 3000, 9.0),
        ]
        conn.executemany(
            "INSERT INTO metrics (host, metric, ts, value) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))

    r = client.get("/api/history")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body["metrics"]) == {"cpu_percent", "memory_percent"}
    assert set(body["hosts"]) == {"host-a", "host-b"}
    # The count for cpu should be 3 across both hosts.
    cpu_counts = [m for m in body["metrics_with_counts"] if m["metric"] == "cpu_percent"]
    assert cpu_counts
    assert cpu_counts[0]["count"] == 3


# ---------------------------- /api/approvals ----------------------------

def test_approvals_list_empty(client, pending_tmp):
    r = client.get("/api/approvals")
    assert r.status_code == 200
    body = r.get_json()
    assert body["count"] == 0
    assert body["pending"] == []


def test_approvals_list_returns_pending(client, pending_tmp):
    from ipracticom_sweeper.repair.pending import create_proposal
    p1 = create_proposal(
        action="service_restart", kwargs={"unit": "nginx"},
        reason="nginx down", proposed_command="systemctl restart nginx",
    )
    create_proposal(
        action="drop_caches", kwargs={"level": 3},
        reason="high memory", proposed_command="sync; echo 3 > /proc/sys/vm/drop_caches",
    )
    r = client.get("/api/approvals")
    assert r.status_code == 200
    body = r.get_json()
    assert body["count"] == 2
    ids = [p["id"] for p in body["pending"]]
    assert p1.id in ids


def test_approvals_approve_executes_repair(client, pending_tmp):
    """Approving a service_restart proposal should call execute_repair and archive it."""
    from ipracticom_sweeper.repair.pending import create_proposal

    # Register a fake repair so we don't actually call systemctl.
    from ipracticom_sweeper.repair import actions as actions_mod
    from ipracticom_sweeper.repair.actions import RepairResult

    @actions_mod.register("test_safe_repair")
    def _fake_safe(**kwargs):
        return RepairResult(
            action="test_safe_repair",
            target="test",
            success=True,
            snapshot_id=None,
            message="ok (test)",
            duration_ms=1,
        )

    # register() mutates the process-wide actions.REPAIRS dict. Without this
    # cleanup, "test_safe_repair" lingers for the rest of the session and
    # test_module_registry's strict drift check fails with
    # "code-only: repair:test_safe_repair not in catalog" in a full-suite run.
    try:
        p = create_proposal(
            action="test_safe_repair", kwargs={"foo": "bar"},
            reason="test", proposed_command="echo ok",
        )

        r = client.post(f"/api/approvals/{p.id}/approve")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert "result" in body
        # The proposal should now be in approved/.
        assert (pending_tmp["approved"] / f"{p.id}.json").exists()
        # And removed from pending/.
        assert not (pending_tmp["pending"] / f"{p.id}.json").exists()
    finally:
        actions_mod.REPAIRS.pop("test_safe_repair", None)


def test_approvals_approve_unknown_id_returns_404(client, pending_tmp):
    r = client.post("/api/approvals/does-not-exist/approve")
    assert r.status_code == 404


def test_approvals_reject_archives_proposal(client, pending_tmp):
    from ipracticom_sweeper.repair.pending import create_proposal

    p = create_proposal(
        action="service_restart", kwargs={"unit": "x"},
        reason="test", proposed_command="systemctl restart x",
    )
    r = client.post(
        f"/api/approvals/{p.id}/reject",
        json={"reason": "test rejection"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert (pending_tmp["rejected"] / f"{p.id}.json").exists()
    assert not (pending_tmp["pending"] / f"{p.id}.json").exists()


def test_approvals_reject_unknown_id_returns_404(client, pending_tmp):
    r = client.post("/api/approvals/no-such/reject")
    assert r.status_code == 404


# ---------------------------- /api/fleet ----------------------------

def test_fleet_list_empty_no_connectors(client, tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    r = client.get("/api/fleet")
    assert r.status_code == 200
    body = r.get_json()
    assert "hosts" in body
    assert "count" in body
    assert body["count"] >= 1  # at least the local host
    names = [h["name"] for h in body["hosts"]]
    assert "local" in names


def test_fleet_list_includes_connectors(client, tmp_path, monkeypatch):
    """Connectors with errors should appear in the fleet as 'error' status."""
    from ipracticom_sweeper.config import (
        Connector,
        add_connector,
        mark_connector_error,
    )

    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    c = Connector(
        name="prod-web-1", instance_id="i-aaaa", region="il-central-1",
        enabled=True,
    )
    add_connector(c)
    mark_connector_error("prod-web-1", "SSM unreachable")

    r = client.get("/api/fleet")
    assert r.status_code == 200
    body = r.get_json()
    names = [h["name"] for h in body["hosts"]]
    assert "prod-web-1" in names
    web_entry = next(h for h in body["hosts"] if h["name"] == "prod-web-1")
    assert web_entry["status"] == "error"
    assert "SSM unreachable" in (web_entry.get("last_error") or "")


def test_fleet_host_local_returns_heartbeat(client, tmp_path, monkeypatch):
    """The local host entry should surface heartbeat + defcon."""
    heartbeat = {
        "ts": 1782761586.0,
        "ts_iso": "2026-06-29T19:33:06+00:00",
        "defcon": 4,
        "problems_found": 1,
        "repairs_attempted": 0,
        "extra": {},
    }
    (tmp_path / "heartbeat.json").write_text(json.dumps(heartbeat))
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))

    r = client.get("/api/fleet/local")
    assert r.status_code == 200
    body = r.get_json()
    assert body["name"] == "local"
    assert body["defcon"] == 4
    assert body["problems_found"] == 1


def test_fleet_host_unknown_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    r = client.get("/api/fleet/nonexistent-host")
    assert r.status_code == 404


# ---------------------------- auth ----------------------------

def test_endpoints_require_auth_when_token_set(tmp_path, monkeypatch):
    """With AGENT_API_TOKEN set, /api/history returns 401 without bearer."""
    import os
    monkeypatch.setenv("AGENT_API_TOKEN", "secret-token-123")
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))

    # We have to rebuild the app because the token is read at create_app().
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/api/history")
        assert r.status_code == 401

        # With correct bearer, succeed.
        r = c.get("/api/history", headers={"Authorization": "Bearer secret-token-123"})
        assert r.status_code == 200

        # With wrong bearer, 401.
        r = c.get("/api/history", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401