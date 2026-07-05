"""Sprint 14.2 — PostgreSQL replication lag detector.

Reads pg_stat_replication and reports the maximum replay_lag across replicas.
Classifies:
  < 10s → ok
  10..60s → warn
  > 60s → crit
No replicas → disabled (stand-alone primary is fine).
psql failure → unknown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .._log import log_suppressed


@dataclass
class ReplicaLag:
    client_addr: str
    state: str
    lag_seconds: float


@dataclass
class PgReplicationResult:
    status: str            # ok | warn | crit | disabled | unknown
    max_lag_seconds: float
    replicas: list[ReplicaLag] = field(default_factory=list)
    warn_threshold_s: float = 10.0
    crit_threshold_s: float = 60.0
    source: str = "psql"
    error: str = ""


def _parse_replicas(stdout: str) -> list[ReplicaLag]:
    """Parse output of:
    SELECT client_addr, state, EXTRACT(EPOCH FROM replay_lag)::numeric(10,2)
    FROM pg_stat_replication
    """
    out: list[ReplicaLag] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        try:
            addr = parts[0] or "(unknown)"
            state = parts[1] or "unknown"
            lag = float(parts[2])
        except (ValueError, IndexError) as e:
            log_suppressed("pg_replication_lag_parse", e)
            continue
        if lag < 0:
            lag = 0.0  # streaming: replay_lag is NULL → COALESCE(..., 0)
        out.append(ReplicaLag(client_addr=addr, state=state, lag_seconds=lag))
    return out


def check_pg_replication_lag(
    warn_threshold_s: float = 10.0,
    crit_threshold_s: float = 60.0,
    psql_runner=None,
) -> PgReplicationResult:
    if psql_runner is None:
        from .pg_long_query import _run_psql
        psql_runner = lambda q: _run_psql(
            "postgresql://localhost/postgres", q
        )

    query = """
    SELECT coalesce(client_addr::text, '(unknown)'),
           state,
           coalesce(EXTRACT(EPOCH FROM replay_lag)::numeric(10,2), 0)
    FROM pg_stat_replication
    """
    stdout = psql_runner(query)
    if stdout is None:
        return PgReplicationResult(
            status="unknown", max_lag_seconds=0.0, source="none",
            error="psql_unavailable_or_query_failed",
        )

    replicas = _parse_replicas(stdout)
    if not replicas:
        return PgReplicationResult(
            status="disabled", max_lag_seconds=0.0, source="psql",
            error="no_replicas",
        )

    max_lag = max(r.lag_seconds for r in replicas)
    if max_lag >= crit_threshold_s:
        status = "crit"
    elif max_lag >= warn_threshold_s:
        status = "warn"
    else:
        status = "ok"

    return PgReplicationResult(
        status=status,
        max_lag_seconds=max_lag,
        replicas=replicas,
        warn_threshold_s=warn_threshold_s,
        crit_threshold_s=crit_threshold_s,
        source="psql",
    )