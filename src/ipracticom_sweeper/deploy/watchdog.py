"""External watchdog: probe /healthz and restart the API service on 5xx.

This is invoked by a systemd oneshot every 60s. It exists outside the
sweeper process so that a hung sweeper can be restarted by something
that doesn't share its fate.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Decision = Literal["ok", "restart", "cooldown"]


def probe_healthz(url: str, timeout: float = 5.0) -> int:
    """Return HTTP status code from /healthz. Returns 0 on connection failure."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return 0


def evaluate_health(url: str) -> Decision:
    """Decide what to do based on the healthz status code.

    - 200 → ok
    - 5xx → restart
    - 0 (connection refused / timeout) → restart (down, not hung)
    - 4xx → ok (operator's problem, not ours)
    """
    code = probe_healthz(url)
    if code == 200:
        return "ok"
    if code == 0 or 500 <= code < 600:
        return "restart"
    return "ok"


# --- Restart tracker ---------------------------------------------------------

@dataclass
class RestartTracker:
    """Persists recent restart timestamps to a state file."""

    state_dir: Path
    cooldown_seconds: float = 300.0  # 5 minutes
    window_seconds: float = 3600.0   # 1 hour
    max_in_window: int = 3

    restarts: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "watchdog_restarts.json"
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.restarts = list(data.get("restarts", []))
            except (json.JSONDecodeError, OSError):
                self.restarts = []

    def _save(self) -> None:
        self.path.write_text(json.dumps({"restarts": self.restarts}))

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self.restarts = [t for t in self.restarts if t >= cutoff]

    def record_restart(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self._prune(now)
        self.restarts.append(now)
        self._save()

    def count_in_window(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        self._prune(now)
        return len(self.restarts)

    def last_restart(self) -> float | None:
        return max(self.restarts) if self.restarts else None


def should_restart(
    tracker: RestartTracker, recent_failure_count: int, now: float | None = None
) -> bool:
    """Decide whether the watchdog should restart the service.

    Cooldown rule: if the last restart was within `cooldown_seconds`,
    suppress. This prevents a flap loop.
    """
    if recent_failure_count == 0:
        return False
    now = now if now is not None else time.time()
    last = tracker.last_restart()
    if last is None:
        return True
    return (now - last) > tracker.cooldown_seconds


def should_alert_admin(tracker: RestartTracker, threshold: int = 3) -> bool:
    """Alert human if we hit `threshold` restarts in the last hour."""
    return tracker.count_in_window() >= threshold