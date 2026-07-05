# Silent-by-default notifications

The sweeper runs on a **5-minute `systemd` timer** (`Type=oneshot`). The goal of
this document: **Telegram/Slack stay quiet when everything is fine, and speak
only when there is a real, actionable change.** If you finish a change here and
still get a ping every 10 minutes on a healthy fleet, it is not done.

## The rule

- **Default = silent.** No commits-equivalent (no new problems), all checks
  green → **no message, no output, no send call.** Not a `[SILENT]` marker, not a
  `print()` — the send function is simply never called.
- **No "all clear" messages.** There is no `✅ all good` / `sweep complete` /
  `scanned N modules, 0 problems`. If there is nothing to report, nothing is
  sent. INFO-level events (`scanned X`) are never a message.
- **Notify only on a real reason:** a new problem, an escalation (worse DEFCON),
  a test failure, a critical event — or an explicit manual request.

## Where the gate lives

Do **not** rely on prompt/log discipline to stay quiet — the silence is enforced
in code, at the single dispatch chokepoint:

```
systemd timer ─every 5 min─▶ python -m ipracticom_sweeper.sweeper --json --quiet
                                          │ (JSON → journal, NOT Telegram)
                                          ▼
                              pipeline.run_pipeline()
                                          │
                                          ▼
                       notify.notify_pipeline_result(payload)
                                          │
                          ┌───────────────┴────────────────┐
                          ▼                                 │
             notify.suppression.should_notify(payload)      │
                          │                                 │
        ┌── send=False ──┤ green / duplicate / rate_limited │
        │                │                                  │
        ▼                └── send=True ─▶ dispatch to channels ─▶ mark_notified()
     return {}                                (only if a channel confirmed)
     (no send)
```

- The CLI (`sweeper.py`) **never sends to Telegram** — it prints JSON to stdout,
  which `systemd` routes to the journal. All channel dispatch goes through
  `notify_pipeline_result`, and every send is gated by
  [`notify/suppression.py`](../src/ipracticom_sweeper/notify/suppression.py).
- `run_pipeline` calls `notify_pipeline_result` on **every** run (including green
  ones) — not because green runs send, but so the gate can *reset* dedup state
  when a problem resolves. The gate, not the call site, decides silence.

## The policy (`notify/suppression.py`)

| Concern | Behaviour |
|---|---|
| **Severity floor** | Only DEFCON ≤ `MIN_DEFCON_NOTIFY` (4 = warn) is eligible. Green (5) never sends; it only resets state. Set to 3 for "critical only". |
| **Dedup "until resolved"** | A problem `kind` already alerted is suppressed until it **escalates** (worse DEFCON) or **disappears and returns**. A steady DEFCON-4 problem is reported once, not every 5 minutes. |
| **Partial improvement** | If one of several problems clears, **no message** — the user is not pinged that things got better, only worse/new. Resolved kinds are silently forgotten so a recurrence re-alerts. |
| **Rate-limit throttle** | At most `RATE_MAX_SENDS` (6) warn-level sends per `RATE_WINDOW_SECONDS` (1 h); beyond that, warn-level changes are throttled so a flapping metric can't spam. **Critical (DEFCON ≤ 3) is never throttled.** |
| **`force=True`** | The manual "test notify" endpoints (`POST /api/notify/test`) bypass the gate and **never touch state** — a test always sends and must not perturb dedup. This is the "explicit user request" path. |
| **Mark after send** | State records a problem as "seen" **only after a channel confirms delivery** — a dropped alert is retried next run, never silently marked seen. |

### State file

Persistent across the oneshot runs (an in-memory cache would be useless — each
timer fire is a fresh process). JSON at `<state-dir>/notify-state.json`
(`paths.ROOT()`, default `/var/lib/ipracticom-sweeper`), `0600`, atomic write,
**never raises on read** (corrupt/missing → empty state → fail *open* toward
alerting, never toward silencing a real problem). Override with
`IPRACTICOM_SWEEPER_NOTIFY_STATE_FILE` (tests).

```json
{ "known": { "disk_full": 4, "mem_warn": 2 }, "sends": [1720000000.0, ...] }
```

`known` maps each active problem `kind` → the worst (lowest) DEFCON it was
alerted at; `sends` is the recent send-timestamp window for the rate-limiter.

## Tuning

All knobs are module constants at the top of `notify/suppression.py`:

- `MIN_DEFCON_NOTIFY = 4` — severity floor (4 = warn+, 3 = crit only).
- `CRITICAL_DEFCON = 3` — at/below this, never throttled.
- `RATE_WINDOW_SECONDS = 3600`, `RATE_MAX_SENDS = 6` — throttle window + cap.

## Do / Don't

- ✅ Exit without sending when there is no actionable finding.
- ✅ Keep the severity floor, dedup, and throttle **in code**, not in prompts.
- ❌ Don't add `print("✅ sweep complete")` — stdout can leak to a chat relay.
- ❌ Don't use a `[SILENT]` text marker — just don't call the sender.
- ❌ Don't send INFO events (`scanned N modules`) or "all clear".

## Acceptance test (clean repo → no message)

```bash
# On a healthy host, a green run must send nothing:
python -m ipracticom_sweeper.sweeper --json --quiet
# Expected: exit 0, JSON on stdout (journal), NO Telegram/Slack message.
```

Regression coverage: [`tests/notify/test_silent_gate.py`](../tests/notify/test_silent_gate.py)
— `test_silent_when_no_issues` and `test_pipeline_result_silent_when_green`
assert that a clean run never calls a sender, plus dedup / escalation /
resolution / throttle / `force` cases. Run via WSL (see
`docs/wsl-runtime.md`): `~/sweeper-venv/bin/python -m pytest tests/notify/ -q`.
