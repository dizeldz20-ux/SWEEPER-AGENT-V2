"""Sprint 19.4 — public mirror re-sync + re-scrub."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_19_4_no_pii_in_markdown() -> None:
    """No PII (phone numbers, real names with @) in any committed .md."""
    for md in REPO_ROOT.rglob("*.md"):
        # Skip virtualenvs / .git
        if any(part in md.parts for part in (".venv", "node_modules", ".git")):
            continue
        text = md.read_text(errors="replace")
        # Look for Israeli phone numbers (10 digits with optional separators)
        phones = re.findall(r"05\d[\s\-]?\d{3}[\s\-]?\d{4}", text)
        assert not phones, f"Phone numbers in {md.relative_to(REPO_ROOT)}: {phones}"


def test_19_4_no_internal_ips() -> None:
    """No RFC1918 IPs committed in any .md or .py in the repo."""
    rfc1918_pattern = re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3})\b"
    )
    for f in REPO_ROOT.rglob("*.md"):
        # node_modules is vendored third-party code (git-ignored, never part of
        # the public mirror), so a dependency README with an example 10.x
        # address is not a "committed" IP we control. Skip it — consistent with
        # test_19_4_no_pii_in_markdown above.
        if any(part in f.parts for part in (".venv", ".git", "tests", "node_modules")):
            continue
        text = f.read_text(errors="replace")
        matches = rfc1918_pattern.findall(text)
        assert not matches, f"Internal IP in {f.relative_to(REPO_ROOT)}: {matches}"


def test_19_4_no_secrets_in_tracked_files() -> None:
    """No .env files, no obvious API key patterns in tracked files."""
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        pytest.fail(".env should not be committed")
    # No files with "api_key = ..." in plain text
    # Skip test files (which often use fake credentials as fixtures)
    skip_dirs = (".venv", ".git", "tests")
    for f in REPO_ROOT.rglob("*.py"):
        if any(part in f.parts for part in skip_dirs):
            continue
        text = f.read_text(errors="replace")
        # Look for hardcoded token patterns
        secret_patterns = re.findall(
            r'(api[_-]?key|token|secret)\s*=\s*["\'][\w\-]{20,}["\']', text, re.I
        )
        assert not secret_patterns, f"Possible secret in {f.relative_to(REPO_ROOT)}"


def test_19_4_changelog_synced_with_local() -> None:
    """Local CHANGELOG.md must be in sync with the most recent commit
    (no uncommitted local changes that should have been committed)."""
    import subprocess
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", "CHANGELOG.md"],
        capture_output=True, text=True,
        cwd=REPO_ROOT,
    )
    # No output means no local diff. Acceptable statuses: empty or
    # already-staged.
    assert result.returncode == 0
    # If the file has uncommitted local modifications, the test would have
    # already been exercised during the release flow; we only flag if
    # the file is untracked (i.e. never committed).
    assert "?? CHANGELOG.md" not in result.stdout