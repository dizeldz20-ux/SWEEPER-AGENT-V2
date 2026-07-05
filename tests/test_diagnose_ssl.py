"""Tests for SSL cert diagnosis."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from ipracticom_sweeper.diagnose.ssl_diagnose import diagnose_ssl


def _cert(days_remaining: int | None, error: str | None = None) -> dict:
    if days_remaining is None:
        expires_at = None
    else:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days_remaining)).isoformat()
    return {
        "host": "example.com",
        "port": 443,
        "subject": "example.com",
        "issuer": "Let's Encrypt",
        "expires_at": expires_at,
        "days_remaining": days_remaining,
        "is_self_signed": False,
        "error": error,
    }


def test_diagnose_flags_expired_cert_as_crit():
    """Cert already expired (negative days) = CRIT."""
    findings = {"metrics": {"certificates": [_cert(-1)]}}
    problems = diagnose_ssl(findings, {})
    assert len(problems) == 1
    assert problems[0].severity == "crit"


def test_diagnose_flags_5_days_remaining_as_crit():
    """Within 7 days default threshold = CRIT."""
    findings = {"metrics": {"certificates": [_cert(5)]}}
    problems = diagnose_ssl(findings, {})
    assert len(problems) == 1
    assert problems[0].severity == "crit"


def test_diagnose_flags_15_days_as_warn():
    """Within 30 days default threshold = WARN."""
    findings = {"metrics": {"certificates": [_cert(15)]}}
    problems = diagnose_ssl(findings, {})
    assert len(problems) == 1
    assert problems[0].severity == "warn"


def test_diagnose_clean_60_days_no_problems():
    """60 days remaining = no problems."""
    findings = {"metrics": {"certificates": [_cert(60)]}}
    problems = diagnose_ssl(findings, {})
    assert problems == []


def test_diagnose_connection_error_is_crit():
    """Connection error = CRIT (can't check cert at all)."""
    findings = {"metrics": {"certificates": [_cert(None, error="gaierror")]}}
    problems = diagnose_ssl(findings, {})
    assert len(problems) == 1
    assert problems[0].severity == "crit"


def test_diagnose_respects_thresholds_from_rules():
    """Override thresholds via rules dict."""
    findings = {"metrics": {"certificates": [_cert(15)]}}
    # Default: warn_days=30 → 15d = warn.
    # Override warn_days=100, crit_days=50 → 15d = crit (more urgent).
    rules = {"ssl": {"warn_days": 100, "crit_days": 50}}
    problems = diagnose_ssl(findings, rules)
    assert len(problems) == 1
    assert problems[0].severity == "crit"
