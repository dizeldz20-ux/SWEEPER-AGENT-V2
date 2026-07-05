# iPracticom Sweeper v0.4.0 — Comprehensive Linux Observability Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Transform iPracticom Sweeper from a basic 11-module health checker into a comprehensive Linux server observability agent that monitors every aspect of a host, with full local data retention and rich dashboard visualization.

**Architecture:** Extend the existing Sweeper collectors (currently 11) to 19+ modules, add local time-series storage (SQLite), wire existing unused subsystems (`predict/`, `runbooks/`, `evidence/`, `notify/`), and add new dashboard sections. All data stays on-host per user requirement. Zero external dependencies beyond what's already declared.

**Tech Stack:** Python 3.10+, Flask, SQLite (local), structlog, boto3 (SSM fleet), existing infrastructure. No new external services, no Prometheus, no Grafana — user explicitly chose local-only with full dashboard.

---

## Pre-Verified Facts (this turn, observed live)

- ✅ 11 collectors exist: `monitor/{cpu,memory,disk,network,processes,services,security,logs,uptime,aws,health}.py` — verified by `ls`
- ✅ `predict/` exists with `linear.py` (linear regression) + `analyzer.py` (crossing prediction) — verified by `grep`
- ✅ `runbooks/` exists with `engine.py` (RunbookEngine class) — verified by `head`
- ✅ `evidence/` exists with `exporter.py`, `retention.py`, `signer.py` — verified by `ls`
- ✅ `notify/` exists with `deduplicator.py`, `fingerprint.py`, `queue.py`, `legacy.py` — verified by `ls`
- ✅ `bootstrap.sh` is the installer (4KB, idempotent) — verified by `head`
- ✅ 9 templates in `templates/`: base, dashboard, error, connectors, fleet, approvals, approval_detail, settings, history
- ✅ `diagnose/engine.py` has `diagnose_cpu`, `diagnose_memory`, `diagnose_disk` etc.
- ✅ Pipeline exists at `pipeline.py` with auto-repair flow
- ✅ Tests: 469 passing in sandbox clone, exit=0
- ✅ `etc/repair_policy.yaml` was added today (fix for 3 failing tests)

**Current gap** (from earlier session_search): missing modules for HTTP, SSL, SMART, AIDE, file integrity, process insights, I/O, kernel Oops, security baseline, time-series DB, growth prediction visualization, anomaly detection, evidence viewer in dashboard.

---

## 5 Build Principles (Contract)

1. **Local-only data retention** — everything stored on the monitored host, no central server required.
2. **Zero new external services** — no Prometheus, no Grafana, no Elasticsearch. SQLite + the existing dashboard.
3. **Extend, don't rewrite** — add collectors to the existing `monitor/` pattern, don't fork the architecture.
4. **Wire the unused subsystems** — `predict/`, `runbooks/`, `evidence/`, `notify/` exist in code but aren't visible in the dashboard. Surface them.
5. **Sandbox-verified before merge** — every slice ends with 469+ tests passing on a fresh clone, plus a manual smoke test of the new feature.

---

## Scope (Locked from user)

- **Time:** Unlimited ("all the time in the world")
- **Storage:** Local on each monitored host
- **Dashboard:** Full documentation in the dashboard UI
- **Initial test:** 2 servers (user has these ready)
- **Production scale:** Many servers, but each is independent (no central aggregation in this slice)
- **OS:** User said "אין לי מושג" — we'll detect OS at install and document supported targets

---

## Slice Plan (8 slices, one slice per user-facing milestone)

Each slice is independently shippable. User approves after each.

---

### Slice 1.1: HTTP healthcheck collector

**Why first:** User's most common pain point. "Is the site up?" is the #1 question. Currently the Sweeper has zero HTTP/endpoint awareness.

