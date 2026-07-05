"""Per-host configuration: YAML + SQLite cache.

Why two stores
--------------
- **YAML** at ``$IPRACTICOM_SWEEPER_STATE_DIR/hosts/<hostname>.yaml``
  is the source of truth. Operators (or the dashboard) edit it;
  ``git diff`` shows what changed; manual recovery is one ``vi`` away.
- **SQLite** at ``$IPRACTICOM_SWEEPER_STATE_DIR/hosts.db`` is a read
  cache. The dashboard query path is "list all hosts, list all
  monitors for host X" — a single SELECT beats reading every YAML
  file every time.

Write path: YAML is rewritten, then SQLite is invalidated and
re-populated on the next read. Read path: SQLite first, fall back to
YAML on miss, write back to SQLite.

File format
-----------
See ``docs/HOST_CONFIG.md`` (TBD) for the full schema. The dataclass
below is the Python mirror.

Suppressions
------------
A *suppression* silences a monitor's alerts for a host until a
given time (or forever, when ``until`` is None). Suppressions are
written to YAML, surfaced in the dashboard, and never executed
automatically — only an operator can suppress a rule.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from ipracticom_sweeper.config.paths import ROOT
from ipracticom_sweeper._log import log_suppressed


# ---------------------------------------------------------------------------
# Schema (dataclass mirror of the YAML)
# ---------------------------------------------------------------------------

@dataclass
class Suppression:
    """One silenced rule for one host."""
    rule: str                   # e.g. "fs_inode_check"
    until: Optional[str] = None # ISO8601 timestamp; None = permanent
    reason: str = ""

    def is_active(self, now: Optional[datetime] = None) -> bool:
        """Return True if this suppression is currently silencing."""
        if self.until is None:
            return True
        try:
            until = datetime.fromisoformat(self.until)
        except (ValueError, TypeError):
            # Malformed `until` (e.g. a bad hand-edit) must not crash every
            # caller of is_suppressed / list_active_suppressions / cleanup.
            # Treat an unparseable expiry as already expired — fail open toward
            # alerting so a typo can't silence a real alert forever.
            return False
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return (now or datetime.now(timezone.utc)) < until


@dataclass
class MonitorConfig:
    """One monitor rule's per-host settings."""
    name: str
    enabled: bool = True
    interval_sec: int = 60
    # Free-form bag for module-specific knobs (threshold_pct, max_offset_ms, ...).
    # YAML side calls this "settings:" — we flatten it for in-memory ease.
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class RepairConfig:
    name: str
    enabled: bool = True
    require_approval: bool = True  # safety default
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunbookConfig:
    name: str
    enabled: bool = True
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class HostConfig:
    """One host's full configuration."""
    name: str
    description: str = ""
    enabled: bool = True
    monitors: list[MonitorConfig] = field(default_factory=list)
    repairs: list[RepairConfig] = field(default_factory=list)
    runbooks: list[RunbookConfig] = field(default_factory=list)
    suppressions: list[Suppression] = field(default_factory=list)
    updated_at: str = ""  # ISO8601 of last write

    # ---- helpers --------------------------------------------------------

    def monitor(self, name: str) -> Optional[MonitorConfig]:
        return next((m for m in self.monitors if m.name == name), None)

    def repair(self, name: str) -> Optional[RepairConfig]:
        return next((r for r in self.repairs if r.name == name), None)

    def runbook(self, name: str) -> Optional[RunbookConfig]:
        return next((r for r in self.runbooks if r.name == name), None)

    def is_suppressed(self, rule: str) -> tuple[bool, Optional[Suppression]]:
        """Return (is_active, suppression) for a given rule on this host."""
        for s in self.suppressions:
            if s.rule == rule and s.is_active():
                return True, s
        return False, None

    def to_yaml_dict(self) -> dict[str, Any]:
        """Serialize back to the YAML schema."""
        d: dict[str, Any] = {
            "host": {
                "name": self.name,
                "description": self.description,
                "enabled": self.enabled,
            },
            "monitors": [
                {"name": m.name, "enabled": m.enabled,
                 "interval_sec": m.interval_sec, **m.settings}
                for m in self.monitors
            ],
            "repairs": [
                {"name": r.name, "enabled": r.enabled,
                 "require_approval": r.require_approval, **r.settings}
                for r in self.repairs
            ],
            "runbooks": [
                {"name": r.name, "enabled": r.enabled, **r.settings}
                for r in self.runbooks
            ],
            "suppressions": [
                {"rule": s.rule, "until": s.until, "reason": s.reason}
                for s in self.suppressions
            ],
        }
        return d

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# YAML load/dump
# ---------------------------------------------------------------------------

