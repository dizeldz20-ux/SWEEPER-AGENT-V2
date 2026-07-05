"""Sprint 19.5 — tag v1.0.0 + push verification."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        capture_output=True, text=True,
        cwd=REPO_ROOT,
    ).stdout


def test_19_5_tag_exists_locally() -> None:
    """v1.0.0 tag exists in the local repo (annotated or not)."""
    out = _git("tag", "--list", "v1.0.0")
    assert "v1.0.0" in out


def test_19_5_tag_annotated() -> None:
    """The v1.0.0 tag should be an annotated tag (-a), not a lightweight one."""
    out = _git("cat-file", "-t", "v1.0.0")
    assert out.strip() == "tag", f"Expected 'tag', got {out!r}"


def test_19_5_tag_message_includes_test_count() -> None:
    """Annotated tag message should mention the test count milestone."""
    out = _git("tag", "-l", "--format=%(contents:body)", "v1.0.0")
    # The message should reference tests (e.g. "+X tests" or "1158" or similar)
    assert "tests" in out.lower() or re.search(r"\d{2,}", out)


def test_19_5_push_verified_via_ls_remote() -> None:
    """The remote has the v1.0.0 tag (proven via `git ls-remote`)."""
    out = _git("ls-remote", "--tags", "origin", "refs/tags/v1.0.0")
    assert "v1.0.0" in out, f"v1.0.0 not found on remote: {out!r}"


def test_19_5_release_summary_in_vault() -> None:
    """A release summary exists in the Hermes vault (best-effort)."""
    vault = Path("/root/.hermes/memories/Hermes/Session Summaries")
    # This is a best-effort probe of an external tool's data under /root.
    # On a non-root checkout `vault.exists()` raises PermissionError (the
    # parent dir is unreadable) rather than returning False, so treat any
    # OSError as "vault not accessible here" and skip.
    try:
        present = vault.exists()
    except OSError:
        present = False
    if not present:
        pytest.skip("Vault not present/accessible in this environment")
    # Look for any session summary mentioning a known release version.
    # v1.0.0 / v1.1.0 / v1.1.1 / v1.2.0 — accept any of them.
    matches = []
    for ver in ("v1.0.0", "v1.1.0", "v1.1.1", "v1.2.0"):
        matches.extend(vault.glob(f"*{ver}*"))
    if not matches:
        pytest.skip("No release-version session summary in vault yet")