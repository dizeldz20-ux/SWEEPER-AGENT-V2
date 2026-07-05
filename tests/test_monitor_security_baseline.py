"""Tests for security baseline collector (SSH config + SUID + open ports)."""
from __future__ import annotations
from ipracticom_sweeper.monitor.security_baseline import (
    parse_sshd_config,
)


SAMPLE_SSHD_CONFIG = """\
# SSH server configuration
Port 22
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
PermitEmptyPasswords no
X11Forwarding yes
"""


def test_parse_sshd_config_extracts_settings():
    """Parser extracts key-value pairs from sshd_config."""
    snap = parse_sshd_config(SAMPLE_SSHD_CONFIG)
    assert snap.get("PermitRootLogin") == "prohibit-password"
    assert snap.get("PasswordAuthentication") == "no"
    assert snap.get("PubkeyAuthentication") == "yes"
    assert snap.get("PermitEmptyPasswords") == "no"
    assert snap.get("X11Forwarding") == "yes"
    assert snap.get("Port") == "22"


def test_parse_sshd_config_ignores_comments():
    """Comment lines are skipped."""
    snap = parse_sshd_config("# this is a comment\nPermitRootLogin yes")
    assert "this is a comment" not in snap
    assert snap.get("PermitRootLogin") == "yes"


def test_parse_sshd_config_handles_empty():
    """Empty input = empty dict."""
    snap = parse_sshd_config("")
    assert snap == {}


def test_parse_sshd_config_handles_no_value():
    """Lines without = (some keywords like 'PasswordAuthentication yes') still parse."""
    snap = parse_sshd_config("PasswordAuthentication yes\n")
    assert snap.get("PasswordAuthentication") == "yes"
