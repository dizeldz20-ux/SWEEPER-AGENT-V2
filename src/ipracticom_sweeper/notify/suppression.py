"""Silent-by-default gate for pipeline notifications.

The sweeper runs on a 5-minute ``systemd`` timer (``Type=oneshot``). Without a
gate, a *single persistent problem* (e.g. a steady DEFCON-4 disk-usage warning)
would re-alert every 5 minutes — the exact Telegram noise this module exists to
kill. The rule is: **notify only when something is genuinely new or worse; stay
silent otherwise, and never send an "all clear".**

Policy (see docs/silent-agent.md):

- **Severity floor** — only DEFCON <= ``MIN_DEFCON_NOTIFY`` (4 = warn) alerts.
  Green runs (DEFCON 5) are INFO: they never send, they only *reset* state so a
  problem that clears and later returns is treated as new.
- **Dedup "until resolved"** — a problem ``kind`` already alerted at a given
  severity is suppressed until it either escalates (worse DEFCON) or disappears
  and comes back. Partial improvements (one of several problems clearing) are
  silent — the user is not pinged that things got *better*.
- **Rate-limit throttle** — at most ``RATE_MAX_SENDS`` warn-level sends per
  ``RATE_WINDOW_SECONDS``; beyond that, warn-level state changes are throttled
  (a flapping metric can't spam). Critical (DEFCON <= ``CRITICAL_DEFCON``) is
  never throttled.
- **force=True** (manual "test notify" endpoints) bypasses the gate entirely and
  never touches state — a test must always send and must not perturb dedup.

Persistence mirrors :mod:`ipracticom_sweeper.notify.store`: a JSON file under the
agent's writable state dir (``paths.ROOT()``), atomic ``0600`` writes, and
**never raises on read** so a corrupt/missing file can never crash the
safety-critical dispatch path (missing file → empty state → everything is
treated as new, i.e. fail-*open* toward alerting, never toward silence of a real
problem). Overridable via ``IPRACTICOM_SWEEPER_NOTIFY_STATE_FILE`` (tests).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

_log = structlog.get_logger("ipracticom.notify.suppression")

# --- Policy knobs ------------------------------------------------------------
# DEFCON scale: 5=green (best) .. 1=black (worst). "Notify at warn and above"
# means DEFCON <= 4. Set to 3 for "critical only".
MIN_DEFCON_NOTIFY = 4
# DEFCON at or below this is critical: never deduped away by rate-limit.
CRITICAL_DEFCON = 3
# Rate-limit: at most this many warn-level sends per window.
RATE_WINDOW_SECONDS = 3600.0
RATE_MAX_SENDS = 6

# In-process serialisation of the read-modify-write. The sweeper timer is a
# single oneshot process (systemd does not run it concurrently) and the only
# other caller — the dashboard "test notify" endpoint — uses force=True and
# never writes state, so an in-process lock (not cross-process flock) suffices;
# this mirrors notify.store._STORE_LOCK.
_STATE_LOCK = threading.Lock()


@dataclass
class SuppressionDecision:
    """Result of :func:`should_notify`. ``send`` gates the actual dispatch."""

    send: bool
    reason: str


def state_file() -> Path:
    """Resolve the notify-state path (env-overridable, read fresh each call)."""
    override = os.environ.get("IPRACTICOM_SWEEPER_NOTIFY_STATE_FILE")
    if override:
        return Path(override)
    # Lazy import (matches the codebase's lazy-import style) so this module stays
    # importable even if config wiring changes, and cold-start stays cheap.
    from ipracticom_sweeper.config import paths

    return paths.ROOT() / "notify-state.json"


def _empty() -> dict[str, Any]:
    return {"known": {}, "sends": []}


def _read() -> dict[str, Any]:
    """Read notify-state.json. Never raises — any problem yields empty state."""
    f = state_file()
    if not f.exists():
        return _empty()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _log.warning("notify_state_read_failed", error=str(e))
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    known = data.get("known")
    sends = data.get("sends")
    return {
        "known": known if isinstance(known, dict) else {},
        "sends": [t for t in sends if isinstance(t, (int, float))]
        if isinstance(sends, list)
        else [],
    }


def _write(data: dict[str, Any]) -> None:
    """Atomically replace notify-state.json (0600). Best-effort, never raises."""
    f = state_file()
    try:
        if not f.parent.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass  # chmod is best-effort (e.g. some filesystems); content still written
        tmp.replace(f)
    except OSError as e:
        # A failed state write must not break dispatch. Worst case the next run
        # re-evaluates from stale state (fail-open toward alerting).
        _log.warning("notify_state_write_failed", error=str(e))


def _extract(payload: dict[str, Any]) -> tuple[int, set[str]]:
    """Pull (defcon, {problem kinds}) from a PipelineResult-shaped payload.

    Falls back to a defcon-derived pseudo-kind when a non-green payload carries
    no explicit problems, so state tracking still functions.
    """
    try:
        defcon = int(payload.get("defcon", 5))
    except (TypeError, ValueError):
        defcon = 5
    problems = payload.get("diagnosis", {}).get("problems", []) or []
    kinds: set[str] = set()
    for p in problems:
        if isinstance(p, dict):
            kinds.add(str(p.get("kind") or "unknown"))
    if not kinds and defcon <= MIN_DEFCON_NOTIFY:
        kinds.add(f"defcon-{defcon}")
    return defcon, kinds


def should_notify(
    payload: dict[str, Any], *, force: bool = False, now: float | None = None
) -> SuppressionDecision:
    """Decide whether this pipeline result should be sent to the channels.

    See the module docstring for the policy. On a suppress decision the state is
    still updated for *resolutions* (a vanished problem is forgotten so it can
    re-alert), but a to-be-sent problem is recorded only by :func:`mark_notified`
    after the send actually succeeds — so a dropped alert is retried next run.
    """
    if force:
        # Manual test: always send, never perturb dedup state.
        return SuppressionDecision(True, "forced")

    now = time.time() if now is None else now
    defcon, kinds_now = _extract(payload)

    with _STATE_LOCK:
        state = _read()
        known: dict[str, Any] = state["known"]

        # Green / below severity floor: not an alert. Reset known so a problem
        # that clears and later returns is treated as new. Never sends.
        if defcon > MIN_DEFCON_NOTIFY:
            if known:
                state["known"] = {}
                _write(state)
            return SuppressionDecision(False, "below_threshold")

        # New problem (kind unseen) or escalation (worse DEFCON than last sent).
        new_or_worse = [
            k for k in kinds_now if k not in known or defcon < int(known.get(k, 99))
        ]

        if not new_or_worse:
            # Nothing new/worse. Forget any resolved kinds (safe: only enables
            # future alerts) and stay silent.
            resolved = [k for k in known if k not in kinds_now]
            if resolved:
                for k in resolved:
                    del known[k]
                _write(state)
            return SuppressionDecision(False, "duplicate")

        # There is a genuine new/escalated problem. Critical bypasses throttle;
        # warn-level is rate-limited so a flapping metric can't spam.
        if defcon > CRITICAL_DEFCON:
            recent = [t for t in state["sends"] if now - t < RATE_WINDOW_SECONDS]
            if len(recent) != len(state["sends"]):
                state["sends"] = recent
                _write(state)
            if len(recent) >= RATE_MAX_SENDS:
                return SuppressionDecision(False, "rate_limited")

        return SuppressionDecision(True, "new_or_escalated")


def mark_notified(payload: dict[str, Any], *, now: float | None = None) -> None:
    """Record a *successful* send. Adopts the current problem set as ``known``
    (which also forgets resolved kinds) and appends the send timestamp for the
    rate-limiter. Call only after at least one channel confirmed delivery.
    """
    now = time.time() if now is None else now
    defcon, kinds_now = _extract(payload)
    with _STATE_LOCK:
        state = _read()
        old = state["known"]
        # Remember the worst (lowest) DEFCON each active kind was alerted at, so
        # a later de-escalation stays silent but a re-escalation past it alerts.
        state["known"] = {
            k: min(int(old.get(k, 99)), defcon) for k in kinds_now
        }
        sends = [t for t in state["sends"] if now - t < RATE_WINDOW_SECONDS]
        sends.append(now)
        state["sends"] = sends
        _write(state)


def reset_state() -> None:
    """Clear persisted state (tests)."""
    with _STATE_LOCK:
        _write(_empty())
