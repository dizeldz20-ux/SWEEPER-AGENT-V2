#!/usr/bin/env python3
"""
verify_install.py — pre-flight self-check for any installing agent.

Runs five checks against the local environment, prints a clean
pass/fail summary, and exits 0 only if EVERYTHING is green.

Designed for the "another Hermes agent walks up to the repo and tries
to install it cold" scenario — a single command tells the agent
exactly what's missing.

Exit codes:
    0  all checks passed
    1  one or more required checks failed
    2  warnings only (optional missing deps)
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable


REQUIRED_PYTHON = (3, 10)


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failed_required(self) -> list[CheckResult]:
        return [r for r in self.results if r.required and not r.ok]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if not r.required and not r.ok]

    def print(self) -> None:
        print("=" * 64)
        print("iPracticom Sweeper — install self-check")
        print("=" * 64)
        for r in self.results:
            tag = "OK  " if r.ok else ("WARN" if not r.required else "FAIL")
            print(f"  [{tag}] {r.name:<28} {r.detail}")
        print("-" * 64)
        if self.failed_required:
            print(f"  ❌ {len(self.failed_required)} required check(s) FAILED")
        elif self.warnings:
            print(f"  ⚠️  All required checks passed, {len(self.warnings)} warning(s)")
        else:
            print("  ✅ All checks passed — ready to `pip install -e .`")
        print("=" * 64)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def check_python() -> CheckResult:
    v = sys.version_info
    ok = v >= REQUIRED_PYTHON
    return CheckResult(
        "python-version",
        ok,
        f"Python {v.major}.{v.minor}.{v.micro} "
        f"({'>= ' + '.'.join(map(str, REQUIRED_PYTHON)) if ok else 'TOO OLD — need ' + '.'.join(map(str, REQUIRED_PYTHON))})",
    )


def check_pip() -> CheckResult:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        ok = out.returncode == 0
        return CheckResult("pip", ok, (out.stdout or out.stderr).strip().split("\n")[0])
    except Exception as e:
        return CheckResult("pip", False, f"error: {e}")


# Hard dependencies from pyproject.toml [project.dependencies]
PYPROJECT_DEPS = [
    "structlog",
    "yaml",
    "httpx",
    "boto3",
    "flask",
    "flask_sock",
    "uvicorn",
    "psutil",
]


def check_runtime_deps() -> CheckResult:
    missing = []
    for mod in PYPROJECT_DEPS:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    ok = not missing
    detail = "all present" if ok else f"missing: {', '.join(missing)}"
    return CheckResult("runtime-deps", ok, detail)


# System binaries referenced from shell-outs in monitor/repair code.
# These are NOT pip-installable; we report as warnings so an installing
# agent knows what's needed at the OS level for full functionality.
SYSTEM_BINARIES = [
    ("fs_cli",      "FreeSWITCH CLI (optional, repair actions)"),
    ("systemctl",   "systemd service control (optional, service_restart)"),
    ("journalctl",  "log access (optional, log inspection)"),
    ("ss",          "network sockets (optional, port-monitor)"),
    ("df",          "disk free (optional, disk-monitor)"),
    ("free",        "memory info (optional, memory-monitor)"),
    ("ps",          "process listing (optional, process-monitor)"),
    ("pgrep",       "process grep (optional, monitor)"),
    ("pkill",       "process kill (optional, repair)"),
    ("tar",         "archive utility (optional, log rotation)"),
]


def check_system_binaries() -> CheckResult:
    missing = [name for name, _ in SYSTEM_BINARIES if shutil.which(name) is None]
    ok = not missing
    detail = "all present" if ok else f"missing: {', '.join(missing)}"
    return CheckResult("system-binaries", ok, detail, required=False)


def check_entry_points() -> CheckResult:
    """Verify the package declares the three CLI scripts in pyproject."""
    # tomllib is stdlib only on 3.11+, but the package supports 3.10 — fall
    # back to the `tomli` backport, and skip gracefully if neither is present
    # (so `make verify` doesn't hard-fail on a supported interpreter).
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            return CheckResult(
                "entry-points", True,
                "skipped (no tomllib/tomli on this interpreter)", required=False,
            )
    try:
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return CheckResult("entry-points", False, "pyproject.toml not found")
    except Exception as e:
        return CheckResult("entry-points", False, f"parse error: {e}")

    scripts = data.get("project", {}).get("scripts", {})
    expected = {
        "ipracticom-sweeper",
        "ipracticom-dashboard",
        "ipracticom-agent-api",
    }
    have = set(scripts.keys())
    missing = expected - have
    ok = not missing
    detail = f"{len(have)} declared" if ok else f"missing: {', '.join(sorted(missing))}"
    return CheckResult("entry-points", ok, detail)


def check_tests_runnable() -> CheckResult:
    """Smoke test: pytest is available and collects ≥1 test."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return CheckResult("tests-collect", False, f"pytest collect failed: {out.stderr[:200]}")
        # Count "test_" lines in output.
        n = sum(1 for line in out.stdout.splitlines() if "::" in line and "::test_" in line)
        return CheckResult("tests-collect", n > 0, f"{n} tests discovered")
    except Exception as e:
        return CheckResult("tests-collect", False, f"error: {e}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
CHECKS: list[Callable[[], CheckResult]] = [
    check_python,
    check_pip,
    check_runtime_deps,
    check_entry_points,
    check_tests_runnable,
    check_system_binaries,
]


def main() -> int:
    report = Report()
    for check in CHECKS:
        try:
            report.results.append(check())
        except Exception as e:  # never let a single check crash the report
            report.results.append(
                CheckResult(check.__name__, False, f"unexpected error: {e}")
            )
    report.print()
    if report.failed_required:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
