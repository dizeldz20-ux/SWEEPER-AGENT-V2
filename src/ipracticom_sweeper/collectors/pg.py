"""PostgreSQL collector: queries pg_stat_activity + pg_stat_database via psql CLI."""
from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass
class PGStats:
    active_connections: int
    idle_connections: int
    max_connections: int
    database_count: int
    cache_hit_ratio: float
    reachable: bool
    error: str | None = None


def collect_pg_stats(
    connection_string: str = "postgresql://localhost/postgres",
    timeout: int = 5,
) -> PGStats:
    """Run psql queries and return parsed stats.

    connection_string: libpq URI or keyword string
    timeout: seconds before giving up
    """
    try:
        # Query 1: connection counts
        conn_query = """
        SELECT
            (SELECT count(*) FROM pg_stat_activity WHERE state = 'active') AS active,
            (SELECT count(*) FROM pg_stat_activity WHERE state = 'idle') AS idle,
            (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max_conn
        """
        result = subprocess.run(
            ["psql", connection_string, "-t", "-A", "-F", "|", "-c", conn_query],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return PGStats(0, 0, 0, 0, 0.0, False, error=result.stderr.strip()[:200])

        parts = result.stdout.strip().split("|")
        if len(parts) < 3:
            return PGStats(0, 0, 0, 0, 0.0, False, error="unexpected output format")

        active = int(parts[0])
        idle = int(parts[1])
        max_conn = int(parts[2])

        # Query 2: database count + cache hit ratio
        stats_query = """
        SELECT
            (SELECT count(*) FROM pg_stat_database) AS db_count,
            CASE
                WHEN sum(blks_hit) + sum(blks_read) = 0 THEN 0
                ELSE round(100.0 * sum(blks_hit) / (sum(blks_hit) + sum(blks_read)), 2)
            END AS cache_hit
        FROM pg_stat_database
        """
        result2 = subprocess.run(
            ["psql", connection_string, "-t", "-A", "-F", "|", "-c", stats_query],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result2.returncode != 0:
            return PGStats(active, idle, max_conn, 0, 0.0, True, error=result2.stderr.strip()[:200])

        parts2 = result2.stdout.strip().split("|")
        db_count = int(parts2[0]) if len(parts2) >= 1 and parts2[0] else 0
        cache_hit = float(parts2[1]) if len(parts2) >= 2 and parts2[1] else 0.0

        return PGStats(
            active_connections=active,
            idle_connections=idle,
            max_connections=max_conn,
            database_count=db_count,
            cache_hit_ratio=cache_hit,
            reachable=True,
        )
    except subprocess.TimeoutExpired:
        return PGStats(0, 0, 0, 0, 0.0, False, error=f"timeout after {timeout}s")
    except Exception as e:
        return PGStats(0, 0, 0, 0, 0.0, False, error=str(e)[:200])


def defcon_from_stats(stats: PGStats) -> int:
    """Map PG stats to DEFCON level (1-5, lower=worse)."""
    if not stats.reachable:
        return 1  # can't connect = critical
    if stats.max_connections > 0:
        usage_pct = (stats.active_connections + stats.idle_connections) / stats.max_connections * 100
        if usage_pct > 90:
            return 2
        if usage_pct > 75:
            return 3
    if stats.cache_hit_ratio < 90:
        return 4
    return 5
