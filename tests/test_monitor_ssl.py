"""Tests for SSL certificate expiry monitor."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from ipracticom_sweeper.monitor.ssl_check import (
    collect_ssl_certs,
    SslCertResult,
)


def test_collect_returns_empty_when_no_hosts():
    """No hosts configured = empty result, not an error."""
    results = collect_ssl_certs([])
    assert results == []


def test_collect_parses_cert_expiry():
    """Mock SSL socket returns valid cert with expiry date."""
    fake_cert = {
        "subject": ((("commonName", "example.com"),),),
        "issuer": ((("commonName", "Let's Encrypt"),),),
        "notAfter": "Dec 31 23:59:59 2026 GMT",
    }
    fake_issuer = ((("commonName", "Let's Encrypt"),),)
    fake_subject = ((("commonName", "example.com"),),)

    # The code uses socket.create_connection() + ssl.create_default_context();
    # patch those exact names, otherwise the mocks miss and the test makes a
    # real network call to example.com:443 (fails with gaierror offline).
    with patch("ipracticom_sweeper.monitor.ssl_check.socket.create_connection") as mock_conn:
        with patch("ipracticom_sweeper.monitor.ssl_check.ssl.create_default_context") as mock_ctx:
            mock_sock = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_sock
            mock_ctx_inst = MagicMock()
            mock_ctx.return_value = mock_ctx_inst
            mock_ssock = MagicMock()
            mock_ctx_inst.wrap_socket.return_value.__enter__.return_value = mock_ssock
            mock_ssock.getpeercert.return_value = {
                "subject": fake_subject,
                "issuer": fake_issuer,
                "notAfter": "Dec 31 23:59:59 2026 GMT",
            }
            results = collect_ssl_certs([{"host": "example.com", "port": 443}])

    assert len(results) == 1
    assert results[0].host == "example.com"
    assert results[0].issuer == "Let's Encrypt"
    # Dec 31 2026 is the expected expiry
    assert results[0].expires_at.year == 2026
    assert results[0].expires_at.month == 12
    assert results[0].expires_at.day == 31
    assert results[0].days_remaining > 0


def test_collect_handles_connection_error():
    """If host unreachable, result has error, not exception."""
    import socket
    with patch("ipracticom_sweeper.monitor.ssl_check.socket.create_connection", side_effect=socket.gaierror("Name or service not known")):
        results = collect_ssl_certs([{"host": "nonexistent.invalid", "port": 443}])

    assert len(results) == 1
    assert results[0].error is not None
    assert results[0].days_remaining is None
