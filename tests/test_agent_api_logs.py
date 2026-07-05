"""Tests for the v0.4.3 agent_api endpoints: /api/logs and /api/logs/download."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ipracticom_sweeper.agent_api import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def state_tmp(tmp_path, monkeypatch):
    """Redirect state dir to tmp + seed it with sample logs."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))

    audit = tmp_path / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)

    # Seed repairs.jsonl
    repairs = audit / "repairs.jsonl"
    repairs.write_text(
        json.dumps({"kind": "repair_executed", "action": "drop_caches", "status": "ok"}) + "\n"
        + json.dumps({"kind": "repair_rejected", "action": "service_restart", "status": "rejected"}) + "\n"
    )

    # Seed monitor.jsonl
    monitor = audit / "monitor.jsonl"
    monitor.write_text(
        json.dumps({"kind": "metric", "name": "cpu_percent", "value": 42.0}) + "\n"
        + json.dumps({"kind": "metric", "name": "memory_percent", "value": 67.0}) + "\n"
    )

    # Seed heartbeat.json
    (tmp_path / "heartbeat.json").write_text(json.dumps({
        "ts": 1782761586.0,
        "defcon": 4,
        "problems_found": 1,
        "repairs_attempted": 0,
    }))

    # Seed last-result.json
    (cache / "last-result.json").write_text(json.dumps({
        "defcon": 4, "server": "x", "modules": {},
    }))

    return tmp_path


# ---------------------------- /api/logs ----------------------------

def test_logs_listing_includes_every_seeded_log(client, state_tmp):
    r = client.get("/api/logs")
    assert r.status_code == 200
    body = r.get_json()
    assert body["available"] is True
    names = {log["name"] for log in body["logs"]}
    assert {"repairs", "monitor", "heartbeat", "last_result"}.issubset(names)


def test_logs_returns_tail_of_each(client, state_tmp):
    r = client.get("/api/logs?tail=10")
    assert r.status_code == 200
    body = r.get_json()
    repairs = next(log for log in body["logs"] if log["name"] == "repairs")
    assert repairs["line_count"] == 2
    assert len(repairs["tail"]) == 2
    assert repairs["tail"][0]["action"] == "drop_caches"


def test_logs_empty_state_dir_returns_available_false(client, tmp_path, monkeypatch):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path / "missing"))
    r = client.get("/api/logs")
    assert r.status_code == 200
    body = r.get_json()
    assert body["available"] is False
    assert body["logs"] == []


def test_logs_handles_missing_files_gracefully(client, state_tmp):
    """If some logs are missing, the endpoint should still return the rest."""
    (state_tmp / "audit" / "repairs.jsonl").unlink()
    r = client.get("/api/logs")
    body = r.get_json()
    names = {log["name"] for log in body["logs"]}
    assert "repairs" not in names
    assert "monitor" in names


def test_logs_truncates_tail_to_param(client, state_tmp):
    # Write 10 lines
    p = state_tmp / "audit" / "repairs.jsonl"
    p.write_text("\n".join(
        json.dumps({"kind": "k", "n": i}) for i in range(10)
    ) + "\n")
    r = client.get("/api/logs?tail=3")
    body = r.get_json()
    repairs = next(log for log in body["logs"] if log["name"] == "repairs")
    assert len(repairs["tail"]) == 3
    # The tail is the *last* 3, so the highest n is 9
    assert repairs["tail"][-1]["n"] == 9


def test_logs_requires_auth_when_token_set(state_tmp, monkeypatch):
    monkeypatch.setenv("AGENT_API_TOKEN", "tok-1")
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        assert c.get("/api/logs").status_code == 401
        assert c.get("/api/logs", headers={"Authorization": "Bearer tok-1"}).status_code == 200


# ---------------------------- /api/logs/download ----------------------------

def test_download_all_returns_combined_file(client, state_tmp):
    r = client.get("/api/logs/download")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    body = r.data.decode("utf-8")
    assert "=== repairs" in body
    assert "=== monitor" in body
    assert "=== heartbeat" in body
    assert "drop_caches" in body  # from repairs
    assert "cpu_percent" in body  # from monitor


def test_download_specific_log(client, state_tmp):
    r = client.get("/api/logs/download?name=repairs")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "drop_caches" in body
    assert "cpu_percent" not in body  # not from monitor
    # Filename should reference the specific log
    cd = r.headers.get("Content-Disposition", "")
    assert "repairs" in cd


def test_download_unknown_log_returns_404(client, state_tmp):
    r = client.get("/api/logs/download?name=does_not_exist")
    assert r.status_code == 404


def test_download_truncates_to_max_bytes(client, state_tmp):
    # Write a big file
    big = state_tmp / "audit" / "repairs.jsonl"
    big.write_text("x" * (2 * 1024 * 1024))  # 2MB

    r = client.get("/api/logs/download?name=repairs&max_bytes=1000")
    assert r.status_code == 200
    assert r.headers.get("X-Sweeper-Truncated") == "1"
    assert len(r.data) <= 1100  # 1000 + header
    assert b"truncated" in r.data


def test_download_caps_max_bytes_to_50mb(client, state_tmp):
    """A user-supplied max_bytes > 50MB is silently capped to 50MB."""
    r = client.get("/api/logs/download?max_bytes=999999999")
    # Just check it doesn't crash with a huge value
    assert r.status_code == 200


def test_download_content_type_is_text(client, state_tmp):
    r = client.get("/api/logs/download?name=monitor")
    assert r.headers.get("Content-Type", "").startswith("text/plain")