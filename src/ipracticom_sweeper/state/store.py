"""Abstract state store interface."""
from __future__ import annotations
from typing import Protocol, Any
from dataclasses import dataclass


@dataclass
class Event:
    ts: float
    host: str
    module: str
    defcon: int
    payload: dict[str, Any]


@dataclass
class Alert:
    fingerprint: str
    first_seen: float
    last_seen: float
    count: int
    acked: bool


@dataclass
class Repair:
    ts: float
    action: str
    target: str
    success: bool
    snapshot_id: str | None


class StateStore(Protocol):
    def record_event(self, host: str, module: str, defcon: int, payload: dict) -> None: ...
    def recent_events(self, host: str, hours: float = 24.0) -> list[Event]: ...
    def get_alert(self, fingerprint: str) -> Alert | None: ...
    def upsert_alert(self, fingerprint: str) -> Alert: ...
    def record_repair(self, action: str, target: str, success: bool, snapshot_id: str | None) -> None: ...
    def cleanup(self, older_than_days: float = 30.0) -> int: ...
    def close(self) -> None: ...
