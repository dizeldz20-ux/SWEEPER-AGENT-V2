"""Sprint 19.2 — README quick-start sections."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
README_MD = REPO_ROOT / "README.md"


def test_19_2_readme_has_install_section() -> None:
    text = README_MD.read_text().lower() if README_MD.exists() else ""
    # Install section can be "installation", "install", "quick start"
    assert ("install" in text) or ("setup" in text)


def test_19_2_readme_has_dashboard_section() -> None:
    text = README_MD.read_text().lower() if README_MD.exists() else ""
    assert "dashboard" in text


def test_19_2_readme_has_repair_workflow_section() -> None:
    text = README_MD.read_text().lower() if README_MD.exists() else ""
    assert "repair" in text


def test_19_2_readme_has_telegram_setup_section() -> None:
    text = README_MD.read_text().lower() if README_MD.exists() else ""
    assert "telegram" in text


def test_19_2_readme_has_faq_section() -> None:
    text = README_MD.read_text().lower() if README_MD.exists() else ""
    assert ("faq" in text) or ("troubleshoot" in text) or ("common questions" in text)