"""FreeSWITCH Tier 2 (FS-06..FS-09) tests — v0.5.0 slice 2.2.

All fs_cli, pgrep, and socket calls are mocked. Tests are independent of
hardware state.
"""
import pytest
from unittest.mock import patch

from ipracticom_sweeper.monitor import freeswitch as fs
from ipracticom_sweeper.monitor.freeswitch import (
    check_fs06_sip_peers,
    check_fs07_sip_registrations,
    check_fs08_gateway_status,
    check_fs09_rtp_ports_open,
    collect_network,
    evaluate_network,
    _parse_int_from_fscli,
    _run_fscli,
)


# --- _parse_int_from_fscli ------------------------------------------------


def test_parse_int_takes_last_numeric_token():
    assert _parse_int_from_fscli("12 endpoints.") == 12


def test_parse_int_handles_no_number():
    assert _parse_int_from_fscli("nothing here") is None


def test_parse_int_handles_empty_string():
    assert _parse_int_from_fscli("") is None


def test_parse_int_strips_trailing_punctuation():
    assert _parse_int_from_fscli("count: 42,") == 42


# --- _run_fscli -----------------------------------------------------------


def test_run_fscli_returns_uniform_envelope_when_missing():
    with patch("ipracticom_sweeper.monitor.freeswitch.shutil.which", return_value=None):
        res = _run_fscli("show endpoints count")
    assert res["rc"] == 127
    assert "PATH" in res["stderr"]


def test_run_fscli_invokes_subprocess_when_present():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "12 endpoints.", ""),
    ) as run_mock:
        res = _run_fscli("show endpoints count")
    assert res["rc"] == 0
    assert res["stdout"] == "12 endpoints."
    run_mock.assert_called_once_with(
        ["fs_cli", "-x", "show endpoints count"], timeout=fs.DEFAULT_CLI_TIMEOUT
    )


# --- FS-06 ----------------------------------------------------------------


def test_fs06_ok_when_count_meets_minimum():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "12 endpoints.", ""),
    ):
        out = check_fs06_sip_peers(min_peers=1)
    assert out["status"] == "ok"
    assert out["values"]["fs06_endpoint_count"] == 12


def test_fs06_warn_when_below_min():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "0 endpoints.", ""),
    ):
        out = check_fs06_sip_peers(min_peers=1)
    assert out["status"] == "warn"
    assert out["values"]["fs06_endpoint_count"] == 0


def test_fs06_warn_when_cli_fails():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(1, "", "connection refused"),
    ):
        out = check_fs06_sip_peers()
    assert out["status"] == "warn"
    assert out["values"]["fs06_endpoint_count"] is None


def test_fs06_warn_when_unparseable():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "no count here", ""),
    ):
        out = check_fs06_sip_peers()
    assert out["status"] == "warn"
    assert out["values"]["fs06_endpoint_count"] is None


# --- FS-07 ----------------------------------------------------------------


def test_fs07_ok_with_registrations():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "5 entries", ""),
    ):
        out = check_fs07_sip_registrations()
    assert out["status"] == "ok"
    assert out["values"]["fs07_registration_count"] == 5


def test_fs07_crit_with_zero_registrations():
    """Zero registered phones = phones cannot ring = crit."""
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "0 entries", ""),
    ):
        out = check_fs07_sip_registrations()
    assert out["status"] == "crit"
    assert out["values"]["fs07_registration_count"] == 0


def test_fs07_warn_when_cli_fails():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(124, "", "timeout"),
    ):
        out = check_fs07_sip_registrations()
    assert out["status"] == "warn"


# --- FS-08 ----------------------------------------------------------------


def test_fs08_ok_when_gateway_reged():
    sample = (
        "Name                      ||  Status\n"
        "==========================||========\n"
        "carrier1                   ||  REGED\n"
    )
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, sample, ""),
    ):
        out = check_fs08_gateway_status()
    assert out["status"] == "ok"
    assert out["values"]["fs08_gateway_up"] >= 1


def test_fs08_warn_when_gateway_noreg():
    sample = "carrier1 || NOREG\n"
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, sample, ""),
    ):
        out = check_fs08_gateway_status()
    assert out["status"] == "warn"
    assert out["values"]["fs08_gateway_up"] == 0


def test_fs08_warn_when_no_gateway_block():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "no sofia profile loaded", ""),
    ):
        out = check_fs08_gateway_status()
    assert out["status"] == "warn"


def test_fs08_warn_when_cli_fails():
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(1, "", "fatal"),
    ):
        out = check_fs08_gateway_status()
    assert out["status"] == "warn"


# --- FS-09 (RTP ports) ---------------------------------------------------


def test_fs09_ok_when_at_least_one_anchor_held():
    with patch("ipracticom_sweeper.monitor.freeswitch.socket.socket") as sm:
        sock = MagicMock_factory()
        sock.bind.side_effect = OSError("address in use")
        sm.return_value = sock
        out = check_fs09_rtp_ports_open()
    assert out["status"] == "ok"
    assert out["values"]["fs09_anchors_held_count"] >= 1


