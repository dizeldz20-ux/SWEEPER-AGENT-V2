"""Sprint 14.5 — PostgreSQL auto-vacuum lag detector.

Reads pg_stat_user_tables.last_autovacuum + last_autoanalyze.
For each table, computes `seconds_since_autovacuum`. Reports the MAX across tables.

Classifies:
  < 1h → ok
  1h..24h → warn
  > 24h → crit
Tables that have NEVER been vacuumed → counted as needing vacuum → contribute.
No DB → disabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .._log import log_suppressed


@dataclass
class TableVacuumLag:
    schemaname: str
    relname: str
    seconds_since_autovacuum: float
    never_vacuumed: bool


@dataclass
class PgAutovacuumResult:
    status: str
    max_lag_seconds: float
    tables: list[TableVacuumLag] = field(default_factory=list)
    warn_threshold_s: float = 3600.0       # 1 hour
    crit_threshold_s: float = 86400.0      # 24 hours
    source: str = "psql"
    error: str = ""


def _parse_autovacuum(stdout: str) -> list[TableVacuumLag]:
    """Parse: schemaname|relname|seconds_since_autovacuum|never_vacuumed
    never_vacuumed is 't' or 'f'.
    """
    out: list[TableVacuumLag] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        try:
            schema = parts[0]
            relname = parts[1]
            seconds = float(parts[2])
            never = parts[3].lower().startswith("t")
        except (ValueError, IndexError) as e:
            log_suppressed("pg_autovacuum_parse", e)
            continue
        out.append(TableVacuumLag(
            schemaname=schema, relname=relname,
            seconds_since_autovacuum=seconds, never_vacuumed=never,
        ))
    return out


def check_pg_autovacuum(
    warn_threshold_s: float = 3600.0,
    crit_threshold_s: float = 86400.0,
    psql_runner=None,
) -> PgAutovacuumResult:
    if psql_runner is None:
        from .pg_long_query import _run_psql
        psql_runner = lambda q: _run_psql(
            "postgresql://localhost/postgres", q
        )

    query = """
    SELECT schemaname,
           relname,
           coalesce(EXTRACT(EPOCH FROM (now() - last_autovacuum))::numeric(10,2), 99999999) AS seconds_since,
           (last_autovacuum IS NULL) AS never_vacuumed
    FROM pg_stat_user_tables
    """
    stdout = psql_runner(query)
    if stdout is None:
        return PgAutovacuumResult(
            status="disabled", max_lag_seconds=0.0, source="none",
            error="psql_unavailable_or_query_failed",
        )

    tables = _parse_autovacuum(stdout)
    if not tables:
        return PgAutovacuumResult(
            status="disabled", max_lag_seconds=0.0, source="psql",
            error="no_tables",
        )

    # never_vacuumed counts as crit (forced to crit_threshold + 1)
    def lag_for(t: TableVacuumLag) -> float:
        if t.never_vacuumed:
            return crit_threshold_s + 1
        return t.seconds_since_autovacuum

    max_lag = max(lag_for(t) for t in tables)

    if max_lag >= crit_threshold_s:
        status = "crit"
    elif max_lag >= warn_threshold_s:
        status = "warn"
    else:
        status = "ok"

    return PgAutovacuumResult(
        status=status, max_lag_seconds=max_lag,
        tables=tables,
        warn_threshold_s=warn_threshold_s,
        crit_threshold_s=crit_threshold_s,
        source="psql",
    )