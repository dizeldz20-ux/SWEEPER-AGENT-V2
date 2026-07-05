# iPracticom Sweeper — FreeSWITCH Monitoring Plan (v0.5.0)

> **Status:** implemented in `src/ipracticom_sweeper/monitor/freeswitch.py`
> and shipped as part of v0.5.0. This document is the canonical reference
> for what each check does, why it exists, and how to operate it.

## Why FreeSWITCH matters for iPracticom

iPracticom runs FreeSWITCH as its PBX on AWS (production target: 2026-07-01,
replacing the OpenAI Realtime voice agent stack). The sweeper already had
15 general-purpose monitors (cpu, memory, disk, network, services, ssl, …)
which cover the *host* but tell you nothing about whether FreeSWITCH itself
is healthy enough to take a phone call. v0.5.0 closes that gap with 25
dedicated checks organized into four tiers.

## Tier model

| Tier | Scope | Latency budget | Runs on |
|------|-------|----------------|---------|
| 1 — Service health | "Is FS up?" | < 5 s | every pipeline run |
| 2 — Network integrity | "Can it talk SIP?" | < 5 s | every pipeline run |
| 3 — Operational | "Is it busy / leaking / drifting?" | < 10 s | every pipeline run |
| 4 — Edge cases | "Is anything subtly broken?" | < 15 s | every pipeline run |

All tiers are independent — a check that times out does not block the others.
The full pipeline runs ~30–60 s on a real FS host; if you want a quick smoke
test, run just `run_fs_tier(1)` from the chat tool surface.

## Tier 1 — Service health (FS-01..FS-05)

| ID | Check | How | Warn | Crit |
|----|-------|-----|------|------|
| FS-01 | `freeswitch` process running | `pidof` / `ps` | — | not running |
| FS-02 | systemd unit active | `systemctl is-active freeswitch` | — | inactive/failed |
| FS-03 | port 5060 listening (SIP UDP) | socket bind to 5060 | — | not bound |
| FS-04 | port 5080 listening (SIP TLS UDP) | socket bind to 5080 | — | not bound |
| FS-05 | `fs_cli -x status` reachable | subprocess w/ 5 s timeout | — | non-zero exit / timeout |

These five answer the question operators ask first: *"is it alive?"*
If any of them flips to crit, page someone. The other tiers are noise
until FS-01..05 are green.

### Tunables (defined in `monitor/freeswitch.py`)

- `DEFAULT_SIP_PORT = 5060`
- `DEFAULT_SIPS_PORT = 5080`
- `DEFAULT_CLI_TIMEOUT = 5` seconds

## Tier 2 — Network integrity (FS-06..FS-09)

| ID | Check | How | Warn | Crit |
|----|-------|-----|------|------|
| FS-06 | SIP peers reachable | `fs_cli sofia status` | < 1 peer | 0 peers |
| FS-07 | SIP registrations present | `fs_cli show registrations count` | — | 0 registrations |
| FS-08 | Gateway status | `fs_cli sofia status gateway` | degraded | down |
| FS-09 | RTP port range 16384–32768 | random-sample bind to 5 ports | < 5 / 5 bind | 0 / 5 bind |

FS-06..08 read state via `fs_cli`. FS-09 samples the RTP port range to
catch kernel-level port exhaustion (a common symptom of `fs_cli` still
reporting "OK" while new calls silently fail because no RTP port can
bind).

### Tunables

- `DEFAULT_REGISTRATIONS_MIN = 1`
- `DEFAULT_RTP_PORT_LOW = 16384`
- `DEFAULT_RTP_PORT_HIGH = 32768`

## Tier 3 — Operational + baseline drift (FS-10..FS-15)

| ID | Check | How | Warn | Crit |
|----|-------|-----|------|------|
| FS-10 | fs_cli latency | `time fs_cli -x status` | > 500 ms | > 2 000 ms |
| FS-11 | Active calls | `fs_cli show calls count` | > 100 | > 500 |
| FS-12 | Active channels | `fs_cli show channels count` | > 200 | > 1 000 |
| FS-13 | Log disk usage | `du -sh /var/log/freeswitch` | > 80 % | > 95 % |
| FS-14 | Config mtime drift | stat `/etc/freeswitch/freeswitch.xml` | > 30 days | > 90 days |
| FS-15 | Baseline calls/hour | rolling window vs hour-24-ago | ±50 % drift | ±80 % drift |

FS-13..FS-15 are *drift* signals — they fire when something is slowly
going wrong (log partition filling up, config untouched since the last
known-good deploy, call volume off-baseline). These are noisier than the
upper tiers; expect an occasional warn that resolves itself.

### Tunables

