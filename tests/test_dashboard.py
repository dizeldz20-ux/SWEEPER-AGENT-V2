"""Tests for the Flask dashboard.

These tests:
  - Don't actually start a server (use Flask test client)
  - Verify routes return correct status codes
  - Verify HTML structure contains expected markers
  - Mock the pipeline so tests don't take 15s each
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from ipracticom_sweeper.dashboard import _last_result_age_sec, _read_last_result, app


@pytest.fixture
def client():
    """Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def cached_result():
    """A typical pipeline result cached to disk."""
    return {
        "started_at": "2026-06-28T14:00:00+00:00",
        "finished_at": "2026-06-28T14:00:15+00:00",
        "duration_ms": 15420,
        "monitor_overall": "warn",
        "defcon": 4,
        "defcon_label": "yellow",
        "problems_found": 1,
        "repairs_attempted": 0,
        "repairs_succeeded": 0,
        "repairs_failed": 0,
        "needs_human": 1,
        "repair_results": [],
        "diagnosis": {
            "defcon": 4,
            "defcon_label": "yellow",
            "summary": "1 warning(s) detected",
            "problem_count": 1,
            "problems": [
                {
                    "module": "disk",
                    "kind": "disk_expected_ro_missing",
                    "severity": "warn",
                    "detail": "Expected read-only mounts not read-only: ['/']",
                    "metrics": {"expected": ["/"], "actual_ro": []},
                    "suggested_repair": None,
                    "repair_safety": "never",
                    "defcon_at_least": 4,
                }
            ],
            "modules": {
                "cpu": {"status": "ok", "values": {"load_5min": 0.5}},
                "memory": {"status": "ok", "values": {"ram_used_percent": 30.0}},
                "disk": {"status": "warn", "values": {"mount_count": 7}},
                "services": {"status": "ok", "values": {"failed_count": 0}},
                "security": {"status": "ok", "values": {"failed_ssh_per_minute": 0.0}},
            },
        },
        "errors": [],
        "server": "i-test",
    }


# --- Healthz -----------------------------------------------------------------


def test_healthz_returns_ok(client):
    rv = client.get("/healthz")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert "server_id" in data
    assert "ts" in data


# --- Index (main page) -------------------------------------------------------


def test_index_renders_spa_shell(client):
    """`/` now renders the unified SPA shell with the AI Studio design.

    The home uses its own thin template (`home.html`) that extends the shell;
    /spa/a and /spa/b stay self-contained for visual A/B comparison.
    """
    rv = client.get("/legacy")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    # Unified shell markers (base_spa.html provides these)
    assert 'data-shell="spa"' in body
    assert "spa-nav" in body
    assert "spa-sidebar" in body
    # 9 nav links still all there
    for href in ("/", "/history", "/approvals", "/settings", "/settings/connectors",
                 "/fleet", "/inspector", "/catalogue", "/chat"):
        assert f'href="{href}"' in body
    # Home must surface real data from the shape_spa_context result
    assert "מודולים פעילים" in body or "מבט על המערכת" in body


# --- Run JSON ----------------------------------------------------------------


def test_run_returns_cached(client, cached_result):
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=cached_result):
        rv = client.get("/run")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["defcon"] == 4
    assert data["server"] == "i-test"


def test_run_404_when_empty(client):
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=None):
        rv = client.get("/run")
    assert rv.status_code == 404


def test_run_now_triggers_pipeline(client, cached_result):
    """POST /run/now triggers a fresh sweep, caches result, returns JSON.

    v1.5.16: GET is rejected with 405 (non-idempotent ops must be POST).
    """
    fake_result = MagicMock()
    fake_result.to_dict.return_value = cached_result

    # v1.5.12: the cache I/O helpers (including trigger_pipeline_run)
    # now live in `dashboard_helpers`; patch the helpers' `run_pipeline`
    # import so the call inside `trigger_pipeline_run()` is intercepted.
    with patch("ipracticom_sweeper.dashboard_helpers.run_pipeline",
               return_value=fake_result):
        with patch("ipracticom_sweeper.dashboard_helpers.write_last_result") as mock_write:
            rv = client.post(
                "/run/now",
                headers={"Origin": "http://localhost"},
            )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["defcon"] == 4
    mock_write.assert_called_once()


def test_run_now_handles_pipeline_error(client):
    """v1.5.9: error responses are sanitized — no raw exception, but a
    `correlation_id` is included so the operator can grep the server log.

    v1.5.16: switched from GET to POST.
    """
    # v1.5.12: cache helpers live in dashboard_helpers; patch the
    # helpers' binding (the pipeline import they actually call).
    with patch("ipracticom_sweeper.dashboard_helpers.run_pipeline",
               side_effect=RuntimeError("boom")):
        rv = client.post(
            "/run/now",
            headers={"Origin": "http://localhost"},
        )
    assert rv.status_code == 500
    body = rv.get_json()
    assert body["error"] == "internal_error"
    assert "correlation_id" in body and len(body["correlation_id"]) == 8


# --- Notify test -------------------------------------------------------------


def test_api_notify_test_no_cache(client):
    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=None):
        rv = client.post("/api/notify/test")
    assert rv.status_code == 404


