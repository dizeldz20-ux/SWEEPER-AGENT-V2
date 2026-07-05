"""v1.5.18 post-review hardening.

Covers three defects found while verifying the v1.5.18 push that the earlier
review missed:

* A. Rate-limit ``_RL_MAX_KEYS`` was a soft cap — it only reclaimed *dead*
     buckets, so a live burst of unique keys grew the map without bound.
     Now a brand-new key is refused once the map is over budget.
* B. ``send_admin_alert`` promised plain text but sent Markdown first, forcing
     a 400 + retry round-trip for any content with Markdown metacharacters.
* (the Telegram 400 -> plain fallback for the *normal* alert path is also
  pinned here so it never silently regresses.)
"""
from __future__ import annotations

import asyncio

import pytest

from ipracticom_sweeper.agent_api import create_app
from ipracticom_sweeper.notify import legacy


# ---------------------------------------------------------------------------
# A. Rate-limit hard cap
# ---------------------------------------------------------------------------

def test_rate_limit_hard_cap_refuses_new_key_over_budget(monkeypatch):
    """Once the bucket map is over ``_RL_MAX_KEYS`` and no dead buckets can be
    reclaimed (a live burst), a *new* key is refused with 429 — the map must
    not grow unbounded."""
    monkeypatch.setenv("AGENT_API_RATELIMIT", "1")
    monkeypatch.setenv("AGENT_API_TRUST_XFF", "1")       # key = X-Forwarded-For
    monkeypatch.setenv("AGENT_API_RATELIMIT_MAX_KEYS", "2")
    monkeypatch.setenv("AGENT_API_RATELIMIT_HEALTHZ", "1000")  # per-key never trips
    c = create_app().test_client()

    # Three distinct fresh keys -> map size climbs to 3 (one past the cap of 2).
    for i in range(3):
        r = c.get("/healthz", headers={"X-Forwarded-For": f"10.0.0.{i}"})
        assert r.status_code == 200, i

    # A fourth, unseen key: map(3) > cap(2), every bucket is fresh so nothing
    # is reclaimed -> the new key is refused.
    r = c.get("/healthz", headers={"X-Forwarded-For": "10.0.0.99"})
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "60"


def test_rate_limit_hard_cap_keeps_serving_existing_key(monkeypatch):
    """The hard cap must never drop a key that is already tracked mid-window —
    only *new* keys are refused when over budget."""
    monkeypatch.setenv("AGENT_API_RATELIMIT", "1")
    monkeypatch.setenv("AGENT_API_TRUST_XFF", "1")
    monkeypatch.setenv("AGENT_API_RATELIMIT_MAX_KEYS", "2")
    monkeypatch.setenv("AGENT_API_RATELIMIT_HEALTHZ", "1000")
    c = create_app().test_client()

    for i in range(3):
        assert c.get("/healthz", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 200
    # New key refused, but an already-seen key is still served.
    assert c.get("/healthz", headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429
    assert c.get("/healthz", headers={"X-Forwarded-For": "10.0.0.0"}).status_code == 200


# ---------------------------------------------------------------------------
# B. Telegram plain-text behaviour
# ---------------------------------------------------------------------------

def _install_fake_telegram(monkeypatch, codes):
    """Replace httpx.AsyncClient so posts are recorded and status codes scripted.

    Returns the list that receives each POST's json body.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    posts: list[dict] = []

    class _Resp:
        def __init__(self, code):
            self.status_code = code

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def post(self, url, json=None):
            posts.append(json)
            idx = len(posts) - 1
            return _Resp(codes[idx] if idx < len(codes) else 200)

        async def aclose(self):
            pass

    monkeypatch.setattr(legacy.httpx, "AsyncClient", _Client)
    return posts


def test_send_telegram_400_markdown_falls_back_to_plain(monkeypatch):
    """A Markdown 400 ('can't parse entities') must trigger a plain-text resend
    so a real alert is never silently dropped."""
    posts = _install_fake_telegram(monkeypatch, [400, 200])
    ok = asyncio.run(legacy._send_telegram("alert with _stray *markdown* [chars]"))
    assert ok is True
    assert len(posts) == 2
    assert posts[0].get("parse_mode") == "Markdown"
    assert "parse_mode" not in posts[1]          # retry is plain text


def test_send_admin_alert_sends_plain_text_first(monkeypatch):
    """``send_admin_alert`` carries arbitrary content — it must go out as plain
    text on the first attempt, with no Markdown round-trip."""
    posts = _install_fake_telegram(monkeypatch, [200])

    async def _no_slack(*a, **k):
        return False

    monkeypatch.setattr(legacy, "_send_slack", _no_slack)
    res = asyncio.run(legacy.send_admin_alert("watchdog: restart-storm _*[ host_a"))
    assert res["telegram"] is True
    assert len(posts) == 1                        # no 400 + retry round-trip
    assert "parse_mode" not in posts[0]           # plain from the very first post
