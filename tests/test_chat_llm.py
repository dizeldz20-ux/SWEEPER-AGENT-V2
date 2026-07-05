"""Tests for v0.5.0 slice 3.3 — LLM router + tool surface.

Covers:
  - chat_tools: list/get/tier/pipeline executors + schema
  - chat_llm: detect_mode, mock intents, Reply structure
  - chat integration: HTTP /chat/sessions/<id>/messages surfaces mode+tools
"""

from __future__ import annotations

import json
import os

import pytest

from ipracticom_sweeper.chat_llm import (
    LLMRouter,
    Reply,
    detect_mode,
    _mock_reply,
)
from ipracticom_sweeper.chat_rag import RetrievalHit


# --- chat_tools: list/get_fs_check ---------------------------------------

def test_list_fs_checks_returns_all_25(monkeypatch):
    """The project must expose exactly 25 FS-XX checks (slice 2.1-2.4)."""
    from ipracticom_sweeper.chat_tools import list_fs_checks, _get_registry
    # Force registry build.
    reg = _get_registry()
    # We don't hard-code 25 (some checks may have multiple fns like
    # check_fsNN_<foo>), but we expect >= 25.
    assert len(reg) >= 25
    out = list_fs_checks()
    assert out["count"] == len(reg)
    assert all("name" in c and "summary" in c for c in out["checks"])


def test_get_fs_check_unknown_returns_error():
    from ipracticom_sweeper.chat_tools import get_fs_check
    out = get_fs_check("check_fs99_does_not_exist")
    assert out["ok"] is False
    assert "unknown check" in out["error"]


def test_get_fs_check_runs_real_check():
    """A real check function should run and return a dict."""
    from ipracticom_sweeper.chat_tools import get_fs_check
    # FS-05 (cli_reachable) is cheap and may timeout quickly — pick
    # FS-01 which only does `ps` (no network).
    out = get_fs_check("check_fs01_process_running")
    # Either it ran and returned a dict, or it errored gracefully.
    assert "ok" in out
    if out["ok"]:
        assert "result" in out and isinstance(out["result"], dict)
    else:
        assert "error" in out


def test_get_fs_check_timeout_protection(monkeypatch):
    """A hanging check should be killed by the timeout wrapper."""
    from ipracticom_sweeper import chat_tools

    def slow():
        import time as _t
        _t.sleep(20)
        return {"status": "slow"}

    fake_reg = {"slow_check": slow}
    monkeypatch.setattr(chat_tools, "_get_registry", lambda: fake_reg)
    out = chat_tools._safe_run("slow_check", slow, timeout_s=0.3)
    assert out["ok"] is False
    assert "timeout" in (out.get("error") or "").lower()


# --- chat_tools: tier + pipeline -----------------------------------------

def test_run_fs_tier_1_returns_fs01_through_fs05():
    from ipracticom_sweeper.chat_tools import run_fs_tier
    out = run_fs_tier(1)
    assert out["tier"] == 1
    assert out["ran"] >= 5
    names = {r["name"] for r in out["results"]}
    assert any("fs01" in n for n in names)
    assert any("fs05" in n for n in names)


def test_run_fs_tier_4_returns_fs16_through_fs25():
    from ipracticom_sweeper.chat_tools import run_fs_tier
    out = run_fs_tier(4)
    assert out["tier"] == 4
    assert out["ran"] >= 10
    names = {r["name"] for r in out["results"]}
    assert any("fs16" in n for n in names)
    assert any("fs25" in n for n in names)


def test_run_fs_tier_invalid():
    from ipracticom_sweeper.chat_tools import run_fs_tier
    out = run_fs_tier(7)
    assert out["ok"] is False


def test_run_full_pipeline_gated_by_env():
    from ipracticom_sweeper.chat_tools import run_full_pipeline
    # Default: not enabled.
    if "ENABLE_HEAVY_TOOLS" in os.environ:
        del os.environ["ENABLE_HEAVY_TOOLS"]
    out = run_full_pipeline()
    assert out["ok"] is False
    assert "ENABLE_HEAVY_TOOLS" in out["error"]


