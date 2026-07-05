"""Sprint 20.3 — built-in rate limiting + localhost-only CORS."""
from __future__ import annotations

import os
import time
from unittest import mock

import pytest

from ipracticom_sweeper.agent_api import create_app


@pytest.fixture
def app(monkeypatch):
    """Fresh Flask app per test, rate-limit forced ON, clean env."""
    monkeypatch.setenv("AGENT_API_RATELIMIT", "1")
    monkeypatch.setenv("AGENT_API_RATELIMIT_API", "5")     # small for tests
    monkeypatch.setenv("AGENT_API_RATELIMIT_HEALTHZ", "3")
    monkeypatch.setenv("AGENT_API_CORS_ORIGINS", "")
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Rate limit — healthz
# ---------------------------------------------------------------------------

def test_20_3_healthz_rate_limit_under_cap(client):
    """3 calls succeed (HEALTHZ cap), 4th returns 429."""
    for _ in range(3):
        r = client.get("/healthz")
        assert r.status_code == 200
    r = client.get("/healthz")
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "60"


def test_20_3_healthz_remaining_header(client):
    """X-RateLimit-Remaining decrements and is present on success."""
    r1 = client.get("/healthz")
    r2 = client.get("/healthz")
    assert int(r1.headers["X-RateLimit-Remaining"]) == 2
    assert int(r2.headers["X-RateLimit-Remaining"]) == 1


# ---------------------------------------------------------------------------
# Rate limit — disabled via env
# ---------------------------------------------------------------------------

def test_20_3_rate_limit_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_API_RATELIMIT", "0")
    monkeypatch.setenv("AGENT_API_RATELIMIT_HEALTHZ", "2")
    a = create_app()
    c = a.test_client()
    # 5 calls all succeed because limit is off
    for _ in range(5):
        r = c.get("/healthz")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# CORS — default allowlist (localhost only)
# ---------------------------------------------------------------------------

def test_20_3_cors_localhost_allowed(client):
    r = client.get("/healthz", headers={"Origin": "http://localhost:5000"})
    assert r.headers.get("Access-Control-Allow-Origin") == "http://localhost:5000"


def test_20_3_cors_127_allowed(client):
    r = client.get("/healthz", headers={"Origin": "http://127.0.0.1"})
    assert r.headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1"


def test_20_3_cors_external_origin_blocked(client):
    """Random domain must NOT get Access-Control-Allow-Origin set."""
    r = client.get("/healthz", headers={"Origin": "https://evil.example.com"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_20_3_cors_preflight_returns_200(client):
    """OPTIONS request to any path returns 200 with CORS headers for allowed origin.

    Flask handles OPTIONS automatically and returns 200 + Allow header. The
    CORS headers we care about (ACAO, ACAM, ACAH) come from after_request.
    """
    r = client.options(
        "/api/snapshot",
        headers={
            "Origin": "http://localhost:5000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)  # both acceptable per CORS spec
    assert r.headers.get("Access-Control-Allow-Origin") == "http://localhost:5000"
    assert "GET" in r.headers.get("Access-Control-Allow-Methods", "")


def test_20_3_cors_custom_origin_via_env(monkeypatch):
    """AGENT_API_CORS_ORIGINS adds to allowlist."""
    monkeypatch.setenv("AGENT_API_CORS_ORIGINS", "https://dash.example.com")
    a = create_app()
    c = a.test_client()
    r = c.get("/healthz", headers={"Origin": "https://dash.example.com"})
    assert r.headers.get("Access-Control-Allow-Origin") == "https://dash.example.com"


def test_20_3_cors_no_origin_header_no_cors_headers(client):
    """If Origin is absent, no CORS headers attached (server-to-server)."""
    r = client.get("/healthz")
    assert "Access-Control-Allow-Origin" not in r.headers


# ---------------------------------------------------------------------------
# X-Forwarded-For respected for per-IP buckets
# ---------------------------------------------------------------------------

def test_20_3_rate_limit_per_ip_via_xff(monkeypatch):
    """Behind a trusted proxy (AGENT_API_TRUST_XFF=1) each XFF value gets its
    own bucket (per-IP isolation)."""
    monkeypatch.setenv("AGENT_API_RATELIMIT", "1")
    monkeypatch.setenv("AGENT_API_RATELIMIT_HEALTHZ", "2")
    monkeypatch.setenv("AGENT_API_TRUST_XFF", "1")
    a = create_app()
    c = a.test_client()
    # 10.0.0.1: first two calls succeed
    r1 = c.get("/healthz", headers={"X-Forwarded-For": "10.0.0.1"})
    r2 = c.get("/healthz", headers={"X-Forwarded-For": "10.0.0.1"})
    # Third call from same IP → 429 (bucket full at 2)
    r3 = c.get("/healthz", headers={"X-Forwarded-For": "10.0.0.1"})
    # Different IP — fresh bucket
    r4 = c.get("/healthz", headers={"X-Forwarded-For": "10.0.0.2"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert r4.status_code == 200


def test_20_3_xff_ignored_by_default(monkeypatch):
    """Without AGENT_API_TRUST_XFF, a spoofed X-Forwarded-For cannot mint a
    fresh bucket — the limit keys on the real peer, so rotating XFF still 429s."""
    monkeypatch.setenv("AGENT_API_RATELIMIT", "1")
    monkeypatch.setenv("AGENT_API_RATELIMIT_HEALTHZ", "2")
    monkeypatch.delenv("AGENT_API_TRUST_XFF", raising=False)
    a = create_app()
    c = a.test_client()
    r1 = c.get("/healthz", headers={"X-Forwarded-For": "10.0.0.1"})
    r2 = c.get("/healthz", headers={"X-Forwarded-For": "10.0.0.2"})
    r3 = c.get("/healthz", headers={"X-Forwarded-For": "10.0.0.3"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429  # same real peer despite rotating XFF
