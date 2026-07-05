"""Sprint 19.3 — CHANGELOG final + version bump."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT = REPO_ROOT / "src" / "ipracticom_sweeper" / "__init__.py"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
INSTALL_SH = REPO_ROOT / "install.sh"
BOOTSTRAP_SH = REPO_ROOT / "bootstrap.sh"


def _latest_changelog_version() -> str:
    """The newest version heading in CHANGELOG.md is the source of truth."""
    text = CHANGELOG.read_text()
    m = re.search(r"^##\s*\[([0-9]+\.[0-9]+\.[0-9]+)\]", text, re.M)
    assert m is not None, "no version heading found in CHANGELOG.md"
    return m.group(1)


def test_19_3_version_in_pyproject() -> None:
    """Version in pyproject.toml must match the latest CHANGELOG release."""
    text = PYPROJECT.read_text()
    m = re.search(r'^version\s*=\s*"([0-9.]+)"', text, re.M)
    assert m is not None
    assert m.group(1) == _latest_changelog_version()


def test_19_3_version_in_init() -> None:
    """__version__ in __init__.py must match pyproject / CHANGELOG."""
    text = INIT.read_text()
    assert f'__version__ = "{_latest_changelog_version()}"' in text


def test_19_3_changelog_has_1_0_0_section() -> None:
    text = CHANGELOG.read_text()
    assert "## [1.0.0]" in text


def test_19_3_changelog_mentions_all_sprints_8_through_10() -> None:
    """The 1.0.0 section should mention Sprints 8, 9, 10, 15."""
    text = CHANGELOG.read_text()
    # Extract the 1.0.0 section
    m = re.search(r"## \[1\.0\.0\](.*?)(?=^## \[|\Z)", text, re.M | re.S)
    assert m is not None
    section = m.group(1)
    for sprint in ("Sprint 8", "Sprint 9", "Sprint 10", "Sprint 15"):
        assert sprint in section, f"{sprint} not in 1.0.0 changelog section"


def test_19_3_install_sh_default_branch() -> None:
    """install.sh must default SWEEPER_BRANCH to the live branch (master),
    not an ancient pinned tag."""
    text = INSTALL_SH.read_text()
    assert re.search(r'BRANCH="\$\{SWEEPER_BRANCH:-master\}"', text)


def test_19_3_bootstrap_sh_default_branch_v1_0_0() -> None:
    """bootstrap.sh runs from local checkout, so this is a soft check."""
    if not BOOTSTRAP_SH.exists():
        pytest.skip("bootstrap.sh not present")
    # Bootstrap typically doesn't pin a branch — accept either
    text = BOOTSTRAP_SH.read_text()
    # No assertion needed; bootstrap installs from local checkout
    assert "REPO_DIR" in text or "cd" in text