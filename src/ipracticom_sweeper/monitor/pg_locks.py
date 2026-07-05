"""Sprint 14.3 — PostgreSQL lock-wait detector.

Reads pg_locks + pg_stat_activity + pg_blocking_pids to find blocked queries.
A blocked query has wait_event_type='Lock' and a non-empty blocking_pid tree.

Classifies:
  0 blocked → ok
  1..crit_count → warn
  > crit_count → crit
No DB → disabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .._log import log_suppressed


@dataclass
class BlockedQuery:
    pid: int
    blocked_by: list[int]
    wait_event: str
    query: str
    duration_seconds: float


@dataclass
class PgLocksResult:
    status: str
    count: int
    blocked: list[BlockedQuery] = field(default_factory=list)
    crit_threshold_count: int = 3
    source: str = "psql"
    error: str = ""


def _parse_blocked(stdout: str) -> list[BlockedQuery]:
    """Parse output of:
    SELECT pid, pg_blocking_pids(pid), wait_event_type, wait_event,
           EXTRACT(EPOCH FROM (now() - state_change))::numeric(10,2),
           query
    FROM pg_stat_activity
    WHERE wait_event_type = 'Lock'
    """
    out: list[BlockedQuery] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        try:
            pid = int(parts[0])
            blockers_str = parts[1].strip("{}")
            blockers = [int(x) for x in blockers_str.split(",") if x.strip()] if blockers_str else []
            wait_type = parts[2] or ""
            wait_event = parts[3] or ""
            duration = float(parts[4])
            query = parts[5][:200]
        except (ValueError, IndexError) as e:
            log_suppressed("pg_locks_parse", e)
            continue
        if wait_type.lower() != "lock":
            continue
        out.append(BlockedQuery(
            pid=pid, blocked_by=blockers,
            wait_event=wait_event, query=query,
            duration_seconds=duration,
        ))
    return out


def check_pg_locks(
    crit_threshold_count: int = 3,
    psql_runner=None,
) -> PgLocksResult:
    if psql_runner is None:
        from .pg_long_query import _run_psql
        psql_runner = lambda q: _run_psql(
            "postgresql://localhost/postgres", q
        )

    query = """
    SELECT pid,
           pg_blocking_pids(pid)::text,
           wait_event_type,
           wait_event,
           EXTRACT(EPOCH FROM (now() - state_change))::numeric(10,2),
           query
    FROM pg_stat_activity
    WHERE wait_event_type IS NOT NULL
    """
    stdout = psql_runner(query)
    if stdout is None:
        return PgLocksResult(
            status="disabled", count=0, source="none",
            error="psql_unavailable_or_query_failed",
        )

    blocked = _parse_blocked(stdout)
    count = len(blocked)
    if count == 0:
        status = "ok"
    elif count > crit_threshold_count:
        status = "crit"
    else:
        status = "warn"

    return PgLocksResult(
        status=status, count=count, blocked=blocked,
        crit_threshold_count=crit_threshold_count, source="psql",
    )