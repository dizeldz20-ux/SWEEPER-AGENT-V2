"""Silent-by-default gate: severity floor, dedup "until resolved", throttle.

These cover notify.suppression (the pure decision logic) and its wiring into
notify_pipeline_result (the real dispatch path). The headline guarantee the
project cares about is test_silent_when_no_issues / _silent_when_green: a clean
run sends NOTHING — no "all clear", no call to any sender.
"""
from __future__ import annotations

import asyncio

import pytest

from ipracticom_sweeper.notify import legacy
from ipracticom_sweeper.notify import suppression as sup

_LABELS = {5: "green", 4: "yellow", 3: "orange", 2: "red", 1: "black"}


def _payload(defcon: int, kinds, server: str = "srv1") -> dict:
    """A PipelineResult-shaped dict with the given defcon + problem kinds."""
    sev = "warn" if defcon >= 4 else "crit"
    problems = [{"kind": k, "severity": sev, "detail": f"{k} bad"} for k in kinds]
    return {
        "server": server,
        "defcon": defcon,
        "defcon_label": _LABELS[defcon],
        "diagnosis": {"summary": "test", "problems": problems},
    }


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    """Isolate the notify-state file (and bots store) to a tmp dir."""
    p = tmp_path / "notify-state.json"
    monkeypatch.setenv("IPRACTICOM_SWEEPER_NOTIFY_STATE_FILE", str(p))
    monkeypatch.setenv("IPRACTICOM_SWEEPER_BOTS_FILE", str(tmp_path / "bots.json"))
    return p


# --- Pure decision logic (should_notify / mark_notified) --------------------


def test_silent_when_no_issues(state_path):
    # Green run: nothing to report -> no send.
    assert sup.should_notify(_payload(5, [])).send is False


def test_new_problem_sends(state_path):
    assert sup.should_notify(_payload(4, ["disk"])).send is True


def test_duplicate_suppressed(state_path):
    p = _payload(4, ["disk"])
    assert sup.should_notify(p).send is True
    sup.mark_notified(p)
    # Same problem, same severity -> silent until it changes/resolves.
    assert sup.should_notify(p).send is False


def test_escalation_sends(state_path):
    p4 = _payload(4, ["disk"])
    assert sup.should_notify(p4).send is True
    sup.mark_notified(p4)
    assert sup.should_notify(p4).send is False
    # Same problem gets worse (DEFCON 4 -> 2) -> alert again.
    p2 = _payload(2, ["disk"])
    assert sup.should_notify(p2).send is True


def test_partial_improvement_is_quiet(state_path):
    p_ab = _payload(4, ["disk", "cpu"])
    assert sup.should_notify(p_ab).send is True
    sup.mark_notified(p_ab)
    # cpu cleared, disk unchanged -> no NEW problem -> stay silent (no "better!").
    assert sup.should_notify(_payload(4, ["disk"])).send is False


def test_resolution_then_recurrence_sends(state_path):
    p = _payload(4, ["disk"])
    assert sup.should_notify(p).send is True
    sup.mark_notified(p)
    # Clears to green (resets state, silently).
    assert sup.should_notify(_payload(5, [])).send is False
    # Same problem returns after resolution -> treated as new -> alerts.
    assert sup.should_notify(p).send is True


def test_recurrence_after_partial_clear_sends(state_path):
    p_ab = _payload(4, ["disk", "cpu"])
    sup.should_notify(p_ab)
    sup.mark_notified(p_ab)
    # cpu clears (forgotten), disk stays.
    sup.should_notify(_payload(4, ["disk"]))
    # cpu comes back -> it's new again -> alert.
    assert sup.should_notify(_payload(4, ["disk", "cpu"])).send is True


def test_rate_limit_throttles_warn(state_path):
    now = 1000.0
    for i in range(sup.RATE_MAX_SENDS):
        p = _payload(4, [f"k{i}"])
        assert sup.should_notify(p, now=now).send is True
        sup.mark_notified(p, now=now)
        now += 1.0
    # One warn-level alert over the cap within the window -> throttled.
    assert sup.should_notify(_payload(4, ["over"]), now=now).send is False


def test_critical_bypasses_rate_limit(state_path):
    now = 1000.0
    for i in range(sup.RATE_MAX_SENDS):
        p = _payload(4, [f"k{i}"])
        sup.should_notify(p, now=now)
        sup.mark_notified(p, now=now)
        now += 1.0
    # Critical (DEFCON <= 3) is never throttled.
    assert sup.should_notify(_payload(2, ["boom"]), now=now).send is True


def test_rate_window_expires(state_path):
    now = 1000.0
    for i in range(sup.RATE_MAX_SENDS):
        p = _payload(4, [f"k{i}"])
        sup.should_notify(p, now=now)
        sup.mark_notified(p, now=now)
        now += 1.0
    # Past the window, the count resets and warn-level alerts flow again.
    now += sup.RATE_WINDOW_SECONDS + 1.0
    assert sup.should_notify(_payload(4, ["fresh"]), now=now).send is True


def test_force_bypasses_and_leaves_state_untouched(state_path):
    # force sends even on green, and must not write dedup state.
    assert sup.should_notify(_payload(5, []), force=True).send is True
    assert not state_path.exists()


def test_state_persists_across_reads(state_path):
    p = _payload(4, ["disk"])
    sup.should_notify(p)
    sup.mark_notified(p)
    assert state_path.exists()
    # A fresh evaluation reads state back from disk (simulates next oneshot run).
    assert sup.should_notify(p).send is False


# --- Wiring into the real dispatch path (notify_pipeline_result) ------------


def _wire_channels(monkeypatch):
    monkeypatch.setattr("ipracticom_sweeper.config.notifications_enabled", lambda: True)
    monkeypatch.setattr("ipracticom_sweeper.config.slack_webhook_url", lambda: "")
    monkeypatch.setattr("ipracticom_sweeper.config.telegram_bot_token", lambda: "T")
    monkeypatch.setattr("ipracticom_sweeper.config.telegram_chat_id", lambda: "C")


def _spy_telegram(monkeypatch):
    calls: list[str] = []

    async def fake_tg(text, markdown=True):
        calls.append(text)
        return True

    monkeypatch.setattr(legacy, "_send_telegram", fake_tg)
    return calls


def test_pipeline_result_silent_when_green(state_path, monkeypatch):
    calls = _spy_telegram(monkeypatch)
    _wire_channels(monkeypatch)
    res = asyncio.run(legacy.notify_pipeline_result(_payload(5, [])))
    assert res == {}
    assert calls == []  # no sender invoked on a clean run


def test_pipeline_result_sends_then_dedupes(state_path, monkeypatch):
    calls = _spy_telegram(monkeypatch)
    _wire_channels(monkeypatch)
    res = asyncio.run(legacy.notify_pipeline_result(_payload(4, ["disk"])))
    assert res.get("telegram") is True
    assert len(calls) == 1
    # Identical follow-up run is suppressed by the gate.
    res2 = asyncio.run(legacy.notify_pipeline_result(_payload(4, ["disk"])))
    assert res2 == {}
    assert len(calls) == 1


def test_pipeline_result_force_sends_even_green(state_path, monkeypatch):
    calls = _spy_telegram(monkeypatch)
    _wire_channels(monkeypatch)
    res = asyncio.run(legacy.notify_pipeline_result(_payload(5, []), force=True))
    assert res.get("telegram") is True
    assert len(calls) == 1