def _hosts_dir() -> Path:
    p = ROOT() / "hosts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _host_yaml_path(name: str) -> Path:
    # Sanitize: only allow [a-zA-Z0-9_.-]
    safe = "".join(c for c in name if c.isalnum() or c in "._-")
    if not safe or safe != name:
        raise ValueError(f"invalid host name: {name!r}")
    return _hosts_dir() / f"{safe}.yaml"


def load_host(name: str) -> HostConfig:
    """Load one host from YAML. Returns default config if file missing."""
    path = _host_yaml_path(name)
    if not path.exists():
        return HostConfig(name=name)
    raw = yaml.safe_load(path.read_text()) or {}
    return _parse_host(raw)


def enabled_monitor_modules(name: str) -> Optional[set[str]]:
    """Canonical monitor stems ENABLED for a host — used to scope a manual scan.

    The per-machine "run scan" must diagnose only the checks the operator
    selected for that machine, not the full agent-wide set. This maps the
    host's saved monitor entries to the canonical module stems the diagnose
    layer keys on: ``cpu_check`` → ``cpu``, ``disk_check`` → ``disk`` (the
    ``_check`` suffix is stripped), while bare names like ``services`` /
    ``security`` pass through unchanged. Those stems match the keys in
    ``diagnose.engine.DIAGNOSERS``.

    Returns:
        ``None`` when the host has NO monitor config at all — the caller must
        then NOT scope (run/diagnose everything), preserving back-compat and
        never hiding problems on an unconfigured host. Otherwise the set of
        enabled stems (possibly a subset, or empty when the operator disabled
        every check).
    """
    cfg = load_host(name)
    if not cfg.monitors:
        return None
    return {m.name.removesuffix("_check") for m in cfg.monitors if m.enabled}


def _parse_host(d: dict[str, Any]) -> HostConfig:
    h = d.get("host", {}) or {}
    cfg = HostConfig(
        name=h.get("name", "unknown"),
        description=h.get("description", ""),
        enabled=h.get("enabled", True),
    )
    for m in d.get("monitors", []) or []:
        name = m.get("name")
        if not name:
            continue
        settings = {k: v for k, v in m.items()
                    if k not in ("name", "enabled", "interval_sec")}
        cfg.monitors.append(MonitorConfig(
            name=name,
            enabled=m.get("enabled", True),
            interval_sec=int(m.get("interval_sec", 60)),
            settings=settings,
        ))
    for r in d.get("repairs", []) or []:
        name = r.get("name")
        if not name:
            continue
        settings = {k: v for k, v in r.items()
                    if k not in ("name", "enabled", "require_approval")}
        cfg.repairs.append(RepairConfig(
            name=name,
            enabled=r.get("enabled", True),
            require_approval=r.get("require_approval", True),
            settings=settings,
        ))
    for r in d.get("runbooks", []) or []:
        name = r.get("name")
        if not name:
            continue
        settings = {k: v for k, v in r.items() if k not in ("name", "enabled")}
        cfg.runbooks.append(RunbookConfig(
            name=name, enabled=r.get("enabled", True), settings=settings,
        ))
    for s in d.get("suppressions", []) or []:
        if s.get("rule"):
            cfg.suppressions.append(Suppression(
                rule=s["rule"],
                until=s.get("until"),
                reason=s.get("reason", ""),
            ))
    return cfg


