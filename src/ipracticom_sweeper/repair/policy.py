"""Repair classification policy loader.

Reads /etc/ipracticom-sweeper/repair_policy.yaml and tells the pipeline
whether each registered repair action should run automatically or wait for
operator approval.

Schema:
    default: auto | needs_approval
    repairs:
      <action_name>: auto | needs_approval
"""

from __future__ import annotations

import os
from pathlib import Path

POLICY_FILE = Path(os.environ.get(
    "SWEEPER_REPAIR_POLICY",
    "/etc/ipracticom-sweeper/repair_policy.yaml",
))


def _load_yaml_simple(text: str) -> dict:
    """Minimal YAML loader — flat structure (key: value, one nesting level).

    Avoids pulling PyYAML just for a 10-line config file. Supports:
      - blank lines and `#` comments
      - `key: value`
      - `key:` followed by indented sub-keys
    """
    out: dict = {}
    current_section: dict | None = None
    current_section_key: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")) and stripped.endswith(":"):
            current_section_key = stripped[:-1]
            current_section = {}
            out[current_section_key] = current_section
        elif line.startswith((" ", "\t")) and current_section is not None:
            key, _, val = stripped.partition(":")
            current_section[key.strip()] = val.strip()
        elif ":" in stripped and not line.startswith((" ", "\t")):
            current_section = None
            current_section_key = None
            key, _, val = stripped.partition(":")
            out[key.strip()] = val.strip()
    return out


def load_policy() -> dict[str, str]:
    """Load the repair policy. Returns dict {action_name: 'auto'|'needs_approval'}.

    Falls back to all-auto if the file is missing or unparseable.
    """
    if not POLICY_FILE.exists():
        return {}
    try:
        raw = _load_yaml_simple(POLICY_FILE.read_text())
    except Exception:
        return {}

    default = (raw.get("default") or "auto").strip().lower()
    if default not in ("auto", "needs_approval"):
        default = "auto"

    repairs_section = raw.get("repairs") or {}
    if not isinstance(repairs_section, dict):
        repairs_section = {}

    out: dict[str, str] = {"__default__": default}
    for action, mode in repairs_section.items():
        m = str(mode).strip().lower()
        if m not in ("auto", "needs_approval"):
            m = default
        out[action] = m
    return out


def needs_approval(action: str, policy: dict[str, str] | None = None) -> bool:
    """True if the action requires operator approval before execution."""
    p = policy if policy is not None else load_policy()
    if action not in p:
        # Unknown repair → use the global default; if policy is empty (file
        # missing or no default set), fail safe = needs_approval.
        return p.get("__default__", "needs_approval") == "needs_approval"
    return p[action] == "needs_approval"