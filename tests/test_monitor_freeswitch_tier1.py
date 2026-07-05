"""FreeSWITCH Tier 1 (FS-01..FS-05) tests — v0.5.0 slice 2.1.

All subprocess / pgrep / ps / fs_cli / port-bind calls are mocked so the
tests run on any developer box without FreeSWITCH installed.
"""
import pytest
from unittest.mock import patch, MagicMock

from ipracticom_sweeper.monitor import freeswitch as fs
from ipracticom_sweeper.monitor.freeswitch import (
    check_fs01_process_running,
    check_fs02_systemd_active,
    check_fs03_sip_port,
    check_fs04_sips_port,
    check_fs05_cli_reachable,
    collect_all,
    evaluate,
)


# --- FS-01 -----------------------------------------------------------------


def test_fs01_running_when_pgrep_returns_pid():
    with patch("ipracticom_sweeper.monitor.freeswitch._run") as r:
        # ps
        r.side_effect = [
            (0, "freeswitch\nsshd\n", ""),  # ps -eo comm
            (0, "4242\n", ""),  # pgrep -x freeswitch
        ]
        out = check_fs01_process_running()
    assert out["status"] == "ok"
    assert out["values"]["fs01_running"] is True
    assert 4242 in out["values"]["fs01_pids"]


def test_fs01_not_running_when_pgrep_empty():
    with patch("ipracticom_sweeper.monitor.freeswitch._run") as r:
        r.side_effect = [
            (0, "sshd\nbash\n", ""),
            (1, "", ""),  # pgrep returns 1 when no match
        ]
        out = check_fs01_process_running()
    assert out["status"] == "crit"
    assert out["values"]["fs01_running"] is False
    assert out["values"]["fs01_pids"] == []


def test_fs01_ps_failure_returns_crit():
    with patch("ipracticom_sweeper.monitor.freeswitch._run") as r:
        r.side_effect = [
            (2, "", "boom"),  # ps failed
            (1, "", ""),
        ]
        out = check_fs01_process_running()
    assert out["status"] == "crit"
    assert out["values"]["fs01_running"] is False


# --- FS-02 -----------------------------------------------------------------


def test_fs02_active():
    with patch("ipracticom_sweeper.monitor.freeswitch._run", return_value=(0, "", "")):
        out = check_fs02_systemd_active("freeswitch")
    assert out["status"] == "ok"
    assert out["values"]["fs02_active"] is True
    assert out["values"]["fs02_unit"] == "freeswitch"


def test_fs02_inactive():
    with patch("ipracticom_sweeper.monitor.freeswitch._run", return_value=(3, "inactive\n", "")):
        out = check_fs02_systemd_active("freeswitch")
    assert out["status"] == "crit"
    assert out["values"]["fs02_active"] is False


def test_fs02_systemctl_not_found():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(127, "", "systemctl not found"),
    ):
        out = check_fs02_systemd_active("freeswitch")
    assert out["status"] == "crit"
    assert out["values"]["fs02_active"] is False


# --- FS-03 / FS-04 (port checks) ------------------------------------------


def test_fs03_port_listening():
    """Port 5060 is held by another process."""
    with patch("ipracticom_sweeper.monitor.freeswitch.socket.socket") as sm:
        sock = MagicMock()
        sock.bind.side_effect = OSError("address in use")
        sm.return_value = sock
        out = check_fs03_sip_port()
    assert out["status"] == "ok"
    assert out["values"]["fs03_listening"] is True


def test_fs03_port_free():
    with patch("ipracticom_sweeper.monitor.freeswitch.socket.socket") as sm:
        sock = MagicMock()
        sock.bind.return_value = None  # bind succeeded → port free
        sm.return_value = sock
        out = check_fs03_sip_port()
    assert out["status"] == "crit"
    assert out["values"]["fs03_listening"] is False


def test_fs04_sips_port_listening():
    with patch("ipracticom_sweeper.monitor.freeswitch.socket.socket") as sm:
        sock = MagicMock()
        sock.bind.side_effect = OSError("address in use")
        sm.return_value = sock
        out = check_fs04_sips_port()
    assert out["status"] == "ok"
    assert out["values"]["fs04_listening"] is True
    assert out["values"]["fs04_port"] == 5080


# --- FS-05 (fs_cli) --------------------------------------------------------


def test_fs05_cli_reachable():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "OK", ""),
    ):
        out = check_fs05_cli_reachable()
    assert out["status"] == "ok"
    assert out["values"]["fs05_reachable"] is True
    assert out["values"]["fs05_output_excerpt"] == "OK"