def save_host(cfg: HostConfig) -> Path:
    """Write a host config to YAML and refresh the SQLite cache.

    Atomic on the YAML side: write to a unique tmp file, fsync, then
    os.replace onto the final path. The rename is atomic on POSIX, so a
    concurrent reader will see either the old file or the new file —
    never a partial one.

    The cache update uses UPSERT (INSERT OR REPLACE) so there's no
    window where the host row is missing for a concurrent reader.

    v1.5.16 hardening:
    - Unique tmp name (UUID) so concurrent saves for the same host
      don't truncate each other's tmp files.
    - fsync before rename so power-cut doesn't leave the YAML empty.
    - Cache upsert (not delete-then-insert) to avoid the read race
      where a concurrent reader sees no row.
    """
    cfg.updated_at = datetime.now(timezone.utc).isoformat()
    path = _host_yaml_path(cfg.name)

    # Write to unique tmp file, fsync, then rename (atomic on POSIX).
    tmp = path.with_suffix(f".yaml.{uuid.uuid4().hex}.tmp")
    try:
        data = yaml.safe_dump(cfg.to_yaml_dict(), sort_keys=False).encode("utf-8")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    finally:
        # Clean up tmp if rename didn't happen (e.g. fsync failed).
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError as exc:
                log_suppressed("host_config.save_host.tmp_cleanup", exc)

    # Cache upsert — never leaves the row missing for concurrent readers.
    try:
        _populate_cache(cfg)
    except sqlite3.Error as exc:
        log_suppressed("host_config.save_host.populate_cache", exc,
                       extras={"host": cfg.name})
    return path


def list_hosts() -> list[str]:
    """Return all known host names (from YAML files)."""
    d = _hosts_dir()
    out = []
    for p in sorted(d.glob("*.yaml")):
        out.append(p.stem)
    return out


def delete_host(name: str) -> bool:
    """Delete a host's YAML config. Returns True if anything was removed."""
    path = _host_yaml_path(name)
    if path.exists():
        path.unlink()
        _invalidate_cache(name)
        return True
    return False


# ---------------------------------------------------------------------------
# Suppression engine (Slice 3)
# ---------------------------------------------------------------------------
#
# CRUD on top of the per-host YAML files. The dataclass + the YAML
# schema already exist (see ``Suppression`` and ``HostConfig.to_yaml_dict``);
# these helpers give callers a stable API that does not require them
# to know the storage layout.
#
# Audit
# -----
# add_suppression and remove_suppression emit one ``suppression.add`` /
# ``suppression.remove`` audit event each via ``ipracticom_sweeper.audit.emit``.
# cleanup_expired_suppressions does not (it is housekeeping, not a
# decision the operator took).


def _emit_audit(event: str, host: str, payload: dict) -> None:
    """Lazy-import audit to avoid an import cycle."""
    from ipracticom_sweeper.audit.logger import emit
    emit(event, {"host": host, **payload})


def add_suppression(
    name: str,
    rule: str,
    *,
    until: Optional[str] = None,
    reason: str = "",
) -> Suppression:
    """Add (or replace) a suppression for ``(name, rule)``.

    Auto-creates the host YAML if it does not yet exist. Re-adding
    the same rule on the same host replaces the existing entry
    (no duplicate entries stacked).

    Returns the stored Suppression. ``name`` must pass the same
    sanitization as ``save_host``; ``until`` must be ISO8601 or
    ``None`` (permanent).
    """
    # _host_yaml_path raises ValueError on bad host names; calling it
    # here gives us the same sanitization for free.
    _host_yaml_path(name)
    if not rule or not rule.strip():
        raise ValueError("suppression rule must be a non-empty string")
    cfg = load_host(name)
    new_entry = Suppression(rule=rule, until=until, reason=reason)
    replaced = False
    for i, s in enumerate(cfg.suppressions):
        if s.rule == rule:
            cfg.suppressions[i] = new_entry
            replaced = True
            break
    if not replaced:
        cfg.suppressions.append(new_entry)
    save_host(cfg)
    _emit_audit("suppression.add", name, {
        "rule": rule,
        "until": until,
        "reason": reason,
        "replaced": replaced,
    })
    return new_entry


