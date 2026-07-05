"""Tests for config validator."""
import pytest
from ipracticom_sweeper.config import validate, is_valid, SCHEMA


def test_valid_default_rules_pass():
    """All defaults are valid — empty overlay should be OK."""
    assert is_valid({}) is True


def test_valid_custom_section():
    rules = {"cpu": {"load_avg_5min_warn": 1.5, "load_avg_5min_crit": 3.0}}
    assert is_valid(rules) is True


def test_unknown_section_detected():
    rules = {"cpu": {}, "weird_typo_section": {}}
    errors = validate(rules)
    assert any("unknown section" in e and "weird_typo_section" in e for e in errors)


def test_unknown_key_in_section_detected():
    rules = {"cpu": {"load_avg_5min_typo_warn": 1.0}}
    errors = validate(rules)
    assert any("cpu.load_avg_5min_typo_warn" in e and "unknown key" in e for e in errors)


def test_wrong_type_string_for_float():
    rules = {"cpu": {"load_avg_5min_warn": "not a number"}}
    errors = validate(rules)
    assert any("cpu.load_avg_5min_warn" in e and "expected float" in e for e in errors)


def test_int_accepted_for_float():
    """80 is fine where 80.0 is expected (Python convention)."""
    rules = {"memory": {"used_percent_warn": 80, "used_percent_crit": 95}}
    assert is_valid(rules) is True


def test_warn_greater_than_crit_detected():
    rules = {"cpu": {"load_avg_5min_warn": 10.0, "load_avg_5min_crit": 5.0}}
    errors = validate(rules)
    assert any("must be less than" in e for e in errors)


def test_warn_equal_to_crit_detected():
    """warn == crit would never trigger crit — this is a config bug."""
    rules = {"cpu": {"load_avg_5min_warn": 5.0, "load_avg_5min_crit": 5.0}}
    errors = validate(rules)
    assert any("must be less than" in e for e in errors)


def test_negative_threshold_detected():
    rules = {"memory": {"used_percent_warn": -10.0}}
    errors = validate(rules)
    assert any("cannot be negative" in e for e in errors)


def test_section_must_be_dict():
    rules = {"cpu": "not a dict"}
    errors = validate(rules)
    assert any("cpu" in e and "must be a dict" in e for e in errors)


def test_rules_must_be_dict():
    errors = validate("not a dict")  # type: ignore
    assert any("must be a dict" in e for e in errors)


def test_missing_section_ok():
    """Missing sections get defaults — that's fine."""
    rules = {"cpu": {"load_avg_5min_warn": 1.0}}
    assert is_valid(rules) is True


def test_full_realistic_config_passes():
    rules = {
        "cpu": {
            "load_avg_5min_warn": 2.0,
            "load_avg_5min_crit": 5.0,
            "iowait_percent_warn": 20.0,
            "steal_percent_warn": 10.0,
        },
        "memory": {
            "used_percent_warn": 80.0,
            "used_percent_crit": 95.0,
            "swap_used_percent_warn": 50.0,
        },
        "disk": {
            "used_percent_warn": 80.0,
            "used_percent_crit": 95.0,
            "inode_used_percent_warn": 80.0,
            "read_only_mounts": ["/", "/boot"],
        },
        "services": {
            "critical_list": ["nginx", "postgresql"],
            "failed_units_window_min": 5,
        },
        "uptime": {
            "short_uptime_warn_seconds": 300,
            "short_uptime_crit_seconds": 60,
        },
    }
    assert is_valid(rules) is True
    assert validate(rules) == []


def test_validate_returns_list_of_strings():
    rules = {"weird_section": {}, "cpu": {"load_avg_5min_warn": -1.0}}
    errors = validate(rules)
    assert isinstance(errors, list)
    for e in errors:
        assert isinstance(e, str)
    assert len(errors) >= 2  # unknown section + negative threshold


def test_schema_lists_all_supported_sections():
    """If a new monitor is added (e.g. uptime), the validator should know about it."""
    assert "uptime" in SCHEMA
    assert "cpu" in SCHEMA
    assert "memory" in SCHEMA
    assert "disk" in SCHEMA
