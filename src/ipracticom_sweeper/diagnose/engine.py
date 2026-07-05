"""Diagnose engine — turns monitor findings into actionable diagnoses.

The diagnose layer is the brain of the sweeper. It takes the raw findings from
the monitor layer and produces a *Diagnosis* object that contains:

  - defcon: the current DEFCON level (1-5)
  - problems: a list of specific problems identified
  - safe_repairs: list of repair actions that are safe to auto-execute
  - needs_human: list of problems that require human intervention
  - summary: human-readable one-liner

Design principles:
  - Deterministic, rules-based — no LLM in the hot path
  - Every action is *recommended* not executed here (that's the repair layer)
  - Idempotent: same findings → same diagnosis
  - Conservative: when in doubt, escalate (defcon+1, or human)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


# --- Repair action classification --------------------------------------------


class RepairSafety(Enum):
    """How safe is it to auto-execute this repair?"""

    SAFE = "safe"                    # reversible, no data loss risk
    GUARDED = "guarded"              # needs pre-snapshot + rollback plan
    DANGEROUS = "dangerous"          # needs human approval
    NEVER = "never"                  # never auto-execute, always alert


# --- Problem types -----------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    """A specific problem identified from monitor findings."""

    module: str               # which monitor module found it
    kind: str                 # machine-readable problem code, e.g. "disk_full"
    severity: str             # "warn" | "crit"
    detail: str               # human-readable explanation
    metrics: dict[str, Any]   # the numbers that triggered this
    suggested_repair: str | None = None       # repair class name
    repair_safety: RepairSafety = RepairSafety.NEVER
    defcon_at_least: int = 3  # minimum defcon level this problem implies


# --- Diagnosis result --------------------------------------------------------


@dataclass
class Diagnosis:
    """The diagnose layer's verdict on the current server state."""

    defcon: int
    defcon_label: str
    summary: str
    problems: list[Problem] = field(default_factory=list)
    safe_repairs: list[str] = field(default_factory=list)  # repair class names
    needs_human: list[Problem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "defcon": self.defcon,
            "defcon_label": self.defcon_label,
            "summary": self.summary,
            "problem_count": len(self.problems),
            "safe_repairs": self.safe_repairs,
            "needs_human_count": len(self.needs_human),
            "problems": [
                {
                    "module": p.module,
                    "kind": p.kind,
                    "severity": p.severity,
                    "detail": p.detail,
                    "metrics": p.metrics,
                    "suggested_repair": p.suggested_repair,
                    "repair_safety": p.repair_safety.value,
                    "defcon_at_least": p.defcon_at_least,
                }
                for p in self.problems
            ],
        }


# --- DEFCON label helper -----------------------------------------------------

DEFCON_LABELS = {
    5: "green",
    4: "yellow",
    3: "orange",
    2: "red",
    1: "black",
}


# --- Individual diagnosers ---------------------------------------------------


def diagnose_cpu(findings: dict, rules: dict) -> list[Problem]:
    problems = []
    metrics = findings.get("metrics", {})
    load5 = metrics.get("load_avg_5min")
    crit = rules.get("cpu", {}).get("load_avg_5min_crit", 5.0)
    warn = rules.get("cpu", {}).get("load_avg_5min_warn", 2.0)

    if load5 is not None and load5 >= crit:
        problems.append(
            Problem(
                module="cpu",
                kind="cpu_load_critical",
                severity="crit",
                detail=f"5-min load average is {load5:.2f} (crit ≥ {crit})",
                metrics={"load_avg_5min": load5, "threshold": crit},
                suggested_repair="top_processes_snapshot",
                repair_safety=RepairSafety.SAFE,
                defcon_at_least=3,
            )
        )
    elif load5 is not None and load5 >= warn:
        problems.append(
            Problem(
                module="cpu",
                kind="cpu_load_warn",
                severity="warn",
                detail=f"5-min load average is {load5:.2f} (warn ≥ {warn})",
                metrics={"load_avg_5min": load5, "threshold": warn},
                suggested_repair="top_processes_snapshot",
                repair_safety=RepairSafety.SAFE,
                defcon_at_least=4,
            )
        )

    iowait = metrics.get("iowait_percent")
    iowait_warn = rules.get("cpu", {}).get("iowait_percent_warn", 20.0)
    if iowait is not None and iowait >= iowait_warn:
        problems.append(
            Problem(
                module="cpu",
                kind="cpu_iowait_high",
                severity="warn",
                detail=f"iowait is {iowait:.1f}% — likely disk-bound",
                metrics={"iowait_percent": iowait, "threshold": iowait_warn},
                suggested_repair=None,  # diagnostic only, no auto-repair
                repair_safety=RepairSafety.NEVER,
                defcon_at_least=4,
            )
        )

    return problems


