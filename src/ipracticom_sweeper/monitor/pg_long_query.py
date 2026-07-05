"""Sprint 14.1 — PostgreSQL long-running query detector.

Reads pg_stat_activity via the psql CLI, filters queries whose `state_change`
is older than `warn_threshold_s` seconds, and classifies:
  0 queries → ok
  1..crit_threshold → warn
  > crit_threshold → crit
If no DB available, returns disabled.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional

from .._log import log_suppressed


@dataclass
class LongQuery:
    pid: int
    duration_seconds: float
    query: str
    state: str
    usename: str


@dataclass
class PgLongQueryResult:
    status: str            # ok | warn | crit | disabled | unknown
    count: int
    queries: list[LongQuery] = field(default_factory=list)
    warn_threshold_s: float = 300.0
    crit_threshold_count: int = 3
    source: str = "psql"
    error: str = ""


def _run_psql(connection_string: str, query: str, timeout: int = 5) -> Optional[str]:
    try:
        r = subprocess.run(
            ["psql", connection_string, "-t", "-A", "-F", "|", "-c", query],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return None
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _parse_long_queries(stdout: str, warn_threshold_s: float) -> list[LongQuery]:
    """Parse `SELECT pid, state, state_change, query, now()-state_change AS age, usename`
    from psql output. The `age` column comes back as interval like `00:05:23.456`
    or in seconds like `323.456` depending on the SQL.

    For Sprint 14.1 we keep it simple: use interval-to-seconds via the helper.
    """
    out: list[LongQuery] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[0])
            state = parts[1]
            # parts[2] = state_change (timestamp)
            query = parts[3]
            # parts[4] = EXTRACT(EPOCH FROM (now()-state_change))
            duration = float(parts[4])
            usename = parts[5] if len(parts) >= 6 else ""
        except (ValueError, IndexError) as e:
            log_suppressed("pg_long_query_parse", e)
            continue
        if duration >= warn_threshold_s and state != "idle":
            out.append(LongQuery(
                pid=pid, duration_seconds=duration,
                query=query[:200], state=state, usename=usename,
            ))
    return out


def check_pg_long_queries(
    warn_threshold_s: float = 300.0,
    crit_threshold_count: int = 3,
    connection_string: str = "postgresql://localhost/postgres",
    psql_runner=None,
) -> PgLongQueryResult:
    """Check for queries running longer than `warn_threshold_s`."""
    if psql_runner is None:
        psql_runner = lambda q: _run_psql(connection_string, q)

    query = """
    SELECT pid, state, state_change, query,
           EXTRACT(EPOCH FROM (now() - state_change))::numeric(10,2) AS age_seconds,
           coalesce(usename, '')
    FROM pg_stat_activity
    WHERE state IS NOT NULL
      AND query NOT LIKE '%pg_stat_activity%'
      AND pid != pg_backend_pid()
    ORDER BY age_seconds DESC
    """
    stdout = psql_runner(query)
    if stdout is None:
        return PgLongQueryResult(
            status="disabled", count=0, source="none",
            error="psql_unavailable_or_query_failed",
        )

    queries = _parse_long_queries(stdout, warn_threshold_s)
    count = len(queries)

    if count == 0:
        status = "ok"
    elif count > crit_threshold_count:
        status = "crit"
    else:
        status = "warn"

    return PgLongQueryResult(
        status=status,
        count=count,
        queries=queries,
        warn_threshold_s=warn_threshold_s,
        crit_threshold_count=crit_threshold_count,
        source="psql",
    )