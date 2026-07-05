"""Escalation block-markers.

When an operator-approved repair FAILS, the sweeper must not silently
re-propose the same repair on the next 5-minute sweep. Instead it records a
block marker for the ``(action, server)`` pair. The pipeline refuses to
create a new proposal for a blocked pair until a human removes the block
(dashboard "allow retry" → ``DELETE /api/approvals/blocked/<key>``).

A manual *rejection* does NOT create a block — only an execution *failure*.

File layout::

    <state>/pending_repairs/blocked/<key>.json
      { action, server, reason, blocked_at }

where ``key = sha1("<action>@<server>")``.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .._log import log_suppressed

_BASE_STATE = Path(
    os.environ.get("IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper")
)
BLOCKED_DIR = _BASE_STATE / "pending_repairs" / "blocked"


def block_key(action: str, server: str) -> str:
    """Stable content-addressed key for an (action, server) pair."""
    raw = f"{action}@{server or ''}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def block(action: str, server: str, reason: str) -> None:
    """Record a block marker so the pipeline won't re-propose (action, server)."""
    BLOCKED_DIR.mkdir(parents=True, exist_ok=True)
    key = block_key(action, server)
    rec = {
        "action": action,
        "server": server or "",
        "reason": (reason or "")[:500],
        "blocked_at": datetime.now(timezone.utc).isoformat(),
    }
    (BLOCKED_DIR / f"{key}.json").write_text(
        json.dumps(rec, indent=2, ensure_ascii=False)
    )


def is_blocked(action: str, server: str) -> bool:
    """True if (action, server) currently has an active block marker."""
    return (BLOCKED_DIR / f"{block_key(action, server)}.json").exists()


def unblock_key(key: str) -> bool:
    """Remove a block marker by key. Returns True iff one was removed.

    Rejects non-hex keys (path-traversal defense) — every real key is a
    sha1 hex digest.
    """
    if not key or not all(c in "0123456789abcdef" for c in key.lower()):
        return False
    p = BLOCKED_DIR / f"{key}.json"
    if p.exists():
        p.unlink()
        return True
    return False


def list_blocked() -> list[dict]:
    """List all active block markers, each annotated with its ``key``."""
    out: list[dict] = []
    if not BLOCKED_DIR.exists():
        return out
    for p in sorted(BLOCKED_DIR.glob("*.json")):
        try:
            rec = json.loads(p.read_text())
        except Exception as e:
            log_suppressed("block_list_read", e)
            continue
        rec["key"] = p.stem
        out.append(rec)
    return out
