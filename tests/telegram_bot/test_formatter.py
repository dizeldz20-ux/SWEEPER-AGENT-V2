"""Tests for telegram_bot.formatter — HTML message formatting.

Follows the patterns from the public `telegram-bot-builder` skill:
visual hierarchy (header → summary → details), HTML escape, smart
truncation. Output is always HTML-safe Telegram.
"""
import pytest

from ipracticom_sweeper.telegram_bot.formatter import (
    escape_html,
    format_snapshot,
    format_problems,
    format_history,
    format_security,
    format_error,
    DEFCON_EMOJI,
)


def test_escape_html_basic():
    """HTML metacharacters are escaped."""
    assert escape_html("a < b") == "a &lt; b"
    assert escape_html('"hello" & "world"') == "&quot;hello&quot; &amp; &quot;world&quot;"


def test_escape_html_passthrough_safe():
    """Safe text passes through unchanged."""
    assert escape_html("hello world") == "hello world"
    assert escape_html("") == ""


def test_defcon_emoji_mapping():
    """DEFCON 1-5 map to known visual signals."""
    assert DEFCON_EMOJI[1] == "🚨"
    assert DEFCON_EMOJI[5] == "🟢"
    assert DEFCON_EMOJI[3] == "🟡"


def test_format_snapshot_minimal():
    """Snapshot with no problems renders a clean status line."""
    text = format_snapshot({"defcon": 5, "modules": {"cpu": {"status": "ok"}}})
    assert "🟢" in text
    assert "DEFCON 5" in text
    assert "<b>" in text  # bold header


def test_format_snapshot_with_problems():
    """Snapshot with active problems lists them."""
    snap = {
        "defcon": 2,
        "modules": {
            "disk": {"status": "warn", "details": "92% full on /var"},
            "memory": {"status": "ok"},
        },
    }
    text = format_snapshot(snap)
    assert "🔴" in text
    assert "DEFCON 2" in text
    assert "92% full on /var" in text


def test_format_snapshot_truncates_long_problem_lists():
    """Snapshots with >5 problem modules get a '+N more' suffix."""
    snap = {
        "defcon": 3,
        "modules": {f"mod_{i}": {"status": "warn", "details": f"issue {i}"} for i in range(8)},
    }
    text = format_snapshot(snap)
    assert "+3 more" in text or "+3" in text


def test_format_problems_empty():
    """No problems returns a happy-state line."""
    text = format_problems({"modules": {}})
    assert "אין בעיות" in text or "no problems" in text.lower()


def test_format_problems_with_active_issues():
    """Active problems are listed with their severity."""
    snap = {
        "modules": {
            "cpu": {"status": "crit", "details": "load 18.0"},
            "disk": {"status": "warn", "details": "85%"},
        }
    }
    text = format_problems(snap)
    assert "load 18.0" in text
    assert "85%" in text
    # Should be scannable — bold for severity
    assert "<b>" in text


def test_format_history_empty():
    """Empty history shows a 'no data' line."""
    text = format_history("defcon", [])
    assert "אין נתונים" in text or "no data" in text.lower()


def test_format_history_with_samples():
    """Non-empty history renders a summary."""
    samples = [
        {"ts": 1700000000, "value": 3.0},
        {"ts": 1700003600, "value": 4.0},
        {"ts": 1700007200, "value": 5.0},
    ]
    text = format_history("defcon", samples)
    assert "defcon" in text
    assert "3 samples" in text or "3 דגימות" in text or "3" in text
    assert "<b>" in text


def test_format_security_clean():
    """Empty security report shows a clean state."""
    text = format_security({
        "ssh_drift": [],
        "suid_changes": [],
        "ports": [{"port": 22, "service": "ssh"}],
    })
    assert "SSH" in text
    assert "SUID" in text
    assert "ports" in text.lower() or "פורטים" in text


def test_format_error_user_friendly():
    """Generic error produces a Hebrew user-friendly message."""
    text = format_error("agent unreachable")
    assert "שגיאה" in text or "error" in text.lower()