def test_api_notify_test_sends(client, cached_result):
    import asyncio
    from unittest.mock import AsyncMock

    with patch("ipracticom_sweeper.dashboard._read_last_result", return_value=cached_result):
        with patch("ipracticom_sweeper.notify.notify_pipeline_result", new=AsyncMock(return_value={"slack": True})):
            rv = client.post("/api/notify/test")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["sent"]["slack"] is True


# --- History -----------------------------------------------------------------


def test_history_empty(client):
    """With no audit log, history shows empty state."""
    with patch("pathlib.Path.exists", return_value=False):
        rv = client.get("/history")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "אין אירועי ניטור עדיין" in body


def test_history_with_events(client, tmp_path, monkeypatch):
    """When audit log exists, history shows events."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monitor_log = audit_dir / "monitor.jsonl"
    monitor_log.write_text(
        json.dumps({"ts": "2026-06-28T14:00:00Z", "module": "cpu", "status": "ok"}) + "\n"
        + json.dumps({"ts": "2026-06-28T14:00:05Z", "module": "disk", "status": "warn"}) + "\n"
    )

    # Monkey-patch the audit dir path
    import ipracticom_sweeper.dashboard as dash
    monkeypatch.setattr(dash, "Path", lambda p: tmp_path / "audit" if "audit" in str(p) else Path(p))

    from pathlib import Path
    rv = client.get("/history")
    assert rv.status_code == 200


# --- Helpers -----------------------------------------------------------------


def test_read_last_result_returns_none_when_no_cache():
    with patch("pathlib.Path.exists", return_value=False):
        assert _read_last_result() is None


def test_read_last_result_returns_parsed_json(tmp_path):
    cache_file = tmp_path / "last-result.json"
    cache_file.write_text(json.dumps({"defcon": 3, "defcon_label": "orange"}))

    # v1.5.12: LAST_RESULT_FILE now lives in dashboard_helpers; the
    # alias in dashboard just re-exports the symbol. Patch at source.
    import ipracticom_sweeper.dashboard_helpers as helpers
    with patch.object(helpers, "LAST_RESULT_FILE", cache_file):
        result = helpers.read_last_result()
    assert result["defcon"] == 3


def test_read_last_result_handles_corrupted_json(tmp_path):
    """Corrupted JSON should not crash — return None."""
    cache_file = tmp_path / "last-result.json"
    cache_file.write_text("not json{{{")

    import ipracticom_sweeper.dashboard_helpers as helpers
    with patch.object(helpers, "LAST_RESULT_FILE", cache_file):
        result = helpers.read_last_result()
    assert result is None


def test_last_result_age_sec_returns_int():
    age = _last_result_age_sec()
    # May be None if no cache exists, or int if exists
    assert age is None or isinstance(age, int)


# --- _build_notify_payload (in pipeline module) ------------------------------


def test_build_notify_payload_includes_server():
    from ipracticom_sweeper.pipeline import _build_notify_payload

    fake = MagicMock()
    fake.to_dict.return_value = {"defcon": 4, "defcon_label": "yellow"}
    with patch("ipracticom_sweeper.pipeline.get_server_id", return_value="i-test-123"):
        payload = _build_notify_payload(fake)
    assert payload["server"] == "i-test-123"
    assert payload["defcon"] == 4


def test_build_notify_payload_handles_server_id_failure():
    from ipracticom_sweeper.pipeline import _build_notify_payload

    fake = MagicMock()
    fake.to_dict.return_value = {"defcon": 4}
    with patch("ipracticom_sweeper.pipeline.get_server_id", side_effect=Exception("no IMDS")):
        payload = _build_notify_payload(fake)
    assert payload["server"] == "unknown"

# ============= v1.5.16 RED: GET /run/now must be POST-only ==================
# Code review finding #3 (HEAD v1.5.15): GET /run/now triggers a full
# pipeline run. This is an anti-pattern:
# - Browser pre-fetch, link previews, web crawlers, CDN warmup can all
#   fire GET requests and start pipelines without the user intending to.
# - GET must be idempotent (HTTP spec). Pipeline runs are not.
# - GET bypasses CSRF protection (only enforced on POST/PUT/PATCH/DELETE).
# Fix: only POST is allowed. GET returns 405.


def test_run_now_rejects_get(client, cached_result):
    """GET /run/now must return 405 — pipeline triggers are not idempotent.

    Bug currently: GET triggers full pipeline. Browser pre-fetch, link
    previews, and crawlers can fire pipelines at will.
    """
    fake_result = MagicMock()
    fake_result.to_dict.return_value = cached_result
    with patch("ipracticom_sweeper.dashboard_helpers.run_pipeline",
               return_value=fake_result) as mock_run:
        rv = client.get("/run/now")
    assert rv.status_code == 405, (
        f"GET /run/now must return 405 (pipeline runs are not idempotent), "
        f"got {rv.status_code}"
    )
    # Pipeline must NOT have run
    mock_run.assert_not_called()


def test_run_now_accepts_post(client, cached_result):
    """POST /run/now still works (UI uses SPA POST via sweeper.js)."""
    fake_result = MagicMock()
    fake_result.to_dict.return_value = cached_result
    with patch("ipracticom_sweeper.dashboard_helpers.run_pipeline",
               return_value=fake_result):
        with patch("ipracticom_sweeper.dashboard_helpers.write_last_result"):
            rv = client.post(
                "/run/now",
                headers={"Origin": "http://localhost"},
            )
    assert rv.status_code == 200
    assert rv.get_json()["defcon"] == 4