- `DEFAULT_FS_CLI_LATENCY_WARN_MS = 500`
- `DEFAULT_FS_CLI_LATENCY_CRIT_MS = 2000`
- `DEFAULT_ACTIVE_CALLS_WARN = 100`
- `DEFAULT_ACTIVE_CALLS_CRIT = 500`
- `DEFAULT_ACTIVE_CHANNELS_WARN = 200`
- `DEFAULT_ACTIVE_CHANNELS_CRIT = 1000`
- `DEFAULT_LOG_DISK_PCT_WARN = 80`
- `DEFAULT_LOG_DISK_PCT_CRIT = 95`

## Tier 4 — Edge cases (FS-16..FS-25)

| ID | Check | How | Warn | Crit |
|----|-------|-----|------|------|
| FS-16 | CDR backup freshness | mtime of last CDR archive | > 24 h old | > 72 h old |
| FS-17 | Recordings age | age of newest .wav in archive | > 30 days | > 90 days |
| FS-18 | sofia packet loss | `fs_cli sofia status profile internal` | > 0.5 % | > 2 % |
| FS-19 | sofia jitter | same source, jitter ms | > 30 ms | > 100 ms |
| FS-20 | codec mismatch | negotiated codec != configured | detected | — |
| FS-21 | FS process RSS | `/proc/<pid>/status` | > 1 GB | > 2 GB |
| FS-22 | FS process CPU% | `/proc/<pid>/stat` over 1 s sample | > 70 % | > 90 % |
| FS-23 | TCP retransmit % | `/proc/net/snmp` Tcp: section | > 1 % | > 5 % |
| FS-24 | fs_log error rate | grep ERROR in last 5 min log | > 10 / min | > 50 / min |
| FS-25 | fail2ban jail active | `fail2ban-client status freeswitch` | — | not active |

FS-23 had two parser bugs at slice 2.4 time that we caught in tests:
the section parser was prematurely exiting the `Tcp:` block when it saw
a "passive" line, and the retransmit-rate denominator was counting
*active* openings (which always include SYN_SENT) instead of *established*
connections. Both fixed.

### Tunables

- jitter_warn_ms = 30, jitter_crit_ms = 100
- rss_warn_bytes = 1 GiB, rss_crit_bytes = 2 GiB
- tcp_retransmit_warn_pct = 1.0, tcp_retransmit_crit_pct = 5.0

## How a check is structured

Every check is a top-level `check_fsNN_<descriptor>()` function in
`monitor/freeswitch.py` that takes optional thresholds and returns a
dict shaped like:

```python
{
    "status": "ok" | "warn" | "crit",
    "detail": "human-readable description",
    "metrics": {...},  # raw numbers, used by the dashboard
    "command": "...",  # exact command run (for forensics)
}
```

The four `collect_*` aggregators (`collect_all`, `collect_network`,
`collect_operational`, `collect_edge_cases`) compose the dicts and
hand them to the corresponding `evaluate_*` functions which collapse
the per-check statuses into one tier-level `defcon`.

## Running checks from the chat assistant

In v0.5.0 the chat UI surfaces these via four tool calls:

| Tool | Behavior |
|------|----------|
| `list_fs_checks()` | enumerate all 25 (no execution) |
| `get_fs_check(check_id)` | run one check by id (e.g. `check_fs01_process_running`) |
| `run_fs_tier(tier)` | run a tier (1–4); returns all checks + their raw outputs |
| `run_full_pipeline()` | full sweep; gated by `ENABLE_HEAVY_TOOLS=1` |

In mock mode the chat assistant simulates the LLM's tool call; in real
mode (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` set) the LLM emits the call
itself and the result is fed back into the conversation.

## Failure handling philosophy

- **Tier 1 failures are page-worthy.** Operator should look within 5 min.
- **Tier 2 failures often cascade from Tier 1.** Fix the service health
  first, the network integrity usually self-heals.
- **Tier 3 / Tier 4 warn levels are investigative.** Don't auto-repair;
  open a ticket, check the metric history.
- **All 25 checks are independent.** A single hung check does not block
  the rest of the pipeline; the chat tool surface enforces a hard 8 s
  timeout around every individual check.

## What v0.5.0 deliberately does NOT do

- **Not auto-repairing.** FreeSWITCH repairs (`service_restart`,
  `reloadxml`, `kill_core`) are still gated behind operator approval
  in the `repair_policy.yaml` default. Daniel's call (2026-06-30):
  "בשלב זה צריך רק להתריע ולבקש אישור לפני התיקון".
- **Not streaming results.** Tool calls return synchronously; the
  multi-iteration LLM tool-use loop lands in v0.6.
- **Not auto-tuning thresholds.** All thresholds are module-level
  constants; per-host overrides are a v0.6 feature.

## Future work (v0.6+)

- Per-host threshold overrides via a new `monitor/overrides.py` module
- Predictive failure windows (e.g. "RSS trending up over last 4 hours")
- Failover support for multi-host FreeSWITCH clusters
- Direct fs_cli connection over the FS event socket (no subprocess)
- Streaming responses in the chat UI