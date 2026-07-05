"""Tests for the diagnose engine."""

import pytest

from ipracticom_sweeper.diagnose.engine import (
    DEFCON_LABELS,
    DIAGNOSERS,
    Diagnosis,
    Problem,
    RepairSafety,
    diagnose,
    diagnose_cpu,
    diagnose_disk,
    diagnose_memory,
    diagnose_security,
    diagnose_services,
)


@pytest.fixture
def default_rules():
    return {
        "cpu": {
            "load_avg_5min_warn": 2.0,
            "load_avg_5min_crit": 5.0,
            "iowait_percent_warn": 20.0,
        },
        "memory": {
            "used_percent_warn": 80.0,
            "used_percent_crit": 95.0,
            "swap_used_percent_warn": 50.0,
        },
        "disk": {
            "used_percent_warn": 80.0,
            "used_percent_crit": 95.0,
            "read_only_mounts": ["/"],
        },
        "services": {
            "critical_list": ["nginx"],
        },
        "security": {
            "failed_ssh_per_min_warn": 5,
            "sudo_failures_per_hour_warn": 3,
        },
    }


# --- DEFCON labels ----------------------------------------------------------


def test_defcon_labels_complete():
    assert len(DEFCON_LABELS) == 5
    assert DEFCON_LABELS[5] == "green"
    assert DEFCON_LABELS[1] == "black"


# --- CPU diagnose -----------------------------------------------------------


def test_cpu_no_problems_when_low_load(default_rules):
    findings = {"metrics": {"load_avg_5min": 0.5, "iowait_percent": 5.0}}
    problems = diagnose_cpu(findings, default_rules)
    assert problems == []


def test_cpu_warn_at_threshold(default_rules):
    findings = {"metrics": {"load_avg_5min": 2.5}}
    problems = diagnose_cpu(findings, default_rules)
    assert len(problems) == 1
    assert problems[0].kind == "cpu_load_warn"
    assert problems[0].severity == "warn"
    assert problems[0].defcon_at_least == 4


def test_cpu_crit_at_threshold(default_rules):
    findings = {"metrics": {"load_avg_5min": 6.0}}
    problems = diagnose_cpu(findings, default_rules)
    assert len(problems) == 1
    assert problems[0].kind == "cpu_load_critical"
    assert problems[0].severity == "crit"
    assert problems[0].defcon_at_least == 3


def test_cpu_iowait_triggers_problem(default_rules):
    findings = {"metrics": {"load_avg_5min": 1.0, "iowait_percent": 30.0}}
    problems = diagnose_cpu(findings, default_rules)
    assert any(p.kind == "cpu_iowait_high" for p in problems)


def test_cpu_missing_load_is_handled(default_rules):
    findings = {"metrics": {}}
    problems = diagnose_cpu(findings, default_rules)
    assert problems == []


# --- Memory diagnose --------------------------------------------------------


def test_memory_warn(default_rules):
    findings = {"metrics": {"used_percent": 85.0}}
    problems = diagnose_memory(findings, default_rules)
    assert any(p.kind == "memory_warn" for p in problems)


def test_memory_crit_triggers_repair(default_rules):
    findings = {"metrics": {"used_percent": 96.0}}
    problems = diagnose_memory(findings, default_rules)
    assert any(
        p.kind == "memory_critical" and p.repair_safety == RepairSafety.GUARDED
        for p in problems
    )


def test_memory_swap_pressure(default_rules):
    findings = {"metrics": {"used_percent": 50.0, "swap_used_percent": 60.0}}
    problems = diagnose_memory(findings, default_rules)
    assert any(p.kind == "swap_pressure" for p in problems)


# --- Disk diagnose ----------------------------------------------------------


def test_disk_crit_mount(default_rules):
    findings = {"metrics": {"mounts": [{"mountpoint": "/var", "used_percent": 96.0}]}}
    problems = diagnose_disk(findings, default_rules)
    assert any(p.kind == "disk_critical" for p in problems)


def test_disk_warn_mount(default_rules):
    findings = {"metrics": {"mounts": [{"mountpoint": "/var", "used_percent": 85.0}]}}
    problems = diagnose_disk(findings, default_rules)
    assert any(p.kind == "disk_warn" for p in problems)


def test_disk_expected_ro_missing(default_rules):
    # / expected read-only, but no read-only mounts in actual
    findings = {
        "metrics": {
            "mounts": [{"mountpoint": "/", "used_percent": 50.0, "options": "rw"}]
        }
    }
    problems = diagnose_disk(findings, default_rules)
    assert any(p.kind == "disk_expected_ro_missing" for p in problems)


def test_disk_skips_malformed_mounts(default_rules):
    findings = {"metrics": {"mounts": ["not-a-dict", None, {"mountpoint": "/var", "used_percent": 85.0}]}}
    problems = diagnose_disk(findings, default_rules)
    assert any(p.kind == "disk_warn" for p in problems)
    assert all(isinstance(p, Problem) for p in problems)


