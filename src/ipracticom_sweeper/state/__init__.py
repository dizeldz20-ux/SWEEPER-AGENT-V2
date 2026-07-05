"""Persistent state store."""
from .store import Event, Alert, Repair, StateStore
from .sqlite_store import SQLiteStateStore


def create_state_store(db_path: str | None = None) -> SQLiteStateStore:
    """Factory: defaults to /var/lib/ipracticom-sweeper/state.db."""
    if db_path is None:
        db_path = "/var/lib/ipracticom-sweeper/state.db"
    return SQLiteStateStore(db_path)


__all__ = ["Event", "Alert", "Repair", "StateStore", "SQLiteStateStore", "create_state_store"]