def test_fs05_cli_not_on_path():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value=None,
    ):
        out = check_fs05_cli_reachable()
    assert out["status"] == "crit"
    assert out["values"]["fs05_reachable"] is False
    assert "PATH" in out["values"]["fs05_reason"]


def test_fs05_cli_failure():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(1, "", "connection refused"),
    ):
        out = check_fs05_cli_reachable()
    assert out["status"] == "crit"
    assert out["values"]["fs05_reachable"] is False


# --- Aggregator ------------------------------------------------------------


def test_collect_all_keys():
    """collect_all produces all five flags + pids + reason."""
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.check_fs01_process_running",
        return_value={"status": "ok", "values": {"fs01_running": True, "fs01_pids": [1], "fs01_ps_rc": 0}},
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch.check_fs02_systemd_active",
        return_value={"status": "ok", "values": {"fs02_active": True, "fs02_unit": "freeswitch", "fs02_systemctl_rc": 0}},
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch.check_fs03_sip_port",
        return_value={"status": "ok", "values": {"fs03_port": 5060, "fs03_listening": True}},
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch.check_fs04_sips_port",
        return_value={"status": "ok", "values": {"fs04_port": 5080, "fs04_listening": True}},
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch.check_fs05_cli_reachable",
        return_value={"status": "ok", "values": {"fs05_cli_rc": 0, "fs05_reachable": True, "fs05_output_excerpt": "OK", "fs05_reason": None}},
    ):
        v = collect_all()
    for key in (
        "fs01_process_running", "fs02_systemd_active",
        "fs03_sip_port_5060", "fs04_sips_port_5080",
        "fs05_cli_reachable", "fs05_cli_reason", "fs01_pids",
    ):
        assert key in v


def test_evaluate_all_ok():
    v = {
        "fs01_process_running": True,
        "fs02_systemd_active": True,
        "fs03_sip_port_5060": True,
        "fs04_sips_port_5080": True,
        "fs05_cli_reachable": True,
    }
    assert evaluate(v) == "ok"


def test_evaluate_each_failure_is_crit():
    """Any individual flag False → crit (FS down = phone system down)."""
    base = {
        "fs01_process_running": True,
        "fs02_systemd_active": True,
        "fs03_sip_port_5060": True,
        "fs04_sips_port_5080": True,
        "fs05_cli_reachable": True,
    }
    for flag in base:
        v = dict(base)
        v[flag] = False
        assert evaluate(v) == "crit", f"flag {flag}=False should be crit"


def test_evaluate_handles_missing_keys():
    """Empty values dict → crit (no flags = no signal = failed)."""
    assert evaluate({}) == "crit"


# --- Integration with monitor.checks.run_all ------------------------------


def test_run_all_includes_freeswitch_module():
    """run_all(run_freeswitch=True) must include the freeswitch module.

    FreeSWITCH is now opt-in per-host (only PBX machines); this asserts the
    tiers still run when explicitly enabled.
    """
    from ipracticom_sweeper.monitor.checks import run_all
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.collect_all",
        return_value={
            "fs01_process_running": True,
            "fs02_systemd_active": True,
            "fs03_sip_port_5060": True,
            "fs04_sips_port_5080": True,
            "fs05_cli_reachable": True,
            "fs05_cli_reason": None,
            "fs01_pids": [42],
        },
    ):
        snap = run_all({}, run_freeswitch=True)
    assert "freeswitch" in snap["modules"]
    fs_block = snap["modules"]["freeswitch"]
    assert fs_block["status"] == "ok"
    assert fs_block["values"]["fs01_pids"] == [42]


def test_run_all_excludes_freeswitch_by_default():
    """The agent's own self-scan (run_all default) must NOT run FreeSWITCH.

    The agent box is not a PBX, so probing fs_cli there produces false crits.
    This is the operator's core requirement: the agent monitors itself with no
    FreeSWITCH checks. collect_all must never even be called.
    """
    from ipracticom_sweeper.monitor.checks import run_all
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.collect_all",
        side_effect=AssertionError("FreeSWITCH must not run on the agent self-scan"),
    ):
        snap = run_all({})
    assert "freeswitch" not in snap["modules"]
    assert "freeswitch_network" not in snap["modules"]
    assert "freeswitch_operational" not in snap["modules"]
    assert "freeswitch_edge" not in snap["modules"]


def test_run_all_swallows_freeswitch_collector_exception():
    """If freeswitch.collect_all raises, pipeline still completes (warn)."""
    from ipracticom_sweeper.monitor.checks import run_all
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.collect_all",
        side_effect=RuntimeError("oops"),
    ):
        snap = run_all({}, run_freeswitch=True)
    assert "freeswitch" in snap["modules"]
    assert snap["modules"]["freeswitch"]["status"] == "warn"
