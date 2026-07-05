"""Sprint 20.2 — log_suppressed helper."""
from __future__ import annotations

import logging
from unittest import mock

import pytest

from ipracticom_sweeper._log import (
    log_suppressed,
    install_root_handler,
    logger as helper_logger,
)


def test_20_2_log_suppressed_emits_warning(caplog):
    """Default level is WARNING and the context name appears in the log."""
    with caplog.at_level(logging.WARNING, logger="ipracticom_sweeper.errors"):
        try:
            raise ValueError("bad value")
        except ValueError as exc:
            log_suppressed("module.thing", exc)
    assert any("module.thing" in r.getMessage() for r in caplog.records)
    assert any("ValueError" in r.getMessage() for r in caplog.records)


def test_20_2_log_suppressed_respects_debug_level(caplog):
    """With level=DEBUG, the record should still be emitted at DEBUG."""
    with caplog.at_level(logging.DEBUG, logger="ipracticom_sweeper.errors"):
        try:
            raise OSError("disk gone")
        except OSError as exc:
            log_suppressed("collector.x", exc, level=logging.DEBUG)
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("collector.x" in r.getMessage() for r in debug_records)


def test_20_2_log_suppressed_appends_extras(caplog):
    """extras dict shows up in the formatted message as key=value pairs."""
    with caplog.at_level(logging.WARNING, logger="ipracticom_sweeper.errors"):
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            log_suppressed("ctx.x", exc, extras={"host_id": "i-0abc", "attempt": 3})
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "host_id='i-0abc'" in msg
    assert "attempt=3" in msg


def test_20_2_log_suppressed_handles_empty_str_exc(caplog):
    """str(exc) == '' must not crash the formatter."""
    class NoStrErr(Exception):
        def __str__(self):
            return ""
    with caplog.at_level(logging.WARNING, logger="ipracticom_sweeper.errors"):
        try:
            raise NoStrErr()
        except NoStrErr as exc:
            log_suppressed("weird.path", exc)
    assert any("weird.path" in r.getMessage() for r in caplog.records)


def test_20_2_log_suppressed_traceback_at_debug(caplog):
    """Traceback is captured at DEBUG level for later inspection."""
    with caplog.at_level(logging.DEBUG, logger="ipracticom_sweeper.errors"):
        try:
            raise KeyError("nope")
        except KeyError as exc:
            log_suppressed("deep.path", exc)
    tb_records = [r for r in caplog.records if r.exc_info]
    assert tb_records, "expected at least one record with exc_info"
    assert "deep.path" in tb_records[0].getMessage()


def test_20_2_install_root_handler_idempotent():
    """Calling install_root_handler twice must not double-attach."""
    install_root_handler()
    n1 = len(logging.getLogger().handlers)
    install_root_handler()
    n2 = len(logging.getLogger().handlers)
    assert n1 == n2
