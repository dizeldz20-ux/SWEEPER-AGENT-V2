# Changelog — iPracticom AWS Linux Sweeper

All notable changes are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [1.6.0] — 2026-07-04 — Multi-Channel Approvals + Agent Self-Monitoring

End-to-end, closed-loop repair approvals that fan out to **every** configured
channel (Telegram + Slack), plus the agent now monitors **itself** and its
notification bots — not just the fleet — and stays silent while everything is
green.

### Added
- **Multi-channel approval flow** (`notify/approvals.py`, `pipeline.py`): when a
  repair needs approval the pipeline pushes one request that fans out to all
  configured channels, deduplicates in-flight proposals per `(host, action)`,
  and closes the loop — the decision (approve/deny) and the repair result are
  pushed back to every channel.
- **Escalation on failure** (`repair/block.py`, `repair/decide.py`): a failed
  repair writes a block-marker and escalates; `/api/blocked` endpoints expose
  blocked items to the dashboard.
- **Unified approval templates** (`notify/templates.py`): one bilingual
  (Hebrew + English) template shared by all channels for request/result/escalation.
- **Slack interactive approvals** (`slack_actions/`): real Approve/Deny buttons
  via `chat.postMessage` actions, and a fixed `/approve` slash command.
- **Agent self-monitoring** (`monitor/bot_connectivity.py`,
  `monitor/self_snapshot.py`, `repair/self_repair.py`): the agent checks its own
  health and every bot's connectivity (Slack `auth.test`, Telegram `getMe`)
  every cycle, and can self-repair (`self_agent_restart`) without approval.
- **Slack health notifier** (`notify/slack_health.py`): dedicated Slack path for
  agent/bot health alerts.
- **Host-scoped agent API**: predictions, evidence and scan results are now
  scoped per host (`?host=`) — see `test_agent_api_host_scoping.py`,
  `test_run_scan_scoping.py`.
- **Frontend**: `AgentHealthPanel` (agent + bot health), a Blocked section
  (`hooks/useBlocked.ts`) in the approvals queue, and machine details inline in
  approvals. Per-host machine console consolidates predictions/evidence.
- **New test suites** for approvals, escalation, self-monitoring, the silent
  gate, host scoping and repair decisions (`tests/`).

### Changed
- **Silent-by-default notifications** (`notify/suppression.py`): a suppression
  gate keeps the agent quiet while green, deduplicates a persisting issue "until
  resolved", and throttles flapping conditions.
- `RepairProposal` gains a `server` field and `find_pending()` for dedup
  (`repair/pending.py`).
- Conditional FreeSWITCH checks: FS on a monitored host is probed only when that
  connector has `freeswitch_enabled`; the agent itself is always monitored.
- Per-host config plumbed through (`config/host_config.py`).

### Fixed
- Dashboard module line-budget kept under limit; pipeline tests isolated.
- **Vendored SPA now ships in git** (`.gitignore`): the built dashboard
  (`src/ipracticom_sweeper/webui/dist`) was swallowed by the global `dist/`
  rule, so a fresh `git clone` + `pip install -e .` (exactly what `install.sh`
  does) carried **no** SPA and `/app` served a "build the frontend" placeholder.
  Added a `.gitignore` exception and committed a fresh build so a cold install
  on a new machine gets a working, up-to-date dashboard with no npm step.

## [1.5.18] — 2026-07-03 — React SPA Dashboard + Slider-Configurable Thresholds

The private agent now ships the same React dashboard as the
ipracticom-sweeper-dashboard project, served by the Flask dashboard at
`/app`, plus a settings page where alert thresholds (disk %, memory %,
CPU load) are configured with sliders and persisted.

### Added
- **React SPA** (`frontend/`, `webui/dist/`): Vite + React 19 + Tailwind 4
  dashboard vendored into the package and served same-origin at `/app`
  (`dashboard_webui.py`). Build with `make webui`.
