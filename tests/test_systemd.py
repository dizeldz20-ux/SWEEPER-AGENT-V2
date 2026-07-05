"""Tests for systemd integration.

These tests don't actually touch systemd (the host might not have it).
They validate:
  - Service file has required fields
  - Timer file is valid syntax
  - install-systemd.sh exists, is executable, has correct structure
"""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
SYSTEMD_DIR = PROJECT_ROOT / "systemd"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


# --- Unit file presence ------------------------------------------------------


def test_service_file_exists():
    assert (SYSTEMD_DIR / "ipracticom-sweeper.service").exists()


def test_timer_file_exists():
    assert (SYSTEMD_DIR / "ipracticom-sweeper.timer").exists()


# --- Service file structure --------------------------------------------------


def test_service_has_required_sections():
    content = (SYSTEMD_DIR / "ipracticom-sweeper.service").read_text()
    assert "[Unit]" in content
    assert "[Service]" in content
    assert "[Install]" in content


def test_service_has_description():
    content = (SYSTEMD_DIR / "ipracticom-sweeper.service").read_text()
    assert "Description=" in content


def test_service_runs_python_sweeper():
    content = (SYSTEMD_DIR / "ipracticom-sweeper.service").read_text()
    assert "ipracticom_sweeper.sweeper" in content


def test_service_runs_as_root():
    """Many repairs (drop_caches, systemctl restart) need root."""
    content = (SYSTEMD_DIR / "ipracticom-sweeper.service").read_text()
    assert "User=root" in content


def test_service_has_success_exit_status():
    """Exit codes 1,2,3 are status indicators, not failures."""
    content = (SYSTEMD_DIR / "ipracticom-sweeper.service").read_text()
    assert "SuccessExitStatus=" in content


def test_service_has_resource_limits():
    """Don't let sweeper eat the box."""
    content = (SYSTEMD_DIR / "ipracticom-sweeper.service").read_text()
    assert "MemoryMax=" in content
    assert "CPUQuota=" in content


# --- Timer file structure ----------------------------------------------------


def test_timer_has_required_sections():
    content = (SYSTEMD_DIR / "ipracticom-sweeper.timer").read_text()
    assert "[Unit]" in content
    assert "[Timer]" in content
    assert "[Install]" in content


def test_timer_has_repeat_interval():
    content = (SYSTEMD_DIR / "ipracticom-sweeper.timer").read_text()
    assert "OnUnitActiveSec=" in content


def test_timer_runs_after_boot():
    content = (SYSTEMD_DIR / "ipracticom-sweeper.timer").read_text()
    assert "OnBootSec=" in content


def test_timer_is_persistent():
    """If the box was off when timer fired, run on next boot."""
    content = (SYSTEMD_DIR / "ipracticom-sweeper.timer").read_text()
    assert "Persistent=true" in content


def test_timer_has_accuracy():
    """Avoid thundering herd in fleet scenarios."""
    content = (SYSTEMD_DIR / "ipracticom-sweeper.timer").read_text()
    assert "AccuracySec=" in content


# --- install script ----------------------------------------------------------


def test_install_script_exists():
    assert (SCRIPTS_DIR / "install-systemd.sh").exists()


def test_install_script_is_executable():
    script = SCRIPTS_DIR / "install-systemd.sh"
    assert script.stat().st_mode & 0o111, f"{script} not executable"


def test_install_script_has_uninstall_flag():
    content = (SCRIPTS_DIR / "install-systemd.sh").read_text()
    assert "--uninstall" in content


def test_install_script_requires_root():
    content = (SCRIPTS_DIR / "install-systemd.sh").read_text()
    assert "id -u" in content or "EUID" in content


def test_install_script_copies_units():
    content = (SCRIPTS_DIR / "install-systemd.sh").read_text()
    assert "/etc/systemd/system/" in content


def test_install_script_enables_timer():
    content = (SCRIPTS_DIR / "install-systemd.sh").read_text()
    assert "systemctl enable" in content
    assert "ipracticom-sweeper.timer" in content


# --- Optional: actually validate with systemd-analyze (skip if unavailable) --


def test_systemd_analyze_validate_service():
    import shutil
    import subprocess

    if not shutil.which("systemd-analyze"):
        import pytest
        pytest.skip("systemd-analyze not available")

    result = subprocess.run(
        ["systemd-analyze", "verify", str(SYSTEMD_DIR / "ipracticom-sweeper.service")],
        capture_output=True,
        text=True,
    )
    # verify returns 0 only if all is well; some warnings are acceptable
    # but errors should not be present
    assert "Failed to" not in result.stderr, f"systemd-analyze errors: {result.stderr}"


def test_systemd_analyze_validate_timer():
    import shutil
    import subprocess

    if not shutil.which("systemd-analyze"):
        import pytest
        pytest.skip("systemd-analyze not available")

    result = subprocess.run(
        ["systemd-analyze", "verify", str(SYSTEMD_DIR / "ipracticom-sweeper.timer")],
        capture_output=True,
        text=True,
    )
    assert "Failed to" not in result.stderr, f"systemd-analyze errors: {result.stderr}"