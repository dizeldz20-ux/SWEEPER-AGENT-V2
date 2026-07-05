"""SSL certificate expiry monitor.

Connects to configured SSL/TLS endpoints, retrieves the peer certificate,
and reports subject, issuer, expiry date, and days remaining. Used to
catch forgotten cert renewals before they cause outages.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
import socket
import ssl


@dataclass
class SslCertResult:
    """Result of inspecting a single SSL cert."""

    host: str
    port: int
    subject: str | None
    issuer: str | None
    expires_at: datetime | None
    days_remaining: int | None
    is_self_signed: bool
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "subject": self.subject,
            "issuer": self.issuer,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "days_remaining": self.days_remaining,
            "is_self_signed": self.is_self_signed,
            "error": self.error,
        }


def _format_name(name_tuple: tuple | None) -> str | None:
    """Convert ((('CN', 'example.com'),),) → 'example.com'."""
    if not name_tuple:
        return None
    try:
        # name_tuple is a tuple of tuples of tuples
        parts = []
        for rdn in name_tuple:
            for attr in rdn:
                if len(attr) == 2 and attr[0] == "commonName":
                    parts.append(attr[1])
        return ", ".join(parts) if parts else str(name_tuple)
    except Exception:
        return str(name_tuple)


def _parse_cert_expiry(not_after: str) -> datetime | None:
    """Parse 'Dec 31 23:59:59 2026 GMT' style dates from ssl.getpeercert()."""
    if not not_after:
        return None
    try:
        # Python's ssl module returns this format
        return parsedate_to_datetime(not_after)
    except Exception:
        return None


def _check_one(host: str, port: int, timeout: float) -> SslCertResult:
    """Inspect the SSL cert of a single host:port."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
    except (socket.gaierror, ConnectionRefusedError, socket.timeout, OSError) as e:
        return SslCertResult(
            host=host, port=port,
            subject=None, issuer=None,
            expires_at=None, days_remaining=None,
            is_self_signed=False,
            error=f"{type(e).__name__}: {e}",
        )
    except Exception as e:
        return SslCertResult(
            host=host, port=port,
            subject=None, issuer=None,
            expires_at=None, days_remaining=None,
            is_self_signed=False,
            error=f"{type(e).__name__}: {e}",
        )

    subject = _format_name(cert.get("subject"))
    issuer = _format_name(cert.get("issuer"))
    expires_at = _parse_cert_expiry(cert.get("notAfter"))

    days_remaining: int | None = None
    if expires_at:
        # Make sure we compare timezone-aware datetimes
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        delta = expires_at - datetime.now(timezone.utc)
        days_remaining = delta.days

    is_self_signed = subject is not None and subject == issuer

    return SslCertResult(
        host=host, port=port,
        subject=subject, issuer=issuer,
        expires_at=expires_at, days_remaining=days_remaining,
        is_self_signed=is_self_signed,
        error=None,
    )


def collect_ssl_certs(hosts: list[dict]) -> list[SslCertResult]:
    """Inspect SSL certs for a list of {host, port?, timeout?} dicts.

    Returns a list of SslCertResult, one per host, in the same order.
    Connection errors are caught and reported, not raised.
    """
    results: list[SslCertResult] = []
    for h in hosts:
        host = h.get("host", "")
        port = h.get("port", 443)
        timeout = h.get("timeout", 5.0)
        if not host:
            continue
        results.append(_check_one(host, port, timeout))
    return results


def evaluate(values: dict, rules: dict) -> str:
    """Return overall status: 'ok' | 'warn' | 'crit'.

    'crit' if any cert expiring within 7 days, 'warn' if within 30 days.
    """
    certs = values.get("certificates", [])
    if not certs:
        return "ok"
    warn_days = rules.get("ssl", {}).get("warn_days", 30)
    crit_days = rules.get("ssl", {}).get("crit_days", 7)
    has_crit = any(
        c.get("error") or (c.get("days_remaining") is not None and c["days_remaining"] < crit_days)
        for c in certs
    )
    has_warn = any(
        c.get("days_remaining") is not None and crit_days <= c["days_remaining"] < warn_days
        for c in certs
    )
    if has_crit:
        return "crit"
    if has_warn:
        return "warn"
    return "ok"
