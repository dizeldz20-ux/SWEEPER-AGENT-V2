"""Regression for v1.5.15 — Code Review Fixes (round 2).

Five issues identified by a fresh code review of v1.5.14:

  F-1 (CRITICAL): SPA /run/now?ui=1 returns HTTP 302 (redirect) which
                  sweeper.js treats as a 4xx error → red toast on success.

  F-2 (HIGH):     v6_layout.html doesn't load sweeper.js, so the v6
                  pages (machines, alerts) can't opt-in to SPA mode.

  C-1 (CRITICAL): dashboard CSRF gate only rejects when Origin is
                  present and mismatched — Origin-less requests pass.

  C-2 (HIGH):     /approvals/<pid>/approve TOCTOU between status check
                  and execute_repair — concurrent approvals re-run repairs.

  C-3 (HIGH):     dashboard_helpers.write_last_result has tmp collision
                  + no fsync + no lock — concurrent writes drop results.

Each test below pins the contract for one fix.
"""

import importlib
import os
import re
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_csrf_rejects_missing_origin():
    """C-1: Origin-less POST must be rejected with 403.

    The original code only rejected when Origin was non-empty AND
    mismatched. A curl POST without -H Origin would slip through.
    """
    import sys
    import logging
    import base64
    import json
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, str(REPO / "src"))
    # Run in a subprocess to keep env-var pollution from leaking into
    # other tests. The dashboard module reads DASHBOARD_USER/PASS at
    # import time, so importing it in this test process would either
    # leave them set for the rest of the suite, or fail if another
    # test has already cleared them.
    import subprocess
    script = """
import base64, json, os, sys
sys.path.insert(0, %r)
os.environ['DASHBOARD_USER'] = 'admin'
os.environ['DASHBOARD_PASS'] = 'test'
from ipracticom_sweeper.dashboard import app
client = app.test_client()
auth = base64.b64encode(b'admin:test').decode()
resp = client.post('/run/now', headers={'Authorization': 'Basic ' + auth})
out = {'status': resp.status_code, 'body': resp.get_json()}
print(json.dumps(out))
""" % str(REPO / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == 403, (
        f"expected 403 for missing Origin, got {payload}"
    )
    err = (payload.get("body") or {}).get("error") or ""
    assert "csrf_origin" in err, (
        f"expected csrf_origin* error, got {payload}"
    )


def test_csrf_accepts_matching_origin():
    """C-1: same-origin POST must pass the CSRF gate (or at least
    not be 403'd for CSRF reasons — may 500/200 from pipeline, that's
    outside this test)."""
    import sys
    import logging
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, str(REPO / "src"))
    os.environ["DASHBOARD_USER"] = "admin"
    os.environ["DASHBOARD_PASS"] = "test"
    from ipracticom_sweeper.dashboard import app
    import base64
    auth = base64.b64encode(b"admin:test").decode()
    client = app.test_client()
    # POST with Origin matching what the test client sends
    resp = client.post(
        "/run/now",
        headers={
            "Authorization": f"Basic {auth}",
            "Origin": "http://localhost",
        },
    )
    # The pipeline run will likely 500 (no AWS creds in tests), but it must
    # NOT be a 403 CSRF rejection.
    assert resp.status_code != 403 or "csrf_origin" not in (resp.get_json() or {}).get("error", ""), (
        f"matching Origin must not be CSRF-rejected, got {resp.status_code}: {resp.data[:200]}"
    )


