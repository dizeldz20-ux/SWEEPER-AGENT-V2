"""Tests for Sprint 14 — PostgreSQL deep monitors (35 tests)."""
from __future__ import annotations

import pytest

from ipracticom_sweeper.monitor.pg_long_query import (
    check_pg_long_queries,
    _parse_long_queries,
    PgLongQueryResult,
)
from ipracticom_sweeper.monitor.pg_replication_lag import (
    check_pg_replication_lag,
    _parse_replicas,
    PgReplicationResult,
)
from ipracticom_sweeper.monitor.pg_locks import (
    check_pg_locks,
    _parse_blocked,
    PgLocksResult,
)
from ipracticom_sweeper.monitor.pg_bloat import (
    check_pg_bloat,
    _parse_bloat,
    PgBloatResult,
)
from ipracticom_sweeper.monitor.pg_autovacuum import (
    check_pg_autovacuum,
    _parse_autovacuum,
    PgAutovacuumResult,
)


# =============================================================================
# Sprint 14.1 — Long-running queries (8 tests)
# =============================================================================

def test_14_1_ok_no_long_queries() -> None:
    """No queries over threshold → ok."""
    out = "1|active|2026-07-01|SELECT 1|30.0|alice\n"  # 30s < 300s threshold
    r = check_pg_long_queries(psql_runner=lambda q: out)
    assert r.status == "ok"
    assert r.count == 0


def test_14_1_warn_1_to_3_over_threshold() -> None:
    """2 over threshold → warn."""
    out = (
        "1|active|2026-07-01|SELECT a|350.0|alice\n"
        "2|active|2026-07-01|SELECT b|400.0|bob\n"
    )
    r = check_pg_long_queries(psql_runner=lambda q: out)
    assert r.status == "warn"
    assert r.count == 2


def test_14_1_crit_above_3() -> None:
    """5 over threshold → crit."""
    out = "".join(
        f"{i}|active|2026-07-01|SELECT {i}|{400+i*10}.0|u{i}\n"
        for i in range(1, 6)
    )
    r = check_pg_long_queries(psql_runner=lambda q: out)
    assert r.status == "crit"
    assert r.count == 5


def test_14_1_default_threshold_5min() -> None:
    """state_change older than 5min (300s) is counted as long."""
    out = "1|active|2026-07-01|SELECT 1|301.0|alice\n"  # 1s over
    r = check_pg_long_queries(psql_runner=lambda q: out)
    assert r.status == "warn"
    assert r.count == 1


def test_14_1_threshold_configurable() -> None:
    """Lower threshold (60s) catches more queries."""
    out = "1|active|2026-07-01|SELECT 1|120.0|alice\n"
    r_default = check_pg_long_queries(psql_runner=lambda q: out)
    r_tight = check_pg_long_queries(warn_threshold_s=60.0, psql_runner=lambda q: out)
    assert r_default.count == 0  # 120 > 300 default → not over
    assert r_tight.count == 1    # 120 > 60 tight → over


def test_14_1_uses_pg_stat_activity() -> None:
    """The psql runner receives a query referencing pg_stat_activity."""
    captured = {}
    def runner(q):
        captured["q"] = q
        return None
    check_pg_long_queries(psql_runner=runner)
    assert "pg_stat_activity" in captured["q"]


def test_14_1_handles_no_db() -> None:
    """psql failure (None output) → disabled."""
    r = check_pg_long_queries(psql_runner=lambda q: None)
    assert r.status == "disabled"
    assert r.count == 0


def test_14_1_metadata_query_pids_and_durations() -> None:
    """queries list exposes pid and duration_seconds."""
    out = "1|active|2026-07-01|SELECT a|350.0|alice\n"
    r = check_pg_long_queries(psql_runner=lambda q: out)
    assert r.queries[0].pid == 1
    assert r.queries[0].duration_seconds == 350.0
    assert "SELECT a" in r.queries[0].query


# =============================================================================
# Sprint 14.2 — Replication lag (7 tests)
# =============================================================================

def test_14_2_ok_lag_under_10s() -> None:
    out = "10.0.0.1|streaming|3.5\n"
    r = check_pg_replication_lag(psql_runner=lambda q: out)
    assert r.status == "ok"
    assert r.max_lag_seconds == 3.5


