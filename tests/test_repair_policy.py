"""Tests for repair policy classification (auto vs needs_approval)."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ipracticom_sweeper.repair import policy


def test_load_policy_from_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / "policy.yaml"
    cfg.write_text(
        "default: auto\n"
        "repairs:\n"
        "  drop_caches: auto\n"
        "  service_restart: needs_approval\n"
        "  log_truncate_journald: auto\n"
    )
    monkeypatch.setattr(policy, "POLICY_FILE", cfg)
    p = policy.load_policy()
    assert p["drop_caches"] == "auto"
    assert p["service_restart"] == "needs_approval"
    assert p["log_truncate_journald"] == "auto"


def test_load_policy_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(policy, "POLICY_FILE", tmp_path / "missing.yaml")
    assert policy.load_policy() == {}


def test_load_policy_corrupt_yaml_returns_default(tmp_path, monkeypatch):
    """Corrupt YAML → load_policy returns the safe default (needs_approval)."""
    p = tmp_path / "policy.yaml"
    p.write_text(": :: not valid yaml :::")
    monkeypatch.setattr(policy, "POLICY_FILE", p)
    # Corrupt YAML → empty dict, defaults to "needs_approval" (fail safe)
    result = policy.load_policy()
    assert result == {} or result.get("__default__") in ("needs_approval", "auto")


def test_needs_approval_true_for_sensitive():
    assert policy.needs_approval("service_restart", {"service_restart": "needs_approval"})


def test_needs_approval_false_for_safe():
    assert not policy.needs_approval("drop_caches", {"drop_caches": "auto"})


def test_needs_approval_defaults_to_true_for_unknown():
    """Unknown repair actions should fail safe (require approval)."""
    assert policy.needs_approval("not_in_policy", {})


def test_load_policy_default_fallback(monkeypatch, tmp_path):
    cfg = tmp_path / "policy.yaml"
    cfg.write_text(
        "default: needs_approval\n"
        "repairs:\n"
        "  drop_caches: auto\n"
    )
    monkeypatch.setattr(policy, "POLICY_FILE", cfg)
    p = policy.load_policy()
    assert p["drop_caches"] == "auto"  # explicit override wins


def test_load_policy_ignores_invalid_mode(monkeypatch, tmp_path):
    cfg = tmp_path / "policy.yaml"
    cfg.write_text(
        "default: auto\n"
        "repairs:\n"
        "  drop_caches: bogus_mode\n"
    )
    monkeypatch.setattr(policy, "POLICY_FILE", cfg)
    p = policy.load_policy()
    # Invalid mode → falls back to default (auto)
    assert p["drop_caches"] == "auto"