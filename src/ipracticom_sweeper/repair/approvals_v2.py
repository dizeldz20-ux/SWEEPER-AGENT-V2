"""Sprint 18 — Approval Workflow v2.

Adds 5 new capabilities on top of the v1 approval flow:

1. **Expiry window** — proposals expire after `ttl_seconds` (default 24h)
   and become non-approvable. A sweeper background task reaps expired ones.

2. **Two-operator quorum** — high-risk actions (denylist) require a second
   approval from a different user_id before execution.

3. **Comment thread** — operators can add comments to a proposal; visible
   in Telegram and dashboard.

4. **Required rejection reason** — POST /reject must include `reason`.
   Optionally supports `dry_run=true` for "what would have happened".

5. **CSV export** — GET /approvals/export.csv returns the full audit log
   with UTF-8 BOM for Excel compatibility, supports date range filter.

This module is additive: it does NOT modify the v1 pending.py schema.
Instead, it stores v2 metadata in a sidecar file `<pid>.v2.json` next to
the existing `<pid>.json` proposal. v1 routes keep working unchanged.
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .._log import log_suppressed
from typing import Any, Optional


# High-risk actions that require two-operator quorum.
# Names must match those registered via @register() in repair/actions.py
# and repair/actions_extra.py (no `repair_` prefix).
HIGH_RISK_ACTIONS = frozenset({
    "reload_freeswitch_config",
    "drop_caches",
    "pg_vacuum",
    "service_restart",
})


DEFAULT_TTL_SECONDS = 24 * 3600  # 24h


@dataclass
class ApprovalComment:
    """A single comment on a proposal."""
    id: str
    author: str
    text: str
    created_at: str
    created_at_ts: float


@dataclass
class ApprovalV2Metadata:
    """Sidecar metadata for v2 features."""
    pid: str
    expires_at: str                # ISO timestamp
    expires_at_ts: float
    approvers: list[str] = field(default_factory=list)
    comments: list[ApprovalComment] = field(default_factory=list)
    rejection_reason: str = ""
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["comments"] = [asdict(c) for c in self.comments]
        return d


def _v2_path(pending_dir: Path, pid: str) -> Path:
    return pending_dir / f"{pid}.v2.json"


def init_v2_metadata(
    pid: str,
    pending_dir: Path,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> ApprovalV2Metadata:
    """Create initial v2 metadata for a new proposal."""
    now = datetime.now(timezone.utc)
    expires = datetime.fromtimestamp(now.timestamp() + ttl_seconds, tz=timezone.utc)
    meta = ApprovalV2Metadata(
        pid=pid,
        expires_at=expires.isoformat(),
        expires_at_ts=expires.timestamp(),
    )
    pending_dir.mkdir(parents=True, exist_ok=True)
    _v2_path(pending_dir, pid).write_text(
        json.dumps(meta.to_dict(), indent=2, ensure_ascii=False)
    )
    return meta


def load_v2_metadata(pid: str, pending_dir: Path) -> Optional[ApprovalV2Metadata]:
    """Load v2 metadata, or None if absent."""
    p = _v2_path(pending_dir, pid)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    comments_raw = data.pop("comments", []) or []
    comments = [ApprovalComment(**c) for c in comments_raw]
    return ApprovalV2Metadata(comments=comments, **data)


def is_expired(meta: ApprovalV2Metadata, now_ts: Optional[float] = None) -> bool:
    if now_ts is None:
        now_ts = time.time()
    return now_ts >= meta.expires_at_ts


def requires_quorum(action: str) -> bool:
    return action in HIGH_RISK_ACTIONS


def record_approval(
    pid: str,
    user_id: str,
    pending_dir: Path,
) -> tuple[ApprovalV2Metadata, bool]:
    """Record an approval. Returns (metadata, ready_to_execute).

    For quorum-required actions: ready_to_execute is True only after ≥2
    distinct user_ids have approved.
    """
    meta = load_v2_metadata(pid, pending_dir)
    if meta is None:
        raise ValueError(f"no v2 metadata for pid={pid}")
    if user_id not in meta.approvers:
        meta.approvers.append(user_id)
    _v2_path(pending_dir, pid).write_text(
        json.dumps(meta.to_dict(), indent=2, ensure_ascii=False)
    )
    return meta, len(meta.approvers) >= 2


def add_comment(
    pid: str,
    author: str,
    text: str,
    pending_dir: Path,
) -> Optional[ApprovalComment]:
    """Append a comment to a proposal's thread."""
    meta = load_v2_metadata(pid, pending_dir)
    if meta is None:
        return None
    now = datetime.now(timezone.utc)
    comment = ApprovalComment(
        id=uuid.uuid4().hex[:12],
        author=author,
        text=text[:1000],  # cap to prevent abuse
        created_at=now.isoformat(),
        created_at_ts=now.timestamp(),
    )
    meta.comments.append(comment)
    _v2_path(pending_dir, pid).write_text(
        json.dumps(meta.to_dict(), indent=2, ensure_ascii=False)
    )
    return comment