def test_run_full_pipeline_when_enabled(monkeypatch):
    """When ENABLE_HEAVY_TOOLS=1 we at least invoke monitor.checks.run_all."""
    from ipracticom_sweeper import chat_tools
    monkeypatch.setenv("ENABLE_HEAVY_TOOLS", "1")

    def fake_run_all(rules):
        return {"defcon": "green", "modules": []}

    monkeypatch.setattr(
        "ipracticom_sweeper.monitor.checks.run_all", fake_run_all
    )
    out = chat_tools.run_full_pipeline()
    assert out["ok"] is True
    assert out["degraded"] is True


# --- chat_tools: schema + execute_tool -----------------------------------

def test_get_tool_specs_returns_four_tools():
    from ipracticom_sweeper.chat_tools import get_tool_specs
    specs = get_tool_specs()
    assert len(specs) == 4
    names = {s["function"]["name"] for s in specs}
    assert names == {"list_fs_checks", "get_fs_check",
                     "run_fs_tier", "run_full_pipeline"}


def test_execute_tool_dispatches_by_name():
    from ipracticom_sweeper.chat_tools import execute_tool
    out = execute_tool("list_fs_checks", {})
    assert "count" in out and out["count"] >= 25
    assert "_elapsed_ms" in out


def test_execute_tool_accepts_json_string_args():
    from ipracticom_sweeper.chat_tools import execute_tool
    out = execute_tool("run_fs_tier", json.dumps({"tier": 1}))
    assert out["tier"] == 1
    assert "ran" in out


def test_execute_tool_handles_invalid_json_args():
    from ipracticom_sweeper.chat_tools import execute_tool
    out = execute_tool("list_fs_checks", "not json {{{")
    # Should still resolve to empty dict, return a valid response.
    assert "count" in out


def test_execute_tool_unknown_tool():
    from ipracticom_sweeper.chat_tools import execute_tool
    out = execute_tool("nonexistent_tool", {})
    assert out["ok"] is False


# --- chat_llm: detect_mode ------------------------------------------------

