"""Regression for v1.5.14 — Progressive SPA Wiring.

The dashboard has been pure server-rendered HTML until now. This release
adds a tiny vanilla-JS layer (no build step) that progressively enhances
forms so they POST via fetch + show a toast instead of doing a full
page reload.

Contract tested here:
  1. The JS bundle exists and is served at /static/sweeper.js
  2. base_spa.html includes the bundle + a toast container
  3. The 3 highest-value forms opt-in via data-spa-action:
       - /run/now               (manual sweep)
       - /approvals/<pid>/approve
       - /approvals/<pid>/reject
  4. The bundle exposes the documented API surface:
       - window.sweeper.post(url, form, opts) → Promise
       - window.sweeper.toast(msg, kind)
  5. CSRF: the JS reads the origin from <meta name="csrf-token"> or
     sends the Origin header so the v1.5.9 CSRF check accepts it.
"""

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
STATIC_JS = REPO / "src/ipracticom_sweeper/static/sweeper.js"
BASE_SPA = REPO / "src/ipracticom_sweeper/templates/base_spa.html"


def test_sweeper_js_bundle_exists():
    assert STATIC_JS.exists(), (
        f"missing JS bundle at {STATIC_JS} — the SPA layer has nothing to load"
    )
    assert STATIC_JS.stat().st_size > 500, (
        f"sweeper.js is only {STATIC_JS.stat().st_size} bytes — looks empty"
    )


def test_base_spa_includes_bundle_and_toast_host():
    text = BASE_SPA.read_text()
    assert "/static/sweeper.js" in text, (
        "base_spa.html does not load sweeper.js — progressive wiring will not boot"
    )
    assert "id=\"toast-host\"" in text or "id='toast-host'" in text, (
        "base_spa.html has no toast container — toasts have nowhere to render"
    )


def test_sweeper_js_exposes_post_and_toast_api():
    text = STATIC_JS.read_text()
    # Window export
    assert re.search(r"window\.sweeper\s*=", text), (
        "sweeper.js does not expose window.sweeper — call sites cannot use it"
    )
    assert "post(" in text, "missing post() in sweeper.js"
    assert "toast(" in text, "missing toast() in sweeper.js"


def test_sweeper_js_sends_origin_header():
    """v1.5.9 CSRF check requires Origin/Referer — the JS MUST send Origin."""
    text = STATIC_JS.read_text()
    assert re.search(r"['\"]Origin['\"]", text), (
        "sweeper.js does not send Origin header — v1.5.9 CSRF check will reject requests"
    )


def test_home_page_run_now_uses_spa_action():
    text = (REPO / "src/ipracticom_sweeper/templates/home.html").read_text()
    assert "data-spa-action" in text and "/run/now" in text, (
        "home.html does not opt-in /run/now into SPA mode — manual sweep still full-reloads"
    )


def test_approvals_uses_spa_action_for_approve_reject():
    """The two approve/reject POSTs are the highest-value UX win."""
    text = (REPO / "src/ipracticom_sweeper/templates/approvals.html").read_text()
    # approval_detail.html is where the actual buttons live
    detail = (REPO / "src/ipracticom_sweeper/templates/approval_detail.html").read_text()
    combined = text + "\n" + detail
    assert "data-spa-action" in combined, (
        "approvals.html/approval_detail.html do not opt-in approve/reject into SPA mode"
    )
    assert "approval_approve" in combined and "approval_reject" in combined, (
        "approval_approve/reject url_for calls missing from approvals templates"
    )


def test_sweeper_js_handles_json_responses():
    """The JS must parse JSON and surface ok:true / error to the toast."""
    text = STATIC_JS.read_text()
    assert "application/json" in text, "JS does not request JSON responses"
    assert "JSON.parse" in text or "response.json" in text, (
        "JS does not parse JSON responses — server's jsonify() output would be a string"
    )


def test_sweeper_js_does_not_introduce_external_runtime_deps():
    """No build step allowed — bundle must work without npm/bundler.

    Only acceptable externals: same-origin /static/* and CDN fonts already
    declared in base_spa.html.
    """
    text = STATIC_JS.read_text()
    # Disallow: unpkg, jsdelivr, esm.sh, skypack, cdn.jsdelivr, fetch-from-CDN
    bad_cdns = ("unpkg.com", "cdn.jsdelivr.net", "esm.sh", "skypack.dev")
    for cdn in bad_cdns:
        assert cdn not in text, (
            f"sweeper.js imports from {cdn} — adds a runtime CDN dependency"
        )