**Files:**
- Create: `src/ipracticom_sweeper/monitor/http_check.py`
- Create: `src/ipracticom_sweeper/diagnose/http_diagnose.py`
- Modify: `src/ipracticom_sweeper/monitor/__init__.py` (export)
- Modify: `src/ipracticom_sweeper/diagnose/engine.py` (call diagnose_http)
- Modify: `src/ipracticom_sweeper/pipeline.py` (run monitor_http)
- Modify: `rules/default.yaml` (default endpoints)
- Modify: `templates/dashboard.html` (HTTP health section)
- Create: `tests/test_monitor_http.py`
- Create: `tests/test_diagnose_http.py`

**TDD:**
1. Test `collect_http_endpoints` returns list of {url, status_code, response_time_ms, ssl_days_remaining, error}
2. Test `diagnose_http` flags 5xx, slow (>2s), SSL expiring <30 days
3. Implement collector (httpx + asyncio.gather for concurrency, 5s timeout)
4. Implement diagnose (5xx → CRIT, slow → WARN, SSL <30d → WARN)
5. Wire into pipeline
6. Add 1 default endpoint: `https://www.google.com` (placeholder, user edits in settings)
7. Dashboard section: green/yellow/red per endpoint + SSL countdown

**Validation:** `pytest -q` shows 469+1+1=471 tests passing. Sandbox smoke: `curl /api/snapshot | jq .diagnosis.modules.http` shows 1 endpoint with status.

**Commit:** `feat(http-check): add HTTP endpoint healthcheck collector with SSL expiry detection`

---

### Slice 1.2: SSL cert expiry monitor

**Why separate from 1.1:** SSL expiry is a class of bug (forgotten renewal → outage) that deserves its own alerts, not buried in HTTP status. Can be reused for non-HTTP services (SMTP TLS, database TLS).

**Files:**
- Create: `src/ipracticom_sweeper/monitor/ssl_check.py`
- Create: `src/ipracticom_sweeper/diagnose/ssl_diagnose.py`
- Modify: `src/ipracticom_sweeper/monitor/__init__.py`
- Modify: `src/ipracticom_sweeper/diagnose/engine.py`
- Modify: `src/ipracticom_sweeper/pipeline.py`
- Modify: `rules/default.yaml`
- Modify: `templates/dashboard.html` (SSL section with countdown per cert)
- Create: `tests/test_monitor_ssl.py`
- Create: `tests/test_diagnose_ssl.py`

**TDD:**
1. Test `collect_ssl_certs` returns list of {host, port, issuer, expires_at, days_remaining, is_self_signed}
2. Test `diagnose_ssl` flags days_remaining < 30 (WARN), < 7 (CRIT), self_signed (INFO only)
3. Implement (uses `ssl.get_server_certificate` + `cryptography.x509`)
4. Wire into pipeline
5. Default hosts: empty (user must add in settings — security tool, not auto-probe)
6. Dashboard section: table of certs with days countdown + colored badge

**Validation:** `pytest -q` shows 473 tests passing.

**Commit:** `feat(ssl-check): add SSL cert expiry monitor with renewal alerts`

---

### Slice 1.3: SMART disk health collector

**Why third:** Disk failure is the #1 cause of data loss. SMART data predicts failures days in advance. Requires `smartctl` binary (apt-get install smartmontools).

**Files:**
- Create: `src/ipracticom_sweeper/monitor/smart_check.py`
- Create: `src/ipracticom_sweeper/diagnose/smart_diagnose.py`
- Modify: `src/ipracticom_sweeper/monitor/__init__.py`
- Modify: `src/ipracticom_sweeper/diagnose/engine.py`
- Modify: `src/ipracticom_sweeper/pipeline.py`
- Modify: `rules/default.yaml`
- Modify: `bootstrap.sh` (apt install smartmontools)
- Modify: `templates/dashboard.html`
- Create: `tests/test_monitor_smart.py` (mock smartctl output)
- Create: `tests/test_diagnose_smart.py`

