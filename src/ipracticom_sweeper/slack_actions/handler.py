"""Slack interactive buttons handler: acknowledge / silence / run repair."""
from __future__ import annotations
import time
from dataclasses import dataclass
from enum import Enum


class SlackActionType(str, Enum):
    ACKNOWLEDGE = "acknowledge"
    SILENCE = "silence"
    RUN_REPAIR = "run_repair"
    APPROVE = "approve"
    REJECT = "reject"


@dataclass
class SlackAction:
    action_type: SlackActionType
    fingerprint: str
    user: str
    timestamp: float


class SlackActionHandler:
    def __init__(self):
        self._acked: dict[str, float] = {}  # fingerprint -> ack timestamp
        self._silenced: dict[str, float] = {}  # fingerprint -> silence_until
        self._action_log: list[SlackAction] = []

    def handle(self, action: SlackAction) -> dict[str, str]:
        """Process a Slack button action. Returns response dict."""
        self._action_log.append(action)
        if action.action_type == SlackActionType.ACKNOWLEDGE:
            self._acked[action.fingerprint] = action.timestamp
            return {"status": "acknowledged", "fingerprint": action.fingerprint}
        elif action.action_type == SlackActionType.SILENCE:
            # Silence for 1 hour
            self._silenced[action.fingerprint] = action.timestamp + 3600
            return {"status": "silenced", "duration": "1h"}
        elif action.action_type == SlackActionType.RUN_REPAIR:
            return {"status": "repair_triggered", "fingerprint": action.fingerprint}
        elif action.action_type == SlackActionType.APPROVE:
            return self._decide("approve", action)
        elif action.action_type == SlackActionType.REJECT:
            return self._decide("reject", action)
        return {"status": "unknown_action"}

    def _decide(self, kind: str, action: SlackAction) -> dict[str, str]:
        """Drive the real approval flow (same as dashboard/API). fingerprint
        carries the proposal id; user is the approver for the audit trail."""
        from ipracticom_sweeper.repair import decide

        pid = action.fingerprint
        if kind == "approve":
            res = decide.approve(pid, actor=action.user)
        else:
            res = decide.reject(pid, actor=action.user, reason="rejected via Slack")
        return {
            "status": res.get("status", res.get("error", "error")),
            "ok": res.get("ok", False),
            "pid": pid,
        }

    def is_acked(self, fingerprint: str) -> bool:
        return fingerprint in self._acked

    def is_silenced(self, fingerprint: str, now: float | None = None) -> bool:
        now = now or time.time()
        until = self._silenced.get(fingerprint, 0)
        return now < until

    def action_count(self) -> int:
        return len(self._action_log)