def diagnose_memory(findings: dict, rules: dict) -> list[Problem]:
    problems = []
    metrics = findings.get("metrics", {})
    used = metrics.get("used_percent")
    crit = rules.get("memory", {}).get("used_percent_crit", 95.0)
    warn = rules.get("memory", {}).get("used_percent_warn", 80.0)

    if used is not None and used >= crit:
        problems.append(
            Problem(
                module="memory",
                kind="memory_critical",
                severity="crit",
                detail=f"Memory at {used:.1f}% (crit ≥ {crit}%)",
                metrics={"used_percent": used, "threshold": crit},
                suggested_repair="drop_caches",
                repair_safety=RepairSafety.GUARDED,
                defcon_at_least=3,
            )
        )
    elif used is not None and used >= warn:
        problems.append(
            Problem(
                module="memory",
                kind="memory_warn",
                severity="warn",
                detail=f"Memory at {used:.1f}% (warn ≥ {warn}%)",
                metrics={"used_percent": used, "threshold": warn},
                suggested_repair="drop_caches",
                repair_safety=RepairSafety.GUARDED,
                defcon_at_least=4,
            )
        )

    swap = metrics.get("swap_used_percent")
    swap_warn = rules.get("memory", {}).get("swap_used_percent_warn", 50.0)
    if swap is not None and swap >= swap_warn:
        problems.append(
            Problem(
                module="memory",
                kind="swap_pressure",
                severity="warn",
                detail=f"Swap at {swap:.1f}% — memory pressure likely",
                metrics={"swap_used_percent": swap, "threshold": swap_warn},
                suggested_repair="drop_caches",
                repair_safety=RepairSafety.GUARDED,
                defcon_at_least=4,
            )
        )

    return problems


def diagnose_disk(findings: dict, rules: dict) -> list[Problem]:
    problems = []
    metrics = findings.get("metrics", {})
    mounts = metrics.get("mounts", [])

    crit = rules.get("disk", {}).get("used_percent_crit", 95.0)
    warn = rules.get("disk", {}).get("used_percent_warn", 80.0)

    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        used = mount.get("used_percent")
        path = mount.get("mountpoint", "?")
        if used is None:
            continue

        if used >= crit:
            problems.append(
                Problem(
                    module="disk",
                    kind="disk_critical",
                    severity="crit",
                    detail=f"Mount {path} is {used:.1f}% full (crit ≥ {crit}%)",
                    metrics={"mountpoint": path, "used_percent": used},
                    suggested_repair="log_truncate_journald",
                    repair_safety=RepairSafety.GUARDED,
                    defcon_at_least=3,
                )
            )
        elif used >= warn:
            problems.append(
                Problem(
                    module="disk",
                    kind="disk_warn",
                    severity="warn",
                    detail=f"Mount {path} is {used:.1f}% full (warn ≥ {warn}%)",
                    metrics={"mountpoint": path, "used_percent": used},
                    suggested_repair=None,
                    repair_safety=RepairSafety.NEVER,
                    defcon_at_least=4,
                )
            )

    # Check read-only mounts expectation
    expected_ro = set(rules.get("disk", {}).get("read_only_mounts", []))
    if expected_ro:
        actual_ro = {
            m.get("mountpoint")
            for m in mounts
            if isinstance(m, dict) and m.get("options", "").startswith("ro")
        }
        missing = expected_ro - actual_ro
        if missing:
            problems.append(
                Problem(
                    module="disk",
                    kind="disk_expected_ro_missing",
                    severity="warn",
                    detail=f"Expected read-only mounts not read-only: {sorted(missing)}",
                    metrics={"expected": sorted(expected_ro), "actual_ro": sorted(actual_ro)},
                    suggested_repair=None,
                    repair_safety=RepairSafety.NEVER,
                    defcon_at_least=4,
                )
            )

    return problems


def diagnose_services(findings: dict, rules: dict) -> list[Problem]:
    problems = []
    metrics = findings.get("metrics", {})
    failed = metrics.get("failed_units", [])
    critical = set(rules.get("services", {}).get("critical_list", []))

    for unit in failed:
        if not isinstance(unit, dict):
            continue
        name = unit.get("unit", "?")
        is_critical = name in critical
        problems.append(
            Problem(
                module="services",
                kind="service_failed",
                severity="crit" if is_critical else "warn",
                detail=f"Service {name} is in failed state"
                + (" (CRITICAL)" if is_critical else ""),
                metrics={"unit": name, "critical": is_critical},
                suggested_repair="service_restart" if is_critical else None,
                repair_safety=RepairSafety.GUARDED if is_critical else RepairSafety.NEVER,
                defcon_at_least=2 if is_critical else 4,
            )
        )
    return problems


