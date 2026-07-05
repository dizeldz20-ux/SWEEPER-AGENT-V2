"""Tests for the monitor→diagnose adapter."""

from ipracticom_sweeper.diagnose.adapter import adapt_for_diagnose


def test_adapter_empty_snapshot():
    assert adapt_for_diagnose({}) == {}
    assert adapt_for_diagnose({"modules": {}}) == {}


def test_adapter_normalizes_cpu():
    snapshot = {
        "modules": {
            "cpu": {"values": {"load_5min": 0.59, "iowait_percent": 0.24, "cores": 6}}
        }
    }
    result = adapt_for_diagnose(snapshot)
    assert "cpu" in result
    assert result["cpu"]["metrics"]["load_avg_5min"] == 0.59
    assert result["cpu"]["metrics"]["iowait_percent"] == 0.24
    assert result["cpu"]["metrics"]["cores"] == 6


def test_adapter_normalizes_memory():
    snapshot = {
        "modules": {
            "memory": {
                "values": {
                    "ram_used_percent": 31.4,
                    "swap_used_percent": 0.0,
                    "ram_available_kb": 8402256,
                }
            }
        }
    }
    result = adapt_for_diagnose(snapshot)
    assert result["memory"]["metrics"]["used_percent"] == 31.4
    assert result["memory"]["metrics"]["swap_used_percent"] == 0.0


def test_adapter_normalizes_disk_mountpoint_field():
    """Monitor uses 'mount', diagnose expects 'mountpoint'."""
    snapshot = {
        "modules": {
            "disk": {
                "values": {
                    "mounts": [
                        {"mount": "/", "used_percent": 53.0, "read_only": False},
                        {"mount": "/var", "used_percent": 85.0, "read_only": True},
                    ]
                }
            }
        }
    }
    result = adapt_for_diagnose(snapshot)
    mounts = result["disk"]["metrics"]["mounts"]
    assert mounts[0]["mountpoint"] == "/"
    assert mounts[0]["options"] == "rw"
    assert mounts[1]["mountpoint"] == "/var"
    assert mounts[1]["options"] == "ro"
    assert mounts[1]["read_only"] is True


def test_adapter_skips_malformed_disk_mounts():
    snapshot = {
        "modules": {
            "disk": {
                "values": {
                    "mounts": ["string-not-dict", {"mount": "/", "used_percent": 50.0, "read_only": False}]
                }
            }
        }
    }
    result = adapt_for_diagnose(snapshot)
    mounts = result["disk"]["metrics"]["mounts"]
    assert len(mounts) == 1
    assert mounts[0]["mountpoint"] == "/"


def test_adapter_normalizes_services_failed_units():
    """Monitor returns list of strings; diagnose expects list of dicts."""
    snapshot = {
        "modules": {
            "services": {"values": {"failed_units": ["nginx", "postgresql"], "failed_count": 2}}
        }
    }
    result = adapt_for_diagnose(snapshot)
    failed = result["services"]["metrics"]["failed_units"]
    assert failed == [{"unit": "nginx"}, {"unit": "postgresql"}]


def test_adapter_keeps_services_dict_format():
    """If already in dict format, leave alone."""
    snapshot = {
        "modules": {
            "services": {
                "values": {
                    "failed_units": [{"unit": "nginx"}],
                    "failed_count": 1,
                }
            }
        }
    }
    result = adapt_for_diagnose(snapshot)
    assert result["services"]["metrics"]["failed_units"] == [{"unit": "nginx"}]


def test_adapter_normalizes_security():
    snapshot = {
        "modules": {
            "security": {
                "values": {"failed_ssh_per_minute": 12.0, "sudo_failures": 3}
            }
        }
    }
    result = adapt_for_diagnose(snapshot)
    assert result["security"]["metrics"]["failed_ssh_per_min"] == 12.0
    assert result["security"]["metrics"]["sudo_failures_per_hour"] == 3


def test_adapter_passes_through_network():
    snapshot = {
        "modules": {
            "network": {
                "values": {
                    "rx_drops_total": 0,
                    "close_wait_count": 5,
                    "listen_count": 30,
                }
            }
        }
    }
    result = adapt_for_diagnose(snapshot)
    assert result["network"]["metrics"]["close_wait_count"] == 5


def test_adapter_handles_missing_modules_gracefully():
    snapshot = {"modules": {"unknown_thing": {"values": {"foo": 1}}}}
    result = adapt_for_diagnose(snapshot)
    assert result == {}


def test_adapter_real_monitor_snapshot():
    """Smoke test against the real output shape."""
    snapshot = {
        "modules": {
            "cpu": {"values": {"load_5min": 0.59, "iowait_percent": 0.24, "cores": 6}},
            "memory": {"values": {"ram_used_percent": 31.4, "swap_used_percent": 0.0}},
            "disk": {
                "values": {
                    "mounts": [
                        {"mount": "/", "used_percent": 53.47, "read_only": False},
                    ]
                }
            },
            "services": {"values": {"failed_units": []}},
            "security": {"values": {"failed_ssh_per_minute": 0.0, "sudo_failures": 0}},
        }
    }
    result = adapt_for_diagnose(snapshot)
    assert set(result.keys()) == {"cpu", "memory", "disk", "services", "security"}