def remove_suppression(name: str, rule: str) -> bool:
    """Remove the suppression for ``(name, rule)``.

    Idempotent: returns False if there is nothing to remove. Returns
    True if an entry was actually deleted.
    """
    path = _host_yaml_path(name)  # raises on bad name
    if not path.exists():
        return False
    cfg = load_host(name)
    original_len = len(cfg.suppressions)
    cfg.suppressions = [s for s in cfg.suppressions if s.rule != rule]
    if len(cfg.suppressions) == original_len:
        return False
    save_host(cfg)
    _emit_audit("suppression.remove", name, {"rule": rule})
    return True


def list_active_suppressions(name: str) -> list[Suppression]:
    """All currently-active suppressions for ``host`` (expired are
    filtered out automatically — they remain on disk until the next
    ``cleanup_expired_suppressions`` pass).

    Returns an empty list for unknown hosts.
    """
    try:
        cfg = load_host(name)
    except ValueError as exc:
        log_suppressed("host_config.list_active_suppressions", exc,
                       extras={"host": name})
        return []
    return [s for s in cfg.suppressions if s.is_active()]


def cleanup_expired_suppressions() -> int:
    """Scan every host YAML, drop expired suppressions from each,
    rewrite the file. Returns the total number of entries removed.

    Hosts with no expired suppressions are NOT rewritten (mtime is
    preserved for the audit trail).
    """
    removed_total = 0
    for host_name in list_hosts():
        cfg = load_host(host_name)
        active = [s for s in cfg.suppressions if s.is_active()]
        dropped = len(cfg.suppressions) - len(active)
        if dropped == 0:
            continue
        cfg.suppressions = active
        save_host(cfg)
        removed_total += dropped
    return removed_total


# ---------------------------------------------------------------------------
# SQLite read-cache
# ---------------------------------------------------------------------------

_DB_LOCK = threading.Lock()
_DB_PATH: Optional[Path] = None


