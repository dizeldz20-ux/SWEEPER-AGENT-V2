"""Local SQLite time-series storage for Sweeper metrics.

Stores one row per (host, metric, timestamp) sample. Supports:
- write: append a new sample
- query: retrieve samples for a host+metric, newest first
- prune_old_data: enforce retention (default 30 days)

Schema is created on first use. Safe for concurrent reads via
SQLite's default mode. Writes are serialized by Python's GIL +
SQLite's locking.
"""
from __future__ import annotations
import sqlite3
import time
from pathlib import Path
from typing import Any


class TimeSeriesDB:
    """SQLite-backed time-series store for host metrics."""

    def __init__(self, db_path: Path | str, retention_days: int = 30):
        self.db_path = Path(db_path)
        self.retention_days = retention_days
        # check_same_thread=False: we serialize writes via the GIL,
        # and the dashboard reads concurrently. For higher concurrency
        # we'd need WAL mode + thread-locks, but for now this is fine.
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False
        )
        # v1.5.8: enable WAL + busy_timeout to avoid 'database is locked'
        # errors under contention and to allow concurrent readers.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                host TEXT NOT NULL,
                metric TEXT NOT NULL,
                ts INTEGER NOT NULL,
                value REAL NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_metrics_host_metric_ts
            ON metrics (host, metric, ts DESC)
        """)
        self._conn.commit()

    def write(self, host: str, metric: str, value: float, ts: int | None = None) -> None:
        """Append a new sample."""
        ts = ts or int(time.time())
        self._conn.execute(
            "INSERT INTO metrics (host, metric, ts, value) VALUES (?, ?, ?, ?)",
            (host, metric, ts, value),
        )
        self._conn.commit()

    def query(
        self,
        host: str,
        metric: str,
        since_ts: int | None = None,
        until_ts: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Return samples for host+metric, newest first, capped at limit."""
        sql = "SELECT ts, value FROM metrics WHERE host = ? AND metric = ?"
        params: list[Any] = [host, metric]
        if since_ts is not None:
            sql += " AND ts >= ?"
            params.append(since_ts)
        if until_ts is not None:
            sql += " AND ts <= ?"
            params.append(until_ts)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [{"ts": r[0], "value": r[1]} for r in rows]

    def prune_old_data(self) -> int:
        """Delete samples older than retention_days. Returns rows deleted."""
        cutoff = int(time.time()) - (self.retention_days * 86400)
        cur = self._conn.execute(
            "DELETE FROM metrics WHERE ts < ?", (cutoff,)
        )
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()