**TDD:**
1. Test `collect_smart_health` returns list of {device, model, reallocated_sectors, temperature_c, overall_assessment}
2. Test `diagnose_smart` flags reallocated_sectors > 0 (WARN), > 100 (CRIT), temp > 55°C (WARN)
3. Implement (shells out to `smartctl -A -H` per disk, 10s timeout, graceful if smartctl missing)
4. Wire into pipeline
5. Graceful degradation: if `smartctl` not installed, status="unavailable" with install hint
6. Dashboard: per-disk health + temperature graph (last 24h from local time-series)

**Validation:** `pytest -q` shows 475 tests passing. Manual: install smartmontools, verify dashboard shows disks.

**Commit:** `feat(smart-check): add SMART disk health monitor with failure prediction`

---

### Slice 1.4: Kernel Oops / hardware error detector

**Why fourth:** Hardware errors and kernel panics are silent killers. `dmesg` shows them. This is the last "catch silent failures" slice before security.

**Files:**
- Create: `src/ipracticom_sweeper/monitor/kernel_errors.py`
- Create: `src/ipracticom_sweeper/diagnose/kernel_diagnose.py`
- Modify: `src/ipracticom_sweeper/monitor/__init__.py`
- Modify: `src/ipracticom_sweeper/diagnose/engine.py`
- Modify: `src/ipracticom_sweeper/pipeline.py`
- Modify: `rules/default.yaml`
- Modify: `templates/dashboard.html`
- Create: `tests/test_monitor_kernel.py` (mock dmesg output)
- Create: `tests/test_diagnose_kernel.py`

**TDD:**
1. Test `collect_kernel_errors` returns list of {timestamp, severity, subsystem, message, count_in_window}
2. Test `diagnose_kernel` flags Oops/panic (CRIT), MCE (CRIT), segfault (>5/hour → WARN)
3. Implement (`dmesg --since "5 min ago" --level err,crit,alert,emerg`, fallback to journalctl)
4. Wire into pipeline
5. Dashboard: count of errors per severity in last 24h, top 5 messages

**Validation:** `pytest -q` shows 477 tests passing.

**Commit:** `feat(kernel-errors): detect Oops, MCE, segfaults from dmesg/journalctl`

---

### Slice 1.5: I/O latency per device (iostat)

**Why fifth:** Disk full is obvious, but slow disk kills apps silently. `iostat -x` gives await (ms), queue depth, utilization.

**Files:**
- Create: `src/ipracticom_sweeper/monitor/iostat.py`
- Create: `src/ipracticom_sweeper/diagnose/iostat_diagnose.py`
- Modify: `src/ipracticom_sweeper/monitor/__init__.py`
- Modify: `src/ipracticom_sweeper/diagnose/engine.py`
- Modify: `src/ipracticom_sweeper/pipeline.py`
- Modify: `rules/default.yaml`
- Modify: `bootstrap.sh` (apt install sysstat for iostat)
- Modify: `templates/dashboard.html`
- Create: `tests/test_monitor_iostat.py` (mock iostat -x output)
- Create: `tests/test_diagnose_iostat.py`

**TDD:**
1. Test `collect_iostat` returns list of {device, await_ms, util_percent, rps, wps, queue_depth}
2. Test `diagnose_iostat` flags await > 50ms (WARN), > 200ms (CRIT), util > 80% (WARN)
3. Implement (parses `iostat -dx 1 2` output)
4. Wire into pipeline
5. Graceful degradation if sysstat not installed
6. Dashboard: per-device I/O latency + util

**Validation:** `pytest -q` shows 479 tests passing.

**Commit:** `feat(iostat): add I/O latency per device monitor`

---

### Slice 1.6: Process resource tracker (top hogs + restart detector)

**Why sixth:** Beyond "240 processes total" — need to know who is eating resources and which apps are crashing repeatedly.

**Files:**
- Create: `src/ipracticom_sweeper/monitor/process_tracker.py`
- Create: `src/ipracticom_sweeper/diagnose/process_diagnose.py`
- Modify: `src/ipracticom_sweeper/monitor/__init__.py`
- Modify: `src/ipracticom_sweeper/diagnose/engine.py`
- Modify: `src/ipracticom_sweeper/pipeline.py`
- Modify: `rules/default.yaml`
- Modify: `templates/dashboard.html`
- Create: `tests/test_monitor_process_tracker.py`
- Create: `tests/test_diagnose_process_tracker.py`

