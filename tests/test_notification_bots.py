"""Tests for the multi-bot notification store and its dashboard JSON API.

Covers the additive multi-bot feature: a JSON store holding a list of Telegram
bots + Slack App bots, exposed via /api/settings/bots. The legacy
notifications.env single channel is unaffected (see test_v6_hardening.py).
"""

from __future__ import annotations

import os
import stat
import threading

import pytest

from ipracticom_sweeper.dashboard import app
from ipracticom_sweeper.notify import store


@pytest.fixture
def bots_path(tmp_path, monkeypatch):
    p = tmp_path / "bots.json"
    monkeypatch.setenv("IPRACTICOM_SWEEPER_BOTS_FILE", str(p))
    return p


@pytest.fixture
def client(bots_path):
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# --- store unit --------------------------------------------------------------


def test_store_starts_empty(bots_path):
    assert store.masked_list() == {"telegram": [], "slack": []}
    assert store.has_any_bot() is False
    assert store.telegram_bots() == []


def test_store_missing_file_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("IPRACTICOM_SWEEPER_BOTS_FILE", str(tmp_path / "nope" / "bots.json"))
    # No file, no parent dir — must degrade to empty, never raise.
    assert store.masked_list() == {"telegram": [], "slack": []}
    assert store.has_any_bot() is False


def test_store_corrupt_file_degrades_to_empty(bots_path):
    bots_path.write_text("{not json", encoding="utf-8")
    assert store.telegram_bots() == []
    assert store.slack_bots() == []


def test_add_telegram_masks_secret_but_keeps_it_internally(bots_path):
    masked, err = store.add_bot("telegram", {"name": "Ops", "bot_token": "123:ABC", "chat_id": "-100"})
    assert err is None
    assert masked is not None
    assert masked["token_set"] is True
    assert masked["chat_id"] == "-100"
    assert "bot_token" not in masked  # secret never surfaced
    platform, raw = store.find_bot(masked["id"])
    assert platform == "telegram"
    assert raw["bot_token"] == "123:ABC"  # dispatch/test can still read it


def test_add_slack_requires_xox_token(bots_path):
    masked, err = store.add_bot("slack", {"bot_token": "nope", "channel": "#a"})
    assert masked is None
    assert err and "xox" in err


def test_add_telegram_requires_chat_id(bots_path):
    masked, err = store.add_bot("telegram", {"bot_token": "123:ABC", "chat_id": ""})
    assert masked is None
    assert err


def test_delete_removes_only_target(bots_path):
    a, _ = store.add_bot("telegram", {"bot_token": "1:a", "chat_id": "1"})
    b, _ = store.add_bot("telegram", {"bot_token": "2:b", "chat_id": "2"})
    assert store.delete_bot(a["id"]) is True
    remaining = [x["id"] for x in store.telegram_bots()]
    assert remaining == [b["id"]]
    assert store.delete_bot("no-such-id") is False


@pytest.mark.skipif(os.name != "posix", reason="perm bits are POSIX-only")
def test_written_file_is_0600(bots_path):
    store.add_bot("slack", {"bot_token": "xoxb-abc", "channel": "#c"})
    mode = stat.S_IMODE(os.stat(bots_path).st_mode)
    assert mode == 0o600


# --- dashboard API -----------------------------------------------------------


def test_api_bots_empty(client):
    rv = client.get("/api/settings/bots")
    assert rv.status_code == 200
    assert rv.get_json() == {"telegram": [], "slack": []}


def test_api_add_list_delete_slack_app(client):
    rv = client.post(
        "/api/settings/bots",
        json={"platform": "slack", "name": "Alerts", "bot_token": "xoxb-abc", "channel": "#alerts"},
    )
    assert rv.status_code == 201
    body = rv.get_json()
    assert body["ok"] is True
    bot = body["bot"]
    assert bot["kind"] == "app"
    assert bot["channel"] == "#alerts"
    assert "bot_token" not in bot

    listed = client.get("/api/settings/bots").get_json()
    assert len(listed["slack"]) == 1
    assert listed["slack"][0]["id"] == bot["id"]

    rv = client.delete(f"/api/settings/bots/{bot['id']}")
    assert rv.status_code == 200
    assert client.get("/api/settings/bots").get_json()["slack"] == []


def test_api_add_telegram(client):
    rv = client.post(
        "/api/settings/bots",
        json={"platform": "telegram", "name": "Primary", "bot_token": "123:ABC", "chat_id": "-100999"},
    )
    assert rv.status_code == 201
    listed = client.get("/api/settings/bots").get_json()
    assert listed["telegram"][0]["chat_id"] == "-100999"


def test_api_add_invalid_returns_400(client):
    rv = client.post("/api/settings/bots", json={"platform": "telegram", "bot_token": "", "chat_id": "1"})
    assert rv.status_code == 400
    assert rv.get_json()["ok"] is False


def test_api_delete_missing_returns_404(client):
    rv = client.delete("/api/settings/bots/does-not-exist")
    assert rv.status_code == 404


# --- concurrency (BUG-1: read-modify-write must be serialised) ----------------


def test_store_concurrent_add_does_not_lose_writes(bots_path):
    """N threads x M add_bot() must persist all N*M bots.

    Without a lock around the read-modify-write, concurrent adders read the
    same list and the last writer wins, silently dropping ~1/N of the bots.
    """
    n_threads, m_per_thread = 8, 5
    barrier = threading.Barrier(n_threads)
    errors: list[str] = []

    def worker(tid: int) -> None:
        barrier.wait()  # release all threads at once to maximise collisions
        for j in range(m_per_thread):
            _, err = store.add_bot(
                "telegram",
                {"name": f"bot-{tid}-{j}", "bot_token": f"T{tid}:{j}", "chat_id": "-1"},
            )
            if err:
                errors.append(err)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"unexpected add errors: {errors}"
    assert len(store.telegram_bots()) == n_threads * m_per_thread


def test_store_concurrent_add_and_delete_never_corrupts(bots_path):
    """Interleaved adds/deletes must never leave bots.json unparseable
    (the shared tmp path would corrupt without serialisation → bots_read_failed)."""
    seed, _ = store.add_bot("telegram", {"bot_token": "T:seed", "chat_id": "-1"})
    barrier = threading.Barrier(6)

    def adder(tid: int) -> None:
        barrier.wait()
        for j in range(5):
            store.add_bot("telegram", {"bot_token": f"A{tid}:{j}", "chat_id": "-1"})

    def deleter() -> None:
        barrier.wait()
        for _ in range(5):
            store.delete_bot(seed["id"])

    threads = [threading.Thread(target=adder, args=(i,)) for i in range(5)]
    threads.append(threading.Thread(target=deleter))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Store still reads cleanly (never raised, never degraded to empty on garbage).
    assert isinstance(store.telegram_bots(), list)
    assert store.masked_list()["slack"] == []


# --- Slack channel format validation (permissive: reject only clear garbage) --


@pytest.mark.parametrize("channel", ["#general", "#alerts-prod", "C01AB2CD3", "general", "team.ops_2"])
def test_slack_channel_valid_formats_accepted(bots_path, channel):
    masked, err = store.add_bot("slack", {"bot_token": "xoxb-abc", "channel": channel})
    assert err is None, f"{channel!r} should be accepted, got: {err}"
    assert masked["channel"] == channel


@pytest.mark.parametrize("channel", ["has space", "https://hooks.slack.com/services/x", "http://x"])
def test_slack_channel_garbage_rejected(bots_path, channel):
    masked, err = store.add_bot("slack", {"bot_token": "xoxb-abc", "channel": channel})
    assert masked is None
    assert err is not None
