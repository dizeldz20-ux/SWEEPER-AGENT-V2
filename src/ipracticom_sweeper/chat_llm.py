"""LLM router for the chat assistant (v0.5.0 slice 3.3).

Routes a user message to one of three modes:

  1. `mock`   — regex + RAG-driven stub that simulates an LLM reply
                (default when no API keys are set)
  2. `openai` — OpenAI Chat Completions with tool-use (set OPENAI_API_KEY)
  3. `anthropic` — Anthropic Messages API with tool-use (set ANTHROPIC_API_KEY)

The router does NOT stream responses in v0.5.0 — that lands in v0.6.
Tool calls are resolved locally via chat_tools.execute_tool and the
results are fed back into the LLM if it's the real provider, or
formatted into the stub reply if it's mock mode.

Mock mode is the production-safe default: the chat UI demos the tool
pipeline without sending any user text to a third party.

Public surface:
    LLMRouter       -- main entry point; .reply(question, hits) -> Reply
    Reply           -- dataclass with text + tool_calls list
    detect_mode()   -- 'mock' / 'openai' / 'anthropic'
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from ipracticom_sweeper.chat_rag import RetrievalHit


@dataclass
class Reply:
    text: str
    mode: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] | None = None  # provider response (for debugging)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "mode": self.mode,
            "tool_calls": self.tool_calls,
        }


def detect_mode() -> str:
    """Return the LLM mode based on env vars."""
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "mock"


# --- Mock mode (default) --------------------------------------------------

_MOCK_INTENTS: list[tuple[re.Pattern, str, str]] = [
    # (regex, intent_key, tool_name)
    (re.compile(r"^(?:מה|what).{0,30}(?:מצב|status)", re.I), "status", "run_fs_tier"),
    (re.compile(r"^(?:בדוק|check|run).{0,30}freeswitch", re.I), "check_all", "list_fs_checks"),
    (re.compile(r"^tier\s*[1-4]", re.I), "tier", "run_fs_tier"),
    (re.compile(r"^(?:fs-?\d+|check_fs\d+)", re.I), "single", "get_fs_check"),
    (re.compile(r"^(?:עזרה|help|\?)\s*$", re.I), "help", "list_fs_checks"),
]


def _mock_reply(question: str, hits: list[RetrievalHit]) -> Reply:
    """Heuristic reply when no LLM API key is configured."""
    q = (question or "").strip()
    tool_calls: list[dict[str, Any]] = []

    # Detect intent via regex.
    intent_key = "open"
    chosen_tool = None
    for pattern, key, tool in _MOCK_INTENTS:
        if pattern.search(q):
            intent_key = key
            chosen_tool = tool
            break

    # Build the actual tool call the assistant "would have made".
    if chosen_tool == "run_fs_tier":
        # Try to extract a tier number.
        m = re.search(r"tier\s*([1-4])", q, re.I)
        if m:
            tier = int(m.group(1))
            args = {"tier": tier}
        elif any(w in q for w in ["מצב", "status", "בדוק"]):
            args = {"tier": 1}  # default to Tier 1 health
        else:
            args = {}
        tool_calls.append({
            "name": "run_fs_tier",
            "arguments": args,
            "note": "mock-tool-call",
        })
        text = (f"(mock) אני אריץ Tier {args.get('tier', 1)} של בדיקות "
                f"FreeSWITCH כדי לענות על '{q[:60]}'.")
    elif chosen_tool == "list_fs_checks":
        tool_calls.append({"name": "list_fs_checks", "arguments": {}})
        text = (f"(mock) הנה רשימת בדיקות ה-FreeSWITCH הזמינות. "
                f"תגיד לי איזו להריץ (למשל 'הרץ FS-01').")
    elif chosen_tool == "get_fs_check":
        m = re.search(r"(check_fs\d+_[a-z_]+|fs-?\d+)", q, re.I)
        check_id = m.group(1).lower() if m else "check_fs01_process_running"
        # Normalize 'fs-1' -> 'check_fs01_process_running' (or first match).
        if not check_id.startswith("check_fs"):
            digits = re.search(r"\d+", check_id)
            if digits:
                n = int(digits.group(0))
                check_id = f"check_fs{n:02d}_*"
        tool_calls.append({"name": "get_fs_check", "arguments": {"check_id": check_id}})
        text = f"(mock) מריץ את {check_id}..."
    else:
        # Fallback: free-form "open" question.
        text = f"(mock) קיבלתי: {q[:120]}\n"
        if hits:
            top = hits[0]
            snippet = top.text.strip().replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."
            text += f"\n[RAG] {top.doc_id}: {snippet}"
        else:
            text += "\nלא מצאתי מקור רלוונטי. נסה 'בדוק FreeSWITCH' או 'עזרה'."

    return Reply(text=text, mode="mock", tool_calls=tool_calls)


# --- Real providers -------------------------------------------------------

def _openai_reply(question: str,
                  hits: list[RetrievalHit]) -> Reply:
    """Real OpenAI Chat Completions with tool-use loop (max 1 iteration in v0.5)."""
    from ipracticom_sweeper.chat_tools import execute_tool, get_tool_specs

    api_key = os.environ["OPENAI_API_KEY"]
    try:
        import urllib.request
        messages = [
            {"role": "system",
             "content": ("You are a Hebrew-speaking assistant for the iPracticom "
                         "AWS Sweeper. Be concise. Prefer tool calls when the "
                         "user asks for system state.")},
            {"role": "user", "content": question},
        ]
        body = json.dumps({
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "messages": messages,
            "tools": get_tool_specs(),
            "tool_choice": "auto",
            "max_tokens": 500,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        executed: list[dict[str, Any]] = []
        # Single-iteration tool loop (v0.5 scope; multi-iter lands in v0.6).
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            args_raw = fn.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except ValueError:
                args = {}
            result = execute_tool(name, args)
            executed.append({
                "name": name,
                "arguments": args,
                "result": result,
            })
        text = msg.get("content") or "(no text reply)"
        return Reply(text=text, mode="openai",
                     tool_calls=executed, raw=data)
    except Exception as exc:
        return Reply(text=f"(openai-error) {exc}", mode="openai-error",
                     tool_calls=[])


def _anthropic_reply(question: str,
                     hits: list[RetrievalHit]) -> Reply:
    """Anthropic Messages API path (single-iteration tool loop)."""
    from ipracticom_sweeper.chat_tools import execute_tool, get_tool_specs

    api_key = os.environ["ANTHROPIC_API_KEY"]
    try:
        import urllib.request
        body = json.dumps({
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
            "max_tokens": 500,
            "system": ("You are a Hebrew-speaking assistant for the iPracticom "
                       "AWS Sweeper. Be concise. Use tools when the user asks "
                       "for system state."),
            "tools": [
                {"name": spec["function"]["name"],
                 "description": spec["function"]["description"],
                 "input_schema": spec["function"]["parameters"]}
                for spec in get_tool_specs()
            ],
            "messages": [{"role": "user", "content": question}],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={"x-api-key": api_key,
                     "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        tool_calls: list[dict[str, Any]] = []
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                args = block.get("input") or {}
                result = execute_tool(name, args)
                tool_calls.append({"name": name, "arguments": args,
                                   "result": result})
        text_blocks = [b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text"]
        text = "\n".join(text_blocks) or "(no text reply)"
        return Reply(text=text, mode="anthropic",
                     tool_calls=tool_calls, raw=data)
    except Exception as exc:
        return Reply(text=f"(anthropic-error) {exc}", mode="anthropic-error",
                     tool_calls=[])


# --- Public entry point ---------------------------------------------------

class LLMRouter:
    """Dispatch a chat turn to the configured (or default) provider."""

    def __init__(self, mode: str | None = None) -> None:
        self.mode = mode or detect_mode()

    def reply(self, question: str,
              hits: list[RetrievalHit]) -> Reply:
        if self.mode == "openai":
            return _openai_reply(question, hits)
        if self.mode == "anthropic":
            return _anthropic_reply(question, hits)
        return _mock_reply(question, hits)


__all__ = ["LLMRouter", "Reply", "detect_mode"]