"""AIDE file integrity monitor.

Parses `aide --check` output to detect unauthorized file changes. Used
to catch intrusions and configuration drift.

Requires the `aide` package (apt install aide). If aide binary is
missing, returns a 'not_available' status.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import re
import shutil
import subprocess


@dataclass
class AideReport:
    """Result of an AIDE integrity check."""

    added: int
    removed: int
    changed: int
    added_files: list[str] = field(default_factory=list)
    removed_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    raw_output: str = ""
    parse_error: str | None = None
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "added_files": self.added_files,
            "removed_files": self.removed_files,
            "changed_files": self.changed_files,
            "parse_error": self.parse_error,
            "available": self.available,
        }


# Lines that look like AIDE file entries:
# "+ /etc/nginx/sites-available/mysite.conf"
# "- /tmp/old.log"
# "f ... .b... /etc/passwd"
ADDED_RE = re.compile(r"^\+\s+(.+)$")
REMOVED_RE = re.compile(r"^-\s+(.+)$")
CHANGED_RE = re.compile(r"^[a-z]+\s+\.[a-z.]+\s+(\S+)\s*$")


def parse_aide_output(output: str) -> AideReport:
    """Parse AIDE --check output into an AideReport.

    Sections are marked by '### Files added', '### Files removed',
    '### Files changed'. Each section lists file paths.
    """
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    section = ""  # "", "added", "removed", "changed"

    for line in output.splitlines():
        if "### Files added" in line:
            section = "added"
            continue
        if "### Files removed" in line:
            section = "removed"
            continue
        if "### Files changed" in line:
            section = "changed"
            continue
        if line.strip().startswith("###") or not line.strip():
            # Other section markers or blanks
            if line.strip().startswith("###") and "match AIDE database" in line:
                # Clean status
                section = ""
            continue

        if section == "added":
            m = ADDED_RE.match(line)
            if m:
                added.append(m.group(1).strip())
        elif section == "removed":
            m = REMOVED_RE.match(line)
            if m:
                removed.append(m.group(1).strip())
        elif section == "changed":
            # Lines look like "f ... .b... /etc/passwd"
            parts = line.split()
            if len(parts) >= 3 and parts[0].isalpha() and parts[0].islower():
                # Take the last token as the filename
                fname = parts[-1]
                if fname.startswith("/"):
                    changed.append(fname)

    return AideReport(
        added=len(added),
        removed=len(removed),
        changed=len(changed),
        added_files=added,
        removed_files=removed,
        changed_files=changed,
        raw_output=output[:2000],  # truncate
    )


def collect_aide_report(timeout: int = 30) -> AideReport:
    """Run `aide --check` and parse output.

    Returns AideReport with available=False if aide binary missing.
    """
    aide = shutil.which("aide")
    if not aide:
        return AideReport(added=0, removed=0, changed=0, available=False,
                         parse_error="aide binary not found (apt install aide)")

    try:
        proc = subprocess.run(
            [aide, "--check"],
            capture_output=True, text=True, timeout=timeout,
        )
        # AIDE returns non-zero if changes detected — that's normal
        output = proc.stdout or proc.stderr
        report = parse_aide_output(output)
        report.parse_error = None
        return report
    except subprocess.TimeoutExpired:
        return AideReport(added=0, removed=0, changed=0,
                         parse_error=f"aide timeout ({timeout}s)")
    except Exception as e:
        return AideReport(added=0, removed=0, changed=0,
                         parse_error=f"{type(e).__name__}: {e}")


def evaluate(values: dict, rules: dict) -> str:
    """Return 'ok' | 'warn' | 'crit'.

    crit if any change in critical paths (/etc, /bin, /usr/bin, /root/.ssh).
    warn if any change anywhere.
    ok if no change OR aide not available.
    """
    report = values
    if not report.get("available", True):
        return "ok"  # graceful: don't alert on missing aide
    if report.get("parse_error"):
        return "ok"  # graceful: don't alert on tool errors

    critical_paths = rules.get("aide", {}).get("critical_paths", [
        "/etc/", "/bin/", "/usr/bin/", "/usr/sbin/", "/sbin/", "/root/.ssh/",
    ])

    # Check critical paths
    all_files = (
        report.get("added_files", [])
        + report.get("removed_files", [])
        + report.get("changed_files", [])
    )
    for f in all_files:
        for cp in critical_paths:
            if f.startswith(cp):
                return "crit"

    total = report.get("added", 0) + report.get("removed", 0) + report.get("changed", 0)
    if total > 0:
        return "warn"
    return "ok"
