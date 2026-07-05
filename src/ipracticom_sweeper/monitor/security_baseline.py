"""Security baseline collector.

Reads sshd_config, scans for SUID binaries, and enumerates listening
ports. Compares against rules.security_baseline.expected_* to detect
drift. Used to spot unauthorized changes (e.g. root login enabled
after a config push, new SUID binary installed by attacker).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os
import re
import subprocess

from .._log import log_suppressed


SSH_PATHS = ["/etc/ssh/sshd_config", "/private/etc/ssh/sshd_config"]
SUID_DIRS = ["/usr/bin", "/usr/sbin", "/usr/local/bin", "/usr/local/sbin", "/bin", "/sbin"]


def parse_sshd_config(content: str) -> dict[str, str]:
    """Parse sshd_config into a dict of {key: value}.

    Ignores comments (lines starting with #) and blank lines.
    Handles both 'Key Value' and 'Key=Value' formats.
    """
    config: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Match "Key Value" or "Key=Value"
        m = re.match(r"^(\S+)[\s=]+(.+)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            config[key] = value
    return config


def collect_sshd_config() -> dict[str, Any]:
    """Read sshd_config from the system, or return a snapshot with available=False."""
    for path in SSH_PATHS:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return {
                        "available": True,
                        "path": path,
                        "config": parse_sshd_config(f.read()),
                    }
            except (OSError, PermissionError) as e:
                log_suppressed("security_baseline_sshd_read", e)
                continue
    return {"available": False, "path": None, "config": {}}


def scan_suid_binaries() -> list[dict[str, str]]:
    """List SUID binaries in standard system directories.

    Returns a list of {path, owner, group} dicts. This is a baseline
    snapshot for drift detection (compared to rules.expected_suid).
    """
    suid_files: list[dict[str, str]] = []
    for d in SUID_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            for entry in os.listdir(d):
                full = os.path.join(d, entry)
                try:
                    st = os.stat(full)
                    # SUID = mode & 0o4000
                    if st.st_mode & 0o4000:
                        import pwd, grp
                        try:
                            owner = pwd.getpwuid(st.st_uid).pw_name
                        except KeyError:
                            owner = str(st.st_uid)
                        try:
                            group = grp.getgrgid(st.st_gid).gr_name
                        except KeyError:
                            group = str(st.st_gid)
                        suid_files.append({
                            "path": full,
                            "owner": owner,
                            "group": group,
                        })
                except (OSError, PermissionError) as e:
                    log_suppressed("security_baseline_suid_stat", e)
                    continue
        except (OSError, PermissionError) as e:
            log_suppressed("security_baseline_suid_walk", e)
            continue
    return suid_files


def collect_listening_ports() -> list[dict[str, Any]]:
    """List TCP listening ports via `ss -tlnp` (or `netstat` as fallback)."""
    for cmd in [["ss", "-tlnH"], ["netstat", "-tlnH"]]:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0 and proc.stdout:
                ports = []
                for line in proc.stdout.splitlines():
                    # ss format: LISTEN 0 128 *:22 *:* users:(("sshd",pid=...))
                    # netstat format: tcp 0 0 0.0.0.0:22 0.0.0.0:* LISTEN
                    m = re.search(r":(\d+)\s", line)
                    if m:
                        ports.append({
                            "port": int(m.group(1)),
                            "raw": line.strip()[:200],
                        })
                return ports
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            log_suppressed("security_baseline_listening_ports", e)
            continue
    return []


def evaluate(values: dict, rules: dict) -> str:
    """Return 'ok' | 'warn' | 'crit'.

    crit: PermitRootLogin=yes, PasswordAuthentication=yes, SUID drift detected
    warn: any unexpected listening port, X11Forwarding=yes
    """
    sec_rules = rules.get("security_baseline", {})
    crit = False
    warn = False

    ssh = values.get("sshd_config", {})
    if ssh.get("available"):
        cfg = ssh.get("config", {})
        if cfg.get("PermitRootLogin", "").lower() in ("yes", "true"):
            crit = True
        if cfg.get("PasswordAuthentication", "").lower() in ("yes", "true"):
            crit = True
        if cfg.get("X11Forwarding", "").lower() in ("yes", "true"):
            warn = True
        # Check expected config keys
        for key, expected in sec_rules.get("expected_ssh_keys", {}).items():
            if cfg.get(key, "").lower() != expected.lower():
                crit = True

    # SUID drift — only meaningful once a baseline is configured. An empty
    # expected_suid means "no baseline captured yet": every standard SUID
    # binary (sudo, passwd, su, mount, ...) would otherwise register as drift
    # and pin the module to crit on every run (alert fatigue → silent-OK by
    # habituation). Skip the check until the operator seeds expected_suid.
    expected = set(sec_rules.get("expected_suid", []))
    if expected:
        suid_now = set(s["path"] for s in values.get("suid_binaries", []))
        new_suid = suid_now - expected
        missing_suid = expected - suid_now
        if new_suid or missing_suid:
            crit = True  # SUID changes are always security-critical

    # Open ports drift
    allowed_ports = set(sec_rules.get("allowed_ports", []))
    actual_ports = set(p["port"] for p in values.get("listening_ports", []))
    unexpected = actual_ports - allowed_ports
    if unexpected:
        warn = True  # unexpected port = warn, not crit (could be new legitimate service)

    if crit:
        return "crit"
    if warn:
        return "warn"
    return "ok"
