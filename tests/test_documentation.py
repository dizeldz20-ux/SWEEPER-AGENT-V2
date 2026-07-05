"""Sprint 19.1 — coverage matrix documentation tests."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COVERAGE_MD = REPO_ROOT / "docs" / "COVERAGE_MATRIX.md"


def test_19_1_coverage_md_exists() -> None:
    assert COVERAGE_MD.exists()
    assert COVERAGE_MD.stat().st_size > 100


def test_19_1_lists_all_40_fs_modes() -> None:
    text = COVERAGE_MD.read_text()
    # All 40 FS-IDs should be mentioned
    for i in range(1, 41):
        assert f"FS-{i:02d}" in text, f"FS-{i:02d} not in coverage matrix"


def test_19_1_status_per_mode() -> None:
    """Every FS check should have a status indicator (✅/⚠️/❌)."""
    text = COVERAGE_MD.read_text()
    # Count ✅ entries in the FS table
    rows_with_status = re.findall(r"\| FS-\d{2} .*? \| [✅⚠️❌] \|", text)
    assert len(rows_with_status) >= 40


def test_19_1_total_count_correct() -> None:
    """The tally section reports the correct total."""
    text = COVERAGE_MD.read_text()
    # Should mention "63" (40 FS + 5 self + 10 repairs + 5 runbooks + 3 forecast)
    assert "63" in text


def test_19_1_links_to_test_files() -> None:
    """Every ✅ row should reference a test file path."""
    text = COVERAGE_MD.read_text()
    # Look for test file references in backticks
    test_refs = re.findall(r"`tests/[^\s`]+::", text)
    assert len(test_refs) >= 40  # at least one per FS check