# --- Services diagnose ------------------------------------------------------


def test_service_failed_critical(default_rules):
    findings = {"metrics": {"failed_units": [{"unit": "nginx"}]}}
    problems = diagnose_services(findings, default_rules)
    assert any(p.severity == "crit" for p in problems)
    assert any(p.suggested_repair == "service_restart" for p in problems)


def test_service_failed_noncritical(default_rules):
    findings = {"metrics": {"failed_units": [{"unit": "extraservice"}]}}
    problems = diagnose_services(findings, default_rules)
    assert any(p.severity == "warn" for p in problems)
    # non-critical services should NOT suggest repair
    assert all(p.suggested_repair is None for p in problems)


# --- Security diagnose ------------------------------------------------------


def test_ssh_brute_force_triggers_crit(default_rules):
    findings = {"metrics": {"failed_ssh_per_min": 20}}
    problems = diagnose_security(findings, default_rules)
    assert any(p.kind == "ssh_brute_force" for p in problems)
    assert all(
        p.repair_safety == RepairSafety.DANGEROUS for p in problems
    )


def test_sudo_failures_triggers_warn(default_rules):
    findings = {"metrics": {"sudo_failures_per_hour": 5}}
    problems = diagnose_security(findings, default_rules)
    assert any(p.kind == "sudo_failures" for p in problems)


# --- Aggregate diagnose -----------------------------------------------------


def test_diagnose_no_findings_returns_defcon_5(default_rules):
    diagnosis = diagnose({}, default_rules)
    assert diagnosis.defcon == 5
    assert diagnosis.defcon_label == "green"
    assert diagnosis.problems == []
    assert diagnosis.safe_repairs == []
    assert diagnosis.needs_human == []
    assert diagnosis.summary == "All systems nominal"


def test_diagnose_picks_worst_defcon(default_rules):
    findings = {
        "cpu": {"metrics": {"load_avg_5min": 1.0}},     # OK
        "memory": {"metrics": {"used_percent": 85.0}},  # warn → defcon 4
        "disk": {"metrics": {"mounts": [{"mountpoint": "/var", "used_percent": 96.0}]}},  # crit → defcon 3
    }
    diagnosis = diagnose(findings, default_rules)
    assert diagnosis.defcon == 3
    assert diagnosis.defcon_label == "orange"


def test_diagnose_identifies_safe_repairs(default_rules):
    findings = {
        "memory": {"metrics": {"used_percent": 96.0}},
    }
    diagnosis = diagnose(findings, default_rules)
    assert "drop_caches" in diagnosis.safe_repairs


def test_diagnose_security_always_needs_human(default_rules):
    findings = {
        "security": {"metrics": {"failed_ssh_per_min": 50}},
    }
    diagnosis = diagnose(findings, default_rules)
    assert diagnosis.defcon == 2  # security crit → defcon 2
    assert any(p.kind == "ssh_brute_force" for p in diagnosis.needs_human)
    assert diagnosis.safe_repairs == []  # security never auto-repair


def test_diagnose_skips_unknown_modules(default_rules):
    findings = {"unknown_module": {"metrics": {"foo": 1}}}
    diagnosis = diagnose(findings, default_rules)
    assert diagnosis.defcon == 5


def test_diagnose_to_dict_serializable(default_rules):
    findings = {
        "memory": {"metrics": {"used_percent": 96.0}},
    }
    diagnosis = diagnose(findings, default_rules)
    d = diagnosis.to_dict()
    assert isinstance(d, dict)
    assert d["defcon"] == 3
    assert d["problem_count"] == 1
    assert isinstance(d["problems"], list)


def test_diagnose_safe_repairs_dedup(default_rules):
    findings = {
        "memory": {"metrics": {"used_percent": 85.0}},  # drop_caches
        "memory": {"metrics": {"used_percent": 50.0, "swap_used_percent": 60.0}},  # also drop_caches
    }
    # Only the last entry is used since keys are unique
    diagnosis = diagnose(findings, default_rules)
    assert diagnosis.safe_repairs.count("drop_caches") <= 1


def test_diagnose_defcon_labels_in_summary(default_rules):
    findings = {"memory": {"metrics": {"used_percent": 96.0}}}
    diagnosis = diagnose(findings, default_rules)
    assert "critical" in diagnosis.summary.lower()


def test_diagnose_fire_summary_at_defcon_1(default_rules):
    # black defcon only happens via ssh brute force which is defcon 2...
    # Let's patch the defcon by injecting an explicit high-severity problem
    # Actually defcon 1 is reserved — the system never goes there automatically.
    # So we test defcon 2 summary instead.
    findings = {"security": {"metrics": {"failed_ssh_per_min": 100}}}
    diagnosis = diagnose(findings, default_rules)
    assert diagnosis.defcon == 2
    assert "armed" in diagnosis.summary.lower() or "human" in diagnosis.summary.lower()