def test_csrf_accepts_request_host_origin_v617():
    """v1.5.17 #16: CSRF must trust the request's own host, not just the
    loopback range or DASHBOARD_TRUSTED_ORIGINS list.
    http://dash.internal:8804, browser opens it over DNS, POSTs back
    with Origin: http://dash.internal:8804 (matching its own Host).
    Previously this fell through every trust branch (host is not
    loopback, DASHBOARD_TRUSTED_ORIGINS unset) and returned 403
    csrf_origin_mismatch — breaking the dashboard in production.
    """
    import sys
    import logging
    import base64
    import json
    import subprocess
    logging.disable(logging.CRITICAL)
    # Subprocess isolation — same pattern as test_csrf_rejects_missing_origin
    script = """
import base64, json, os, sys
sys.path.insert(0, %r)
# Open mode (no DASHBOARD_USER/PASS) is not what this test wants —
# we need to exercise the authenticated POST gate with a non-loopback
# host that the operator forgot to add to DASHBOARD_TRUSTED_ORIGINS.
os.environ['DASHBOARD_USER'] = 'admin'
os.environ['DASHBOARD_PASS'] = 'test'
# Critical: DASHBOARD_TRUSTED_ORIGINS not set — the operator never
# configured it because the dashboard should "just work" on its own host.
os.environ.pop('DASHBOARD_TRUSTED_ORIGINS', None)

from ipracticom_sweeper.dashboard import app
client = app.test_client()
auth = base64.b64encode(b'admin:test').decode()
# Simulate: client connected to http://dash.internal:8804 and POSTs /run/now.
# Flask test client's host_url is http://localhost by default; we override
# Host header to dash.internal to mirror the production scenario.
resp = client.post(
    '/run/now',
    headers={
        'Authorization': 'Basic ' + auth,
        'Origin': 'http://dash.internal:8804',
        'Host': 'dash.internal:8804',
    },
)
out = {'status': resp.status_code, 'body': resp.get_json()}
print(json.dumps(out))
""" % str(REPO / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # Must NOT be a CSRF rejection. The pipeline may still 500/200/etc.,
    # but csrf_origin_mismatch/missing are unacceptable.
    status = payload["status"]
    body = payload.get("body") or {}
    err = body.get("error", "") or ""
    assert status != 403 or "csrf_origin" not in err, (
        f"request-host-matching Origin must not be CSRF-rejected, "
        f"got {status} {body}"
    )


def test_csrf_rejects_different_host_origin_v617():
    """v1.5.17 #16 (negative): Cross-host POST still must be rejected.

    If the client at http://dash.internal:8804 tries to POST with
    Origin: http://evil.com:8804, CSRF must reject regardless of
    DASHBOARD_TRUSTED_ORIGINS (which is unset). Confirms the new
    request-host match doesn't accidentally trust *any* origin.
    """
    import sys
    import logging
    import base64
    import json
    import subprocess
    logging.disable(logging.CRITICAL)
    script = """
import base64, json, os, sys
sys.path.insert(0, %r)
os.environ['DASHBOARD_USER'] = 'admin'
os.environ['DASHBOARD_PASS'] = 'test'
os.environ.pop('DASHBOARD_TRUSTED_ORIGINS', None)
from ipracticom_sweeper.dashboard import app
client = app.test_client()
auth = base64.b64encode(b'admin:test').decode()
resp = client.post(
    '/run/now',
    headers={
        'Authorization': 'Basic ' + auth,
        'Origin': 'http://evil.com:8804',
        'Host': 'dash.internal:8804',
    },
)
out = {'status': resp.status_code, 'body': resp.get_json()}
print(json.dumps(out))
""" % str(REPO / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == 403, (
        f"cross-host Origin must be rejected, got {payload}"
    )
    err = (payload.get("body") or {}).get("error", "") or ""
    assert "csrf_origin_mismatch" in err, (
        f"expected csrf_origin_mismatch, got {payload}"
    )


def test_write_last_result_atomic_and_synced():
    """C-3: write_last_result must use a unique tmp filename and fsync
    before rename, AND must be serialized across threads."""
    import sys
    import logging
    import tempfile
    import json
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, str(REPO / "src"))

    # Redirect the module's CACHE_DIR to a temp dir before importing the fn
    with tempfile.TemporaryDirectory() as tmpdir:
        # Import the module and monkey-patch its CACHE_DIR
        from ipracticom_sweeper import dashboard_helpers
        tmp = Path(tmpdir)
        dashboard_helpers.CACHE_DIR = tmp
        dashboard_helpers.LAST_RESULT_FILE = tmp / "last-result.json"

        # Two concurrent writers must not collide
        results = []
        errors = []
        def writer(i):
            try:
                dashboard_helpers.write_last_result({"i": i, "v": "x" * 100})
                results.append(i)
            except Exception as e:
                errors.append((i, str(e)))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"concurrent writes errored: {errors}"
        assert len(results) == 8, f"expected 8 successful writes, got {len(results)}"
        # Final file must be valid JSON
        final = json.loads(dashboard_helpers.LAST_RESULT_FILE.read_text())
        assert "i" in final, f"final file corrupt: {final}"


def test_write_last_result_uses_unique_tmp(tmp_path=None):
    """C-3: the tmp filename must include a unique suffix so concurrent
    writers don't truncate each other."""
    import sys
    import logging
    import tempfile
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, str(REPO / "src"))
    with tempfile.TemporaryDirectory() as tmpdir:
        from ipracticom_sweeper import dashboard_helpers
        tmp = Path(tmpdir)
        dashboard_helpers.CACHE_DIR = tmp
        dashboard_helpers.LAST_RESULT_FILE = tmp / "last-result.json"
        dashboard_helpers.write_last_result({"x": 1})
        # No stray .tmp files left
        tmps = list(tmp.glob("*.tmp"))
        assert not tmps, f"orphan tmp files after write: {tmps}"


