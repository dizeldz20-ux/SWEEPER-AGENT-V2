"""Tests for disk monitor module."""

from ipracticom_sweeper.monitor import disk


def test_collect_returns_mounts():
    snap = disk.collect()
    assert "mounts" in snap
    assert snap["mount_count"] >= 1
    assert all("filesystem" in m for m in snap["mounts"])


def test_each_mount_has_required_fields():
    snap = disk.collect()
    for m in snap["mounts"]:
        assert "mount" in m
        assert "size_kb" in m
        assert "used_kb" in m
        assert "used_percent" in m
        assert "read_only" in m
        assert 0 <= m["used_percent"] <= 100


def test_evaluate_crit_on_full_disk():
    rules = {"disk": {"used_percent_warn": 80.0, "used_percent_crit": 95.0, "inode_used_percent_warn": 80.0, "read_only_mounts": []}}
    values = {"mounts": [{"mount": "/", "used_percent": 99.0, "inode_used_percent": 50.0, "read_only": False}]}
    assert disk.evaluate(values, rules) == "crit"


def test_evaluate_warn_on_high_disk():
    rules = {"disk": {"used_percent_warn": 80.0, "used_percent_crit": 95.0, "inode_used_percent_warn": 80.0, "read_only_mounts": []}}
    values = {"mounts": [{"mount": "/", "used_percent": 85.0, "inode_used_percent": 50.0, "read_only": False}]}
    assert disk.evaluate(values, rules) == "warn"


def test_evaluate_warn_on_ro_mount_violation():
    """If / must be RO but is RW, that's a security violation."""
    rules = {"disk": {"used_percent_warn": 80.0, "used_percent_crit": 95.0, "inode_used_percent_warn": 80.0, "read_only_mounts": ["/"]}}
    values = {"mounts": [{"mount": "/", "used_percent": 30.0, "inode_used_percent": 5.0, "read_only": False}]}
    assert disk.evaluate(values, rules) == "warn"


def test_evaluate_ok_normal():
    rules = {"disk": {"used_percent_warn": 80.0, "used_percent_crit": 95.0, "inode_used_percent_warn": 80.0, "read_only_mounts": []}}
    values = {"mounts": [{"mount": "/", "used_percent": 30.0, "inode_used_percent": 5.0, "read_only": False}]}
    assert disk.evaluate(values, rules) == "ok"