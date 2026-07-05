"""Tool-call surface for the chat assistant (v0.5.0 slice 3.3).

Exposes a small, stable set of operations the LLM can invoke from a chat
turn. Each tool is a pure-Python callable that returns a JSON-serializable
dict; the chat layer formats the result into the assistant reply.

Tools are intentionally narrow:
  - list_fs_checks()              — enumerate available FreeSWITCH checks
  - get_fs_check(check_id)        — run a single check by id (FS-01..FS-25)
  - run_fs_tier(tier)             — run a tier (1..4) subset
  - run_full_pipeline()           — full snapshot via monitor.checks.run_all

Heavy / blocking operations (real pipeline) are gated behind
`ENABLE_HEAVY_TOOLS=1` env flag so production stays safe-by-default.

Mock mode (default): when neither OpenAI nor Anthropic API keys are
configured, the chat layer still emits tool calls but resolves them via
this module — the UI demos the tool pipeline without external deps.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

# Lazy imports of FreeSWITCH checks (heavy: psutil, socket, fs_cli).
_CHECKS: dict[str, Any] | None = None


def _build_registry() -> dict[str, Any]:
    from ipracticom_sweeper.monitor import freeswitch as fs
    registry: dict[str, Any] = {}
    for name in dir(fs):
        if not name.startswith("check_fs"):
            continue
        fn = getattr(fs, name, None)
        if callable(fn):
            registry[name] = fn
    return registry


def _get_registry() -> dict[str, Any]:
    global _CHECKS
    if _CHECKS is None:
        _CHECKS = _build_registry()
    return _CHECKS


def list_fs_checks() -> dict[str, Any]:
    """Enumerate all available FreeSWITCH checks (light — no execution)."""
    reg = _get_registry()
    items = []
    for name in sorted(reg.keys()):
        fn = reg[name]
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        items.append({"name": name, "summary": doc[:120]})
    return {"count": len(items), "checks": items}


def _safe_run(name: str, fn: Callable[[], dict[str, Any]],
              timeout_s: float = 8.0) -> dict[str, Any]:
    """Run a single check with a hard wall-clock timeout.

    Threading-based timeout is intentionally simple — none of the FS
    checks are themselves async, and we never want one stuck call to
    wedge the chat worker.
    """
    import threading

    result: dict[str, Any] = {"name": name, "ok": False, "error": None}
    holder: dict[str, Any] = {}

    def runner() -> None:
        try:
            holder["value"] = fn()
        except Exception as exc:
            holder["error"] = str(exc)

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        result["error"] = f"timeout after {timeout_s}s"
        return result
    if "error" in holder:
        result["error"] = holder["error"]
        return result
    value = holder.get("value", {})
    if not isinstance(value, dict):
        result["error"] = f"non-dict result: {type(value).__name__}"
        return result
    result["ok"] = True
    result["result"] = value
    return result


def get_fs_check(check_id: str) -> dict[str, Any]:
    """Run a single FreeSWITCH check by id (e.g. 'check_fs01_process_running')."""
    reg = _get_registry()
    fn = reg.get(check_id)
    if fn is None:
        return {"ok": False, "error": f"unknown check: {check_id}",
                "available": sorted(reg.keys())[:10]}
    return _safe_run(check_id, fn)


def run_fs_tier(tier: int) -> dict[str, Any]:
    """Run a tier subset of FreeSWITCH checks (1, 2, 3, or 4).

    Tier definitions match slice 2.1-2.4:
      1 -> FS-01..FS-05  (service health)
      2 -> FS-06..FS-09  (network integrity)
      3 -> FS-10..FS-15  (operational + baseline)
      4 -> FS-16..FS-25  (edge cases)
    """
    reg = _get_registry()
    prefix_map = {1: "check_fs0", 2: "check_fs0",
                  3: "check_fs1", 4: "check_fs1"}
    if tier not in prefix_map:
        return {"ok": False, "error": f"invalid tier: {tier}"}
    # Tier 1+2 share 'check_fs0' (FS-01..FS-09). Distinguish by FS-XX number.
    tier_ranges = {1: range(1, 6), 2: range(6, 10),
                   3: range(10, 16), 4: range(16, 26)}
    wanted = set()
    for n in tier_ranges[tier]:
        # Names like check_fs01_process_running, check_fs25_fail2ban_active.
        wanted.update(k for k in reg if f"{n:02d}" in k)
    results = []
    for name in sorted(wanted):
        results.append(_safe_run(name, reg[name]))
    return {"tier": tier, "ran": len(results), "results": results}


def run_full_pipeline() -> dict[str, Any]:
    """Run the full sweeper pipeline. Gated by ENABLE_HEAVY_TOOLS env.

    The full pipeline writes to disk cache, may run network probes, and
    is intentionally slow (~30-60s on a real box). We refuse to run it
    unless the operator opts in.
    """
    if os.environ.get("ENABLE_HEAVY_TOOLS", "").lower() not in ("1", "true", "yes"):
        return {"ok": False,
                "error": "ENABLE_HEAVY_TOOLS not set; refusing to run pipeline"}
    from ipracticom_sweeper.monitor import checks as mchecks
    try:
        snapshot = mchecks.run_all({})
        return {"ok": True, "degraded": True,
                "summary": "snapshot truncated for chat context",
                "snapshot_keys": sorted(snapshot.keys()) if isinstance(snapshot, dict) else None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# --- Tool registry (OpenAI-style schema + executor map) --------------------

def _tool_schema(name: str, description: str,
                 parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def get_tool_specs() -> list[dict[str, Any]]:
    """OpenAI-style tool specs the LLM can choose from."""
    return [
        _tool_schema(
            "list_fs_checks",
            "List all available FreeSWITCH health checks (no execution).",
            {"type": "object", "properties": {}, "required": []},
        ),
        _tool_schema(
            "get_fs_check",
            "Run a single FreeSWITCH check by id (e.g. 'check_fs01_process_running').",
            {"type": "object",
             "properties": {"check_id": {"type": "string",
                                         "description": "Exact check function name"}},
             "required": ["check_id"]},
        ),
        _tool_schema(
            "run_fs_tier",
            "Run a tier (1..4) of FreeSWITCH checks.",
            {"type": "object",
             "properties": {"tier": {"type": "integer", "minimum": 1, "maximum": 4}},
             "required": ["tier"]},
        ),
        _tool_schema(
            "run_full_pipeline",
            "Run the full sweeper pipeline (heavy — gated by ENABLE_HEAVY_TOOLS).",
            {"type": "object", "properties": {}, "required": []},
        ),
    ]


def execute_tool(name: str, arguments: dict[str, Any] | str) -> dict[str, Any]:
    """Dispatch a tool call to its executor. Returns a dict result."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except (ValueError, AttributeError):
            arguments = {}
    args = arguments or {}
    started = time.monotonic()
    try:
        if name == "list_fs_checks":
            out = list_fs_checks()
        elif name == "get_fs_check":
            out = get_fs_check(str(args.get("check_id", "")))
        elif name == "run_fs_tier":
            tier = args.get("tier")
            try:
                tier_int = int(tier)
            except (TypeError, ValueError):
                return {"ok": False, "error": f"invalid tier: {tier!r}"}
            out = run_fs_tier(tier_int)
        elif name == "run_full_pipeline":
            out = run_full_pipeline()
        else:
            out = {"ok": False, "error": f"unknown tool: {name}"}
    except Exception as exc:
        out = {"ok": False, "error": f"executor crashed: {exc}"}
    out["_elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return out


__all__ = [
    "execute_tool",
    "get_fs_check",
    "get_tool_specs",
    "list_fs_checks",
    "run_fs_tier",
    "run_full_pipeline",
]