def list_comments(pid: str, pending_dir: Path) -> list[ApprovalComment]:
    meta = load_v2_metadata(pid, pending_dir)
    if meta is None:
        return []
    return sorted(meta.comments, key=lambda c: c.created_at_ts)


def record_rejection(
    pid: str,
    reason: str,
    pending_dir: Path,
    dry_run: bool = False,
) -> Optional[ApprovalV2Metadata]:
    """Record rejection with a required reason."""
    meta = load_v2_metadata(pid, pending_dir)
    if meta is None:
        return None
    meta.rejection_reason = reason[:500]
    meta.dry_run = dry_run
    _v2_path(pending_dir, pid).write_text(
        json.dumps(meta.to_dict(), indent=2, ensure_ascii=False)
    )
    return meta


def reap_expired(
    pending_dir: Path,
    rejected_dir: Path,
    now_ts: Optional[float] = None,
) -> list[str]:
    """Move expired pending proposals into rejected/ with status=expired.

    Returns list of pids that were reaped.
    """
    if now_ts is None:
        now_ts = time.time()
    rejected_dir.mkdir(parents=True, exist_ok=True)
    reaped: list[str] = []
    for v2_path in pending_dir.glob("*.v2.json"):
        try:
            data = json.loads(v2_path.read_text())
        except Exception as e:
            log_suppressed("approvals_v2_v2_read", e)
            continue
        exp_ts = float(data.get("expires_at_ts") or 0)
        pid = data.get("pid")
        if not pid or exp_ts <= 0:
            continue
        if exp_ts <= now_ts:
            # Find the matching pending proposal and move to rejected/
            pending_proposal = pending_dir / f"{pid}.json"
            if pending_proposal.exists():
                try:
                    pdata = json.loads(pending_proposal.read_text())
                    pdata["status"] = "expired"
                    (rejected_dir / f"{pid}.json").write_text(
                        json.dumps(pdata, indent=2, ensure_ascii=False)
                    )
                    pending_proposal.unlink()
                except Exception as e:
                    log_suppressed("approvals_v2_reap_proposal", e)
                    continue
            # Drop the v2 sidecar too
            v2_path.unlink(missing_ok=True)
            reaped.append(pid)
    return reaped


def export_audit_csv(
    pending_dir: Path,
    approved_dir: Path,
    rejected_dir: Path,
    audit_log: Path,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> bytes:
    """Export approval audit log as CSV with UTF-8 BOM.

    Columns: timestamp, action, target, decision, reason, operator
    Filters: since/until as ISO date strings (inclusive).
    """
    since_ts = _parse_date(since) if since else None
    until_ts = _parse_date(until, end_of_day=True) if until else None

    rows: list[dict[str, str]] = []

    # Read audit log
    if audit_log.exists():
        for line in audit_log.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception as e:
                log_suppressed("approvals_v2_audit_parse", e)
                continue
            ts_str = entry.get("logged_at") or entry.get("timestamp") or ""
            ts = _parse_iso(ts_str)
            if since_ts and (ts is None or ts < since_ts):
                continue
            if until_ts and (ts is None or ts > until_ts):
                continue
            rows.append({
                "timestamp": ts_str,
                "action": entry.get("action", ""),
                "target": entry.get("target", ""),
                "decision": entry.get("kind", ""),
                "reason": entry.get("reason", ""),
                "operator": entry.get("operator", ""),
            })

    # Also include v2 rejections that may not have audit entries
    for rdir in (approved_dir, rejected_dir):
        if not rdir.exists():
            continue
        for fp in rdir.glob("*.json"):
            try:
                data = json.loads(fp.read_text())
            except Exception as e:
                log_suppressed("approvals_v2_rejected_read", e)
                continue
            ts = float(data.get("created_at_ts") or 0)
            if since_ts and ts < since_ts:
                continue
            if until_ts and ts > until_ts:
                continue
            rows.append({
                "timestamp": data.get("created_at", ""),
                "action": data.get("action", ""),
                "target": "",
                "decision": data.get("status", ""),
                "reason": data.get("reason", ""),
                "operator": "",
            })

    # Sort by timestamp
    rows.sort(key=lambda r: r["timestamp"])

    # Write CSV with UTF-8 BOM for Excel
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "timestamp", "action", "target", "decision", "reason", "operator",
    ])
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _parse_date(s: str, end_of_day: bool = False) -> float:
    """Parse YYYY-MM-DD → epoch seconds. end_of_day=True → 23:59:59."""
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Try date-only
        try:
            dt = datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            return 0.0
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.timestamp()


def _parse_iso(s: str) -> float:
    """Parse ISO timestamp → epoch seconds, or 0.0 on failure."""
    if not s:
        return 0.0
    try:
        from datetime import datetime
        # Handle Z suffix
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0