def diagnose_security(findings: dict, rules: dict) -> list[Problem]:
    problems = []
    metrics = findings.get("metrics", {})

    failed_ssh = metrics.get("failed_ssh_per_min", 0)
    ssh_warn = rules.get("security", {}).get("failed_ssh_per_min_warn", 5)
    if failed_ssh >= ssh_warn:
        problems.append(
            Problem(
                module="security",
                kind="ssh_brute_force",
                severity="crit",
                detail=f"{failed_ssh} failed SSH attempts/min (warn ≥ {ssh_warn})",
                metrics={"failed_ssh_per_min": failed_ssh, "threshold": ssh_warn},
                suggested_repair=None,  # security actions always need human
                repair_safety=RepairSafety.DANGEROUS,
                defcon_at_least=2,
            )
        )

    sudo_fails = metrics.get("sudo_failures_per_hour", 0)
    sudo_warn = rules.get("security", {}).get("sudo_failures_per_hour_warn", 3)
    if sudo_fails >= sudo_warn:
        problems.append(
            Problem(
                module="security",
                kind="sudo_failures",
                severity="warn",
                detail=f"{sudo_fails} sudo failures/hour (warn ≥ {sudo_warn})",
                metrics={"sudo_failures_per_hour": sudo_fails, "threshold": sudo_warn},
                suggested_repair=None,
                repair_safety=RepairSafety.DANGEROUS,
                defcon_at_least=3,
            )
        )

    return problems


# --- Aggregate diagnose ------------------------------------------------------


# Map monitor module name → diagnoser function
DIAGNOSERS = {
    "cpu": diagnose_cpu,
    "memory": diagnose_memory,
    "disk": diagnose_disk,
    "services": diagnose_services,
    "security": diagnose_security,
}


def diagnose_http_wrapper(findings: dict, rules: dict) -> list[Problem]:
    """Lazy import to avoid circular dependency at module load."""
    from ipracticom_sweeper.diagnose.http_diagnose import diagnose_http
    return diagnose_http(findings, rules)


def diagnose_ssl_wrapper(findings: dict, rules: dict) -> list[Problem]:
    """Lazy import to avoid circular dependency at module load."""
    from ipracticom_sweeper.diagnose.ssl_diagnose import diagnose_ssl
    return diagnose_ssl(findings, rules)


# Register lazy wrappers
DIAGNOSERS["http"] = diagnose_http_wrapper
DIAGNOSERS["ssl"] = diagnose_ssl_wrapper


def diagnose(findings: dict[str, Any], rules: dict[str, Any]) -> Diagnosis:
    """Run all diagnosers and produce a Diagnosis.

    Args:
        findings: the dict of {module_name: findings} from the monitor layer.
        rules: the threshold rules from config.

    Returns:
        A Diagnosis with defcon level, problems, and repair recommendations.
    """
    all_problems: list[Problem] = []

    for module_name, diagnoser in DIAGNOSERS.items():
        module_findings = findings.get(module_name, {})
        if not module_findings:
            logger.debug("diagnose_skip_no_findings", module=module_name)
            continue
        all_problems.extend(diagnoser(module_findings, rules))

    # DEFCON = max of (minimum defcon implied by each problem)
    if all_problems:
        defcon = min(p.defcon_at_least for p in all_problems)
    else:
        defcon = 5  # all good

    # Safe repairs = unique repair actions marked SAFE or GUARDED
    safe_repairs: list[str] = []
    needs_human: list[Problem] = []
    for p in all_problems:
        if p.repair_safety in (RepairSafety.SAFE, RepairSafety.GUARDED) and p.suggested_repair:
            if p.suggested_repair not in safe_repairs:
                safe_repairs.append(p.suggested_repair)
        if p.repair_safety == RepairSafety.DANGEROUS or p.repair_safety == RepairSafety.NEVER:
            if p.kind not in [n.kind for n in needs_human]:
                needs_human.append(p)

    # Summary
    if defcon == 5:
        summary = "All systems nominal"
    elif defcon == 4:
        summary = f"{len(all_problems)} warning(s) detected"
    elif defcon == 3:
        summary = f"{len(all_problems)} critical issue(s) — auto-repair eligible"
    elif defcon == 2:
        summary = f"{len(all_problems)} critical issue(s) — auto-repair armed"
    else:
        summary = f"🚨 {len(all_problems)} fire(s) — human intervention required"

    diagnosis = Diagnosis(
        defcon=defcon,
        defcon_label=DEFCON_LABELS.get(defcon, "unknown"),
        summary=summary,
        problems=all_problems,
        safe_repairs=safe_repairs,
        needs_human=needs_human,
    )

    logger.info(
        "diagnose_complete",
        defcon=defcon,
        defcon_label=diagnosis.defcon_label,
        problems=len(all_problems),
        safe_repairs=len(safe_repairs),
        needs_human=len(needs_human),
    )
    return diagnosis