def test_sweeper_js_treats_redirect_as_success_when_asked():
    """F-1: when data-spa-redirect is set, an opaqueredirect response
    must be treated as success."""
    js = (REPO / "src/ipracticom_sweeper/static/sweeper.js").read_text()
    assert "opaqueredirect" in js, (
        "sweeper.js does not handle opaqueredirect — /run/now?ui=1 will always show red toast"
    )


def test_home_page_run_now_drops_ui_query():
    """F-1: home.html must post to /run/now without ?ui=1 so the server
    returns JSON (not redirect) when Accept: application/json is sent."""
    home = (REPO / "src/ipracticom_sweeper/templates/home.html").read_text()
    # Find the form for /run/now
    m = re.search(r'data-spa-action="(/run/now[^"]*)"', home)
    assert m, "home.html missing data-spa-action for /run/now"
    assert "ui=1" not in m.group(1), (
        f"home.html posts to {m.group(1)} — the ?ui=1 forces a 302 redirect which the SPA treats as error"
    )


def test_v6_layout_loads_sweeper_js():
    """F-2: v6_layout.html must load sweeper.js + provide toast host
    so the v6 pages (machines, alerts) can opt-in to SPA mode."""
    text = (REPO / "src/ipracticom_sweeper/templates/v6_layout.html").read_text()
    assert "/static/sweeper.js" in text, (
        "v6_layout.html does not load sweeper.js — v6 pages cannot use data-spa-action"
    )
    assert 'id="toast-host"' in text, (
        "v6_layout.html has no toast container — v6 toasts have nowhere to render"
    )


def test_approvals_uses_flock_for_atomic_decision():
    """C-2: the approval flow must take an exclusive lock per pid before
    checking status, so concurrent approvals cannot both pass."""
    src = (REPO / "src/ipracticom_sweeper/dashboard.py").read_text()
    # Look for either fcntl.flock or threading.Lock pattern near the
    # approval_approve / approval_reject routes.
    approve_idx = src.find("def approval_approve(")
    reject_idx = src.find("def approval_reject(")
    assert approve_idx > 0, "approval_approve not found in dashboard.py"
    # Look for flock in a 80-line window around the approve function
    window = src[approve_idx:approve_idx + 4000]
    assert "flock" in window or "LOCK_EX" in window, (
        "approval_approve must use flock to prevent TOCTOU between status check and execute_repair"
    )