- **Settings JSON API** (`dashboard_webui.py`): `/api/settings/notifications`
  (GET/PUT/test), `/api/settings/thresholds` (GET/**PUT**),
  `/api/settings/filter_rules` (GET). Secrets are never returned.
- **Slider-editable thresholds**: PUT `/api/settings/thresholds` persists an
  allowlisted subset (cpu.load_avg warn/crit, memory %, disk %, inode %)
  to `$STATE_DIR/rules.local.yaml` with range + warn<crit validation.
- **Rules override file** (`config/legacy.py`): `load_rules()` now merges
  `rules.local.yaml` from the state dir on top of defaults + packaged rules,
  so UI changes survive package upgrades; `save_rules_override()` writes it
  atomically and re-validates.
- **`register_api_routes()`** (`agent_api.py`): the full agent REST API can
  now be mounted on the dashboard app (passthrough auth — Basic already
  gates it; rate limiting off; Slack + duplicate endpoints skipped). The
  standalone `create_app()` behaviour is unchanged.

### Changed
- `agent_api.py` imports the result cache from `dashboard_helpers` (not
  `dashboard`) so the dashboard can import it without a circular import.
- `pyproject.toml`/`MANIFEST.in`: package `webui/dist/**` so wheels and
  sdists carry the built SPA.

### Fixed (post-review hardening)
- **Concurrent bot-store writes** (`notify/store.py`): `add_bot`/`delete_bot`
  did a read-modify-write with no lock, so two parallel `/api/settings/bots`
  requests (the dashboard is single-process, threaded) clobbered each other —
  a bot returned 201 but silently vanished, and the shared tmp path could
  corrupt `bots.json` (`bots_read_failed`). A module-level `threading.Lock`
  now serialises the read-modify-write. Slack `channel` also gets a permissive
  format check (accepts `#name` / `C…` id / bare name; rejects URLs, spaces).
- **Rate-limit hard cap** (`agent_api.py`): `_RL_MAX_KEYS` previously only
  reclaimed *stale* buckets, so a live burst of unique keys (spoofed
  `X-Forwarded-For` / botnet) grew the bucket map without bound. A brand-new
  key is now refused once the map is over budget; keys already being tracked
  are never dropped mid-window.
- **`send_admin_alert` sends plain text first** (`notify/legacy.py`): it
  promised plain text but sent Markdown, forcing a 400 + retry round-trip for
  any content with Markdown metacharacters. It now sends plain from the start
  (`_send_telegram(..., markdown=False)`); the regular alert path keeps its
  Markdown→plain 400 fallback so a real alert is never silently dropped.
- **Missing `import sys`** (`agent_api.py`): the CLI fail-closed guard for an
  unauthenticated non-loopback bind called `sys.stderr`/`sys.exit` without
  importing `sys`, raising `NameError` instead of the intended clean refusal.
- **`--rules` help text** (`sweeper.py`): now states the default is the
  packaged `ipracticom_sweeper/rules/default.yaml`, merged over built-in
  defaults and any `rules.local.yaml` override — not the repo-root file.
- New coverage in `tests/test_v518_review_fixes.py` (rate-limit hard cap,
  Telegram 400→plain fallback, `send_admin_alert` plain-first).

## [1.5.17] — 2026-07-03 — CSRF Production Fix

One critical finding from the v1.5.16 review pass: CSRF rejected every
state-mutating POST in production deployments where the dashboard
served on a non-loopback hostname not pre-listed in
`DASHBOARD_TRUSTED_ORIGINS`. Fixed.

### Security
- **CSRF — trust the dashboard's own host** (`dashboard.py`):
  `_csrf_origin_ok()` now also accepts an `Origin` whose hostname
  matches `request.host` (auto-stripped of port). Previously, only
  loopback origins and `DASHBOARD_TRUSTED_ORIGINS` entries were
  trusted. On any production deployment bound to e.g.
  `http://dash.internal:8804`, browsers always send
  `Origin: http://dash.internal:8804`, but with no operator
  configuration that hostname was unknown to the gate — every
  save/repair POST was rejected with 403 `csrf_origin_mismatch`.
  The dashboard was unusable in production without operator
  setup. After the fix, same-host POSTs are accepted automatically
  while cross-host POSTs (Origin doesn't match `request.host`,
  e.g. `Origin: http://evil.com`) are still 403-rejected.

  `DASHBOARD_TRUSTED_ORIGINS` is no longer required for single-host
  deployments; it remains the override for operators using a
  reverse proxy, CDN, or CNAME alias.

  2 RED regression tests added to `tests/test_v615_code_review_fixes.py`:
  - `test_csrf_accepts_request_host_origin_v617` — same-host Origin
    passes (previously 403)
  - `test_csrf_rejects_different_host_origin_v617` — cross-host Origin
    still 403, confirmed the new match isn't overtrusting.

### Differential
- v1.5.16 → v1.5.17: 1 fix, 2 new tests, 0 changes to monitor pipeline,
  audit, predict, host_config, repair, or notify. Pure security fix.
- README version badge 0.6.2 → 1.5.17 (the badge had drifted from the
  real git tags since v1.0.0; this release brings it back in sync).

## [1.5.16] — 2026-07-03 — Code Review Fixes (round 3)

Seven findings from a deep code-review sweep, each fixed with a RED test
that fails on the unfixed code and passes on the fix. No behavior change
on the happy path.

### Reliability / data integrity
- **Predict — NaN/Inf/decreasing-trend safety** (`predict/analyzer.py`):
  linear-regression slope handling now skips thresholds when the slope is
  NaN, Inf, or ≤ 0 (decreasing usage, not a future breach). Without this,
  a noisy-but-stable series could emit a false "disk full by Tuesday"
  prediction; with this, only genuinely-rising series emit. 5 RED tests.
- **Audit log — atomic writes + thread lock** (`audit/logger.py`):
  `_write_atomic` now writes to a UUID-8 tmp file, `fsync`'s the data and
  the directory entry, then `os.replace()`. A `threading.Lock` serializes
  the rename so two concurrent audit events no longer truncate each
  other. On a busy dashboard this was the difference between a clean audit
  trail and a missing one. 3 RED tests.
- **host_config — atomic save** (`config/host_config.py`): same pattern
  as audit: UUID-8 tmp + `fsync` + `os.replace`. Two concurrent
  `/api/host_config` saves no longer race to overwrite each other. 5 RED
  tests.

### Security
- **Dashboard — `GET /run/now` removed** (`dashboard.py`): the route now
  rejects GET with HTTP 405. Previously, a `curl -X GET /run/now` with
  valid basic-auth credentials could trigger a full monitor sweep — a
  CSRF amplifier. POST is the only accepted method; browsers always use
  GET for navigation, so legitimate flows are unaffected. 5 RED tests.

### Repair surface
- **Repair actions — register reboot / agent_restart / ssm_connect**
  (`repair/actions.py`): three actions that the dashboard UI offered but
  the orchestrator never registered. Clicking "reboot machine" did
  nothing silently. All three are now registered in `actions_index`.
  4 RED tests.

### Concurrency / startup
- **Notification — explicit `AsyncClient` cleanup** (`notify/legacy_notify.py`):
  the catch-all `finally` now closes any lingering `httpx.AsyncClient`,
  so a cancelled task mid-send no longer leaks an open socket that
  `python -X dev` would flag at shutdown. Defence-in-depth alongside the
  existing per-call close. 2 RED tests.

### Monitor pipeline — all-or-nothing safety
- **Monitor — per-module exception isolation** (`monitor/checks.py`):
  every module's `(collect, evaluate)` pair is now wrapped in
  `_safe_module()`. A single failure (disk I/O error, broken permissions,
  AWS throttle) records `warn` + an `error` field in that module's slot
  instead of aborting the rest of the pipeline. Without this, a hung
  `disk.collect()` would lose ALL modules — cpu, memory, services,
  everything after the failure. Failed modules are skipped in audit
  emission and in the AWS-only-when-available conditional, so a
  half-broken run is observable and bounded instead of catastrophic.
  2 RED tests (`test_run_all_continues_when_module_raises`,
  `test_run_all_returns_partial_when_late_module_raises`).

### Test changes
- 26 RED contract tests across 7 modules, all GREEN. Tests live in
  their respective module test files:
  - `tests/test_predict_integration.py` (5) — NaN/Inf/decreasing-trend
  - `tests/test_audit_logger.py` (3) — atomic writes + thread lock
  - `tests/test_host_config.py` (5) — atomic save race + fsync
  - `tests/test_repair_actions_extra.py` (4) — register reboot/agent_restart/ssm_connect
  - `tests/test_dashboard.py` (5) — POST-only /run/now
  - `tests/test_notify_pipeline.py` (2) — AsyncClient cleanup
  - `tests/test_catalogue_missing_modules.py` (2) — run_all survives module errors

### Differential
- v1.5.15 → v1.5.16: 7 fixes, 24 new tests, 1 file rewritten for
  the per-module isolation wrapper (no new public API).

## [1.5.15] — 2026-07-03 — Code Review Fixes (round 2)

### Security
- **C-1 (CRITICAL) — CSRF on missing Origin**: `dashboard._require_basic_auth`
  now rejects state-mutating requests (`POST/PUT/PATCH/DELETE`) with HTTP
  403 and `error: csrf_origin_missing` when the `Origin` header is absent.
  The previous gate (`if origin and not _csrf_origin_ok(origin)`) was a
  pass-through for any HTTP client that omits Origin — a curl POST with
  valid basic-auth credentials bypassed CSRF entirely. Browsers always
  send Origin on POST, so legitimate flows are unaffected.
- **CSRF comment** updated to explain why the absent-Origin branch is
  now closed: the only clients that omit Origin are scripts/curl, both
  trivially fixable to include it.

### Concurrency / atomicity
- **C-2 (HIGH) — TOCTOU on `/approvals/<pid>/(approve|reject)`**: both
  routes now acquire a per-proposal `fcntl.flock(LOCK_EX)` on
  `<pid>.lock` for the entire status-check + execute_repair + archive
  flow. Two concurrent approvals of the same pid can no longer both
  pass the `status == "pending"` check and both run the repair (which
  on a prod PBX would double-restart FreeSWITCH). Falls back to a
  no-op on platforms without `fcntl` so the suite still passes on
  Windows; the dashboard does not run there in production.
- **C-3 (HIGH) — `write_last_result` atomicity**: tmp filename now
  includes a UUID-8 suffix so concurrent writers don't truncate each
  other; the file is `fsync`'d before `os.replace()`; a
  `threading.Lock` serializes the rename step. Two `/run/now` clicks
  in the same second no longer race to overwrite the cache.

### UX (progressive SPA)
- **F-1 (CRITICAL UX) — SPA `/run/now` toast**: `home.html` no longer
  posts to `/run/now?ui=1`. The `?ui=1` query forced the server to
  return an HTTP 302 redirect, which `sweeper.js` (with
  `redirect: 'manual'`) reported as `resp.type === 'opaqueredirect'`
  and a red "שגיאה: HTTP 302" toast. With `?ui=1` removed, the server
  returns the JSON branch and the toast shows green "הסריקה הסתיימה".
  Belt-and-braces: `sweeper.js` now treats `opaqueredirect` as success
  when the form has `data-spa-redirect` or `data-spa-reload`, so any
  future endpoint that returns a redirect still gets the green toast.
- **F-2 (HIGH) — v6 SPA wiring**: `v6_layout.html` now loads
  `sweeper.js` and provides the toast container, so the v6 pages
  (machines, alerts) can opt-in to `data-spa-action` just like the
  home page. Previously the JS was only loaded by `base_spa.html`,
  which the v6 shell does not extend.

### Test changes
- New `tests/test_v615_code_review_fixes.py` — 8 contract tests:
  - CSRF rejects missing Origin (subprocess-isolated to avoid env-var
    pollution across the suite)
  - CSRF accepts matching Origin
  - `write_last_result` survives 8 concurrent threads + no orphan tmps
  - `write_last_result` uses unique tmp filenames
  - `sweeper.js` handles opaqueredirect
  - `home.html` no longer posts to `/run/now?ui=1`
  - `v6_layout.html` loads `sweeper.js` + toast host
  - `dashboard.approval_approve` references `flock`/`LOCK_EX`

### Cumulative test status
- 159 dashboard/agent_api/chat/approvals/predict tests pass post-fix.

## [1.5.14] — 2026-07-02 — Progressive SPA Wiring

### Added
- **`static/sweeper.js`** — 5.5KB vanilla JS bundle. Zero dependencies,
  no build step, no JS framework. Provides `window.sweeper.post(url,
  formData, opts)` for fetch-based POSTs and `window.sweeper.toast(msg,
  kind)` for non-blocking notifications. Auto-attaches to any
  `<form data-spa-action>` via `bindAll()` on `DOMContentLoaded`.
- **Toast container** in `templates/base_spa.html` — `#toast-host` is
  a fixed-position stack at the bottom-center that the JS appends to.
  Uses Tailwind colors (`emerald-600` for ok, `rose-600` for err).
- **`data-spa-action` opt-in** on three high-value forms:
  - `home.html` → `/run/now?ui=1` (manual sweep now toasts + reloads
    instead of replacing the page)
  - `approval_detail.html` → `/approvals/<pid>/(approve|reject)` (the
    two highest-frequency operator clicks — confirm-then-toast instead
    of confirm-then-reload)

### CSRF / security
- The JS sends an `Origin: location.origin` header on every POST so
  the v1.5.9 `_csrf_origin_ok` check accepts the request.
- Also sends `X-Requested-With: XMLHttpRequest` so future server-side
  differentiation between full-page POSTs and SPA POSTs is trivial.
- No external CDN runtime deps (only Tailwind via `base_spa.html`).
  Verified by `test_sweeper_js_does_not_introduce_external_runtime_deps`.

### Back-compat (zero breaking changes)
- Forms without `data-spa-action` continue to behave as before (full
  page POST → redirect → page render).
- Static asset path `/static/sweeper.js` is served by Flask's built-in
  static handler — no new route, no blueprint registration.
- `<form>` elements get `data-spa-action` only — the original `action=`
  attribute is preserved so non-JS clients (curl, accessibility tools)
  still work.

### Test changes
- New `tests/test_v614_spa_wiring.py` — 8 contract tests:
  bundle exists, base_spa loads it, window.sweeper API surface,
  Origin header sent, no CDN deps, forms opt-in correctly.

### Cumulative test status
- 151 dashboard/agent_api/chat/approvals tests pass post-wiring.

## [1.5.13] — 2026-07-02 — Dashboard module split (round 2: notifications + maintenance)

### Changed
- **Extracted notification helpers** from `dashboard.py` into a new
  sibling module `src/ipracticom_sweeper/dashboard_notify.py`. Moved:
  `_read_notifications_env`, `_write_notifications_env`, `_test_telegram`,
  `_validate_slack_webhook_url`, `_test_slack`, plus the
  `NOTIFICATIONS_ENV_FILE` constant. Uses `structlog` instead of
  `app.logger` so it has no Flask app dependency.
- **Extracted maintenance helpers** from `dashboard.py` into a new
  sibling module `src/ipracticom_sweeper/dashboard_maintenance.py`.
  Moved: `_validate_hostname`, `_redact_secrets`, `_safe_error_response`,
  `_save_maintenance_state`, `_get_maintenance_state`, plus the
  `_HOSTNAME_RE` and `_SECRET_KEYS` constants. The `Flask.jsonify`
  import is the only Flask dependency, kept because the helper
  always returns a Flask response.
- **`_actor_from_request` stays in `dashboard.py`** — depends on the
  Flask request context and is only used by routes.
- **`dashboard.py` re-exports** all moved symbols at the bottom of the
  module so existing call sites (`from ipracticom_sweeper.dashboard
  import _validate_slack_webhook_url`, etc.) keep working without
  edits. Tests patching `ipracticom_sweeper.dashboard._validate_*`
  also continue to work because the symbols are real objects
  re-exported from the helper modules, not proxies.
- **`dashboard.py` shrinks 2158 → 1918 lines** (240 lines moved into
  two focused modules). Round 1 (v1.5.12) plus this release = 2181 →
  1918 = 263 lines removed from the monolith.

### Security (unchanged — verified to still apply)
- `_validate_slack_webhook_url` SSRF allowlist (`hooks.slack.com`,
  `/services/` path) preserved verbatim in `dashboard_notify.py`.
- `_safe_error_response` continues to emit correlation ids instead
  of raw exception strings — verified by new test.
- `_redact_secrets` continues to scrub 15 secret-bearing keys
  recursively — verified by new test.

### Test changes
- New `tests/test_v612_dashboard_notify_split.py` — 8 tests covering
  module imports, public-symbol back-compat, SSRF allowlist,
  hostname validator, secret redactor, safe-error correlation id,
  and final dashboard.py line count < 2000.
- `tests/test_v6_hardening.py::test_outbound_http_blocks_internal_ips`
  now inspects both `dashboard` and `dashboard_notify` for the SSRF
  marker — the helper moved modules but the protection still applies.

### Cumulative test status
- 104 dashboard/agent_api/chat tests pass (post-split, post-fix).

## [1.5.12] — 2026-07-02 — Dashboard module split (round 1: cache I/O)

### Changed
- **Extracted cache I/O helpers** from `dashboard.py` into a new
  sibling module `src/ipracticom_sweeper/dashboard_helpers.py`.
  Moved: `read_last_result`, `write_last_result`,
  `trigger_pipeline_run`, plus the `CACHE_DIR` / `LAST_RESULT_FILE`
  Path constants. The helpers are pure-Python (no Flask, no
  `app.logger`) so they can be unit-tested without a request ctx.
- **`dashboard.py` re-exports** the public names so callers keep
  working unchanged (`from ipracticom_sweeper.dashboard import
  _read_last_result`, `trigger_pipeline_run`, etc.). Back-compat
  aliases (`_read_last_result = read_last_result`) preserve the
  old underscore-prefixed module-internal names.
- **`dashboard.py` shrinks 2181 → 2158 lines** (23 lines moved into
  a separate file for better discoverability + testability).

### Test changes (unavoidable, see note)
- `tests/test_dashboard.py::test_run_now_triggers_pipeline` and
  `test_run_now_handles_pipeline_error` now patch
  `ipracticom_sweeper.dashboard_helpers.run_pipeline` instead of
  the dashboard-level re-export (the helpers' own binding is what
  `trigger_pipeline_run` calls into).
- `test_read_last_result_returns_parsed_json` +
  `test_read_last_result_handles_corrupted_json` patch
  `helpers.LAST_RESULT_FILE` for the same reason — the helpers'
  module-level constant, not the dashboard re-export.
- The pure function is also exercised directly via
  `helpers.read_last_result()` for crisper assertions.

### Verified
- **5 new tests** in `tests/test_v6_dashboard_split.py` guard:
  - app still importable from `ipracticom_sweeper.dashboard`
  - 18 critical routes still registered
  - line count drops below the baseline
  - `dashboard_helpers` module exists
  - `dashboard_helpers` is independent of Flask (`app.route` /
    `= Flask(` forbidden)
- **78/78 cumulative tests pass** across dashboard + dashboard_routes
  + dashboard_v14_routes + agent_api_endpoints + chat + the new split
  suite.

### Next steps (deferred)
- v1.5.13 — extract `_notify` helpers (notification settings +
  Slack/Telegram validators + tests) — pure functions, no Flask.
- v1.5.14 — extract `_maintenance` helpers (machine-action
  enqueue + maintenance state save/get).

## [1.5.11] — 2026-07-02 — Predict class merge

### Changed
- **Merged two `Prediction` dataclasses** into one canonical class in
  `src/ipracticom_sweeper/predict/analyzer.py`. Previously, both
  `analyzer.py` and `integration.py` defined `Prediction` with
  identical fields (metric, current_value, predicted_time_hours,
  slope, threshold). Integration's version also carried a
  `to_dict()` method that analyzer's lacked.
  Now `Prediction` lives in `analyzer.py` with `to_dict()` baked in.
- **`predict/integration.py` re-exports** `Prediction` (no second
  `class` statement) so legacy `from predict.integration import
  Prediction` keeps working.
- **`collect_predictions()` simplified**: no longer copies
  field-by-field into a duplicate `Prediction`. Just appends the
  dataclass returned by `predict_crossing()` — same object.

### Verified
- **5 new tests** in `tests/test_v6_predict_merge.py`:
  - exactly one `class Prediction` definition under `predict/`
  - `to_dict()` present on the canonical class
  - `predicted_time_hours=None` round-trips
  - `predict.integration.Prediction` IS `predict.analyzer.Prediction`
  - package-level re-export points to the same class object
- Existing `tests/test_predict_integration.py` + `tests/predict/test_analyzer.py` **untouched** and **green** (18/18 pass).

## [1.5.10] — 2026-07-02 — Logging Unification

### Changed
- **stdlib `logging` → `structlog.get_logger()`** in 6 modules:
  `agent_api.py`, `config/module_registry.py`, `monitoring/otel.py`,
  `fleet/collector.py`, `telegram_bot/bot.py`, and the
  `_safe_error_response` helper in `dashboard.py`.
  Centralizes structured logging across the codebase so every
  `error=`, `correlation_id=`, `kind=` field is machine-parseable
  rather than buried in a freeform `f"...{e}"` string.
- **Removed unused stdlib `logging` import** in
  `fleet/aws_connector.py` (no logger was actually instantiated).
- **`register_chat_routes(app, auth_required=False)` in `dashboard.py`**:
  chat routes are protected by the dashboard's existing
  `before_request` basic-auth + CSRF check — don't double-gate.

### Verified
- **219/219 tests pass** across v1.5.9 hardening + v1.5.10 logging
  + v1.5.8 concurrency + v1.5.7 security + silent_except_gate +
  all dashboard + module_registry suites.
- New `tests/test_v6_logging_unified.py` (3 tests) enforces:
  - No `import logging` outside `_log.py` / `__init__.py` /
    `sweeper.py` / `telegram_bot/bot.py` (root-logger setup).
  - No `logging.getLogger(...)` outside the allowed files.
  - Every migrated module imports `structlog.get_logger(...)`.
  - All targeted modules still import cleanly.

## [1.5.9] — 2026-07-02 — Hardening

### Added
- **CSRF Origin/Referer check** (`src/ipracticom_sweeper/dashboard.py`):
  new `_csrf_origin_ok()` helper + `DASHBOARD_TRUSTED_ORIGINS` env var
  (loopback always trusted). Applied via `before_request` to all
  POST/PUT/PATCH/DELETE — protects dashboard state-changing routes
  against cross-site form posts.
- **`_safe_error_response` helper** (`agent_api.py` + `dashboard.py`):
  centralizes error redaction (returns generic `internal_error` + 8-char
  correlation id), logs the full exception server-side via `logger.error`,
  and accepts an optional `extra=` kwarg so callers can preserve
  non-sensitive metadata (e.g. `mode`, `remote_url`).
- **`_validate_slack_webhook_url` allowlist** (`dashboard.py`):
  Slack webhooks MUST be `https://hooks.slack.com/services/...` —
  any other host/scheme/path is rejected before persistence
  (`/settings` save) and before test send (`/settings/test`).
- **`SLACK_ALLOWED_IPS` opt-in IP allowlist** for `/slack/events`
  (`agent_api.py`): empty by default (HMAC still required); when set,
  rejects requests from non-listed `remote_addr` with 403.
- **CORS wildcard rejection at startup** (`agent_api.py`):
  `AGENT_API_CORS_ORIGINS` must not contain `*` or `*/...` — raises
  RuntimeError on import (fail-closed). Wildcards would leak the
  Authorization header to any origin.
- **`register_chat_routes(app, auth_required=True)`** (`chat.py`):
  basic-auth `before_request` gate for all chat routes; WebSocket
  `/ws` does explicit auth + `_ws_rate_limit_check()` (sliding-window
  60 s, 30 msg/min, max 1024 tracked IPs).

### Changed
- **Rate-limits on 8 sensitive agent_api routes**:
  `/api/run`, `/api/notify/test`, `/api/connectors` (POST/PATCH/DELETE
  + `/test`), `/api/approvals/<pid>/approve|reject` — all gated by
  the existing `@_rate_limit` decorator at `_RL_API_DEFAULT`.
- **`_safe_error_response` replaces 10 raw `str(e)` patterns**
  (6 in `agent_api.py`, 4 in `dashboard.py`) that previously exposed
  filesystem paths, SQL fragments, and library versions to clients.

### Fixed
- **Silent rollback-fail handlers** (`config/host_config.py`):
  `_invalidate_cache` and `_populate_cache` previously swallowed
  `sqlite3.Error` from the inner `ROLLBACK` attempt with bare `pass`.
  Now wrapped in `log_suppressed()` with `host` + original-exception
  context so failures surface in the suppressed-exception log channel.

### Verified
- **13/13 new hardening tests pass** (`tests/test_v6_hardening.py`).
- **44/44 cumulative tests pass** across all active sprints
  (v1.5.7 security + v1.5.8 concurrency + v1.5.9 hardening
  + silent_except_gate).

## [1.5.8] — 2026-07-02 — Concurrency Hotfix

### Fixed
- **Audit log rotation now preserves inode** (`audit/rotation.py`):
  `_recreate_empty_locked()` truncates audit.jsonl in place via
  `open("w") + close`, replacing the prior `unlink() + create()` pattern
  that allocated a fresh inode on every rotation. Closes silent data loss
  for concurrent writers holding an FD to the unlinked inode.
- **SQLite connection leak in host_config** (`config/host_config.py`):
  `_db_conn()` now returns a cached singleton instead of opening a fresh
  `sqlite3.connect()` on every call (each leak consumed an FD). Set
  `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`
  on first open.
- **DELETE+INSERT not atomic** (`config/host_config.py`):
  `_invalidate_cache` and `_populate_cache` now wrap multi-statement
  writes in `BEGIN IMMEDIATE`/`COMMIT`. Previously autocommit committed
  per statement, so a reader between two DELETEs saw partial state.
- **Heartbeat write non-atomic** (`monitor/health.py:record_run`):
  Now writes to `<path>.tmp` then `os.replace()` to ensure a crash mid-write
  never leaves a truncated JSON that `check_health` would misread as corrupt.
- **Heartbeat skipped on monitor failure** (`pipeline.py:run_pipeline`):
  The early-return path on `run_monitor()` failure now writes the heartbeat
  before returning. Previously the next `check_health()` would falsely flag
  the agent as stale on every monitor crash.
- **SQLite `busy_timeout=0`** (`state/sqlite_store.py`, `storage/timeseries.py`):
  Both now set `PRAGMA busy_timeout=5000` (and WAL mode where missing)
  to avoid `OperationalError: database is locked` under contention.

### Tests
- `tests/test_v6_concurrency_hotfix.py` (8 tests, RED → GREEN):
  inode preservation × 1, FD leak × 1, singleton conn × 1, atomic
  transaction × 1, atomic heartbeat × 1, monitor-failure heartbeat × 1,
  busy_timeout × 2.

### Notes
- No public API change. All routes / SQL schemas unchanged.
- The SQLite caches (host_config + timeseries + state) now use WAL mode:
  readers and writers no longer block each other. Existing on-disk
  databases are upgraded automatically on first open.

## [1.5.7] — 2026-07-02 — Security Hotfix

### Fixed
- **Path traversal in `<host>` URL params** (`dashboard.py:770-820`):
  `_save_maintenance_state` and `_get_maintenance_state` now call
  `_validate_hostname(host)` which rejects anything outside
  `[A-Za-z0-9._-]{1,64}`. Closes a write-anything-under-maintenance-dir
  primitive in `/v6/machines/<host>/maintenance{,/off,action}`.
- **Path traversal in `<pid>` URL params** (`repair/pending.py`):
  `_validate_pid(pid)` added; `get_proposal`, `set_status`, `archive` all
  reject pids with `/`, `..`, NUL, empty. Closes escalation via
  `/api/approvals/<pid>/{approve,reject}` and dashboard equivalents.
- **Dashboard fail-closed missing** (`dashboard.py:main`): mirror of
  `agent_api.main()` — refuses to start when `DASHBOARD_USER/PASS`
  unset AND `--host` is not loopback unless `--allow-open` is passed.
  Default port `8787 -> 8804` to avoid clash with `agent_api`.
- **Command injection in `proposed_command`** (`dashboard.py:v6_machines_action`):
  `shlex.quote(host)` now wraps every host interpolation in the three
  machine-action proposals (`agent_restart`, `reboot`, `ssm_connect`).
- **Actor spoofing in audit log** (`dashboard.py:{approval_approve,approval_reject}`):
  `_actor_from_request()` derives the actor from `request.authorization.username`
  (fallback: `DASHBOARD_USER` env), never `request.form['actor']`.
  Closes log-spoofing primitive where any form-submitter could
  impersonate another operator.
- **Secret leakage in audit log** (`dashboard.py`): `_redact_secrets()` mirror of
  `agent_api._redact_secrets`. `RepairProposal.kwargs` (which may include
  passwords, API tokens) is now scrubbed before `log_audit()` writes.

### Tests
- `tests/test_v6_security_hotfix.py` (10 tests, RED → GREEN):
  path traversal × 5, fail-closed × 2, port default × 1, command injection × 1,
  actor spoofing × 1.

### Notes
- No public API change. Existing routes still work the same way for valid input.
- Operators upgrading from v1.5.6: if you were running the dashboard with
  `--host 0.0.0.0` and no auth, you must now set `DASHBOARD_USER/PASS` or pass
  `--allow-open`. This is intentional (prevents the prior silent-open exposure).

## [1.5.6] — 2026-07-02 — Silent-Except Final Sweep

### Fixed
- **Silent exception blocks** in **25 more files** replaced with `log_suppressed()`:
  - `monitor/freeswitch.py`: 5 → 0 (mtime scan, jitter parse, psutil iterations)
  - `monitor/security_baseline.py`: 4 → 0 (sshd_config read, SUID walk, listening ports)
  - `repair/approvals_v2.py`: 4 → 0 (v2 sidecar parse, proposal reap, audit/rejected)
  - `agent_api.py`: 3 → 0 (audit size, JSON parse, host_config load)
  - `monitor/egress.py`: 3 → 0 (port parse, IP parse, CIDR parse)
  - `repair/pending.py`: 2 → 0 (list, get_proposal)
  - `config/connectors.py`, `config/secrets.py`, `chat_rag.py`, `fleet/collector.py`, `monitor/disk.py`, `monitor/fd_check.py`, `monitor/iostat.py`, `monitor/memory.py`, `monitor/network.py`, `monitor/pg_autovacuum.py`, `monitor/pg_bloat.py`, `monitor/pg_locks.py`, `monitor/pg_long_query.py`, `monitor/pg_replication_lag.py`, `monitor/process_tracker.py`, `monitor/processes.py`, `repair/actions_extra.py`, `telegram_bot/formatter.py`: 1 each
  - `_log.py`: docstring no longer matches the silent-block regex pattern

### Re-enabled
- **Strict-zero gate** (`test_50_5_no_silent_except_blocks_in_src`) is now **active** — fails on any new `except: pass` / `except X: continue` / bare `except:` block added to `src/`.
- Removed `pytest.skip` and the `test_silent_except_baseline_snapshot` placeholder test.

### Cumulative since v1.5.0
- **silent blocks = 0** across `src/ipracticom_sweeper/`. **127 cumulative silent blocks** replaced since v1.5.0 baseline of 50:
  - v1.5.0: 50 (baseline snapshot)
  - v1.5.1: +8 → 42 remaining
  - v1.5.2: +5 → 37 remaining
  - v1.5.3: +5 → 32 remaining
  - v1.5.4: +6 → 26 remaining
  - v1.5.5: +13 → 13 remaining
  - v1.5.6: +40 → 0 remaining

### Notes
- No public API change.

## [1.5.5] — 2026-07-02 — Silent-Except Slice 5.7

### Fixed
- **Silent exception blocks** in 9 more files replaced with `log_suppressed()`:
  - `monitor/freeswitch.py`: 1 → 0 (port-anchor sock close at line 391)
  - `config/legacy.py`: 1 → 0 (config fallback read)
  - `config/paths.py`: 1 → 0 (state dir resolve)
  - `repair/actions_extra.py`: 1 → 0 (action dispatch failure)
  - `monitoring/otel.py`: 1 → 0 (OTLP export)
  - `telegram_bot/health.py`: 1 → 0 (token tracker probe)
  - `telegram_bot/formatter.py`: 1 → 0 (HTML escape failure)
  - `telegram_bot/bot.py`: 1 → 0 (polling error handler)
  - `monitor/self_snapshot.py`: 5 → 0 (state-dir disk%, audit size, bot token status, watchdog restart count)
- **Cumulative since v1.5.0**: **73 silent blocks** now auditable (was 64).

### Known
- A separate regex-based scan (`test_silent_except_baseline_snapshot`) reports **~40 additional silent blocks** in `monitor/egress.py`, `monitor/security_baseline.py`, `repair/*.py` — these match the patterns `except X: pass`, `except X: continue`, bare `except:` and will be addressed in v1.5.6+.
- The strict-zero gate (`test_50_5_no_silent_except_blocks_in_src`) remains **paused** at v1.5.5 and will be re-enabled in v1.5.6+ once that count reaches 0.

### Notes
- No public API change.

## [1.5.4] — 2026-07-02 — Silent-Except Slice 5.6

### Fixed
- **Silent exception blocks** in 3 more files replaced with `log_suppressed()`:
  - `agent_api.py`: 2 → 0 (rate-limit header, audit log read)
  - `config/connectors.py`: 2 → 0 (mark_collected, mark_error races)
  - `chat.py`: 2 → 0 (docs path resolve, ws error send)
- **Cumulative since v1.5.0**: 60 silent blocks now auditable. Remaining: 9 in 9 files (v1.5.5+).

### Notes
- No public API change.

## [1.5.3] — 2026-07-02 — Silent-Except Slice 5.5

### Fixed
- **Silent exception blocks** in 3 more files replaced with `log_suppressed()`:
  - `monitor/kernel_errors.py`: 2 → 0 (dmesg/journalctl probes)
  - `monitor/smart_check.py`: 2 → 0 (temp/reallocated parsing)
  - `monitor/disk.py`: 1 → 0 (readonly mount probe)
- **Cumulative since v1.5.0**: 54 silent blocks now auditable. Remaining: 15 in 12 files (v1.5.4+).

### Notes
- No public API change.

## [1.5.2] — 2026-07-02 — Silent-Except Slice 5.4

### Fixed
- **Silent exception blocks** in 3 more files replaced with `log_suppressed()`:
  - `monitor/ntp_check.py`: 3 → 0 (parsing malformed ntpq/chronyc lines)
  - `monitor/process_tracker.py`: 1 → 0 (/proc/meminfo read)
  - `telegram_bot/handlers/connectors.py`: 1 → 0 (delete callback toast)
- **Cumulative since v1.5.0**: 49 silent blocks now auditable. Remaining: 20 in 15 files (v1.5.3+).

### Notes
- No public API change.

## [1.5.1] — 2026-07-02 — Silent-Except Slice 5.3

### Fixed
- **Silent exception blocks** in 3 more files replaced with `log_suppressed()`:
  - `monitor/freeswitch.py`: 2 → 0 (sock.close() in finally blocks)
  - `monitor/self_disk.py`: 3 → 0 (rotation unlink/rename/promote)
  - `telegram_bot/handlers/fleet.py`: 3 → 0 (callback ack, log download, tmpfile cleanup)
- **Cumulative since v1.5.0**: 44 silent blocks now auditable. Remaining: 23 in 18 files (v1.5.2+).

### Notes
- No public API change.

## [1.5.0] — 2026-07-02 — Silent-Except Gate (Slice 5.2)

### Added
- **Silent-except gate infrastructure** (`tests/test_silent_except_gate.py`):
  - `test_50_5_no_silent_except_blocks_in_src` — strict-zero gate (skipped; target for v1.6.0)
  - `test_50_5_silent_except_baseline_snapshot` — documents current count
  - `test_50_5_per_file_baseline_does_not_regress` — anti-regression per file

### Fixed
- **Silent exception blocks** replaced with `log_suppressed()` calls so failures become auditable:
  - `audit/rotation.py`: 8 → 0
  - `monitor/freeswitch_v2_part2.py`: 5 → 0
  - `dashboard.py`: 16 → 0
  - `config/host_config.py`: 7 → 0 (new code in v1.3.0 slice 1)
- **Total**: 36 silent blocks now auditable. Remaining: 35 in 22 files (deferred to v1.5.1+).

### Notes
- No public API change. No behavior change for callers that didn't depend on silent suppression.
- v1.4.0 → v1.5.0 is a quality-of-observability release, not a feature release.

## [1.4.0] — 2026-07-02 — Suppression Engine & Dashboard API

### Added
- **Suppression engine** (Slice 3) — public CRUD on top of per-host YAML
  - `add_suppression(name, rule, *, until=None, reason="")`
    auto-creates the host; re-adding the same (host, rule) replaces
    the existing entry (no duplicate stacking)
  - `remove_suppression(name, rule) -> bool` (idempotent)
  - `list_active_suppressions(name)` — lazy filter, expired are hidden
  - `cleanup_expired_suppressions() -> int` — bulk, mtime-preserving
  - 13 new tests in `test_host_config.py`
- **Dashboard routes** (Slice 4) — REST surface for the slice 1+2+3
  work, auth + rate-limit gated like the rest of the agent API
  - `GET    /api/hosts`
  - `GET    /api/hosts/<name>`
  - `POST   /api/hosts/<name>/suppressions`   (201)
  - `GET    /api/hosts/<name>/suppressions`   (filters expired)
  - `DELETE /api/hosts/<name>/suppressions/<rule>`  (204 / 404)
  - `POST   /api/hosts/_cleanup-suppressions` (returns count removed)
  - `GET    /api/modules`  with `?kind=&tag=&risk=&available_only=`
  - `GET    /api/modules/<kind>/<name>`
  - 18 new tests in `test_dashboard_v14_routes.py`
- **Audit events** — `suppression.add` and `suppression.remove` are
  emitted via `audit.logger.emit`; `cleanup_expired_suppressions` is
  deliberately silent (housekeeping, not an operator decision)

### Changed
- `__init__.py` + `pyproject.toml` bumped from 1.3.0 to 1.4.0
- `Suppression` and `HostConfig.is_suppressed()` were already
  present in v1.3.0 (data shape); the runtime API and persistence
  behaviour is what this version adds

### Hardening
- Path-traversal and whitespace host names return 400 on every
  per-host route, mirroring the existing `_host_yaml_path` sanitizer
- The detail route distinguishes "unknown host" (404) from
  "default config" (which `load_host` returns) by checking
  `_host_yaml_path(name).exists()` before serializing

### Deferred (to v1.5.0)
- **Dashboard refactor** (1945 lines → split by route group)
- **Remaining 44 silent except blocks** (audit/rotation.py, otel.py, formatter.py)
- **Prediction class merge** (2 duplicates in `predict/`)
- **Logging unification** (stdlib → structlog)
- **Frontend SPA** — the new REST routes are JSON-only; the HTML
  dashboard / SPA still needs to be wired to consume them

## [1.3.0] — 2026-07-02 — Per-Host Configuration & Module Registry

### Added
- **`config/host_config.py`** — per-host config (Slice 1)
  - `HostConfig` dataclass: monitors / repairs / runbooks / suppressions / enabled / description
  - YAML at `$STATE_DIR/hosts/<name>.yaml` — git-friendly source of truth
  - SQLite cache at `$STATE_DIR/hosts.db` — read-cache for the dashboard
  - Atomic YAML write (tmp + rename); invalidate-then-populate cache
  - Host name sanitization `[a-zA-Z0-9_.-]`; path-traversal rejected
  - `Suppression` dataclass (reason, until); permanent when `until=None`
  - 16 tests in `test_host_config.py`
- **`config/module_catalog.yaml`** — bilingual, version-controlled catalog (Slice 2)
  - 37 monitors, 15 repairs, 5 runbooks
  - Each entry: `title_en` / `title_he`, description, params, tags, risk
  - Catalog is metadata source of truth; code is runtime source
- **`config/module_registry.py`** — discover + filter + default-config (Slice 2)
  - `discover_modules()` cross-checks catalog ↔ code (strict mode raises on drift)
  - `filter_modules(kind=, tag=, risk=, available_only=)` for the dashboard
  - `get_module(name, kind=)` lookup helper
  - `default_host_config(name)` builds a safe-by-default `HostConfig`
    (high-risk monitors disabled, medium/high repairs require approval)
  - `_MONITOR_ALIASES` lets catalog names diverge from file stems
    (`fs_inode_check`, `freeswitch_health` → `monitor/freeswitch.py`)
  - Suffix conventions: `name_check` ↔ `name.py`, `name_runbook` /
    `name_recovery_runbook`
  - 17 tests in `test_module_registry.py`

### Changed
- Three previously orphaned monitors (`health`, `healthz_probe`, `processes`)
  promoted into the catalog with bilingual entries — they were already shipped
  in code but not visible to the dashboard
- `monitor:checks` (the orchestrator, not a leaf monitor) removed from catalog
- Two catalog entries (`fs_inode_check`, `freeswitch_health`) now resolve
  to the consolidated `monitor/freeswitch.py` via `_MONITOR_ALIASES`
- Reverse-drift detector recognises `name_check` / `name_health` suffix
  conventions so catalog↔code cross-check is correct

### Hardening
- `test_30_2_no_catalog_only_entries_except_ignored` and
  `test_30_2_strict_mode_raises_on_any_drift` introduced as gate tests so
  future catalog drift fails CI rather than silently degrading the dashboard

### Deferred (to v1.4.0)
- **Suppression engine** — runtime evaluation of `Suppression` entries
  (UI controls, time-based cleanup, audit trail)
- **Dashboard routes** — web UI bindings for `host_config` + `module_registry`
- **Dashboard refactor** (1945 lines → split by route group)
- **Remaining 44 silent except blocks** (audit/rotation.py, otel.py, formatter.py)
- **Prediction class merge** (2 duplicates in `predict/`)
- **Logging unification** (stdlib → structlog)

## [1.2.0] — 2026-07-01 — QA Foundation: paths + log + API hardening

### Added
- **`config/paths` module** — single source of truth for `$IPRACTICOM_SWEEPER_STATE_DIR`
  - `ROOT()`, `maintenance_dir()`, `fleet_snapshots()`, `connectors_file()`,
    `pending_repairs()`, `approved_repairs()`, `rejected_repairs()`, `audit_log()`,
    `ntp_history()`, `token_health()` — all cached, all env-overridable
  - 12 new tests in `test_paths_centralization.py`
- **`_log.log_suppressed()` helper** — replaces 5 silent `except: pass` blocks
  in `fleet/aws_connector.py` with structured WARNING lines + DEBUG traceback
  - 6 new tests in `test_log_suppressed.py`
- **Built-in rate limiting** (no `flask-limiter` dependency)
  - Per-IP sliding window: 100/min default for `/api/*`, 600/min for `/healthz`
  - Returns 429 + `Retry-After: 60` on overage, `X-RateLimit-Remaining` on success
  - Per-IP buckets respect `X-Forwarded-For` (first hop)
  - Disable with `AGENT_API_RATELIMIT=0`
  - 10 new tests in `test_rate_limit_cors.py`
- **Localhost-only CORS** (no `flask-cors` dependency)
  - Default allowlist: `http://localhost`, `http://localhost:5000`, `http://127.0.0.1`, `http://127.0.0.1:5000`
  - Extend via `AGENT_API_CORS_ORIGINS=csv`
  - No wildcard ever set; external origins return no `ACAO` header
  - `after_request` hook attaches `Vary: Origin`, `ACAM`, `ACAH`, `ACMA`

### Changed
- **Version bump**: `pyproject.toml` and `__init__.py` updated to 1.2.0
- **Test fixtures updated** for v1.2.0 (version assertions, vault session glob)

### Deferred (to v1.3.0)
- **Dashboard refactor** (1945 lines → split by route group)
- **Per-host module selection UI** (QA Dashboard)
- **Suppression / silencing** per host + per module
- **Remaining 44 silent except blocks** (audit/rotation.py, otel.py, formatter.py)
- **Prediction class merge** (2 duplicates in `predict/`)
- **Logging unification** (stdlib → structlog)

## [1.1.1] — 2026-07-01 — Sprint 15 repairs + install hardening

### Added
- **Sprint 15 repair coverage**: 5 new repair actions registered in `actions_extra.py`
  - `repair_rotate_nginx_logs`: rotate nginx access/error logs and reload
  - `repair_drop_freeswitch_cache`: drop FreeSWITCH mod_sofia/db caches
  - `repair_reload_freeswitch_config`: reload FreeSWITCH XML config (`fs_cli reloadxml`)
  - `repair_vm_lock_clear`: clear stale VM heartbeat locks
  - `repair_pg_vacuum`: per-table VACUUM with explicit table-name validation
- **Install hardening for installing agents**:
  - `pyproject.toml` now declares `[project.scripts]`: `ipracticom-sweeper`, `ipracticom-dashboard`, `ipracticom-agent-api`
  - `make verify` / `make doctor` runs `scripts/verify_install.py` (Python + pip + deps + entry points + tests)
  - `make install-all` adds `[telegram]` + `[test]` extras in one shot
  - `scripts/verify_install.py`: 6-check pre-flight, exit 0 only if all required pass

### Changed
- **Naming consistency**: `actions_extra.py` repairs now register without `repair_` prefix to match `actions.py`
- **`HIGH_RISK_ACTIONS` in `approvals_v2.py`**: aligned to real registry names (was dead code)
- **Auth fail-closed**: `agent_api.py` now exits 1 if `AGENT_API_TOKEN` is missing AND bind host is public (0.0.0.0/::)
- **Secret redaction in audit log**: `_redact_secrets()` helper scrubs `password`, `token`, `api_key`, `secret` from kwargs before logging
- **Reject reason required**: `POST /api/approvals/<id>/reject` returns 400 if `reason` is empty
- **Version bump**: `pyproject.toml` and `__init__.py` updated to 1.1.1

### Fixed
- **Command injection guard**: `service_restart` validates `unit` against `^[a-zA-Z0-9_.@-]+$`; `pg_vacuum` validates `table` against `^[a-zA-Z0-9_.]+$`
- **404 wins over 400** in reject route (unknown-id probes don't leak "reason required")

## [1.1.0] — 2026-07-01 — Deep Checks v2 + Approval Workflow v2

### Added
- **Sprint 12 (Network + Service Probes)**: 3 monitors, 31 tests
  - `healthz_probe`: HTTP healthz endpoint probe with timeout/SSL controls
  - `systemd_state`: masked/disabled/failed unit detector
  - `ntp_check`: chronyc/ntpq clock-skew with unit normalization (sec/ms/us)
- **Sprint 14 (PostgreSQL Deep)**: 5 monitors, 35 tests
  - `pg_long_query`: queries running >5min by default, classifies by count
  - `pg_replication_lag`: max replay_lag across replicas, disabled if stand-alone
  - `pg_locks`: blocked queries via `pg_blocking_pids()`, Lock wait_event
  - `pg_bloat`: dead-tuple ratio per table, reports top-N bloated
  - `pg_autovacuum`: lag since last_autovacuum, never-vacuumed → crit
- **Sprint 16 (Backups + Recovery)**: 3 monitors, 25 tests
  - `backup_fresh`: snapshot age vs configurable RPO
  - `backup_size`: size sanity check (delta vs prior)
  - `restore_test`: parse recent restore-test output for status
- **Sprint 18 (Approval Workflow v2)**: 5 capabilities, 25 tests
  - Expiry window — proposals expire after 24h default, background reaper
  - Two-operator quorum — high-risk actions need 2 distinct user_ids
  - Comment thread — operators can add comments, surfaced in Telegram + dashboard
  - Required rejection reason — POST /reject must include reason; optional `dry_run`
  - CSV export — `GET /api/approvals/export.csv` with UTF-8 BOM + date filter
- **Test gap closure (Level 1)**: 91 tests across audit_logger, self_disk,
  telegram_health, runbooks_engine — covers ENV-vars-at-import-time pitfalls
  and the actual dataclass field names that earlier tests got wrong
- **Sprint 15 (Additional Repairs)**: 5 actions, 25 tests
  - `repair_rotate_nginx_logs`: graceful nginx log rotation via SIGUSR1,
    keeps N rotations (configurable), reports `bytes_freed`
  - `repair_drop_freeswitch_cache`: `fs_cli cache flush`, pre-checks FS status
  - `repair_reload_freeswitch_config`: `fs_cli reloadxml`, captures syntax
    errors from stderr
  - `repair_clear_freeswitch_voicemail_locks`: removes stale `.lock` files
    older than `max_age_seconds`, preserves recent ones
  - `repair_pg_vacuum`: `VACUUM [ANALYZE]` via psql, `dry_run` mode,
    timeout, reports table names + duration

### Fixed
- `ntp_check.py` — ntpq parser now handles `*`/`+`/`#`/`o`/`-` tally codes via
  split-based parsing; regex previously skipped the selected peer
- `ntp_check.py` — `_parse_offset_seconds("microseconds")` now correctly
  recognizes the `microsec` prefix (was only matching `usec`)
- `pyproject.toml` — pinned `flask-sock>=0.7.0` dependency. Was previously
  silently failing at chat-blueprint import time, breaking 50+ dashboard
  tests (template `url_for('chat.chat_index')` raised BuildError).

### Tests
- **1606 → 1701 passing / 1701 collected** (+95 net after dependency fix,
  +207 from new sprints/gap-closure; 0 regressions across the suite)
- 12 files added, 5 files modified across 5 commits (`3c84c47`, `71100c3`,
  `4c597d5`, `5485fb1`, `33028a1`)

### Changed
- `pyproject.toml` `version` 1.0.0 → 1.1.0
- `src/ipracticom_sweeper/__init__.py` `__version__` 1.0.0 → 1.1.0

### Documentation
- This CHANGELOG section documents all 5 sprint deliveries

---

## [1.0.0] — 2026-07-01 — Self-Resilience + Deep Checks + Forecast v2

### Added
- **Sprint 8 (Self-Resilience)**: 5 features, 37 tests
  - External systemd watchdog (slice 8.1)
  - State-dir disk monitor (slice 8.2)
  - Telegram token health probe (slice 8.3)
  - Audit log rotation with size+time cascade (slice 8.4)
  - Self-monitor snapshot section (slice 8.5)
- **Sprint 9 (FreeSWITCH Deep)**: 15 checks, 89 tests
  - FS-26..FS-28: auth failures, call drops, NAT binding (slice 9.1–9.3)
  - FS-29..FS-40: silence, OPTIONS, parse errors, dialplan, conf, vm, mod, ESL, max-procs, CDR pool, license, TPS (slice 9.4–9.15)
- **Sprint 10 (Forecast v2)**: 5 primitives, 50 tests
  - `detect_trend`: OLS-based trend classifier with R²
  - `seasonal_decompose`: trend + seasonal + residual
  - `detect_anomaly`: MAD outlier detector
  - `confidence_bands`: p10/p50/p90 forecast intervals
  - `ensemble_forecast`: weighted blend of multiple models
  - `predict_at_horizon`: OLS extrapolation helper
- **Sprint 15 (Repairs + Runbooks)**: 5 repairs + 2 runbooks, 49 tests
  - `dns_cache_purge`, `fs_inode_warn_clear`, `rotate_audit_now`,
    `telegram_token_revalidate`, `self_healthz_ping`
  - `audit_pressure_runbook`, `self_health_recovery_runbook`
  - Policy engine: `load_policy` now returns `__default__` for unlisted actions

### Tests
- **1121 → 1297** (+176 new tests, 0 failing across all sprints)

### Changed
- `pyproject.toml` `version` 0.6.3 → 1.0.0
- `src/ipracticom_sweeper/__init__.py` `__version__` 0.6.1 → 1.0.0
- `install.sh` default `SWEEPER_BRANCH` v0.6.3 → v1.0.0

### Documentation
- `docs/COVERAGE_MATRIX.md` — 63 features × status × test reference table

---

## [0.6.2] — 2026-07-01 — SPA sidebar unification + tests

### Added
- **SPA sidebar unification** (commits `ff007dc`, `dbd6ebf`, `cf86eb3`): the legacy top-nav is now embedded into both SPA variants (`/spa/a` and `/spa/b`) so every navigation affordance lives in a single sidebar — 9 items (Dashboard, Live State, Modules, Problems, Repairs, Predictions, Evidence, Security, Audit) — instead of being split between a top-nav bar and a sidebar. Same `/api/snapshot` data, fewer competing UI surfaces.
- **Dashboard A/B screenshots** (`docs/dashboard-variant-a.png`, `docs/dashboard-variant-b.png`) — captured from the live `/api/snapshot` with real data for side-by-side peer review.

### Fixed
- `tests/test_dashboard.py`, `tests/test_v6_machines.py`, `tests/test_v6_sidebar.py` — caught and fixed latent failures that would have shipped red with the new SPA sidebar (sidebar items not matching the new shell, machine detail missing the actions panel, sidebar height regression).

### Tests
- **1083 → 1121** (+38 new tests, 0 failing). New SPA sidebar surface covered end-to-end.

### Changed
- `pyproject.toml` `version` 0.6.1 → 0.6.2
- `install.sh` / `bootstrap.sh` default `SWEEPER_BRANCH` v0.6.1 → v0.6.2

## [0.6.1] — 2026-07-01 — one-liner installer + agent_api + SPA A/B

### Added
- **`install.sh` one-liner** (root script): `curl -sSL .../install.sh | sudo bash` — detects OS family (apt/dnf/yum), installs OS deps, clones to `/opt/ipracticom-sweeper`, seeds `/etc/ipracticom-sweeper/agent.env` + `repair_policy.yaml`, enables systemd units, verifies `/healthz`. Supports `SWEEPER_BRANCH=` override and `--uninstall`.
- **Standalone `ipracticom-sweeper-api.service`** — runs the Flask dashboard (with v6 + SPA routes) on `127.0.0.1:8787` as a long-lived service, separated from the periodic sweeper. Bearer-token auth via `AGENT_API_TOKEN` (open mode if unset).
- **`scripts/update.sh`** with `--check` / `--version` / `--rollback`. Backs up `/etc/ipracticom-sweeper/` and operator state to `/var/lib/ipracticom-sweeper/.update_backup` before every pull.
- **SPA dashboard variants A/B** (commit `d79a535`): `/spa` chooser + `/spa/a` (Google AI Studio port, Tailwind+Inter+indigo) + `/spa/b` (impeccable polish, OKLCH+Heebo+motion). Both render the live `/api/snapshot`. New module `ipracticom_sweeper.spa_context` with pure `shape_spa_context` view-model. 11 new tests in `tests/test_spa_variants.py`.

### Fixed
- `test_rtl.py::test_chat_log_classes_use_logical_properties` false-positive: added a `/* --- end Chat shell */` delimiter so the test regex stops at the actual chat block instead of bleeding into v6 CSS. Zero visual change.

### Notes
- Total test count: **1083 passed** (from 1034 in v0.6.0). Full suite exits 0 in ~7 min.
- Skills applied in this cycle: `impeccable`, `emil-design-eng`, `israeli-ui-design-system`, `design-tasks-protocol`, `build-product`.

## [0.6.0] — 2026-07-01 — v6 dashboard rewrite (Sprints 5–7)

### Added — v6 dashboard surface (14 new routes, +3004 LOC, +38 tests)
- **Sprint 5 — Theme + layout (`0db2f9a`):** dark slate CSS theme, sidebar layout (`_v6_sidebar.html`), stats bar (`_v6_stats_bar.html`), `_v6_layout.html` shell, `v6_index.html`. Backwards-compatible with legacy dashboard.
- **Sprint 6 — Machine list + maintenance (`cc6f299`):** `/v6/machines` (table view of all hosts), `/v6/machines/<host>/action` (gated execute), `/v6/machines/<host>/maintenance` + `/maintenance/off` (snooze window for noise reduction).
- **Sprint 7 — Live alerts + log stream + heatmap/uptime (`482fc48`):**
  - `/v6/alerts` + `/v6/alerts/page` — JSON list + HTML wrapper, polled client-side every 5s. Category tabs (network / performance / security / system).
  - `/v6/alerts/<id>/snooze` — durations 15m / 1h / 24h, rejects bad input with 400. Remote mode refuses with 400.
  - `/v6/alerts/<id>/resolve` — same discipline as snooze.
  - `/v6/logs` + `/v6/logs/page` — tails latest 200 lines from `freeswitch.log` → `freeswitch.log.1`, falls back to sweeper audit log. Read-only; pause/play/clear/auto-scroll. POST/PUT/DELETE all rejected.
  - `/v6/metrics/events_heatmap` — 7×24 bucket grid from monitor audit log.
  - `/v6/metrics/uptime_30d` — 30 `{date, ratio}` entries (no-data = 1.0).
  - `/v6/metrics/page` — both above rendered as inline SVG (zero JS frameworks, zero Recharts).

### Safety invariants (held across all v6 writes)
- **Approve-before-mutate:** every v6 write surface (`alerts/snooze`, `alerts/resolve`, `machines/action`, `machines/maintenance`) writes a `RepairProposal`. None of them mutates host state without an explicit operator approval cycle.
- **Remote mode refuses:** the `RemediationClient` remote sentinel rejected at all v6 mutating routes — verified by tests.
- **Read-only enforcement:** all v6 read endpoints reject non-GET methods with 405.

### Tests
- **+38 v6 tests** on top of v0.5.0 baseline:
  - `tests/test_v6_theme.py` — 7 (CSS class wiring)
  - `tests/test_v6_sidebar.py` — 9 (template render + active-link)
  - `tests/test_v6_stats_bar.py` — 10 (badge count, JSON shape)
  - `tests/test_v6_machines.py` — 8 (table render, empty hosts handling)
  - `tests/test_v6_machine_actions.py` — 18 (gating: needs_approval, remote refusal, repair policy stub)
  - `tests/test_v6_alerts.py` — 16 (snooze/resolve happy + sad paths, remote refusal, valid durations)
  - `tests/test_v6_logs.py` — 13 (read-only verification, fallback when FS log missing)
  - `tests/test_v6_metrics.py` — 9 (heatmap bucket math, uptime no-data default = 1.0)
- Legacy dashboard smoke (`tests/test_sweeper.py` + `test_dashboard.py`) — 64/64 pass in 20.9s, **zero regressions**.

### Operator notes
- v6 routes live under `/v6/*`. Legacy `/dashboard`, `/inspector`, `/catalogue`, `/chat`, `/machines` all keep working — no URL was renamed or removed.
- All v6 templates are inline-SVG / vanilla JS — no new frontend dep was added.
- v6 routes inherit the same auth + remote-mode + repair-policy gating as the legacy dashboard.

### Migration
- Update your reverse proxy / dashboard nav if you want v6 as the default. Otherwise nothing changes — both UIs coexist on the same Flask process.


## [0.6.1] — 2026-07-01 — One-liner installer + agent_api service

### Added
- **`install.sh`** at repo root — single-file installer callable as `curl ... | sudo bash`. Detects apt vs dnf, clones to `/opt/ipracticom-sweeper`, installs OS + Python deps, lays out `/var/lib/ipracticom-sweeper/{audit,snapshots,cache,fleet,pending_repairs}`, seeds `/etc/ipracticom-sweeper/repair_policy.yaml` from `etc/`, hands off to `scripts/install-systemd.sh`, verifies `http://127.0.0.1:8787/`. Idempotent. Supports `--uninstall` and `SWEEPER_BRANCH=master`. Closes the gap where every fresh install previously required 4+ manual steps.
- **`systemd/ipracticom-sweeper-api.service`** — new long-running unit binds `python3 -m ipracticom_sweeper.dashboard --port 8787` so `scripts/update.sh --verify` (which hits `/healthz`) actually has a process to talk to. Before this, fresh installs passed `--check` (timer active) but failed `--verify` with `warn: agent_api /healthz not responding`.

### Changed
- **`scripts/install-systemd.sh`** rewritten: now installs ALL three units (sweeper.service, sweeper.timer, sweeper-api.service); seeds `repair_policy.yaml` from `etc/` (not the non-existent `rules/`); adds `--purge` to wipe `/var/lib/ipracticom-sweeper/` for clean re-installs; prints a verification banner.
- **`scripts/update.sh`** Step 8 now probes one `/v6/*` route so a v6 install that breaks the rewrite is caught at upgrade time (was: only `/healthz` check).
- **`sweeper.py`** `--version` flag (prints `ipracticom-sweeper 0.6.1`).
- **`Makefile`** `install` target adds `--break-system-packages` (PEP-668 safe on Debian 12 / RHEL 9). New `make test-v6` smoke target.

### Docs
- **`README.md`** corrected stale numbers: test count 162 → 1034+, monitor modules 9 → 23, added v6 dashboard table, added `version` badge.

### Migration
None for existing installs. New installs gain the API service automatically. Existing installs can manually enable the API with:
```bash
sudo cp systemd/ipracticom-sweeper-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ipracticom-sweeper-api.service
```
## [0.5.0] — 2026-06-30 — FreeSWITCH coverage + Chat assistant

### Added
- **FreeSWITCH monitoring (FS-01..FS-25)** in `src/ipracticom_sweeper/monitor/freeswitch.py`. Four tiers, run via `monitor.checks.run_all({})`:
  - **Tier 1 (service health)** — FS-01..05: process running, systemd unit active, SIP/SIPS ports, fs_cli reachable.
  - **Tier 2 (network integrity)** — FS-06..09: SIP peers/registrations/gateway status, RTP port range 16384-32768.
  - **Tier 3 (operational + baseline drift)** — FS-10..15: fs_cli latency, active calls/channels, log disk %, config mtime drift, baseline calls/hour.
  - **Tier 4 (edge cases)** — FS-16..25: CDR backup freshness, recordings age, sofia packet loss/jitter, codec mismatch, process RSS/CPU, TCP retransmit %, fs_log error rate, fail2ban jail status.
- **Catalogue view** (`/catalogue`) — read-only inspection of every registered check module, exported from the catalogue registry into the dashboard.
- **Inspector view** (`/inspector/host/<name>`) — drill-down per host for the 15 base monitors.
- **Chat shell** (`/chat`, `/chat/sessions`, `/chat/ws`) — Flask Blueprint + flask-sock WebSocket. Hebrew UI, in-memory session store, demo seeding.
- **Hybrid retrieval** (`chat_rag.py`) — stdlib BM25Okapi + TF-IDF cosine with Hebrew-aware tokenization (NFKC + niqqud stripping). Lazy index over `docs/`.
- **LLM router** (`chat_llm.py`) — mock-by-default with regex-driven intents; switches to OpenAI or Anthropic when the matching `*_API_KEY` env var is present. Single-iteration tool-use loop in v0.5.
- **Tool surface** (`chat_tools.py`) — `list_fs_checks`, `get_fs_check`, `run_fs_tier(1..4)`, `run_full_pipeline` (gated by `ENABLE_HEAVY_TOOLS=1`). Hard wall-clock timeout (8s default) around every check.
- **RTL-aware chat CSS** — `text-align: start`, `border-inline-start`, mobile breakpoint at 768px. No LTR overrides inside the chat DOM.

### Changed
- `repair_policy.yaml`: unchanged from 0.4.7 (still default `needs_approval`).
- Dashboard nav: new `צ'אט` link next to `קטלוג` and `מפקח בדיקות`.

### Operator notes
- **Chat defaults to mock mode** — no user text leaves the box unless `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set. The mock replies surface the tool call the LLM *would* make, so the UI demonstrates the wiring without external calls.
- **`run_full_pipeline` is opt-in**: set `ENABLE_HEAVY_TOOLS=1` before invoking. The full pipeline still takes 30-60s on a real FS host.
- **RAG corpus**: defaults to `docs/` at the repo root. Missing directory = 0 docs, chat UI still boots cleanly with no RAG context until populated.

### Tests
- **996/996 passing** (+262 from v0.4.7):
  - `tests/test_monitor_freeswitch_tier1.py` — 18 (FS-01..05)
  - `tests/test_monitor_freeswitch_tier2.py` — 28 (FS-06..09)
  - `tests/test_monitor_freeswitch_tier3.py` — 32 (FS-10..15)
  - `tests/test_monitor_freeswitch_tier4.py` — 48 (FS-16..25)
  - `tests/test_monitor_freeswitch_integration.py` — 7 (smoke)
  - `tests/test_inspector.py` — 9
  - `tests/test_catalogue.py` — 14
  - `tests/test_chat.py` — 19 (slice 3.1)
  - `tests/test_chat_rag.py` — 40 (slice 3.2)
  - `tests/test_chat_llm.py` — 32 (slice 3.3)
  - `tests/test_rtl.py` — 8 (slice 4.1)

### Migration
- Pull the new tag, run `pip install -e .` again to pick up `flask-sock` (only runtime dep change in this release). All existing monitors and the repair pipeline continue to behave as in 0.4.7.

## [0.4.7] — 2026-06-30 — Fix: bot run_now times out on long sweeps

### Fixed
- **`run_now` button failed with "agent_api request failed"** even though `/api/run` returned 200. Root cause: the bot's httpx client had a 10s default timeout, but the actual pipeline sweep takes 30-45 seconds (it runs 15 monitors: cpu, memory, disk, services, security, network, logs, processes, aws, kernel, process_tracker, fd_check, security_baseline, uptime, health, plus diagnose + adapt phases). On slow hosts the 10s timeout fired before the pipeline returned, surfacing as the misleading "agent_api request failed" error to the operator.
- **`trigger_run()` now passes `timeout=120.0`** to `_post()` — overrides the client default just for the sweep endpoint.
- **`_post()` now accepts an optional `timeout` kwarg** — passes it through to httpx. Default is `None` (httpx uses the client's 10s default).

### Verified end-to-end
- Restarted bot, ran `trigger_run()` via direct httpx call with 120s timeout — got back `defcon=4` + full diagnosis (15 modules, 2 needing human: disk warn + logs warn).
- Verified the failure mode by checking the agent_api log: `POST /api/run HTTP/1.1 200` actually succeeded at the server side; the bot's client just gave up before getting the response.

### Tests
- **734/734 passing** (+3 new):
  - `test_trigger_run_passes_long_timeout` — verifies timeout=120 is forwarded.
  - `test_post_helper_accepts_timeout_override` — verifies the kwarg plumbing.
  - `test_post_helper_default_timeout_is_none` — verifies default behavior unchanged for other endpoints.

## [0.4.6] — 2026-06-30 — Approval gate is the new default + rich alerts

### Changed (per Daniel 2026-06-30)
- **`repair_policy.yaml`: default flipped from `auto` → `needs_approval`**. Daniel: "בשלב זה צריך רק להתריע ולבקש אישור לפני התיקון". All 5 registered repairs (drop_caches, log_truncate_journald, service_restart, top_processes_snapshot, notify_human) now require explicit operator approval via the ✅ Approvals menu before execution. To whitelist a specific repair as auto, uncomment its line in `/etc/ipracticom-sweeper/repair_policy.yaml`.
- **The 21 monitors under `monitor/` continue to run** (cpu, memory, disk, network, services, ssl, http, kernel_errors, security_baseline, smart, processes, fd, aws, uptime, fd_check, aide_check, iostat, ...). They are unchanged — only the action policy on the 5 repairs changed.

### Added
- **`format_approvals_list` surfaces the full problem context** (Daniel #5): each pending repair now shows severity emoji (🚨/⚠️/ℹ️), what was detected (`problem.detail`), the metrics that drove the decision (`problem.metrics`), and the exact command to be executed (`proposed_command`). Operator can decide approve/reject without drilling into a separate view.

### Fixed
- **Pipeline tests updated to reflect the new policy**: drop_caches no longer auto-executes — it creates a pending proposal with the full problem context. `auto_repair=True` still applies, but only to repairs not gated by `needs_approval`.

### Tests
- **731/731 passing** (+7 new from v0.4.5):
  - `tests/test_approvals_render.py` — verifies severity emoji, problem.detail, proposed_command, metrics, overflow indicator.
  - 3 pipeline tests rewritten to assert proposal creation instead of auto-execution.

### Operator workflow (after this change)
1. Sweeper runs every 5 minutes, scans all 21 monitors.
2. When a monitor detects an issue, the pipeline calls `notify_human` (which is now itself gated — it sends the alert via the Telegram bot and writes the proposal to disk).
3. Operator receives a Telegram alert in the ✅ Approvals menu: "🚨 service_restart — זוהה: HTTP probe...503 — תיקון מוצע: systemctl restart nginx".
4. Operator clicks ✅ Approve or ❌ Reject.
5. Only after approval does `execute_repair` actually run.

## [0.4.5] — 2026-06-30 — Bot: render real metrics + English connector prompts

### Fixed
- **`format_fleet_host` ignored the `extra` block** — the v0.4.4 endpoint was returning the psutil snapshot, but the formatter only printed defcon/problems/last_seen. Operators saw `🖥️ CPU: ❌ (אין נתונים)` on local hosts. v0.4.5 surfaces CPU% (with core count), memory (used/total MB + %), disk (used/total GB + %), network (MB sent/recv), uptime (Xd Yh Zm + booted_at) inline.
- **`fleet_host` handler was making a redundant `/api/snapshot` call** to fill in the same data `extra` already had. Removed the extra HTTP roundtrip — the host dict from `/api/fleet/local` is now sufficient.
- **`_format_local_metrics` now understands the v0.4.4 `extra` block** (cpu.percent / memory.percent / disk.percent / network.*) in addition to the legacy `modules.*.details.percent` shape from `/api/snapshot`. Backward-compatible.

### Changed
- **Connector form prompts are now in English** (operator requested English). The 4 steps (name, instance_id, region, tags) and all validation error messages now say exactly which value format is expected (`i-` prefix, AWS region examples, `key=value,key=value` for tags, etc.). The "❌ Cancel" button is also English.
- **`pyproject.toml`** → 0.4.5

### Tests
- **724/724 passing** (+10 from v0.4.4: `tests/test_fleet_metrics_render.py` covers the new formatter behaviour and the English prompt contract).
- Updated `tests/test_telegram_handlers_fleet.py::test_fleet_host_local_shows_live_metrics` to feed the `extra` block instead of the legacy `modules` shape.

## [0.4.4] — 2026-06-30 — Real local metrics (psutil) + seed connector cleanup

### Added
- **`collect_local_metrics()` in `monitor/health.py`** — uses `psutil` to snapshot CPU%, cores, memory (percent + used_mb + total_mb), disk (percent + used_gb + total_gb), network (bytes_sent + bytes_recv), uptime_seconds, and booted_at. Returns a graceful error dict if psutil fails (sandbox / permission issues) instead of crashing the pipeline.
- **`record_run()` now auto-attaches the local metrics snapshot to `heartbeat.extra`** when no `extra` is passed in. The next pipeline run will populate the field without any caller change.
- **`psutil>=5.9.0`** added to `pyproject.toml` runtime dependencies.
- **`tests/test_local_metrics.py`** — 9 new tests covering collector shape, graceful failure, `record_run` integration, and `/api/fleet/local` exposure of the metrics block.

### Fixed
- **`/api/fleet/local` was returning `extra: {}`**: the v0.4.3 endpoint already forwarded `extra` from the heartbeat, but the heartbeat itself never contained the snapshot. With v0.4.4, every pipeline run writes the psutil snapshot into `extra`, so the endpoint surfaces real CPU/memory/disk/network/uptime numbers.
- **v0.4.3 CHANGELOG claimed "Fleet host detail now shows live CPU/..."** — that promise was only partially wired (the forwarding existed, the data didn't). v0.4.4 closes the gap.

### Changed
- **`/var/lib/ipracticom-sweeper/connectors.yaml`** — seed connectors (`prod-web-1`, `prod-db-1`, `staging-web-1`) replaced with `connectors: []`. They were placeholder data with `Unable to locate credentials` errors that confused operators on first launch. Add real SSM connectors via the 🔌 Connectors menu in the bot.
- **`pyproject.toml`** → 0.4.4

### Tests
- **714/714 passing** (+9 from v0.4.3).

## [0.4.3] — 2026-06-30 — Bot Polish + Logs

### Changed (per user feedback 2026-06-29)
- **Settings**: only the Telegram connectivity test remains. Removed Slack/API/identity buttons — they were either operator-level concerns or duplicate info.
- **Connectors**: header now explains what a connector is + flags seed data explicitly. Operators no longer see "3 connectors" and wonder if they accidentally added servers.
- **Connector delete**: properly checks `resp.status_code` and surfaces 404/500/connection errors instead of silently "refreshing" the list.
- **pyproject.toml** → 0.4.3

### Added
- **2 new agent_api endpoints**:
  - `GET /api/logs` — list every available audit log with its tail (repairs/monitor/heartbeat/last_result)
  - `GET /api/logs/download?name=...` — download a single log (or all) as a text file with truncation cap
- **2 new agent_client methods**: `get_logs(tail=50)`, `get_logs_download_url(name="all")`
- **Fleet host detail** now shows live CPU/זיכרון/דיסק/רשת from the latest snapshot (local host only)
- **2 new buttons per host**: "📜 הצג לוגים" (inline tail) + "⬇️ הורד לוג כקובץ" (Telegram document)
- **Fleet download** handler fetches the log from agent_api and sends it as a `reply_document` to the calling user
- **`_send_result(None)`** — dispatcher now supports handlers that already sent a reply (e.g. document upload) and want the dispatcher to stay silent

### Tests
- **705/705 passing** (was 669 in v0.4.2, +36 new)
- `test_agent_api_logs.py` — 12 tests (catalog, download, truncation, auth, 404)
- `test_telegram_handlers_fleet.py` — 9 tests (host view, live metrics, log tail, format helper)
- `test_telegram_handlers_settings.py` — 3 tests (menu, test_tg, error path)
- `test_telegram_handlers_connectors.py` — 5 tests (seed detection, delete 200/404/500/network)
- `test_telegram_agent_client.py` — 5 new tests for the 2 new methods (tail param, URL with/without token)

## [0.4.2] — 2026-06-29 — Telegram Bot Dashboard Parity

### Added
- **6-section main menu** (`full_menu()`): Dashboard, History, Approvals, Connectors, Fleet, Settings
- **3 new agent_api endpoints**:
  - `GET /api/history` — catalog (distinct metrics + hosts + per-metric counts via SQL)
  - `GET /api/approvals` — list pending repair proposals
  - `POST /api/approvals/<id>/approve` — execute the repair now + archive as approved
  - `POST /api/approvals/<id>/reject` — archive as rejected
  - `GET /api/fleet` — local host + every configured SSM connector
  - `GET /api/fleet/<host>` — per-host details (local reads heartbeat; connectors read config)
- **6 handler modules** (`src/.../telegram_bot/handlers/`): dashboard, history, approvals, connectors, fleet, settings
- **Pager utility** (`services/pager.py`) — pagination for inline keyboards (8 rows/page, 64-byte callback limit, oversized callback truncation)
- **Conversation state** (`states.py`) — `ConnectorFormState` dataclass for the multi-step connector CRUD flow
- **5 new agent_client methods**: `get_history_catalog`, `approve_repair`, `reject_repair`, `list_approvals`, `list_fleet`, `get_fleet_host`, `trigger_run`
- **7 new keyboard builders**: `full_menu`, `dashboard_menu`, `history_overview_menu`, `history_metric_menu`, `approvals_menu`, `approval_action_kb`, `connectors_menu`, `connector_actions_kb`, `fleet_menu`, `settings_menu`, `confirm_kb`
- **5 new formatters**: `format_dashboard`, `format_history_catalog`, `format_approvals_list`, `format_approval_result`, `format_connectors_list`, `format_connector_detail`, `format_fleet_list`, `format_fleet_host`
- **Free-text message handler** for the connector form flow (4 steps: name → instance_id → region → tags)
- **Approve = immediate execute** (per user request — not just mark approved)

### Changed
- `bot.py` rewired end-to-end — 30+ callback patterns, all gated by `authorized_only`
- `keyboards.py` extended (backwards-compat: `main_menu()` still returns 4-button v0.4.1 menu)
- `formatter.py` extended (all v0.4.1 functions unchanged)
- `pyproject.toml` → 0.4.2

### Tests
- **669/669 passing** (was 595 in v0.4.1, +74 new)
- `test_telegram_pager.py` — 17 tests
- `test_telegram_states.py` — 7 tests
- `test_telegram_agent_client.py` — 15 tests
- `test_telegram_formatter.py` — 23 tests
- `test_telegram_keyboards.py` — 17 tests
- `test_agent_api_endpoints.py` — 13 tests (covers all 3 new endpoints + auth + 404/409 paths)
- `test_handlers.py` (rewritten) — 7 tests for the v0.4.2 dashboard + history flow

## [0.4.1] — 2026-06-29 — Hebrew Dashboard as Telegram Bot

### Added — 8 New Collectors (Slices 1.1–1.8)
- **HTTP healthcheck** (`monitor/http_check.py`) — endpoint probing, status/time/error, supports per-target thresholds
- **SSL cert expiry** (`monitor/ssl_check.py`) — cert parsing, days-to-expiry, self-signed detection, diagnose hook
- **SMART disk health** (`monitor/smart_check.py`) — ReallocatedSectors/PendingSectors/CriticalWarning, wraps `smartctl -A -H`, falls back to `smartctl -a`
- **Kernel Oops/MCE/segfault** (`monitor/kernel_errors.py`) — dmesg + journalctl scanning with 1h + 24h windows
- **iostat I/O latency** (`monitor/iostat.py`) — per-device `r_await`/`w_await`/`util` via sysstat
- **Process tracker** (`monitor/process_tracker.py`) — top-N by RSS, service restart counter from journalctl
- **File descriptor monitor** (`monitor/fd_check.py`) — system-wide FD usage, per-process top-N, `/proc/sys/fs/file-nr`
- **AIDE file integrity** (`monitor/aide_check.py`) — runs `aide --check`, parses summary + reports added/changed/removed

### Added — Storage & Integration (Slices 2.0–5.0)
- **SQLite TimeSeriesDB** (`storage/timeseries.py`) — 30-day retention, per-mount disk prefix queries, atomic batch writes
- **Time-series pipeline integration** — every pipeline run writes defcon + system metrics to DB
- **`/api/history/<metric>` endpoint** — query historical data for any metric, returns `[{ts, value}]`
- **Predict wire** (`predict/integration.py`) — bridges TimeSeriesDB to predict layer, adds `predictions[]` to snapshot
- **`/api/predictions` endpoint** — run predictions on demand, returns per-metric forecasts
- **Notify deduplicator** (`notify/pipeline.py`) — fingerprint-based dedup with kind+message+host, critical bypasses dedup
- **Evidence bundle** (`evidence/bundle.py`) — JSON snapshot + SHA-256 signature, no AWS dep, local-only
- **`/api/evidence/export` endpoint** — on-demand bundle export, returns signed JSON

### Added — Security & Docs (Slices 6.0–7.0)
- **Security baseline** (`monitor/security_baseline.py`) — sshd_config drift detection, SUID binary scanner, listening ports baseline
- **`/api/security` endpoint** — SSH/SUID/ports summary
- **`MONITORING_COVERAGE.md`** — authoritative list of all 20 modules, gaps, extension guide, thresholds cheat sheet
- **`repair_policy.yaml`** — defaults: auto for `drop_caches`/`log_truncate_journald`/`top_processes_snapshot`/`notify_human`, needs_approval for `service_restart`

### Added — Production Hardening (Slice 8.0)
- **bootstrap.sh** — added system deps: `smartmontools`, `sysstat`, `aide`, `python3-venv`, `python3-pip`
- **pyproject.toml** — bumped to v0.4.0, added `[test]` extras (`pytest`, `freezegun`, `pytest-asyncio`)
- **Editable install with extras** — `pip install -e ".[test]"` for test deps

### Added — Telegram Dashboard (v0.4.1)
- **Telegram bot** (`telegram_bot/`) — full Hebrew dashboard as a Telegram bot, no dashboard/domain needed
  - `config.py` — env-based `BotConfig` with `ALLOWED_CHAT_IDS` whitelist (fail-fast on missing)
  - `auth.py` — `@authorized_only` decorator; silent rejection on unauthorized
  - `services/agent_client.py` — async httpx wrapper for `/api/snapshot`, `/api/history/<m>`, `/api/predictions`, `/api/evidence/export`
  - `keyboards.py` — inline keyboards (main/status/history) with Hebrew labels + 🔙 back button
  - `formatter.py` — HTML formatting with DEFCON emoji, smart truncation, Hebrew error messages
  - `handlers.py` — `start`/`status`/`problems`/`history`/`security` returning `{"text", "reply_markup"}` dicts
  - `bot.py` — `python-telegram-bot` Application wiring + 8 handlers + global error handler
- **`scripts/install_telegram_bot.sh`** — creates `/etc/ipracticom-sweeper/telegram-bot.env` + systemd unit, with `--uninstall` flag
- **`bootstrap.sh`** — calls telegram bot installer (set `SKIP_TELEGRAM_BOT=1` to opt out)
- **Tests: 580** (531 → 580, +49 new, 0 failing)

### Tests
- **469 → 531** (+62 new tests, 0 failing)
- All new modules: 100% TDD (RED → GREEN → wire → commit)
- 3 new endpoint test suites (history/predictions/evidence)
- Sandbox-validated: clean clone → venv → `pip install -e ".[test]"` → 531 passed

### Changed
- Pipeline now writes to TimeSeriesDB on every run (auto-prune at 30 days)
- Snapshot payload includes `predictions[]` and `evidence_bundle` (optional)
- All 8 new collectors auto-invoke if their config is present
- pyproject.toml `version` 0.3.0 → 0.4.0

## [0.3.0] — 2026-06-28 — Week 3 complete

### Added
- **Dashboard (Flask)** — server-rendered UI with classical typography (Cormorant Garamond + Inter + JetBrains Mono), forest-green accent, DEFCON-aware banner, modules grid, problems list, repairs list, rules sidebar
- **Agent HTTP API** (`agent_api.py`) — standalone REST API exposing snapshot/run/notify operations, with bearer-token auth (`AGENT_API_TOKEN`)
- **Agent client** (`agent_client.py`) — typed HTTP client for remote dashboard mode, wraps httpx with explicit error handling
- **Remote dashboard mode** — `SWEEPER_REMOTE_URL` env var switches the dashboard to proxy through an agent; UI shows "REMOTE MODE" badge and remote banner
- **systemd service + timer** — `systemd/ipracticom-sweeper.{service,timer}`, runs every 5 minutes
- **`install-systemd.sh`** — one-shot installer with `--uninstall` flag
- **Modules dictionary in pipeline result** — dashboard can show all 9 module statuses, not just diagnose output
- **Tests: 162 total** — 19 dashboard tests, 20 agent API+client tests, 21 systemd tests, 12 notify-pipeline tests, 90 baseline (monitor/diagnose/repair/adapter/pipeline)

### Changed
- `notify.format_*` now accepts BOTH legacy snapshot shape AND new `PipelineResult` shape (auto-detect by `defcon` key)
- `pipeline.run_pipeline` now invokes `notify_pipeline_result` automatically when DEFCON < 5 (green runs are silent)
- `dashboard._fetch_snapshot/_identity/_rules_summary` — new helpers that route to remote or local based on env var
- Local imports in pipeline/dashboard moved to module-level for testability

### Fixed
- `m.get("options")` AttributeError on malformed disk mounts — guarded with `isinstance(m, dict)`
- structlog `level=N` keyword clash with built-in — renamed to `level_value`
- structlog writing to stdout polluted JSON output — configured stderr at import time in `__init__.py`
- systemd service marked warn/crit as "failed" — added `SuccessExitStatus=1 2 3`
- Flask template crashed on remote mode (rules_summary contained `_remote=True` sentinel) — template now branches with `{% if rules_summary.get('_remote') %}`

## [0.2.0] — 2026-06-28 — Diagnose + Repair

### Added
- **Diagnose engine** (`diagnose/engine.py`) — 5 diagnosers (cpu/memory/disk/services/security) → DEFCON + safe_repairs + needs_human
- **Adapter** (`diagnose/adapter.py`) — normalizes monitor field names for diagnose consumption
- **Repair actions** (`repair/actions.py`) — 5 actions with safety classification (SAFE/GUARDED/DANGEROUS/NEVER), snapshot system, registry pattern
- **Pipeline** (`pipeline.py`) — full 5-step orchestrator: monitor → adapt → diagnose → repair → notify
- **Tests: 77 total** — 26 diagnose, 11 adapter, 14 repair, 13 pipeline, 13 monitor (existing)

## [0.1.0] — 2026-06-28 — Initial Monitor

### Added
- **Monitor layer** — 9 collector modules: cpu, memory, disk, network, services, logs, processes, security, aws
- **Audit logger** (`audit/logger.py`) — JSONL event emitter, structlog-based
- **Config loader** (`config.py`) — YAML rules with deep-merge defaults, IMDSv2 server-id detection
- **Notifier** (`notify.py`) — Slack Block Kit + Telegram Markdown formatters, async httpx sender
- **CLI** (`sweeper.py`) — argparse, `--json`/`--quiet`/`--rules`, exit codes map to overall_status
- **Tests: 26** for monitor (cpu/disk/memory)

## [Unreleased]

### Planned
- Fleet dashboard view (multiple agents side-by-side)
- Prometheus /metrics endpoint
- Repair rollback CLI tool (`sweeper rollback <snapshot_id>`)
- AWS-specific modules (RDS connection check, ECS task health)