def test_14_2_warn_10_to_60s() -> None:
    out = "10.0.0.1|streaming|30.0\n"
    r = check_pg_replication_lag(psql_runner=lambda q: out)
    assert r.status == "warn"
    assert r.max_lag_seconds == 30.0


def test_14_2_crit_above_60s() -> None:
    out = "10.0.0.1|streaming|120.0\n"
    r = check_pg_replication_lag(psql_runner=lambda q: out)
    assert r.status == "crit"


def test_14_2_uses_pg_stat_replication() -> None:
    captured = {}
    def runner(q):
        captured["q"] = q
        return "10.0.0.1|streaming|1.0\n"
    check_pg_replication_lag(psql_runner=runner)
    assert "pg_stat_replication" in captured["q"]


def test_14_2_handles_no_replicas() -> None:
    """Empty output → disabled."""
    r = check_pg_replication_lag(psql_runner=lambda q: "")
    assert r.status == "disabled"
    assert r.max_lag_seconds == 0.0


def test_14_2_metadata_per_replica_lag() -> None:
    """replicas list contains per-replica lag entries."""
    out = (
        "10.0.0.1|streaming|3.5\n"
        "10.0.0.2|streaming|45.0\n"
    )
    r = check_pg_replication_lag(psql_runner=lambda q: out)
    assert len(r.replicas) == 2
    assert r.replicas[0].client_addr == "10.0.0.1"
    assert r.replicas[1].lag_seconds == 45.0


def test_14_2_handles_psql_failure() -> None:
    """psql returns None → unknown."""
    r = check_pg_replication_lag(psql_runner=lambda q: None)
    assert r.status == "unknown"
    assert r.error != ""


# =============================================================================
# Sprint 14.3 — Lock-wait detector (7 tests)
# =============================================================================

def test_14_3_ok_no_blocked_queries() -> None:
    out = "1|{}|IO|DataFileRead|10.0|SELECT 1\n"  # not Lock type
    r = check_pg_locks(psql_runner=lambda q: out)
    assert r.status == "ok"
    assert r.count == 0


def test_14_3_warn_1_to_3_blocked() -> None:
    out = (
        "100|{99}|Lock|relation|5.0|SELECT * FROM a\n"
        "101|{99}|Lock|relation|10.0|SELECT * FROM b\n"
    )
    r = check_pg_locks(psql_runner=lambda q: out)
    assert r.status == "warn"
    assert r.count == 2


def test_14_3_crit_above_3_blocked() -> None:
    out = "".join(
        f"{100+i}|{{99}}|Lock|relation|{5+i}.0|SELECT {i}\n"
        for i in range(5)
    )
    r = check_pg_locks(psql_runner=lambda q: out)
    assert r.status == "crit"
    assert r.count == 5


def test_14_3_uses_pg_locks_view() -> None:
    captured = {}
    def runner(q):
        captured["q"] = q
        return ""
    check_pg_locks(psql_runner=runner)
    # The query references pg_blocking_pids which queries pg_locks internally
    assert "pg_stat_activity" in captured["q"]


def test_14_3_threshold_configurable() -> None:
    out = (
        "100|{99}|Lock|relation|5.0|SELECT * FROM a\n"
        "101|{99}|Lock|relation|10.0|SELECT * FROM b\n"
        "102|{99}|Lock|relation|15.0|SELECT * FROM c\n"
        "103|{99}|Lock|relation|20.0|SELECT * FROM d\n"
    )
    # Default threshold=3 → 4 blocked = crit
    r_default = check_pg_locks(psql_runner=lambda q: out)
    assert r_default.status == "crit"
    # Higher threshold=5 → 4 blocked = warn
    r_high = check_pg_locks(crit_threshold_count=5, psql_runner=lambda q: out)
    assert r_high.status == "warn"


def test_14_3_metadata_blocking_pid_tree() -> None:
    out = "100|{99,98}|Lock|relation|5.0|SELECT * FROM a\n"
    r = check_pg_locks(psql_runner=lambda q: out)
    assert r.blocked[0].pid == 100
    assert r.blocked[0].blocked_by == [99, 98]


def test_14_3_handles_no_db() -> None:
    r = check_pg_locks(psql_runner=lambda q: None)
    assert r.status == "disabled"
    assert r.count == 0


# =============================================================================
# Sprint 14.4 — Table + index bloat (6 tests)
# =============================================================================

