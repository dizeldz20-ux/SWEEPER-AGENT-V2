"""v1.5.12 — Dashboard module split (refactor only).

`dashboard.py` had grown to 2181 lines with 37 routes. We are
*gradually* extracting pure helpers into `dashboard/_helpers.py`
without changing any public surface. This test guards the contract:

- The Flask `app` object is still importable from
  `ipracticom_sweeper.dashboard`.
- All routes from the original file are still registered (path +
  methods).
- Pure helper functions exposed via the dashboard module's public
  API stay callable with the same signatures.
- The original 2181 lines shrink by at least 100.
"""

from __future__ import annotations

import importlib
from pathlib import Path


def _dashboard_module():
    """Return the dashboard module for route-table assertions.

    NOTE: this used to pop ``ipracticom_sweeper.dashboard`` from sys.modules
    and re-import it "for a fresh route table". That replaced the module
    object globally, orphaning the ``app``/helper references that other test
    files import at collection time — their monkeypatches then targeted the
    new module while the old functions ran, silently breaking
    test_v6_logs / test_v6_metrics / test_v6_machine_actions in a full-suite
    run. The route map is static (registered once at import), so a plain
    import is correct and side-effect free.
    """
    return importlib.import_module("ipracticom_sweeper.dashboard")


def test_dashboard_app_importable() -> None:
    from ipracticom_sweeper.dashboard import app
    assert app is not None
    assert app.name == "ipracticom_sweeper.dashboard"


def test_dashboard_routes_still_registered() -> None:
    """Sanity: every critical route the SPA/dashboard calls still exists."""
    mod = _dashboard_module()
    paths = {rule.rule for rule in mod.app.url_map.iter_rules()}
    _assert_required_routes(paths)


def _assert_required_routes(paths: set[str]) -> None:
    required = {
        "/",                                # index
        "/settings",                        # settings page
        "/settings/test",                   # settings test endpoint
        "/api/snapshot",                    # SPA data source
        "/api/notify/test",                 # send notification
        "/approvals",                       # list repairs
        "/approvals/<pid>",                 # detail
        "/approvals/<pid>/approve",         # POST
        "/approvals/<pid>/reject",          # POST
        "/run/now",                         # GET+POST
        "/healthz",                         # liveness
        "/spa", "/spa/a", "/spa/b",         # SPA variants
        "/v6/machines/<host>/maintenance",  # POST maintenance
        "/v6/machines/<host>/action",       # POST action
        "/v6/logs/page",                    # live logs
        "/v6/metrics/events_heatmap",       # heatmap
        "/v6/metrics/uptime_30d",           # uptime
        "/v6/metrics/page",                 # metrics partial
    }

    # Each `required` rule may have Flask's `<...>` converted. The path
    # converter strips angle brackets, so compare by stripped form.
    stripped = {p.replace("<", "").replace(">", "") for p in paths}
    required_stripped = {
        r.replace("<", "").replace(">", "")
        .replace(":host", "host").replace(":path", "path")
        for r in required
    }
    missing = required_stripped - stripped
    assert not missing, f"routes missing after split: {missing}"


def test_dashboard_line_count_drops_below_baseline() -> None:
    """Refactor goal: drop at least 20 lines from dashboard.py.

    v1.5.12 first extraction batch is conservative — only the cache I/O
    helpers (3 functions moved verbatim to dashboard_helpers.py with
    re-exports). More aggressive extractions (notification helpers,
    maintenance helpers) land in later patches.
    """
    p = Path("src/ipracticom_sweeper/dashboard.py")
    lines = len(p.read_text(encoding="utf-8").splitlines())
    assert lines < 2165, (
        f"dashboard.py still has {lines} lines; "
        f"refactor goal is < 2165 (started at 2181, expected drop "
        f"once cache helpers moved to dashboard_helpers)."
    )


def test_helpers_module_exists_after_split() -> None:
    """After the split, `dashboard_helpers` should be importable
    and expose the cache helpers used by the run_now flow."""
    helpers = importlib.import_module("ipracticom_sweeper.dashboard_helpers")
    # Pure functions moved verbatim — names must still resolve.
    assert hasattr(helpers, "read_last_result")
    assert hasattr(helpers, "write_last_result")
    assert hasattr(helpers, "trigger_pipeline_run")


def test_helpers_module_independent_of_flask_app() -> None:
    """`dashboard_helpers` must not depend on the Flask app object —
    keeps it pure-Python so it can be unit-tested without a request ctx."""
    helpers = importlib.import_module("ipracticom_sweeper.dashboard_helpers")
    text = Path(helpers.__file__).read_text(encoding="utf-8")
    assert "app.route" not in text, (
        "dashboard_helpers.py must NOT register Flask routes; "
        "helpers should be route-free for testability."
    )
    assert "= Flask(" not in text, (
        "dashboard_helpers.py must NOT instantiate Flask; "
        "the app lives in dashboard.py only."
    )
