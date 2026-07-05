"""Tests for HTTP endpoint healthcheck collector."""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import httpx

from ipracticom_sweeper.monitor.http_check import (
    collect_http_endpoints,
    HttpEndpointResult,
)


# ---- Test 1: returns list of results for all endpoints ----
def test_collect_returns_list_of_results():
    """Should return one HttpEndpointResult per endpoint."""
    endpoints = [
        {"url": "https://example.com", "name": "example"},
        {"url": "https://google.com", "name": "google"},
    ]

    fake_response = MagicMock(spec=httpx.Response)
    fake_response.status_code = 200
    fake_response.elapsed = MagicMock()
    fake_response.elapsed.total_seconds.return_value = 0.123

    with patch("httpx.get", return_value=fake_response):
        results = collect_http_endpoints(endpoints)

    assert len(results) == 2
    assert all(isinstance(r, HttpEndpointResult) for r in results)
    assert results[0].name == "example"
    assert results[0].url == "https://example.com"
    assert results[0].status_code == 200
    assert results[0].response_time_ms == 123


# ---- Test 2: handles connection errors gracefully ----
def test_collect_handles_connection_error():
    """If endpoint is unreachable, result has error, not exception."""
    endpoints = [{"url": "https://nonexistent.invalid", "name": "broken"}]

    with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        results = collect_http_endpoints(endpoints)

    assert len(results) == 1
    assert results[0].status_code is None
    assert results[0].error is not None
    assert "refused" in results[0].error.lower() or "connect" in results[0].error.lower()


# ---- Test 3: captures 5xx as error ----
def test_collect_captures_5xx_status():
    """5xx responses are returned with the status code, not as errors."""
    endpoints = [{"url": "https://example.com/api", "name": "api"}]

    fake_response = MagicMock(spec=httpx.Response)
    fake_response.status_code = 503
    fake_response.elapsed = MagicMock()
    fake_response.elapsed.total_seconds.return_value = 0.5

    with patch("httpx.get", return_value=fake_response):
        results = collect_http_endpoints(endpoints)

    assert results[0].status_code == 503
    assert results[0].error is None  # not a transport error
