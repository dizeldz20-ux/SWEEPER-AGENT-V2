"""Tests for ConfigLoader + token rotation."""
import json
import time
import os
from ipracticom_sweeper.config import ConfigLoader, load_token


def test_loader_initial(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text('{"threshold": 80}')
    loader = ConfigLoader(cfg)
    assert loader.get()["threshold"] == 80


def test_loader_hot_reload(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text('{"threshold": 80}')
    loader = ConfigLoader(cfg)
    assert loader.get()["threshold"] == 80
    time.sleep(0.05)
    cfg.write_text('{"threshold": 95}')
    assert loader.get()["threshold"] == 95


def test_loader_missing_file(tmp_path):
    loader = ConfigLoader(tmp_path / "nope.json")
    assert loader.get() == {}


def test_loader_save(tmp_path):
    cfg = tmp_path / "c.json"
    loader = ConfigLoader(cfg)
    loader.save({"k": "v"})
    assert json.loads(cfg.read_text()) == {"k": "v"}
    assert loader.get()["k"] == "v"


def test_loader_force_reload(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text('{"a": 1}')
    loader = ConfigLoader(cfg)
    loader.get()
    cfg.write_text('{"a": 2}')
    # without sleep, mtime might be same
    result = loader.reload()
    assert result["a"] == 2


def test_token_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_TOKEN_ROTATION_ENABLED", raising=False)
    monkeypatch.setenv("AGENT_TOKEN_20260101", "secret123")
    assert load_token() is None


def test_token_enabled(monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN_ROTATION_ENABLED", "true")
    future = time.strftime("%Y%m%d", time.gmtime(time.time() + 86400 * 30))
    monkeypatch.setenv(f"AGENT_TOKEN_{future}", "secret_future")
    info = load_token()
    assert info is not None
    assert info.token == "secret_future"
    assert info.days_until_expiry > 0


def test_token_expired(monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN_ROTATION_ENABLED", "true")
    past = time.strftime("%Y%m%d", time.gmtime(time.time() - 86400))
    monkeypatch.setenv(f"AGENT_TOKEN_{past}", "secret_past")
    assert load_token() is None
