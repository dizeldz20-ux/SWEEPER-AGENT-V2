"""Connector persistence — load/save AWS SSM connector configs.

A connector is a remote host the operator wants to monitor via SSM.
Stored as a flat YAML file in $IPRACTICOM_SWEEPER_STATE_DIR/connectors.yaml.

Schema (one entry per connector):
    name: str               (unique, becomes the snapshot filename + Slack host id)
    instance_id: str        (EC2 instance id, e.g. i-0abc123)
    region: str             (AWS region, e.g. il-central-1)
    tags: dict[str, str]    (optional, for filtering)
    enabled: bool           (default True; collectors skip disabled)
    created_at: float       (unix timestamp)
    last_collected_at: float|None  (last successful SSM collection)
    last_error: str|None    (last failure reason, cleared on success)

The file is small (1 entry per host, even at 50 hosts it's < 50 lines).
YAML hand-edits between dashboard sessions are preserved as long as the
keys above are honored.

Why not SQLite: the data is tiny, queries are "list all + get one by name",
and a YAML file can be diffed/backed-up with git. SQL would be over-engineering.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .._log import log_suppressed


# --- Schema --------------------------------------------------------------

# Reserved keys in the YAML schema — used to validate hand-edits.
_RESERVED_KEYS = frozenset({
    "name", "instance_id", "region", "tags", "enabled",
    "created_at", "last_collected_at", "last_error",
    "freeswitch_enabled",
})


@dataclass
class Connector:
    """One remote host to monitor via AWS SSM."""
    name: str
    instance_id: str
    region: str = "il-central-1"
    tags: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    # Whether this machine runs FreeSWITCH (a PBX). Only when True does the
    # remote collection probe fs_cli — the operator opts in per-machine in the
    # add-machine wizard. Off by default: most machines are not PBXs, and the
    # agent's own box never is.
    freeswitch_enabled: bool = False
    created_at: float = field(default_factory=lambda: time.time())
    last_collected_at: float | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Connector":
        """Build from YAML/JSON dict. Drops unknown keys, fills defaults."""
        clean = {k: v for k, v in data.items() if k in _RESERVED_KEYS}
        if "name" not in clean or "instance_id" not in clean:
            raise ValueError("Connector requires 'name' and 'instance_id'")
        # Ensure tags is a dict (YAML may parse {} correctly, but be defensive)
        if not isinstance(clean.get("tags"), dict):
            clean["tags"] = {}
        return cls(**clean)


# --- File location -------------------------------------------------------

def state_dir() -> Path:
    """Resolve the state directory from env (set by systemd / conftest)."""
    base = os.environ.get("IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper")
    return Path(base)


def connectors_file() -> Path:
    return state_dir() / "connectors.yaml"


# --- Read / Write --------------------------------------------------------

def load_all() -> list[Connector]:
    """Read all connectors from disk. Returns [] if the file is missing/empty.

    Skips (does NOT raise) on a malformed entry — operators need a dashboard
    that boots even if they fat-fingered a hand-edit.
    """
    path = connectors_file()
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[Connector] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(Connector.from_dict(entry))
        except (ValueError, TypeError) as e:
            log_suppressed("connectors_from_dict", e)
            continue
    return out


def _load_all_strict() -> list[Connector]:
    """Like ``load_all`` but REFUSES to proceed on a corrupt file.

    The lenient ``load_all`` returns ``[]`` on unparseable YAML so the
    dashboard still boots. Mutations must NOT reuse that behaviour: a
    read-modify-write that starts from ``[]`` would ``save_all`` over a
    file the operator merely fat-fingered, silently deleting every existing
    connector. Raising here aborts the write and preserves the file.
    """
    path = connectors_file()
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(
            f"connectors file is corrupt (YAML error); refusing to overwrite: {e}"
        ) from e
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("connectors file is not a YAML list; refusing to overwrite")
    out: list[Connector] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("connectors file has a non-mapping entry; refusing to overwrite")
        out.append(Connector.from_dict(entry))
    return out


def save_all(connectors: list[Connector]) -> None:
    """Atomic write — write to .tmp then rename, so a crash mid-write
    never leaves the dashboard reading a half-written file."""
    path = connectors_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    payload = [c.to_dict() for c in connectors]
    tmp.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(path)


# --- Convenience mutations (return the updated list, never mutate in place) ---

def get_by_name(name: str) -> Connector | None:
    """Find one connector by its name. Case-sensitive."""
    for c in load_all():
        if c.name == name:
            return c
    return None


def add(connector: Connector) -> Connector:
    """Insert. Raises ValueError on duplicate name."""
    existing = _load_all_strict()
    if any(c.name == connector.name for c in existing):
        raise ValueError(f"connector name already exists: {connector.name}")
    existing.append(connector)
    save_all(existing)
    return connector


def update(name: str, **changes: Any) -> Connector:
    """Update fields on an existing connector. Raises KeyError if missing.

    Reserved keys only — caller cannot inject arbitrary new keys via update.
    `created_at` is immutable: it's filtered out if the caller tries to change it.
    """
    if "created_at" in changes:
        changes.pop("created_at")
    unknown = set(changes) - _RESERVED_KEYS
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    existing = _load_all_strict()
    for i, c in enumerate(existing):
        if c.name == name:
            for k, v in changes.items():
                setattr(existing[i], k, v)
            save_all(existing)
            return existing[i]
    raise KeyError(name)


def remove(name: str) -> bool:
    """Delete by name. Returns True if removed, False if not found."""
    existing = _load_all_strict()
    new = [c for c in existing if c.name != name]
    if len(new) == len(existing):
        return False
    save_all(new)
    return True


def mark_collected(name: str, ts: float | None = None) -> None:
    """Record a successful SSM collection. Used by the collector loop."""
    try:
        update(name, last_collected_at=ts or time.time(), last_error=None)
    except KeyError as e:
        # Connector was deleted while we were collecting — race is benign.
        log_suppressed("connectors_mark_collected", e)
    except ValueError as e:
        # Corrupt connectors file — skip the write rather than wipe it.
        log_suppressed("connectors_mark_collected_corrupt", e)


def mark_error(name: str, error: str) -> None:
    """Record a failed SSM collection (e.g. timeout, no IAM role on remote)."""
    try:
        update(name, last_error=error[:500])  # cap to keep YAML readable
    except KeyError as e:
        log_suppressed("connectors_mark_error", e)
    except ValueError as e:
        # Corrupt connectors file — skip the write rather than wipe it.
        log_suppressed("connectors_mark_error_corrupt", e)