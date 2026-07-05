"""Sprint 14.4 — PostgreSQL table/index bloat detector.

Reads pg_stat_user_tables and computes dead-tuple ratio:
  ratio = n_dead_tup / (n_live_tup + n_dead_tup)
Threshold is per-table; top-N bloated tables reported in metadata.

Classifies by MAX ratio across tables:
  < 20% → ok
  20..40% → warn
  > 40% → crit
No DB → disabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .._log import log_suppressed


@dataclass
class TableBloat:
    schemaname: str
    relname: str
    n_live_tup: int
    n_dead_tup: int
    ratio: float  # 0..1


@dataclass
class PgBloatResult:
    status: str
    max_ratio: float
    tables: list[TableBloat] = field(default_factory=list)
    warn_threshold: float = 0.20
    crit_threshold: float = 0.40
    top_n: int = 5
    source: str = "psql"
    error: str = ""


def _parse_bloat(stdout: str) -> list[TableBloat]:
    """Parse: schemaname|relname|n_live_tup|n_dead_tup"""
    out: list[TableBloat] = []
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
            live = int(parts[2])
            dead = int(parts[3])
        except (ValueError, IndexError) as e:
            log_suppressed("pg_bloat_parse", e)
            continue
        total = live + dead
        ratio = (dead / total) if total > 0 else 0.0
        out.append(TableBloat(
            schemaname=schema, relname=relname,
            n_live_tup=live, n_dead_tup=dead, ratio=ratio,
        ))
    return out


def check_pg_bloat(
    warn_threshold: float = 0.20,
    crit_threshold: float = 0.40,
    top_n: int = 5,
    psql_runner=None,
) -> PgBloatResult:
    if psql_runner is None:
        from .pg_long_query import _run_psql
        psql_runner = lambda q: _run_psql(
            "postgresql://localhost/postgres", q
        )

    query = """
    SELECT schemaname, relname, n_live_tup, n_dead_tup
    FROM pg_stat_user_tables
    WHERE n_live_tup + n_dead_tup > 0
    ORDER BY n_dead_tup DESC
    LIMIT 100
    """
    stdout = psql_runner(query)
    if stdout is None:
        return PgBloatResult(
            status="disabled", max_ratio=0.0, source="none",
            error="psql_unavailable_or_query_failed",
        )

    tables = _parse_bloat(stdout)
    max_ratio = max((t.ratio for t in tables), default=0.0)

    if max_ratio >= crit_threshold:
        status = "crit"
    elif max_ratio >= warn_threshold:
        status = "warn"
    else:
        status = "ok"

    return PgBloatResult(
        status=status, max_ratio=max_ratio,
        tables=tables[:top_n],
        warn_threshold=warn_threshold,
        crit_threshold=crit_threshold,
        top_n=top_n,
        source="psql",
    )