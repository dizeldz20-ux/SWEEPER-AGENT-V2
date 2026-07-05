"""Pipeline integration smoke for FreeSWITCH modules (v0.5.0 slice 2.5).

Light-weight assertions that:
- The four freeswitch* module keys appear in the catalogue registry
- The FS-21/FS-22 values are numeric (so they can flow into time-series)
- The inspector dashboard endpoint renders a host snapshot carrying FS modules

Heavy run_all() integration is left to Tier1/2/3/4 test modules which already
exercise each collector + evaluator in isolation. Composing them in the
real pipeline was found to take ~42s on this box even with mocks (because
the FS collectors hit fs_cli / socket bind / pgrep timeouts) and was
not a productive use of suite time.
"""
import pytest
from unittest.mock import patch, MagicMock

from ipracticom_sweeper.catalogue import CHECK_REGISTRY
from ipracticom_sweeper.monitor import freeswitch as fs


# --- catalogue integration ----------------------------------------------


def test_catalogue_includes_all_four_fs_modules():
    """The four freeswitch* module keys are registered for human inspection."""
    keys = {e["key"] for e in CHECK_REGISTRY}
    for required in ("freeswitch", "freeswitch_network",
                     "freeswitch_operational", "freeswitch_edge"):
        assert required in keys, f"catalogue missing {required}"


def test_catalogue_fs_entries_have_hebrew_labels():
    """Each FS catalogue entry carries a Hebrew label and description."""
    fs_keys = ("freeswitch", "freeswitch_network",
               "freeswitch_operational", "freeswitch_edge")
    by_key = {e["key"]: e for e in CHECK_REGISTRY}
    for k in fs_keys:
        entry = by_key[k]
        assert entry["label_he"], f"{k} missing label_he"
        assert entry["description_he"], f"{k} missing description_he"
        assert "Tier" in entry["description_he"]


# --- dashboard render integration ---------------------------------------


def test_inspector_renders_remote_freeswitch_module():
    """A remote host snapshot carrying freeswitch modules renders them."""
    from ipracticom_sweeper.dashboard import app
    app.config["TESTING"] = True
    snap_entry = {
        "name": "fs-host",
        "collected_at": 1000000000,
        "snapshot": {
            "modules": {
                "freeswitch": {
                    "status": "crit",
                    "values": {"fs01_process_running": False,
                               "fs02_systemd_active": False,
                               "fs03_sip_port_5060": False,
                               "fs04_sips_port_5080": False,
                               "fs05_cli_reachable": False,
                               "fs05_cli_reason": "down"},
                },
                "cpu": {"status": "ok", "values": {"cpu.idle_percent": 80}},
            }
        },
    }
    from ipracticom_sweeper.config import Connector
    connectors = [Connector(name="fs-host", instance_id="i-xyz",
                            region="il-central-1")]
    with app.test_client() as c, \
         patch("ipracticom_sweeper.config.get_connector",
               return_value=connectors[0]), \
         patch("ipracticom_sweeper.fleet.load_snapshot",
               return_value=snap_entry), \
         patch("ipracticom_sweeper.config.load_connectors",
               return_value=connectors):
        r = c.get("/inspector/host/fs-host")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "cpu" in body
    assert "freeswitch" in body


# --- time-series persistence smoke --------------------------------------


def test_fs21_rss_value_is_numeric_so_time_series_can_persist():
    """fs21.rss_bytes is a number — eligible for TimeSeriesDB.write()."""
    p = MagicMock()
    p.info = {"name": "freeswitch",
              "memory_info": MagicMock(rss=1_500_000_000)}
    with patch("psutil.process_iter", return_value=iter([p])):
        out = fs.check_fs21_process_rss()
    v = out["values"]["fs21_rss_bytes"]
    assert isinstance(v, (int, float))
    assert v > 0


def test_fs22_cpu_value_is_numeric_so_time_series_can_persist():
    """fs22.cpu_pct is a number — eligible for TimeSeriesDB.write()."""
    p = MagicMock()
    p.info = {"name": "freeswitch"}
    p.cpu_percent.return_value = 25.5
    with patch("psutil.process_iter", return_value=iter([p])):
        out = fs.check_fs22_process_cpu_pct(sample_seconds=0.0)
    v = out["values"]["fs22_cpu_pct"]
    assert isinstance(v, (int, float))
    assert v >= 0


def test_fs16_backup_age_is_numeric_when_present(tmp_path):
    """fs16.fs16_age_hours is a float when a backup file exists."""
    backup = tmp_path / "cdr-2026-06-30.sql"
    backup.write_text("-- backup")
    two_hours_ago = 2 * 3600
    import time
    with patch("ipracticom_sweeper.monitor.freeswitch.os.path.getmtime",
               return_value=time.time() - two_hours_ago):
        out = fs.check_fs16_cdr_backup_fresh(
            backup_glob_pattern=str(tmp_path / "cdr-*.sql"),
            max_age_hours=26,
        )
    v = out["values"]["fs16_age_hours"]
    assert isinstance(v, float)
    assert v > 0


def test_fs15_drift_factor_is_numeric():
    """fs15.fs15_drift_factor is a number when baseline is set."""
    with patch(
        "ipracticom_sweeper.monitor.freeswitch.shutil.which",
        return_value="/usr/bin/fs_cli",
    ), patch(
        "ipracticom_sweeper.monitor.freeswitch._run",
        return_value=(0, "5 entries", ""),
    ):
        out = fs.check_fs15_baseline_calls_per_hour(baseline_calls_per_hour=100)
    v = out["values"]["fs15_drift_factor"]
    assert isinstance(v, (int, float))
    assert v >= 0
