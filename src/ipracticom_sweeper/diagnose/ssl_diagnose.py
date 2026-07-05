"""Diagnose SSL cert results → Problem objects.

Defaults (overridable via rules["ssl"]):
    warn_days: 30 (cert expiring within 30 days = warn)
    crit_days: 7 (cert expiring within 7 days = crit)
"""
from __future__ import annotations
from typing import Any

from ipracticom_sweeper.diagnose.engine import Problem, RepairSafety


def diagnose_ssl(findings: dict, rules: dict) -> list[Problem]:
    ssl_rules = rules.get("ssl", {})
    warn_days = ssl_rules.get("warn_days", 30)
    crit_days = ssl_rules.get("crit_days", 7)

    metrics = findings.get("metrics", {})
    certs = metrics.get("certificates", [])

    problems: list[Problem] = []
    for cert in certs:
        host = cert.get("host", "unknown")
        error = cert.get("error")
        days = cert.get("days_remaining")

        # Connection error → CRIT
        if error:
            problems.append(Problem(
                module="ssl",
                kind="ssl_check_failed",
                severity="crit",
                detail=f"SSL check for {host} failed: {error}",
                metrics={"host": host, "error": error},
                suggested_repair="notify_human",
                repair_safety=RepairSafety.NEVER,
            ))
            continue

        if days is None:
            continue

        # Already expired (negative) or expiring within crit_days
        if days < crit_days:
            sev = "crit"
            kind = "ssl_cert_expired" if days < 0 else "ssl_cert_expiring_crit"
            problems.append(Problem(
                module="ssl",
                kind=kind,
                severity=sev,
                detail=f"SSL cert for {host} expires in {days} days",
                metrics={"host": host, "days_remaining": days, "crit_days": crit_days},
                suggested_repair="notify_human",
                repair_safety=RepairSafety.NEVER,
            ))
        # Within warn window
        elif days < warn_days:
            problems.append(Problem(
                module="ssl",
                kind="ssl_cert_expiring_warn",
                severity="warn",
                detail=f"SSL cert for {host} expires in {days} days (warn threshold {warn_days})",
                metrics={"host": host, "days_remaining": days, "warn_days": warn_days},
                suggested_repair="notify_human",
                repair_safety=RepairSafety.NEVER,
            ))

    return problems