**TDD:**
1. Test `collect_process_top` returns list of top 10 by {pid, name, cpu_pct, mem_pct, runtime_seconds, restart_count}
2. Test `collect_process_restarts` counts systemd service restarts in last 1h from journalctl
3. Test `diagnose_process` flags restarts > 3/h (WARN), > 10/h (CRIT), single proc > 80% CPU/mem (WARN)
4. Implement (parse `/proc/[pid]/*` for current sample, journalctl for restart history)
5. Wire into pipeline
6. Dashboard: top 10 resource hogs + service restart counter per service

**Validation:** `pytest -q` shows 481 tests passing.

**Commit:** `feat(process-tracker): add top-N resource hogs and service restart detector`

---

### Slice 1.7: File descriptor + ulimit monitor

**Why seventh:** FD exhaustion is a silent app killer ("too many open files"). Worth its own slice because detection is tricky.

**Files:**
- Create: `src/ipracticom_sweeper/monitor/fd_check.py`
- Modify: `src/ipracticom_sweeper/monitor/__init__.py`
- Modify: `src/ipracticom_sweeper/diagnose/engine.py` (or a new diagnose module)
- Modify: `src/ipracticom_sweeper/pipeline.py`
- Modify: `rules/default.yaml`
- Modify: `templates/dashboard.html`
- Create: `tests/test_monitor_fd.py`

**TDD:**
1. Test `collect_fd_usage` returns {system_total, system_used, system_max, per_process: [{pid, name, fd_count}]}
2. Test `diagnose_fd` flags system_used/system_max > 0.8 (WARN), > 0.95 (CRIT)
3. Implement (reads `/proc/sys/fs/file-nr`, walks `/proc/[pid]/fd/` counts)
4. Wire into pipeline
5. Dashboard: system FD bar + top 5 FD users

**Validation:** `pytest -q` shows 482 tests passing.

**Commit:** `feat(fd-check): add file descriptor exhaustion detector`

---

### Slice 1.8: AIDE file integrity monitor (security)

**Why eighth and last of slice 1:** Foundation. AIDE detects file changes (config drift, unauthorized writes). Needs daily baseline + diff checks. Security domain.

**Files:**
- Create: `src/ipracticom_sweeper/monitor/aide_check.py`
- Create: `src/ipracticom_sweeper/diagnose/aide_diagnose.py`
- Modify: `src/ipracticom_sweeper/monitor/__init__.py`
- Modify: `src/ipracticom_sweeper/diagnose/engine.py`
- Modify: `src/ipracticom_sweeper/pipeline.py`
- Modify: `bootstrap.sh` (apt install aide, init baseline)
- Modify: `templates/dashboard.html`
- Create: `scripts/aide_init.sh` (first-time baseline creation)
- Create: `scripts/aide_check.sh` (daily check wrapper)
- Create: `tests/test_monitor_aide.py` (mock aide report)
- Create: `tests/test_diagnose_aide.py`

**TDD:**
1. Test `collect_aide_report` returns {last_check, added, removed, changed, total_changes}
2. Test `diagnose_aide` flags any non-zero change in `/etc`, `/bin`, `/usr/bin` (WARN), any in `/root/.ssh` (CRIT)
3. Implement (parses `aide --check` output, JSON if possible, fallback regex)
4. Wire into pipeline (run daily, not every 5min — use separate cron)
5. Dashboard: "last check", counts, link to diff report

**Validation:** `pytest -q` shows 484 tests passing. Manual: `aide --init`, `aide --check`, verify dashboard updates.

**Commit:** `feat(aide): add file integrity monitoring with daily baseline check`

---

### Slice 2.0: Local time-series storage (SQLite)

**Why after slice 1:** We have 19 collectors all returning data. Now we need to store it locally for trend analysis and dashboard history.

