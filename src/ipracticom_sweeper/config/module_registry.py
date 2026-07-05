"""Module registry — catalog of all available monitors/repairs/runbooks.

Two sources of truth
--------------------
1. ``config/module_catalog.yaml`` — hand-curated, bilingual descriptions
   and per-module params. Lives in the repo so it's version-controlled.
2. The actual code in ``monitor/``, ``repair/``, and ``runbooks/``.
   This is what the runtime can actually execute.

This module:
- Loads the catalog
- Cross-checks each catalog entry against the registered functions
  in the code
- Exposes a flat list of ``ModuleInfo`` records for the dashboard
- Exposes a filter helper for the host-config page

Drift detection
---------------
``discover_modules(strict=False)`` returns the union and logs warnings
for catalog entries with no matching function, or registered functions
with no catalog entry. ``strict=True`` raises so a CI step can fail
on drift.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog
import yaml

from ipracticom_sweeper.config.paths import ROOT

# Keep the dotted logger name identical to the stdlib version so existing
# log-scrapers (`--log-filter ipracticom_sweeper.module_registry`) keep
# matching after v1.5.10.
logger = structlog.get_logger("ipracticom_sweeper.module_registry")

# Catalog path is co-located with this module so it ships in the wheel.
CATALOG_PATH = Path(__file__).parent / "module_catalog.yaml"

KIND_MONITOR = "monitor"
KIND_REPAIR = "repair"
KIND_RUNBOOK = "runbook"
ALL_KINDS = (KIND_MONITOR, KIND_REPAIR, KIND_RUNBOOK)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str                       # "int" | "float" | "str" | "list" | "bool"
    default: Any = None
    description: str = ""


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    kind: str                       # "monitor" | "repair" | "runbook"
    title_en: str
    title_he: str
    description: str
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    risk: str = "low"               # "low" | "medium" | "high"
    in_code: bool = True            # False = catalog-only, missing in code
    catalog_only: bool = False      # True = no code backing; cannot be enabled


# ---------------------------------------------------------------------------
# Catalog loader
# ---------------------------------------------------------------------------

def _load_catalog() -> list[dict[str, Any]]:
    """Return raw catalog entries as a list of dicts."""
    if not CATALOG_PATH.exists():
        logger.warning("catalog not found", path=str(CATALOG_PATH))
        return []
    # Explicit utf-8: the catalog has Hebrew titles + em-dashes, and read_text()
    # otherwise falls back to the platform locale (cp1252 on Windows) and raises
    # UnicodeDecodeError.
    return yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or []


def _to_module_info(d: dict[str, Any], in_code: bool) -> ModuleInfo:
    return ModuleInfo(
        name=d["name"],
        kind=d["kind"],
        title_en=d.get("title_en", ""),
        title_he=d.get("title_he", ""),
        description=d.get("description", ""),
        params=tuple(
            ParamSpec(
                name=p["name"],
                type=p.get("type", "str"),
                default=p.get("default"),
                description=p.get("description", ""),
            )
            for p in (d.get("params") or [])
        ),
        tags=tuple(d.get("tags") or ()),
        risk=d.get("risk", "low"),
        in_code=in_code,
        catalog_only=not in_code,
    )


# ---------------------------------------------------------------------------
# Code-side discovery
# ---------------------------------------------------------------------------

def _registered_function_names(package: str) -> set[str]:
    """Return every top-level function name defined in ``package``."""
    pkg = importlib.import_module(package)
    names: set[str] = set()
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{package}.{info.name}")
        except Exception as exc:
            logger.debug("skipping module",
                         package=package,
                         name=info.name,
                         exc_type=type(exc).__name__,
                         exc=str(exc))
            continue
        for name, obj in vars(mod).items():
            if name.startswith("_") or not callable(obj):
                continue
            if not inspect.isfunction(obj):
                continue
            names.add(name)
    return names


_MONITOR_ALIASES: dict[str, str] = {
    # catalog name → canonical monitor file stem
    "fs_inode_check": "freeswitch",          # see check_fs13_log_disk_usage
    "freeswitch_health": "freeswitch",       # see check_fs01..15 in monitor/freeswitch.py
}


def _monitor_names() -> set[str]:
    """Monitor modules live in ipracticom_sweeper.monitor/ as one file
    per monitor. The file stem is the canonical name — there's no
    per-monitor top-level function we can introspect reliably.

    Aliases in ``_MONITOR_ALIASES`` let the catalog name diverge from
    the file stem (e.g. for grouped sub-checks inside a single file).
    """
    import ipracticom_sweeper.monitor as pkg
    out: set[str] = set()
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_") or info.name in {"checks", "bot_connectivity"}:
            # 'checks' is the orchestrator; 'bot_connectivity' is the self-
            # monitoring bot-health aggregator (lives in snapshot["self"], not a
            # per-host catalog monitor). Neither is a leaf monitor.
            continue
        out.add(info.name)
    out.update(_MONITOR_ALIASES.values())
    return out


def _repair_names() -> set[str]:
    """Repair names are the names registered via actions.register() and
    actions_extra module-level repair_* functions.
    """
    from ipracticom_sweeper.repair.actions import list_available_repairs
    in_actions = set(list_available_repairs())
    extras = _registered_function_names("ipracticom_sweeper.repair")
    # Strip the "repair_" prefix that the extras module uses
    extras_short = {n.removeprefix("repair_") for n in extras if n.startswith("repair_")}
    return in_actions | extras_short


def _runbook_names() -> set[str]:
    """Runbook names = every ``*_runbook`` top-level function in
    ipracticom_sweeper.runbooks.engine. Catalog entries strip the
    ``_runbook`` suffix; the code uses the suffix.
    """
    pkg = importlib.import_module("ipracticom_sweeper.runbooks.engine")
    out: set[str] = set()
    for name, obj in vars(pkg).items():
        if name.endswith("_runbook") and callable(obj) and inspect.isfunction(obj):
            out.add(name)  # keep the suffix for matching
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover_modules(*, strict: bool = False, lang: str = "en") -> list[ModuleInfo]:
    """Return all catalog entries, annotated with whether code exists.

    Args:
        strict: if True, raise ValueError on catalog/code drift.
        lang:   currently unused; reserved for future i18n selection.

    Returns:
        A list of ``ModuleInfo`` sorted by (kind, name).
    """
    catalog = _load_catalog()
    monitors = _monitor_names()
    repairs = _repair_names()
    runbooks = _runbook_names()

    def in_code(entry: dict[str, Any]) -> bool:
        name = entry["name"]
        kind = entry["kind"]
        if kind == KIND_MONITOR:
            # Catalog names may have suffixes (e.g. "memory_check");
            # match by either exact stem or _check suffix.
            if name in _MONITOR_ALIASES:
                return _MONITOR_ALIASES[name] in monitors
            return (name in monitors
                    or name.removesuffix("_check") in monitors
                    or name.removesuffix("_v2") in monitors
                    or name.removesuffix("_v2_part2") in monitors
                    or f"{name}_check" in monitors
                    or name.removesuffix("_health") in monitors)
        if kind == KIND_REPAIR:
            return name in repairs
        if kind == KIND_RUNBOOK:
            return (f"{name}_runbook" in runbooks
                    or f"{name}_recovery_runbook" in runbooks)
        return False

    result = [_to_module_info(d, in_code(d)) for d in catalog]

    # Drift reporting
    for m in result:
        if m.catalog_only:
            msg = f"catalog entry {m.kind}:{m.name} has no matching code"
            if strict:
                raise ValueError(msg)
            logger.warning(msg)

    # Reverse drift: code with no catalog entry. We must account for
    # catalog aliases AND for the ``name+_check``/``name+_health``
    # suffix convention — the catalog can name a monitor ``disk_check``
    # while the on-disk file is ``disk.py``. Those stems are legitimate
    # backends for catalog entries and must not appear as code-only.
    catalog_names: set[tuple[str, str]] = {(m.kind, m.name) for m in result}
    catalog_stems_by_kind: dict[str, set[str]] = {}
    for m in result:
        catalog_stems_by_kind.setdefault(m.kind, set()).add(m.name)
    canonical_monitor_targets = set(_MONITOR_ALIASES.values())
    alias_keys = set(_MONITOR_ALIASES.keys())  # names that have explicit aliases

    for kind, names in (
        (KIND_MONITOR, monitors),
        (KIND_REPAIR, repairs),
        (KIND_RUNBOOK, runbooks),
    ):
        catalog_stems = catalog_stems_by_kind.get(kind, set())
        for n in names:
            if (kind, n) in catalog_names:
                continue
            if kind == KIND_MONITOR and n in canonical_monitor_targets:
                # legitimate alias target; skip
                continue
            if kind == KIND_MONITOR:
                # suffix-stripped catalog names map to these stems
                if n in alias_keys:
                    continue
                catalog_stripped = {
                    c.removesuffix("_check") for c in catalog_stems
                } | {
                    c.removesuffix("_health") for c in catalog_stems
                }
                if n in catalog_stripped:
                    continue
            if kind == KIND_RUNBOOK:
                # runbooks may use ``name_runbook`` or ``name_recovery_runbook``
                if (n in catalog_stems
                        or n.removesuffix("_runbook") in catalog_stems
                        or n.removesuffix("_recovery_runbook") in catalog_stems
                        or f"{n}_runbook" in catalog_stems
                        or f"{n}_recovery_runbook" in catalog_stems):
                    continue
            msg = f"code-only: {kind}:{n} not in catalog"
            if strict:
                raise ValueError(msg)
            logger.info(msg)

    result.sort(key=lambda m: (m.kind, m.name))
    return result


# Modules tagged with this belong to the agent's OWN self-monitoring (its state
# dir, heartbeat, snapshot freshness). They run always-on in the background on
# the agent machine and must NOT appear in the add-machine catalog or a
# monitored host's per-host config.
SELF_TAG = "self"


def filter_modules(
    modules: list[ModuleInfo],
    *,
    kind: Optional[str] = None,
    tag: Optional[str] = None,
    risk: Optional[str] = None,
    available_only: bool = False,
    exclude_self: bool = False,
) -> list[ModuleInfo]:
    """Filter helper for the dashboard / API.

    ``exclude_self=True`` drops agent self-monitoring modules (tag ``self``) —
    used for the add-machine catalog and new-host defaults, since those checks
    are the agent watching itself, not a monitored machine.
    """
    out = []
    for m in modules:
        if kind is not None and m.kind != kind:
            continue
        if tag is not None and tag not in m.tags:
            continue
        if risk is not None and m.risk != risk:
            continue
        if available_only and m.catalog_only:
            continue
        if exclude_self and SELF_TAG in m.tags:
            continue
        out.append(m)
    return out


def get_module(name: str, *, kind: Optional[str] = None) -> Optional[ModuleInfo]:
    """Lookup by name. If kind is given, also matches it."""
    for m in discover_modules():
        if m.name != name:
            continue
        if kind is not None and m.kind != kind:
            continue
        return m
    return None


# ---------------------------------------------------------------------------
# Defaults — what a new host gets if no per-host config exists
# ---------------------------------------------------------------------------

def default_host_config(name: str) -> dict[str, Any]:
    """Build a sensible default HostConfig dict for a brand-new host.

    Strategy:
    - All low-risk monitors: enabled
    - All medium-risk monitors: enabled
    - High-risk monitors: disabled (operator opts in)
    - Repairs: enabled but require_approval=True
    - Runbooks: enabled
    - No suppressions
    """
    # exclude_self: a new monitored host must not inherit the agent's own
    # self-monitoring checks — those run always-on on the agent box.
    mods = filter_modules(discover_modules(), available_only=True, exclude_self=True)
    monitors: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    runbooks: list[dict[str, Any]] = []

    for m in mods:
        defaults = {p.name: p.default for p in m.params}
        if m.kind == KIND_MONITOR:
            monitors.append({
                "name": m.name,
                "enabled": m.risk != "high",
                **({"interval_sec": defaults["interval_sec"]}
                   if "interval_sec" in defaults else {}),
                **{k: v for k, v in defaults.items()
                   if k not in ("interval_sec",)},
            })
        elif m.kind == KIND_REPAIR:
            repairs.append({
                "name": m.name,
                "enabled": m.risk == "low",
                "require_approval": m.risk != "low",
                **defaults,
            })
        elif m.kind == KIND_RUNBOOK:
            runbooks.append({
                "name": m.name,
                "enabled": m.risk != "high",
                **defaults,
            })

    return {
        "host": {"name": name, "description": "", "enabled": True},
        "monitors": monitors,
        "repairs": repairs,
        "runbooks": runbooks,
        "suppressions": [],
    }


__all__ = [
    "ModuleInfo", "ParamSpec",
    "KIND_MONITOR", "KIND_REPAIR", "KIND_RUNBOOK", "ALL_KINDS",
    "discover_modules", "filter_modules", "get_module",
    "default_host_config",
]
