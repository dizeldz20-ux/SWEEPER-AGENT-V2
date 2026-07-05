"""SQLite implementation of StateStore."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .store import Event, Alert, Repair


_SCHEMA_FILE = Path(__file__).parent / "schema.sql"


class SQLiteStateStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")  # v1.5.8: was 0, fail-fast on lock
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        schema = _SCHEMA_FILE.read_text()
        with self._lock:
            self._conn.executescript(schema)

    def record_event(self, host: str, module: str, defcon: int, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, host, module, defcon, payload) VALUES (?, ?, ?, ?, ?)",
                (time.time(), host, module, defcon, json.dumps(payload)),
            )

    def recent_events(self, host: str, hours: float = 24.0) -> list[Event]:
        cutoff = time.time() - (hours * 3600)
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, host, module, defcon, payload FROM events WHERE host=? AND ts>=? ORDER BY ts DESC",
                (host, cutoff),
            ).fetchall()
        return [
            Event(ts=r[0], host=r[1], module=r[2], defcon=r[3], payload=json.loads(r[4]) if r[4] else {})
            for r in rows
        ]

    def get_alert(self, fingerprint: str) -> Alert | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT fingerprint, first_seen, last_seen, count, acked FROM alerts WHERE fingerprint=?",
                (fingerprint,),
            ).fetchone()
        if not row:
            return None
        return Alert(
            fingerprint=row[0], first_seen=row[1], last_seen=row[2],
            count=row[3], acked=bool(row[4]),
        )

    def upsert_alert(self, fingerprint: str) -> Alert:
        now = time.time()
        with self._lock:
            existing = self.get_alert(fingerprint)
            if existing:
                self._conn.execute(
                    "UPDATE alerts SET last_seen=?, count=count+1 WHERE fingerprint=?",
                    (now, fingerprint),
                )
            else:
                self._conn.execute(
                    "INSERT INTO alerts (fingerprint, first_seen, last_seen, count) VALUES (?, ?, ?, 1)",
                    (fingerprint, now, now),
                )
            return self.get_alert(fingerprint)  # type: ignore

    def record_repair(self, action: str, target: str, success: bool, snapshot_id: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO repairs (ts, action, target, success, snapshot_id) VALUES (?, ?, ?, ?, ?)",
                (time.time(), action, target, 1 if success else 0, snapshot_id),
            )

    def cleanup(self, older_than_days: float = 30.0) -> int:
        cutoff = time.time() - (older_than_days * 86400)
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE ts<?", (cutoff,))
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
