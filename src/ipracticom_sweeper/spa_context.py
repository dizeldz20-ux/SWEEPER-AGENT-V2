"""SPA view-model shaping — turns a raw pipeline snapshot into a compact,
template-ready context shared by both dashboard SPA variants (A + B).

Pure functions, no Flask, no I/O — fully unit-testable. The dashboard route
calls :func:`shape_spa_context` with the same snapshot dict that ``/api/snapshot``
returns, so both variants render REAL data (no MOCK_* fixtures).
"""

from __future__ import annotations

from typing import Any

# Hebrew labels (mirror dashboard.py constants, kept local to avoid import cycle)
_DEFCON_HE = {
    "green": "ירוק",
    "blue": "כחול",
    "yellow": "צהוב",
    "orange": "כתום",
    "red": "אדום",
}
_STATUS_HE = {
    "ok": "תקין",
    "warn": "אזהרה",
    "crit": "קריטי",
    "critical": "קריטי",
    "unknown": "לא ידוע",
}
# Rank used to sort modules worst-first and to bucket counts.
_STATUS_RANK = {"crit": 0, "critical": 0, "warn": 1, "unknown": 2, "ok": 3}


def _status_he(status: str) -> str:
    return _STATUS_HE.get((status or "").lower(), status or "")


def _defcon_he(label: str) -> str:
    return _DEFCON_HE.get((label or "").lower(), label or "")


def _norm_status(status: str) -> str:
    s = (status or "unknown").lower()
    if s == "critical":
        return "crit"
    return s


def shape_modules(diagnosis: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten diagnosis.modules into a worst-first sorted list of view rows."""
    modules = (diagnosis or {}).get("modules") or {}
    rows: list[dict[str, Any]] = []
    for name, body in modules.items():
        status = _norm_status((body or {}).get("status", "unknown"))
        rows.append(
            {
                "name": name,
                "status": status,
                "status_he": _status_he(status),
            }
        )
    rows.sort(key=lambda r: (_STATUS_RANK.get(r["status"], 9), r["name"]))
    return rows


def shape_problems(diagnosis: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a template-friendly problem list (module, severity, detail)."""
    problems = (diagnosis or {}).get("problems") or []
    out: list[dict[str, Any]] = []
    for p in problems:
        if not isinstance(p, dict):
            continue
        sev = _norm_status(p.get("severity", "unknown"))
        out.append(
            {
                "module": p.get("module", "?"),
                "kind": p.get("kind", ""),
                "severity": sev,
                "severity_he": _status_he(sev),
                "detail": p.get("detail", ""),
            }
        )
    out.sort(key=lambda r: _STATUS_RANK.get(r["severity"], 9))
    return out


def _count_pbx(diagnosis: dict[str, Any]) -> int:
    """Best-effort PBX host count via FreeSWITCH hostname heuristic."""
    modules = (diagnosis or {}).get("modules") or {}
    fs = modules.get("freeswitch") or {}
    hosts = (fs.get("values") or {}).get("hosts") if isinstance(fs, dict) else None
    if isinstance(hosts, (list, dict)):
        return len(hosts)
    return 0


def shape_spa_context(result: dict[str, Any] | None) -> dict[str, Any]:
    """Build the shared SPA view model from a raw snapshot.

    Safe on ``None`` / empty / partial snapshots — always returns a fully
    populated dict so templates never hit ``Undefined``.
    """
    result = result or {}
    diagnosis = result.get("diagnosis") or {}

    modules = shape_modules(diagnosis)
    problems = shape_problems(diagnosis)

    counts = {"crit": 0, "warn": 0, "ok": 0}
    for m in modules:
        if m["status"] in ("crit", "critical"):
            counts["crit"] += 1
        elif m["status"] == "warn":
            counts["warn"] += 1
        elif m["status"] == "ok":
            counts["ok"] += 1

    defcon_label = result.get("defcon_label") or diagnosis.get("defcon_label") or ""

    return {
        "defcon": result.get("defcon", diagnosis.get("defcon")),
        "defcon_label": defcon_label,
        "defcon_label_he": _defcon_he(defcon_label),
        "modules": modules,
        "total_modules": len(modules),
        "counts": counts,
        "problems": problems,
        "problems_found": result.get("problems_found", diagnosis.get("problem_count", len(problems))),
        "pbx_count": _count_pbx(diagnosis),
        "duration_ms": result.get("duration_ms", 0),
        "repairs_attempted": result.get("repairs_attempted", 0),
        "repairs_succeeded": result.get("repairs_succeeded", 0),
        "repairs_failed": result.get("repairs_failed", 0),
        "needs_human": result.get("needs_human", 0),
        "summary": diagnosis.get("summary", ""),
        "server": result.get("server", ""),
        "started_at": result.get("started_at", ""),
        "finished_at": result.get("finished_at", ""),
        "has_data": bool(result),
    }
