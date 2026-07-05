"""Validate sweeper rules YAML against an expected schema.

The validator catches common mistakes before the sweeper starts running:
  - Missing required sections (cpu/memory/disk/etc.)
  - Wrong types (string instead of number)
  - warn >= crit (which would never trigger crit)
  - Negative thresholds (impossible)
  - Unknown sections (typo detection)

Returns a list of validation errors (empty = OK). Does NOT raise —
the sweeper should be able to run with defaults if validation fails.
"""
from __future__ import annotations

from typing import Any


# Spec: section -> {key: (type, required)}
SCHEMA: dict[str, dict[str, tuple[type, bool]]] = {
    "cpu": {
        "load_avg_5min_warn": (float, False),
        "load_avg_5min_crit": (float, False),
        "iowait_percent_warn": (float, False),
        "steal_percent_warn": (float, False),
    },
    "memory": {
        "used_percent_warn": (float, False),
        "used_percent_crit": (float, False),
        "swap_used_percent_warn": (float, False),
    },
    "disk": {
        "used_percent_warn": (float, False),
        "used_percent_crit": (float, False),
        "inode_used_percent_warn": (float, False),
        "read_only_mounts": (list, False),
    },
    "network": {
        "dropped_packets_warn": (int, False),
        "tcp_retransmit_percent_warn": (float, False),
        "connections_close_wait_warn": (int, False),
    },
    "services": {
        "critical_list": (list, False),
        "failed_units_window_min": (int, False),
    },
    "logs": {
        "error_rate_per_min_warn": (float, False),
        "oom_events_window_min": (int, False),
        "error_window_min": (int, False),
    },
    "processes": {
        "zombie_count_warn": (int, False),
        "stuck_proc_minutes_warn": (int, False),
    },
    "security": {
        "failed_ssh_per_min_warn": (float, False),
        "sudo_failures_per_hour_warn": (int, False),
    },
    "uptime": {
        "short_uptime_warn_seconds": (int, False),
        "short_uptime_crit_seconds": (int, False),
    },
    # Sections added after the original 9 monitors. They were absent here, so
    # loading the full rules/default.yaml logged a spurious "unknown section"
    # warning for each on every run. Keep in sync with rules/default.yaml.
    "http": {
        "slow_response_ms": (int, False),
        "endpoints": (list, False),
    },
    "ssl": {
        "warn_days": (int, False),
        "crit_days": (int, False),
        "hosts": (list, False),
    },
    "smart": {
        "reallocated_warn": (int, False),
        "reallocated_crit": (int, False),
        "temp_warn_c": (int, False),
        "devices": (list, False),
    },
    "kernel": {
        "window_minutes": (int, False),
    },
    "iostat": {
        "await_warn_ms": (float, False),
        "await_crit_ms": (float, False),
        "util_warn_percent": (float, False),
        "util_crit_percent": (float, False),
    },
    "process_tracker": {
        "window_minutes": (int, False),
        "top_n": (int, False),
        "cpu_warn_percent": (float, False),
        "cpu_crit_percent": (float, False),
        "restart_warn": (int, False),
        "restart_crit": (int, False),
    },
    "fd_check": {
        "warn_percent": (float, False),
        "crit_percent": (float, False),
        "top_n": (int, False),
    },
    "aide": {
        "critical_paths": (list, False),
    },
    "storage": {
        "enabled": (bool, False),
        "retention_days": (int, False),
    },
    "predict": {
        "thresholds": (dict, False),
    },
    "security_baseline": {
        "expected_ssh_keys": (dict, False),
        "expected_suid": (list, False),
        "allowed_ports": (list, False),
    },
}


# Sections that have both warn and crit — warn must be < crit
WARN_CRIT_PAIRS: list[tuple[str, str, str]] = [
    ("cpu", "load_avg_5min_warn", "load_avg_5min_crit"),
    ("memory", "used_percent_warn", "used_percent_crit"),
    ("disk", "used_percent_warn", "used_percent_crit"),
    ("iostat", "await_warn_ms", "await_crit_ms"),
    ("iostat", "util_warn_percent", "util_crit_percent"),
    ("fd_check", "warn_percent", "crit_percent"),
    ("process_tracker", "cpu_warn_percent", "cpu_crit_percent"),
    ("process_tracker", "restart_warn", "restart_crit"),
    ("smart", "reallocated_warn", "reallocated_crit"),
    # NOTE: ssl uses warn_days > crit_days (more days = earlier warning), so it
    # is intentionally NOT a warn<crit pair.
]


def validate(rules: dict[str, Any]) -> list[str]:
    """Return a list of human-readable error strings. Empty list = OK."""
    errors: list[str] = []
    if not isinstance(rules, dict):
        return [f"rules must be a dict, got {type(rules).__name__}"]

    # 1. Unknown sections (typos)
    for section in rules:
        if section not in SCHEMA:
            errors.append(
                f"unknown section: {section!r} "
                f"(known: {sorted(SCHEMA.keys())})"
            )

    # 2. Per-section checks
    for section, keys in SCHEMA.items():
        section_data = rules.get(section)
        if section_data is None:
            continue  # missing sections are OK — defaults are merged
        if not isinstance(section_data, dict):
            errors.append(
                f"{section}: must be a dict, got {type(section_data).__name__}"
            )
            continue

        for key, (expected_type, required) in keys.items():
            if key not in section_data:
                if required:
                    errors.append(f"{section}.{key}: required but missing")
                continue
            value = section_data[key]
            if not isinstance(value, expected_type):
                # int is acceptable where float is expected (e.g. 80 instead of 80.0)
                if expected_type is float and isinstance(value, int):
                    continue
                errors.append(
                    f"{section}.{key}: expected {expected_type.__name__}, "
                    f"got {type(value).__name__}"
                )

        # Check for unknown keys within a section
        for key in section_data:
            if key not in keys:
                errors.append(
                    f"{section}.{key}: unknown key (typo?) "
                    f"(known: {sorted(keys.keys())})"
                )

    # 3. warn < crit invariants
    for section, warn_key, crit_key in WARN_CRIT_PAIRS:
        section_data = rules.get(section, {})
        if not isinstance(section_data, dict):
            continue
        warn = section_data.get(warn_key)
        crit = section_data.get(crit_key)
        if warn is None or crit is None:
            continue
        if not isinstance(warn, (int, float)) or not isinstance(crit, (int, float)):
            continue
        if warn >= crit:
            errors.append(
                f"{section}: {warn_key}={warn} must be less than "
                f"{crit_key}={crit} (warn must fire before crit)"
            )

    # 4. Negative thresholds
    for section, keys in SCHEMA.items():
        section_data = rules.get(section, {})
        if not isinstance(section_data, dict):
            continue
        for key in keys:
            value = section_data.get(key)
            if isinstance(value, (int, float)) and value < 0:
                errors.append(f"{section}.{key}: threshold cannot be negative ({value})")

    return errors


def is_valid(rules: dict[str, Any]) -> bool:
    """True if rules pass all validation checks."""
    return len(validate(rules)) == 0