def _db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = ROOT() / "hosts.db"
    return _DB_PATH


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hosts (
            name TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS host_monitors (
            host TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            interval_sec INTEGER NOT NULL DEFAULT 60,
            settings TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (host, name)
        );
        CREATE TABLE IF NOT EXISTS host_repairs (
            host TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            require_approval INTEGER NOT NULL DEFAULT 1,
            settings TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (host, name)
        );
        CREATE TABLE IF NOT EXISTS host_runbooks (
            host TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            settings TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (host, name)
        );
        CREATE TABLE IF NOT EXISTS host_suppressions (
            host TEXT NOT NULL,
            rule TEXT NOT NULL,
            until TEXT,
            reason TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (host, rule)
        );
        CREATE INDEX IF NOT EXISTS idx_monitors_host
            ON host_monitors(host);
        CREATE INDEX IF NOT EXISTS idx_repairs_host
            ON host_repairs(host);
    """)
    conn.commit()


# Module-level cached SQLite connection (singleton).
# None = not yet opened. First call to _db_conn() opens it.
_CONN: sqlite3.Connection | None = None
# The DB path _CONN was opened against. Used to detect a path change (tests
# repointing IPRACTICOM_SWEEPER_STATE_DIR) so the cache reopens instead of
# silently serving a stale database. In production the path never changes.
_CONN_PATH: Optional[Path] = None


def _db_conn() -> sqlite3.Connection:
    """Return a process-wide cached SQLite connection.

    v1.5.8 fix: previously this opened a fresh connection on every call and
    never closed it. After a few thousand cache reads the agent would hit
    EMFILE. The connection is now cached and reused across threads
    (check_same_thread=False). WAL + busy_timeout are set on first open.
    """
    global _CONN, _CONN_PATH
    # Use globals() so we don't raise NameError if a test (or a fresh
    # import in an unusual context) has popped _CONN from the module namespace.
    p = _db_path()
    cached = globals().get("_CONN")
    # Reopen when never opened, or when the resolved DB path changed since the
    # cached connection was created. In production the path is fixed so this
    # opens exactly once; tests that repoint IPRACTICOM_SWEEPER_STATE_DIR (and
    # reset _DB_PATH) would otherwise keep serving the previous test's database
    # — the root cause of cross-test host-state leakage.
    if cached is None or globals().get("_CONN_PATH") != p:
        if cached is not None:
            try:
                cached.close()
            except sqlite3.Error as exc:
                log_suppressed("host_config._db_conn.close_stale", exc)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Default isolation_level is "" (deferred); we use explicit BEGIN/COMMIT
        # in callers so multi-statement writes are atomic.
        conn = sqlite3.connect(
            str(p), timeout=5, check_same_thread=False,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        _init_db(conn)
        _CONN = conn
        _CONN_PATH = p
    return _CONN


# Cache invalidation: when a YAML file is written, drop the SQLite
# rows for that host. The next read repopulates from YAML.
def _invalidate_cache(name: str) -> None:
    with _DB_LOCK:
        try:
            conn = _db_conn()
            # v1.5.8 fix: wrap DELETE statements in a single transaction.
            # Previously, isolation_level=None (autocommit) committed each
            # DELETE separately, so a reader between two DELETEs saw partial state.
            conn.execute("BEGIN IMMEDIATE")
            for table in ("host_monitors", "host_repairs",
                          "host_runbooks", "host_suppressions", "hosts"):
                conn.execute(f"DELETE FROM {table} WHERE host = ?", (name,))
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error as rollback_exc:
                log_suppressed(
                    "host_config._invalidate_cache.rollback",
                    rollback_exc,
                    extras={"host": name, "original": type(exc).__name__},
                )
            log_suppressed("host_config._invalidate_cache.delete", exc,
                           extras={"host": name})


def _populate_cache(cfg: HostConfig) -> None:
    """Write a HostConfig to the SQLite cache. Idempotent."""
    with _DB_LOCK:
        conn = _db_conn()
        # v1.5.8 fix: wrap the wipe+rewrite in one transaction. Previously,
        # DELETE-then-INSERT under autocommit committed per statement — a
        # reader could observe the host row with zero monitors/repairs
        # between the DELETE pass and the INSERT pass.
        try:
            conn.execute("BEGIN IMMEDIATE")
            for table in ("host_monitors", "host_repairs",
                          "host_runbooks", "host_suppressions"):
                conn.execute(
                    f"DELETE FROM {table} WHERE host = ?", (cfg.name,),
                )
            conn.execute(
                "INSERT OR REPLACE INTO hosts VALUES (?, ?, ?, ?)",
                (cfg.name, cfg.description, int(cfg.enabled), cfg.updated_at),
            )
            for m in cfg.monitors:
                conn.execute(
                    "INSERT INTO host_monitors VALUES (?, ?, ?, ?, ?)",
                    (cfg.name, m.name, int(m.enabled), m.interval_sec,
                     yaml.safe_dump(m.settings) or "{}"),
                )
            for r in cfg.repairs:
                conn.execute(
                    "INSERT INTO host_repairs VALUES (?, ?, ?, ?, ?)",
                    (cfg.name, r.name, int(r.enabled), int(r.require_approval),
                     yaml.safe_dump(r.settings) or "{}"),
                )
            for rb in cfg.runbooks:
                conn.execute(
                    "INSERT INTO host_runbooks VALUES (?, ?, ?, ?)",
                    (cfg.name, rb.name, int(rb.enabled),
                     yaml.safe_dump(rb.settings) or "{}"),
                )
            for s in cfg.suppressions:
                conn.execute(
                    "INSERT INTO host_suppressions VALUES (?, ?, ?, ?)",
                    (cfg.name, s.rule, s.until, s.reason),
                )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error as rollback_exc:
                log_suppressed(
                    "host_config._populate_cache.rollback",
                    rollback_exc,
                    extras={"host": cfg.name, "original": type(exc).__name__},
                )
            log_suppressed("host_config._populate_cache.write", exc,
                           extras={"host": cfg.name})


def _cache_get(name: str) -> Optional[HostConfig]:
    """Read one host from cache; return None if not present."""
    with _DB_LOCK:
        try:
            conn = _db_conn()
            row = conn.execute(
                "SELECT description, enabled, updated_at FROM hosts WHERE name=?",
                (name,),
            ).fetchone()
            if row is None:
                return None
            cfg = HostConfig(
                name=name, description=row[0], enabled=bool(row[1]),
                updated_at=row[2] or "",
            )
            for r in conn.execute(
                "SELECT name, enabled, interval_sec, settings "
                "FROM host_monitors WHERE host=?", (name,)
            ):
                cfg.monitors.append(MonitorConfig(
                    name=r[0], enabled=bool(r[1]), interval_sec=r[2],
                    settings=yaml.safe_load(r[3]) or {},
                ))
            for r in conn.execute(
                "SELECT name, enabled, require_approval, settings "
                "FROM host_repairs WHERE host=?", (name,)
            ):
                cfg.repairs.append(RepairConfig(
                    name=r[0], enabled=bool(r[1]), require_approval=bool(r[2]),
                    settings=yaml.safe_load(r[3]) or {},
                ))
            for r in conn.execute(
                "SELECT name, enabled, settings FROM host_runbooks WHERE host=?",
                (name,),
            ):
                cfg.runbooks.append(RunbookConfig(
                    name=r[0], enabled=bool(r[1]),
                    settings=yaml.safe_load(r[2]) or {},
                ))
            for r in conn.execute(
                "SELECT rule, until, reason FROM host_suppressions WHERE host=?",
                (name,),
            ):
                cfg.suppressions.append(Suppression(
                    rule=r[0], until=r[1], reason=r[2] or "",
                ))
            return cfg
        except sqlite3.Error as exc:
            log_suppressed("host_config._load_from_cache", exc,
                           extras={"host": name})
            return None


def get_host(name: str) -> HostConfig:
    """Public API: load host (cache-first, falls back to YAML)."""
    cached = _cache_get(name)
    if cached is not None:
        return cached
    cfg = load_host(name)
    try:
        _populate_cache(cfg)
    except sqlite3.Error as exc:
        log_suppressed("host_config.get_host.populate_cache", exc,
                       extras={"host": name})
    return cfg


def list_all_hosts() -> list[HostConfig]:
    """Return HostConfig for every known host."""
    out: list[HostConfig] = []
    seen: set[str] = set()
    # Prefer cache for hosts that have one populated already
    with _DB_LOCK:
        try:
            conn = _db_conn()
            for r in conn.execute("SELECT name FROM hosts ORDER BY name"):
                seen.add(r[0])
        except sqlite3.Error as exc:
            log_suppressed("host_config.list_all_hosts.cache_read", exc)
    # Always reconcile with the YAML directory (in case YAML changed
    # without invalidating the cache, or a new file landed)
    for name in list_hosts():
        if name in seen:
            out.append(get_host(name))
        else:
            cfg = load_host(name)
            try:
                _populate_cache(cfg)
            except sqlite3.Error as exc:
                log_suppressed("host_config.list_all_hosts.populate_cache", exc,
                               extras={"host": name})
            out.append(cfg)
    return out


__all__ = [
    "HostConfig", "MonitorConfig", "RepairConfig", "RunbookConfig",
    "Suppression",
    "load_host", "save_host", "get_host", "list_hosts", "list_all_hosts",
    "delete_host",
]