**Files:**
- Create: `src/ipracticom_sweeper/storage/__init__.py`
- Create: `src/ipracticom_sweeper/storage/timeseries.py` (SQLite wrapper, retention policies)
- Create: `src/ipracticom_sweeper/storage/migrations.py` (schema versioning)
- Modify: `src/ipracticom_sweeper/pipeline.py` (write to timeseries on every run)
- Modify: `src/ipracticom_sweeper/agent_api.py` (add `/api/history/<metric>` endpoint)
- Modify: `templates/dashboard.html` (use history endpoint for graphs)
- Create: `tests/test_storage_timeseries.py`

**TDD:**
1. Test `timeseries.write(host, metric, value, ts)` inserts into SQLite
2. Test `timeseries.query(host, metric, since, until)` returns list of (ts, value)
3. Test retention: metrics older than 30d are auto-pruned
4. Implement (SQLite, schema: `snapshots(host, ts, json_blob)`, `metrics(host, metric, ts, value)`)
5. Pipeline writes both on every run
6. API endpoint returns 24h/7d/30d data per metric
7. Dashboard adds simple time-series chart (use Chart.js or similar — already in static?)

**Validation:** `pytest -q` shows 487 tests passing. Manual: see data accumulate over hours, see chart render.

**Commit:** `feat(storage): add local SQLite time-series store with 30-day retention`

---

### Slice 3.0: Wire existing `predict/` subsystem into pipeline + dashboard

**Why:** The `predict/linear.py` and `predict/analyzer.py` exist but aren't called. Use them now that we have time-series data.

**Files:**
- Modify: `src/ipracticom_sweeper/pipeline.py` (after timeseries write, run predict on disk metrics)
- Modify: `src/ipracticom_sweeper/agent_api.py` (add `/api/predictions` endpoint)
- Modify: `templates/dashboard.html` (show "Disk will fill in X days" per mount)
- Create: `tests/test_predict_integration.py`

**TDD:**
1. Test `pipeline.run` populates `predictions` field with crossing times for disk, memory, inodes
2. Test `/api/predictions` returns dict of {metric: {days_until_critical, slope_per_day}}
3. Wire predict into pipeline
4. Dashboard: prominent "X days until disk full" banner when days < 30

**Validation:** `pytest -q` shows 488 tests passing.

**Commit:** `feat(predict): surface disk/memory filling predictions in dashboard`

---

### Slice 4.0: Wire `notify/deduplicator` + `notify/fingerprint` to avoid alert storms

**Why:** The notify subsystem exists but might not be wired. Alert deduplication prevents the same error from paging 100 times.

**Files:**
- Read: `src/ipracticom_sweeper/notify/deduplicator.py`
- Read: `src/ipracticom_sweeper/notify/fingerprint.py`
- Read: `src/ipracticom_sweeper/notify/queue.py`
- Modify: `src/ipracticom_sweeper/pipeline.py` (route through deduplicator)
- Modify: `src/ipracticom_sweeper/notify/__init__.py` if needed
- Create: `tests/test_notify_dedup.py`

**TDD:**
1. Test `deduplicator.should_send(fingerprint, window_seconds=300)` returns False if same fingerprint sent in last 5min
2. Test fingerprint is stable across runs (same problem = same hash)
3. Wire deduplicator into pipeline notifications
4. Verify no duplicate alerts in repeated runs

**Validation:** `pytest -q` shows 490 tests passing.

**Commit:** `feat(notify): wire deduplicator to prevent alert storms`

---

### Slice 5.0: Wire `evidence/exporter` + dashboard evidence viewer

**Why:** The `evidence/exporter.py` exists — let users export the audit log + repair history for compliance.

**Files:**
- Read: `src/ipracticom_sweeper/evidence/exporter.py`
- Read: `src/ipracticom_sweeper/evidence/signer.py`
- Modify: `src/ipracticom_sweeper/agent_api.py` (add `/api/evidence/export?from=&to=&format=json|csv`)
- Modify: `templates/dashboard.html` (new "Evidence" section with export buttons)
- Create: `tests/test_evidence_export.py`

