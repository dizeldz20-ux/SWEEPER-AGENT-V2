# iPracticom AWS Linux Sweeper

> Server health monitor and safe auto-repair agent for AWS Linux fleets.
> Built for [iPracticom](https://github.com/dizeldz20-ux) production operations.

[![version](https://img.shields.io/badge/version-1.5.17-blue)]()
[![tests](https://img.shields.io/badge/tests-1147%20passing-brightgreen)]()
[![python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![license](https://img.shields.io/badge/license-internal-lightgrey)]()

## What it does

The sweeper runs every 5 minutes on each host (via systemd timer) and:

1. **Monitors** 23 subsystems: CPU, memory, disk, network, services, logs, processes, security, AWS metadata, FreeSWITCH (FS-01..25 across 4 tiers), SSL certs, HTTP endpoints, SMART disk health, kernel errors (Oops/MCE/segfault), iostat, process tracker, fd exhaustion, AIDE file integrity, security baseline (SSH/SUID/ports), uptime, health
2. **Diagnoses** findings against threshold rules and assigns a DEFCON level (5=green, 1=black)
3. **Repairs** safe issues automatically (drop_caches, journald vacuum)
4. **Notifies** Slack and/or Telegram when DEFCON ≤ 4 or when a security issue is detected
5. **Audits** every action to an append-only JSONL log

All monitoring, diagnosis and repair is **deterministic, rules-based, and audit-able**. The agent never calls an LLM in the hot path.

## Architecture

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  Monitor     │──▶│   Diagnose   │──▶│   Repair     │
│ 23 modules   │   │   DEFCON     │   │  safe only   │
└──────────────┘   └──────────────┘   └──────────────┘
       │                  │                  │
       ▼                  ▼                  ▼
   /proc/* + sofia    Rules + RepairSafety   /proc/sys/vm
   journalctl          Worst-wins defcon     journalctl --vacuum
   systemctl           safe_repairs list     systemctl restart
   freeswitch CLI      needs_human flags     fs_cli recovery
```

Every repair is preceded by a snapshot (`/var/lib/ipracticom-sweeper/snapshots/<uuid>.json`) for rollback.

## Quick start

### One-shot run

```bash
pip install -e .[dev]  # or: /usr/bin/python3 -m pip install -e . --break-system-packages
python3 -m ipracticom_sweeper.sweeper --json
```

### As a systemd service (recommended)

```bash
sudo bash scripts/install-systemd.sh
```

This will:
- Install the package site-wide
- Create `/var/lib/ipracticom-sweeper/{audit,snapshots,cache}`
- Enable the timer to run every 5 minutes
- Trigger one initial run

```bash
journalctl -u ipracticom-sweeper -f           # follow logs
systemctl list-timers ipracticom-sweeper       # next runs
systemctl start ipracticom-sweeper.service     # manual run
sudo bash scripts/install-systemd.sh --uninstall  # remove
```

### Dashboard (local mode)

```bash
python3 -m ipracticom_sweeper.dashboard --port 8787
open http://127.0.0.1:8787/
```

### Agent API (for fleet / remote dashboards)

```bash
AGENT_API_TOKEN=your-secret-token \
    python3 -m ipracticom_sweeper.agent_api --port 8787 --host 0.0.0.0
```

Endpoints:
- `GET  /healthz` — liveness + identity
- `GET  /api/snapshot` — latest cached result (auth: `Bearer <token>` if token configured)
- `GET  /api/snapshot/raw` — last 100 audit events (JSONL)
- `POST /api/run` — trigger fresh sweep
- `POST /api/notify/test` — send a test notification

### Dashboard connecting to remote agent

```bash
SWEEPER_REMOTE_URL=http://freeswitch.local:8787 \
SWEEPER_REMOTE_TOKEN=your-secret-token \
    python3 -m ipracticom_sweeper.dashboard --port 8790
```

The dashboard auto-detects `SWEEPER_REMOTE_URL` and proxies all data through the agent. The UI shows a "REMOTE MODE" badge.

## Configuration

Threshold rules live in `rules/default.yaml`. The agent loads defaults if the file is missing:

```yaml
cpu:
  load_avg_5min_warn: 2.0
  load_avg_5min_crit: 5.0
memory:
  used_percent_warn: 80
  used_percent_crit: 95
disk:
  used_percent_warn: 80
  used_percent_crit: 95
  read_only_mounts: ["/"]  # which mounts must be RO
services:
  critical_list: [nginx, postgresql]  # must be running
```

CLI overrides:

```bash
python3 -m ipracticom_sweeper.sweeper --rules /etc/iprcticom/rules.yaml --json
```

## Notifications

Set these env vars on the agent host:

```bash
export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
export TELEGRAM_BOT_TOKEN=123456:abc...
export TELEGRAM_CHAT_ID=-1001234567890
```

The agent sends notifications when:
- DEFCON ≤ 4 (yellow / orange / red / black)
- `force=True` (manual override for "still alive" pings)

Green runs are silent — no notification spam on healthy hosts.

## DEFCON levels

| Level | Label | Meaning | Auto-repair? |
|---|---|---|---|
| 5 | green | All healthy | n/a |
| 4 | yellow | Warning threshold tripped | No (log only) |
| 3 | orange | Critical threshold tripped | Yes if safe |
| 2 | red | Critical + persistent | Yes if GUARDED (with snapshot) |
| 1 | black | Unknown state / monitor crashed | No — alert humans |

## Repair actions

| Action | Safety | What it does |
|---|---|---|
| `drop_caches` | SAFE | Writes to `/proc/sys/vm/drop_caches` (level 1/2/3) |
| `log_truncate_journald` | GUARDED | `journalctl --vacuum-time=7d` |
| `service_restart` | GUARDED | `systemctl restart <unit>` (critical services only) |
| `top_processes_snapshot` | SAFE | Captures top-N by CPU for forensics |
| `notify_human` | SAFE | Sends to Slack/Telegram |

Snapshots are stored in `/var/lib/ipracticom-sweeper/snapshots/<uuid>.json`. Rollback commands are encoded in each snapshot.

## Testing

```bash
python3 -m pytest tests/ -v
```

Test count: **1145+ tests** across 23 monitor modules + 25 FreeSWITCH checks + 14 v6 dashboard routes + 11 SPA variant tests + 9 sidebar unification tests + 24 v1.5.16 code-review regression tests + test fixes (test_dashboard/v6_machines/v6_sidebar). Run with `make test-fast` (targeted) or `pytest -q tests/test_v6_*` for the v6 surface, `pytest -q tests/test_spa_variants.py` for the SPA comparison, `pytest -q tests/test_v616_code_review_fixes.py` for the v1.5.16 sweep.

## Architecture diagrams

The pipeline runs 5 steps:

1. **Monitor** → 23 modules collect from `/proc`, journalctl, systemctl, boto3, `fs_cli`, `ntptime`, SMART, AIDE, optional Prometheus exporters
2. **Adapt** → normalize field names (e.g. `mount` → `mountpoint`)
3. **Diagnose** → 5 diagnosers (cpu/memory/disk/services/security) → DEFCON + safe_repairs + needs_human
4. **Repair** → iterate over safe_repairs, snapshot → execute → audit
5. **Notify** → DEFCON<5: send Slack/Telegram, else silent

## Security notes

- The agent API requires a bearer token (`AGENT_API_TOKEN`) for any operation beyond `/healthz`.
- Default bind is `127.0.0.1` — change to `0.0.0.0` only inside a trusted network or behind a reverse proxy with TLS.
- The dashboard exposes only cached data — no write access.
- Repair actions are tagged with safety levels. SECURITY-class actions always escalate to humans (`DANGEROUS`, never auto-executed).

## Files

```
src/ipracticom_sweeper/
├── sweeper.py          # CLI entry point
├── pipeline.py         # Orchestrator (5 steps)
├── config.py           # Rules + DEFCON levels
├── notify.py           # Slack + Telegram formatter
├── monitor/            # 9 collector modules
├── diagnose/           # DEFCON engine + adapter
├── repair/             # 5 repair actions + snapshot system
├── audit/              # JSONL event log
├── dashboard.py        # Flask UI (local + remote)
├── agent_api.py        # Standalone HTTP API
└── agent_client.py     # Typed HTTP client
tests/                  # 1083+ tests across all surfaces
systemd/                # service + timer + agent_api units
scripts/                # install-systemd.sh, update.sh, install_telegram_bot.sh
install.sh              # one-liner installer (curl | bash)
```

## SPA dashboard variants (added in v0.6.1, commit `d79a535`)

Side-by-side comparison of two dashboard redesigns, served from the same Flask process and pulling from the live `/api/snapshot` (no mock fixtures). Designed so a peer can A/B pick the more comfortable one.

| Route | Purpose | Read/Write |
|---|---|---|
| `/spa` | Chooser landing (side-by-side cards) | read |
| `/spa/a` | **Variant A** — faithful Google AI Studio port (Tailwind, Inter, indigo, rounded-3xl) | read |
| `/spa/b` | **Variant B** — impeccable polish (OKLCH tokens, Heebo+Inter, motion with `prefers-reduced-motion`, semantic RTL, gapless bento) | read |

Both variants render the same real snapshot: 15 modules, `security_baseline=crit`, `disk=warn`, DEFCON 4, etc. The shared view-model lives in `ipracticom_sweeper.spa_context.shape_spa_context` (pure, fully unit-tested). Skills applied: `impeccable` (OKLCH, no `#000`/`#fff`, single emerald accent, no em-dashes), `emil-design-eng` (cubic-bezier easing, scale(0.97) on press, page-entry translateY+fade, reduced-motion), `israeli-ui-design-system` (Heebo+Inter, CSS logical properties, RTL, tabular-nums), `design-tasks-protocol` (survey-before-code), `build-product` (ship discipline, verify-in-tunnel).

Tests: `tests/test_spa_variants.py` — 11 tests (pure view-model + 3 routes with real-data assertions + empty-snapshot safety + reduced-motion marker).

## v6 Dashboard (added in v0.6.0)

The v6 surface ships alongside the legacy dashboard at `/v6/*` — both run on the same Flask process, no migration needed.

| Route | Purpose | Read/Write |
|---|---|---|
| `/v6/machines` | Host list + per-host status | read |
| `/v6/machines/<host>/action` | Trigger safe action (approve-before-mutate) | write (gated) |
| `/v6/machines/<host>/maintenance` (+ `/off`) | Snooze noise window | write (gated) |
| `/v6/alerts` + `/v6/alerts/page` | Live alert list (5s polling, category tabs) | read |
| `/v6/alerts/<id>/snooze` | 15m / 1h / 24h | write (gated) |
| `/v6/alerts/<id>/resolve` | Mark resolved | write (gated) |
| `/v6/logs` + `/v6/logs/page` | FS log tail (read-only), pause/play/clear | read |
| `/v6/metrics/events_heatmap` | 7×24 bucket grid | read |
| `/v6/metrics/uptime_30d` | 30 days ratio, no-data = 1.0 | read |
| `/v6/metrics/page` | Inline SVG render of both | read |

All v6 write routes produce a `RepairProposal` (never mutate state without operator approval). Remote mode refuses all writes (400).

## License

## FAQ

**Q: Does the sweeper call an LLM in the hot path?**
No. All monitoring, diagnosis, and repair is deterministic and rules-based.

**Q: How does it handle FreeSWITCH failures?**
It runs 40 checks (FS-01..FS-40) covering process, SIP, RTP, CDR, license, etc.

**Q: What if a repair breaks something?**
Every repair takes a pre-action snapshot. Repairs are classified SAFE / GUARDED /
DANGEROUS; the policy file controls which run automatically.

**Q: Can I run it without root?**
The watchdog and some repairs (drop_caches, service_restart) need root. The
monitoring layer works as an unprivileged user.

Internal — iPracticom 2026.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).