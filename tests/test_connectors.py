"""Tests for the connector persistence layer (config/connectors.py)."""
from __future__ import annotations

import os
import time

import pytest

from ipracticom_sweeper.config import connectors as conn
from ipracticom_sweeper.config.connectors import (
    Connector,
    add,
    connectors_file,
    get_by_name,
    load_all,
    mark_collected,
    mark_error,
    remove,
    save_all,
    state_dir,
    update,
)


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    """Point the connector module at a per-test tmp dir."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))


def test_connector_dataclass_roundtrip():
    c = Connector(name="web1", instance_id="i-abc", region="eu-west-1")
    d = c.to_dict()
    assert d["name"] == "web1"
    assert d["enabled"] is True
    c2 = Connector.from_dict(d)
    assert c == c2


def test_from_dict_drops_unknown_keys():
    c = Connector.from_dict({"name": "x", "instance_id": "i-1", "mystery": "ignore"})
    assert c.name == "x"
    assert c.instance_id == "i-1"


def test_from_dict_requires_name_and_instance_id():
    with pytest.raises(ValueError):
        Connector.from_dict({"name": "x"})
    with pytest.raises(ValueError):
        Connector.from_dict({"instance_id": "i-1"})


def test_load_all_empty_when_file_missing():
    assert load_all() == []


def test_add_and_get():
    add(Connector(name="a", instance_id="i-1"))
    add(Connector(name="b", instance_id="i-2"))
    all_c = load_all()
    assert [c.name for c in all_c] == ["a", "b"]
    assert get_by_name("a").instance_id == "i-1"


def test_add_rejects_duplicate_name():
    add(Connector(name="dup", instance_id="i-1"))
    with pytest.raises(ValueError, match="already exists"):
        add(Connector(name="dup", instance_id="i-2"))


def test_remove_existing_returns_true():
    add(Connector(name="x", instance_id="i-1"))
    assert remove("x") is True
    assert get_by_name("x") is None


def test_remove_missing_returns_false():
    assert remove("never-existed") is False


def test_update_changes_field():
    add(Connector(name="x", instance_id="i-1"))
    update("x", enabled=False)
    assert get_by_name("x").enabled is False
    update("x", last_collected_at=12345.0)
    assert get_by_name("x").last_collected_at == 12345.0


def test_update_missing_raises_keyerror():
    with pytest.raises(KeyError):
        update("never-existed", enabled=False)


def test_update_rejects_created_at_change():
    add(Connector(name="x", instance_id="i-1"))
    original_ts = get_by_name("x").created_at
    update("x", created_at=999.0)
    # created_at is immutable — should be ignored
    assert get_by_name("x").created_at == original_ts


def test_update_rejects_unknown_keys():
    add(Connector(name="x", instance_id="i-1"))
    with pytest.raises(ValueError, match="unknown fields"):
        update("x", totally_injected="bad")


def test_mark_collected_sets_timestamp_and_clears_error():
    add(Connector(name="x", instance_id="i-1"))
    mark_error("x", "boom")
    assert get_by_name("x").last_error == "boom"
    mark_collected("x", ts=1000.0)
    c = get_by_name("x")
    assert c.last_collected_at == 1000.0
    assert c.last_error is None


def test_mark_collected_on_missing_is_noop():
    # Should NOT raise — race with concurrent delete
    mark_collected("never-existed")


def test_mark_error_caps_long_strings():
    add(Connector(name="x", instance_id="i-1"))
    mark_error("x", "X" * 1000)
    # Capped at 500 chars to keep YAML readable
    assert len(get_by_name("x").last_error) == 500


def test_save_all_atomic_writes_file(tmp_path):
    # The .tmp-then-rename pattern means a crash mid-write never leaves
    # a half-written file. We can verify the final file is complete.
    add(Connector(name="x", instance_id="i-1"))
    final = connectors_file()
    assert final.exists()
    assert not final.with_suffix(".yaml.tmp").exists()
    # Final file parses back to a list of dicts
    import yaml
    data = yaml.safe_load(final.read_text())
    assert isinstance(data, list) and data[0]["name"] == "x"


def test_load_all_handles_malformed_yaml_gracefully(tmp_path):
    final = connectors_file()
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text("this is not valid: yaml: [unclosed")
    assert load_all() == []  # Should not raise


def test_load_all_skips_malformed_entries(tmp_path):
    final = connectors_file()
    final.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    final.write_text(yaml.safe_dump([
        {"name": "good", "instance_id": "i-1"},
        "this is a string, not a dict",
        {"name": "missing_instance_id"},
    ]))
    loaded = load_all()
    assert len(loaded) == 1
    assert loaded[0].name == "good"