**TDD:**
1. Test `/api/evidence/export?from=...&to=...&format=json` returns signed bundle
2. Test signer produces deterministic signature
3. Dashboard: 3 export buttons (repairs log, audit log, full bundle)

**Validation:** `pytest -q` shows 491 tests passing.

**Commit:** `feat(evidence): add evidence export UI with signed bundles`

---

### Slice 6.0: Security baseline (SSH config + SUID + open ports)

**Why:** Wrap up the security section started in slice 1.8.

**Files:**
- Create: `src/ipracticom_sweeper/monitor/security_baseline.py`
- Create: `src/ipracticom_sweeper/diagnose/security_baseline_diagnose.py`
- Modify: `src/ipracticom_sweeper/monitor/__init__.py`
- Modify: `src/ipracticom_sweeper/diagnose/engine.py`
- Modify: `src/ipracticom_sweeper/pipeline.py`
- Modify: `rules/default.yaml`
- Modify: `templates/dashboard.html`
- Create: `tests/test_monitor_security_baseline.py`
- Create: `tests/test_diagnose_security_baseline.py`

**TDD:**
1. Test `collect_ssh_config` returns {PermitRootLogin, PasswordAuth, etc.}
2. Test `collect_suid_binaries` returns list of SUID files (compare to baseline)
3. Test `collect_open_ports` returns {port, process, baseline_match}
4. Test diagnose flags: root login enabled (WARN), password auth (WARN), new SUID (CRIT), new open port (WARN)
5. Implement
6. Dashboard: security score (0-100) + breakdown

**Validation:** `pytest -q` shows 494 tests passing.

**Commit:** `feat(security-baseline): add SSH/SUID/port security drift detection`

---

### Slice 7.0: `MONITORING_COVERAGE.md` documentation in dashboard

**Why:** User asked for "all possible problems". Document what we cover, what we don't, and how to add more.

**Files:**
- Create: `docs/MONITORING_COVERAGE.md` (authoritative list of every check)
- Modify: `src/ipracticom_sweeper/agent_api.py` (serve the doc at `/docs/coverage`)
- Modify: `templates/dashboard.html` (link in nav)
- Modify: `templates/error.html` (link if page not found)

**Content:** Markdown table of all 19+ collectors: name, what it checks, threshold, how to disable, known limitations.

**Validation:** Manual: load `/docs/coverage` in dashboard, see full list.

**Commit:** `docs: add MONITORING_COVERAGE.md and link from dashboard`

---

### Slice 8.0: Production hardening + 2-server sandbox validation

**Why:** User has 2 real servers. Slice 1-7 might be sandbox-only. This is the production gate.

**Files:**
- Modify: `bootstrap.sh` (include all new dependencies)
- Modify: `pyproject.toml` (bump to v0.4.0, add new deps)
- Modify: `README.md` (document 19+ modules)
- Modify: `CHANGELOG.md` (slice-by-slice changelog)
- Create: `docs/DEPLOY_2_SERVERS.md` (the actual 2-server rollout)
- Modify: `templates/dashboard.html` (add health summary, version)

**Validation:**
- `bootstrap.sh` on fresh Ubuntu 22.04 → installs all deps (apt + pip)
- Run on user's 2 servers
- Monitor for 1 week
- Fix any issues found
- Document rollout in `docs/DEPLOY_2_SERVERS.md`

**Commit:** `release(v0.4.0): comprehensive observability — 19+ collectors, time-series, predictions, security`

---

## Test Counts (Projected Growth)

