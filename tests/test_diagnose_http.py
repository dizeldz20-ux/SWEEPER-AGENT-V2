"""Tests for HTTP endpoint diagnosis."""
from __future__ import annotations
from ipracticom_sweeper.monitor.http_check import HttpEndpointResult
from ipracticom_sweeper.diagnose.http_diagnose import diagnose_http


def test_diagnose_flags_5xx_as_crit():
    """5xx status codes are CRIT severity."""
    findings = {
        "metrics": {
            "endpoints": [
                HttpEndpointResult("api", "https://api.example.com", 500, 100, None).to_dict(),
            ]
        }
    }
    problems = diagnose_http(findings, {})
    assert len(problems) == 1
    assert problems[0].severity == "crit"
    assert "api" in problems[0].detail or "500" in problems[0].detail or "5xx" in problems[0].detail.lower()


def test_diagnose_flags_4xx_as_warn():
    """4xx is WARN (caller error, not outage)."""
    findings = {
        "metrics": {
            "endpoints": [
                HttpEndpointResult("api", "https://api.example.com", 404, 50, None).to_dict(),
            ]
        }
    }
    problems = diagnose_http(findings, {})
    assert len(problems) == 1
    assert problems[0].severity == "warn"


def test_diagnose_flags_slow_response_as_warn():
    """Response time > 2s is WARN."""
    findings = {
        "metrics": {
            "endpoints": [
                HttpEndpointResult("slow", "https://slow.example.com", 200, 5000, None).to_dict(),
            ]
        }
    }
    problems = diagnose_http(findings, {})
    assert len(problems) == 1
    assert problems[0].severity == "warn"
    assert "slow" in problems[0].detail.lower() or "response" in problems[0].detail.lower()


def test_diagnose_flags_connection_error_as_crit():
    """Transport errors (no status_code) are CRIT."""
    findings = {
        "metrics": {
            "endpoints": [
                HttpEndpointResult("down", "https://down.example.com", None, None, "ConnectError: connection refused").to_dict(),
            ]
        }
    }
    problems = diagnose_http(findings, {})
    assert len(problems) == 1
    assert problems[0].severity == "crit"


def test_diagnose_clean_200_no_problems():
    """200 with fast response = no problems."""
    findings = {
        "metrics": {
            "endpoints": [
                HttpEndpointResult("ok", "https://ok.example.com", 200, 100, None).to_dict(),
            ]
        }
    }
    problems = diagnose_http(findings, {})
    assert problems == []


def test_diagnose_uses_thresholds_from_rules():
    """Thresholds from rules dict override defaults."""
    findings = {
        "metrics": {
            "endpoints": [
                HttpEndpointResult("slow", "https://x.com", 200, 500, None).to_dict(),
            ]
        }
    }
    # Default 2000ms: WARN. With 1000ms threshold: also WARN.
    # With 10000ms threshold: should NOT warn on 500ms.
    rules = {"http": {"slow_response_ms": 10000}}
    problems = diagnose_http(findings, rules)
    assert problems == []