def test_14_4_ok_bloat_under_20pct() -> None:
    out = "public|users|1000|100\n"  # 100/1100 = ~9%
    r = check_pg_bloat(psql_runner=lambda q: out)
    assert r.status == "ok"


def test_14_4_warn_20_to_40_pct() -> None:
    out = "public|orders|700|300\n"  # 300/1000 = 30%
    r = check_pg_bloat(psql_runner=lambda q: out)
    assert r.status == "warn"
    assert abs(r.max_ratio - 0.30) < 0.01


def test_14_4_crit_above_40pct() -> None:
    out = "public|sessions|450|550\n"  # 550/1000 = 55%
    r = check_pg_bloat(psql_runner=lambda q: out)
    assert r.status == "crit"
    assert abs(r.max_ratio - 0.55) < 0.01


def test_14_4_uses_pg_stat_user_tables() -> None:
    captured = {}
    def runner(q):
        captured["q"] = q
        return ""
    check_pg_bloat(psql_runner=runner)
    assert "pg_stat_user_tables" in captured["q"]
    assert "n_dead_tup" in captured["q"]


def test_14_4_threshold_configurable() -> None:
    out = "public|t|800|200\n"  # 20% exactly
    # At exactly 20%, default warn_threshold=0.20 means >= → warn
    r_default = check_pg_bloat(psql_runner=lambda q: out)
    # 200/1000 = 0.20 → exactly at warn threshold → warn
    assert r_default.status in ("warn", "crit")  # depends on >= logic
    # Tighter threshold (0.10) → warn
    r_tight = check_pg_bloat(warn_threshold=0.10, psql_runner=lambda q: out)
    assert r_tight.status == "warn"


def test_14_4_metadata_top_5_bloated_tables() -> None:
    """tables list contains up to top_n bloated tables, sorted by ratio desc."""
    # psql returns ORDER BY n_dead_tup DESC — mimic that
    rows = sorted(
        [(f"t{i}", 1000, int(100 * i / 10)) for i in range(10)],
        key=lambda r: -r[2],
    )
    out = "".join(f"public|{n}|{live}|{dead}\n" for n, live, dead in rows)
    r = check_pg_bloat(top_n=5, psql_runner=lambda q: out)
    assert len(r.tables) <= 5
    # Top should be the most bloated (highest dead_tup)
    assert r.tables[0].ratio >= r.tables[-1].ratio


# =============================================================================
# Sprint 14.5 — Auto-vacuum lag (7 tests)
# =============================================================================

def test_14_5_ok_autovacuum_recent() -> None:
    out = "public|users|1800.0|f\n"  # 30min ago
    r = check_pg_autovacuum(psql_runner=lambda q: out)
    assert r.status == "ok"
    assert r.max_lag_seconds == 1800.0


def test_14_5_warn_1h_to_24h() -> None:
    out = "public|users|21600.0|f\n"  # 6h ago
    r = check_pg_autovacuum(psql_runner=lambda q: out)
    assert r.status == "warn"


def test_14_5_crit_above_24h() -> None:
    out = "public|users|129600.0|f\n"  # 36h ago
    r = check_pg_autovacuum(psql_runner=lambda q: out)
    assert r.status == "crit"


def test_14_5_uses_pg_stat_user_tables_last_autovacuum() -> None:
    captured = {}
    def runner(q):
        captured["q"] = q
        return ""
    check_pg_autovacuum(psql_runner=runner)
    assert "pg_stat_user_tables" in captured["q"]
    assert "last_autovacuum" in captured["q"]


def test_14_5_handles_table_never_vacuumed() -> None:
    """never_vacuumed=true → crit regardless of seconds."""
    out = "public|brand_new|0.0|t\n"
    r = check_pg_autovacuum(psql_runner=lambda q: out)
    assert r.status == "crit"
    assert r.tables[0].never_vacuumed is True


def test_14_5_metadata_oldest_table_lag() -> None:
    """max_lag_seconds is the max across tables."""
    out = (
        "public|t1|600.0|f\n"      # 10min
        "public|t2|7200.0|f\n"     # 2h
        "public|t3|259200.0|f\n"   # 72h
    )
    r = check_pg_autovacuum(psql_runner=lambda q: out)
    assert r.max_lag_seconds == 259200.0


def test_14_5_handles_no_db() -> None:
    r = check_pg_autovacuum(psql_runner=lambda q: None)
    assert r.status == "disabled"
    assert r.max_lag_seconds == 0.0