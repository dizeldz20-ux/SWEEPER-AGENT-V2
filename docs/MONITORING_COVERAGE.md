# iPracticom Sweeper — Monitoring Coverage

This document is the **authoritative list of every check** the iPracticom Sweeper
performs on a monitored Linux host. If a check isn't here, it isn't running.

**Version**: v0.4.0
**Last updated**: 2026-06-29
**Total modules**: 20

---

## Default Modules (always running)

These modules run on every sweep and need no extra configuration.

| Module | What it checks | Source | Severity |
|---|---|---|---|
| `cpu` | Load average (1/5/15min), iowait%, steal%, idle% | `/proc/loadavg` | warn/crit |
| `memory` | RAM used%, swap used% | `/proc/meminfo` | warn/crit |
| `disk` | Disk used% per mount, inode%, read-only mount compliance | `df` | warn/crit |
| `network` | Packet drops, TCP retransmits, per-interface | `/proc/net/dev` | warn/crit |
| `services` | Failed systemd units | `systemctl` | warn/crit |
| `logs` | journald error/emerg/alert events per min, OOM events | `journalctl` | warn/crit |
| `processes` | Total procs, zombies, uninterruptible, running | `/proc/[pid]/stat` | warn/crit |
| `security` | Failed SSH attempts, sudo failures | `journalctl` | warn/crit |
| `aws` | EC2 instance status, CloudWatch quick stats (only on EC2) | boto3 | warn/crit |
| `uptime` | Boot time, uptime | `/proc/stat` btime | warn |
| `health` | Self-monitor: heartbeat freshness, last run | local file | warn |
| `kernel` | Oops, kernel panic, MCE, segfaults in last 5 min | `dmesg`/`journalctl` | crit for Oops/panic/MCE, warn for segfault |

---

## Optional Modules (require configuration)

These modules run only when configured in `rules/default.yaml` or override file.

### Application-level

| Module | What it checks | Config | Default severity |
|---|---|---|---|
| `http_check` | HTTP/HTTPS endpoint status, response time, transport errors | `rules.http.endpoints` (list of {url, name, timeout?}) | crit (5xx, unreachable), warn (4xx, slow >2s) |
| `ssl_check` | SSL cert expiry, issuer, self-signed detection | `rules.ssl.hosts` (list of {host, port?, timeout?}) | crit (<7d), warn (<30d) |
| `process_tracker` | Top 10 resource hogs (CPU+MEM), service restart counter | `rules.process_tracker.{top_n, window_minutes}` | crit (>95%), warn (>80%), crit (restarts>10/h) |

### Storage

| Module | What it checks | Config | Default severity |
|---|---|---|---|
| `smart_check` | SMART reallocated sectors, temperature, overall health | `rules.smart.devices` (list of /dev/...) | crit (FAILED, >100 realloc), warn (>0 realloc, >55°C) |
| `iostat` | Per-device I/O latency, util% | Auto-detects `iostat` binary | crit (>200ms await, >95% util), warn (>50ms, >80%) |
| `fd_check` | System-wide FD usage + top 5 consumers | None (always on, low cost) | crit (>95%), warn (>80%) |

### Security

| Module | What it checks | Config | Default severity |
|---|---|---|---|
| `aide` | File integrity (added/removed/changed) | Auto-detects `aide` binary | crit (changes in /etc, /bin, /usr, /sbin, /root/.ssh), warn (anywhere) |
| `security_baseline` | sshd_config hardening, SUID binaries, open ports | `rules.security_baseline.{expected_ssh_keys, expected_suid, allowed_ports}` | crit (root login, password auth, SUID drift), warn (X11, unexpected ports) |

---

## Subsystems (not modules, but data sources)

| Subsystem | Purpose | Where data lives |
|---|---|---|
| `storage` | Local time-series DB (SQLite) | `IPRACTICOM_SWEEPER_STATE_DIR/metrics.db` |
| `predict` | Linear regression on time-series data | Same DB; runs after each sweep |
| `notify` | Alert deduplication (5-min window) | In-memory cache per process |
| `evidence` | Signed audit/repair bundles | `IPRACTICOM_SWEEPER_STATE_DIR/evidence/` |
| `repair` | Auto-fix actions (drop_caches, log_truncate, etc.) | `etc/repair_policy.yaml` + `audit/repairs.jsonl` |
| `fleet` | Multi-host via AWS SSM | SSM SendCommand |

---

## What this does NOT check (gaps, future work)

| Gap | Why | When to add |
|---|---|---|
| Application logs (file-based, not journald) | Custom parsing per app | When user has a specific app |
| DNS resolution health | Needs dig/per-domain logic | When DNS is critical to user |
| Outbound connectivity checks | Smoke vs external | When egress is critical |
| Network interfaces detail (errors, drops) | Aggregate already in `network` | When user reports NIC issues |
| Per-package CVE feed | Needs apt/yum + vuln DB | When security is compliance-driven |
| Backup verification | Backup-specific | When user has backups |
| Filesystem errors (dmesg EXT4) | dmesg already partially covered in `kernel` | When corruption seen |
| Clock skew vs NTP | Not in scope | When time-sensitive ops matter |
| In-memory cache (NUMA, slab) | Memory module is high-level only | When tuning needed |

---

## How to extend

To add a new check:

1. **Create** `src/ipracticom_sweeper/monitor/<name>.py`:
   - `collect()` returns the raw data
   - `evaluate(values, rules)` returns `'ok' | 'warn' | 'crit'`
2. **Create** `src/ipracticom_sweeper/diagnose/<name>_diagnose.py`:
   - `diagnose_<name>(findings, rules)` returns list of `Problem`
3. **Register** in:
   - `src/ipracticom_sweeper/monitor/__init__.py` (add to imports + __all__)
   - `src/ipracticom_sweeper/monitor/checks.py` (call collect in run_all)
   - `src/ipracticom_sweeper/diagnose/engine.py` (add to DIAGNOSERS dict)
4. **Tests** in `tests/test_monitor_<name>.py` + `tests/test_diagnose_<name>.py`
5. **Add to this doc**

---

## Thresholds cheat sheet

| Metric | warn | crit | Where |
|---|---|---|---|
| CPU idle% | <20% | <10% | rules.cpu |
| Memory used% | >80% | >95% | rules.memory |
| Disk used% (any mount) | >80% | >95% | rules.disk |
| HTTP response time | >2s | n/a | rules.http.slow_response_ms |
| SSL cert days remaining | <30 | <7 | rules.ssl |
| SMART reallocated sectors | ≥1 | >100 | rules.smart |
| I/O await (ms) | >50 | >200 | rules.iostat |
| FD system used% | >80% | >95% | rules.fd_check |
| AIDE changes | any | in /etc, /bin, /usr, /sbin, /root/.ssh | rules.aide.critical_paths |
| Service restarts/h | >3 | >10 | rules.process_tracker |
| Process CPU/MEM | >80% | >95% | rules.process_tracker |