def test_fs09_warn_when_no_anchors_held():
    with patch("ipracticom_sweeper.monitor.freeswitch.socket.socket") as sm:
        sock = MagicMock_factory()
        sock.bind.return_value = None
        sm.return_value = sock
        out = check_fs09_rtp_ports_open()
    assert out["status"] == "warn"
    assert out["values"]["fs09_anchors_held_count"] == 0


def test_fs09_uses_custom_range():
    """Custom range passed through to anchors."""
    with patch("ipracticom_sweeper.monitor.freeswitch.socket.socket") as sm:
        sock = MagicMock_factory()
        sock.bind.return_value = None
        sm.return_value = sock
        out = check_fs09_rtp_ports_open(low=20000, high=20010)
    assert out["values"]["fs09_port_range"] == [20000, 20010]


# --- collect_network aggregator ------------------------------------------


def test_collect_network_returns_expected_keys():
    expected = {
        "fs06_endpoint_count", "fs06_min_peers",
        "fs07_registration_count", "fs07_min_registrations",
        "fs08_gateway_up", "fs08_gateway_count",
        "fs09_anchors_held_count", "fs09_port_range",
        "fs06_status", "fs07_status", "fs08_status", "fs09_status",
    }
    with patch.object(fs, "check_fs06_sip_peers",
                      return_value={"status": "ok", "values": {
                          "fs06_endpoint_count": 5, "fs06_min_peers": 1,
                          "fs06_cli_rc": 0, "fs06_reason": None}}), \
         patch.object(fs, "check_fs07_sip_registrations",
                      return_value={"status": "ok", "values": {
                          "fs07_registration_count": 3, "fs07_min_registrations": 1,
                          "fs07_cli_rc": 0, "fs07_reason": None}}), \
         patch.object(fs, "check_fs08_gateway_status",
                      return_value={"status": "ok", "values": {
                          "fs08_gateway_count": 1, "fs08_gateway_up": 1,
                          "fs08_cli_rc": 0, "fs08_reason": None}}), \
         patch.object(fs, "check_fs09_rtp_ports_open",
                      return_value={"status": "ok", "values": {
                          "fs09_port_range": [16384, 32768],
                          "fs09_anchors_checked": [16384, 24576, 32768],
                          "fs09_anchors_held": {16384: True, 24576: False, 32768: False},
                          "fs09_anchors_held_count": 1}}):
        v = collect_network()
    assert expected.issubset(v.keys())


# --- evaluate_network -----------------------------------------------------


def test_evaluate_network_ok_when_all_ok():
    v = {
        "fs07_registration_count": 5, "fs07_min_registrations": 1,
        "fs06_status": "ok", "fs08_status": "ok", "fs09_status": "ok",
    }
    assert evaluate_network(v) == "ok"


def test_evaluate_network_crit_when_no_registrations():
    v = {
        "fs07_registration_count": 0, "fs07_min_registrations": 1,
        "fs06_status": "ok", "fs08_status": "ok", "fs09_status": "ok",
    }
    assert evaluate_network(v) == "crit"


def test_evaluate_network_warn_when_gateway_noreg():
    v = {
        "fs07_registration_count": 5, "fs07_min_registrations": 1,
        "fs06_status": "ok", "fs08_status": "warn", "fs09_status": "ok",
    }
    assert evaluate_network(v) == "warn"


def test_evaluate_network_handles_none_count():
    """CLI failure → registration_count None → don't crit, return worst of others."""
    v = {
        "fs07_registration_count": None, "fs07_min_registrations": 1,
        "fs06_status": "warn", "fs08_status": "ok", "fs09_status": "ok",
    }
    assert evaluate_network(v) == "warn"


def test_evaluate_network_handles_empty():
    assert evaluate_network({}) == "warn"


# --- Integration with monitor.checks.run_all ----------------------------


def test_run_all_emits_freeswitch_network_module():
    from ipracticom_sweeper.monitor.checks import run_all
    with patch.object(fs, "collect_network", return_value={
        "fs06_endpoint_count": 5, "fs06_min_peers": 1,
        "fs07_registration_count": 3, "fs07_min_registrations": 1,
        "fs08_gateway_up": 1, "fs08_gateway_count": 1,
        "fs09_anchors_held_count": 1, "fs09_port_range": [16384, 32768],
        "fs06_status": "ok", "fs07_status": "ok", "fs08_status": "ok",
        "fs09_status": "ok",
    }):
        snap = run_all({}, run_freeswitch=True)
    assert "freeswitch_network" in snap["modules"]
    assert snap["modules"]["freeswitch_network"]["status"] == "ok"


def test_run_all_swallows_freeswitch_network_collector_exception():
    from ipracticom_sweeper.monitor.checks import run_all
    with patch.object(fs, "collect_network", side_effect=RuntimeError("oops")):
        snap = run_all({}, run_freeswitch=True)
    assert "freeswitch_network" in snap["modules"]
    assert snap["modules"]["freeswitch_network"]["status"] == "warn"


# --- helpers --------------------------------------------------------------


def MagicMock_factory():
    """Return a fresh MagicMock — function form keeps patch.object args readable."""
    from unittest.mock import MagicMock
    return MagicMock()