| Slice | New tests | Total |
|-------|-----------|-------|
| Baseline (today) | — | 469 |
| 1.1 HTTP | +2 | 471 |
| 1.2 SSL | +2 | 473 |
| 1.3 SMART | +2 | 475 |
| 1.4 Kernel | +2 | 477 |
| 1.5 I/O | +2 | 479 |
| 1.6 Process | +2 | 481 |
| 1.7 FD | +1 | 482 |
| 1.8 AIDE | +2 | 484 |
| 2.0 Time-series | +3 | 487 |
| 3.0 Predict | +2 | 488 |
| 4.0 Notify dedup | +2 | 490 |
| 5.0 Evidence | +1 | 491 |
| 6.0 Security baseline | +3 | 494 |
| 7.0 Docs | 0 | 494 |
| 8.0 Hardening | 0 | 494 |

---

## Files Likely to Change (Full List)

**New files (16):**
- `src/ipracticom_sweeper/monitor/{http_check,ssl_check,smart_check,kernel_errors,iostat,process_tracker,fd_check,aide_check,security_baseline}.py`
- `src/ipracticom_sweeper/diagnose/{http_diagnose,ssl_diagnose,smart_diagnose,kernel_diagnose,iostat_diagnose,process_diagnose,aide_diagnose,security_baseline_diagnose}.py`
- `src/ipracticom_sweeper/storage/{__init__,timeseries,migrations}.py`
- `scripts/{aide_init,aide_check}.sh`
- `docs/MONITORING_COVERAGE.md`
- `docs/DEPLOY_2_SERVERS.md`

**Modified files (~10):**
- `src/ipracticom_sweeper/monitor/__init__.py`
- `src/ipracticom_sweeper/diagnose/engine.py`
- `src/ipracticom_sweeper/pipeline.py`
- `src/ipracticom_sweeper/agent_api.py`
- `src/ipracticom_sweeper/notify/__init__.py`
- `rules/default.yaml`
- `templates/dashboard.html`
- `bootstrap.sh`
- `pyproject.toml`
- `README.md` + `CHANGELOG.md`

---

## Risks & Tradeoffs

1. **Local SQLite may not scale to 100+ collectors at 5-min intervals** — 30 days of 100 metrics * 5min = 864,000 rows. SQLite handles this fine. If user later adds 1000+ metrics, will need partitioning.
2. **AIDE on every server = baseline per server** — no central baseline. This is by user request (local-only). Tradeoff: new attack on all servers won't be caught by a shared baseline.
3. **No central aggregation** — if user has 50 servers, must SSH to each dashboard. User accepted this. Future slice could add a "read-only aggregator" that scrapes each agent's `/api/snapshot`.
4. **iostat and smartctl need apt packages** — bootstrap.sh handles, but some minimal containers might not have apt.
5. **Predict is linear regression** — fine for disk/memory, bad for seasonal metrics (CPU at 9am spike). Out of scope for v0.4.0.

---

## Open Questions (for user, not blocking)

1. **Dashboard auth** — current dashboard has basic auth. OK for 2-3 operators, painful for 10+. Out of scope for v0.4.0; flag for v0.5.
2. **What "all possible problems" means** — v0.4.0 covers the 19 collectors above. Theoretically more can always be added (eBPF, perf, strace, etc.). User can request new collectors as needed.
3. **Production scale** — when user has 50+ servers, will the local-only model still work? If not, a separate "fleet aggregator" slice can be added without changing the per-server design.

---

## Execution Approach

After user approves this plan, I'll use `subagent-driven-development`:
- Fresh `delegate_task` per slice with full context
- Spec compliance review after each slice (does it match this plan?)
- Code quality review after spec passes
- Per-slice approval from user before next slice
- Each slice ends with verified `pytest -q` count and a smoke test

**Per-slice loop:**
1. Build slice (TDD: red → green → refactor)
2. Run `pytest -q` yourself (not subagent's count)
3. Show user: slice done, test count, smoke evidence
4. WAIT for "continue" / "next slice" / "change plan"

---

**Plan complete and saved.** Path: `.hermes/plans/2026-06-29_153322-sweeper-comprehensive-observability.md`

**Total: 8 phases, 16 slices, ~16 days of focused work, no time pressure.**

Ready to execute Slice 1.1 (HTTP healthcheck) when you say go.