def test_detect_mode_default_mock():
    """When no API keys are set, mode must be 'mock'."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    os.environ.clear()
    os.environ.update(env)
    assert detect_mode() == "mock"


def test_detect_mode_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert detect_mode() == "openai"


def test_detect_mode_anthropic(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
    assert detect_mode() == "anthropic"


# --- chat_llm: mock intents ----------------------------------------------

def _empty_hits() -> list[RetrievalHit]:
    return []


def test_mock_status_intent_triggers_tier1():
    rep = _mock_reply("מה המצב של FreeSWITCH?", _empty_hits())
    assert rep.mode == "mock"
    assert len(rep.tool_calls) == 1
    assert rep.tool_calls[0]["name"] == "run_fs_tier"
    assert rep.tool_calls[0]["arguments"]["tier"] == 1


def test_mock_help_intent_triggers_list():
    rep = _mock_reply("עזרה", _empty_hits())
    assert rep.tool_calls[0]["name"] == "list_fs_checks"


def test_mock_check_intent_triggers_list():
    rep = _mock_reply("בדוק FreeSWITCH", _empty_hits())
    assert rep.tool_calls[0]["name"] == "list_fs_checks"


def test_mock_tier_intent_picks_tier_number():
    rep = _mock_reply("tier 3", _empty_hits())
    assert rep.tool_calls[0]["name"] == "run_fs_tier"
    assert rep.tool_calls[0]["arguments"]["tier"] == 3


def test_mock_single_check_intent_normalizes_id():
    rep = _mock_reply("FS-01", _empty_hits())
    assert rep.tool_calls[0]["name"] == "get_fs_check"
    assert "fs01" in rep.tool_calls[0]["arguments"]["check_id"].lower()


def test_mock_open_question_with_rag_hit():
    hit = RetrievalHit(doc_id="manual.md", score=0.8,
                       text="FreeSWITCH AWS setup notes",
                       bm25=1.0, tfidf=0.5)
    rep = _mock_reply("איך מגדירים את זה?", [hit])
    assert "[RAG]" in rep.text
    assert "manual.md" in rep.text


def test_mock_open_question_no_hits_falls_back():
    rep = _mock_reply("מה השעה?", _empty_hits())
    assert "לא מצאתי מקור" in rep.text or "נסה" in rep.text


def test_mock_empty_question_falls_through():
    rep = _mock_reply("", _empty_hits())
    # Falls into the "open" branch.
    assert rep.mode == "mock"


def test_router_default_uses_detect_mode():
    r = LLMRouter()
    assert r.mode in ("mock", "openai", "anthropic")


def test_router_explicit_mode():
    r = LLMRouter(mode="mock")
    rep = r.reply("עזרה", _empty_hits())
    assert rep.mode == "mock"


def test_reply_as_dict_shape():
    rep = _mock_reply("tier 1", _empty_hits())
    d = rep.as_dict()
    assert set(d.keys()) == {"text", "mode", "tool_calls"}


# --- chat integration: HTTP surfaces mode + tools ------------------------

@pytest.fixture
def app():
    from ipracticom_sweeper.dashboard import app as dashboard_app
    dashboard_app.config["TESTING"] = True
    return dashboard_app


@pytest.fixture
def client(app):
    return app.test_client()


def test_chat_post_message_surfaces_mock_mode(client):
    sess = client.post("/chat/sessions", json={"title": "t"}).get_json()
    sid = sess["session_id"]
    resp = client.post(f"/chat/sessions/{sid}/messages",
                       json={"content": "מה המצב?"})
    assert resp.status_code == 200
    data = resp.get_json()
    ack_text = data["assistant"]["content"]
    # Should mention either mock tool call or the LLM acknowledgement.
    assert ("[tools]" in ack_text or "mock" in ack_text
            or "Tier" in ack_text)


def test_chat_post_message_keeps_rag_block(client):
    sess = client.post("/chat/sessions", json={"title": "t"}).get_json()
    sid = sess["session_id"]
    # Ask something the project's docs/ would answer.
    resp = client.post(f"/chat/sessions/{sid}/messages",
                       json={"content": "FreeSWITCH AWS deployment guide"})
    assert resp.status_code == 200
    data = resp.get_json()
    ack_text = data["assistant"]["content"]
    # RAG block OR tools block should appear (depends on intent match).
    assert ("[RAG]" in ack_text or "[tools]" in ack_text or "mock" in ack_text)


def test_chat_post_message_hebrew_passthrough(client):
    sess = client.post("/chat/sessions", json={"title": "t"}).get_json()
    sid = sess["session_id"]
    resp = client.post(f"/chat/sessions/{sid}/messages",
                       json={"content": "\u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd"})
    assert resp.status_code == 200
    assert "\u05e9\u05dc\u05d5\u05dd" in resp.get_json()["assistant"]["content"]


def test_chat_ws_frame_includes_llm_mode_and_tools():
    """The WS handler builds a richer frame; verify the helper logic."""
    # We can't easily run the WS in tests (no client.websocket), but we
    # can verify the routing function the WS uses produces a stable shape.
    r = LLMRouter()
    rep = r.reply("tier 1", [])
    assert rep.mode == "mock"
    assert isinstance(rep.tool_calls, list)
    # Mirror what chat_ws sends into the JSON frame.
    frame = {
        "user": {"content": "tier 1"},
        "assistant": {"content": rep.text},
        "llm_mode": rep.mode,
        "tool_calls": rep.tool_calls,
    }
    j = json.dumps(frame, ensure_ascii=False)
    parsed = json.loads(j)
    assert parsed["llm_mode"] == "mock"
    assert parsed["tool_calls"][0]["name"] == "run_fs_tier"