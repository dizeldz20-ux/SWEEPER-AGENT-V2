"""Chat shell for iPracticom AWS Sweeper (v0.5.0 slice 3.1).

Infrastructure-only:
  - WebSocket endpoint at /ws for bidirectional conversation
  - Session list + per-session message history
  - Echo acknowledgement with optional RAG context (slice 3.2)
  - In-memory store + stdlib RAG (BM25 + TF-IDF cosine) wired in slice 3.2;
    langchain/pgvector remains a future option if multi-host scale demands it.

All identifiers use ULID for sortability; messages persisted in-memory until 3.2.

Public surface:
    register_chat_routes(app)  -- attach /chat + /ws to a Flask app
    ChatStore                  -- global in-memory store
    ChatMessage, ChatSession   -- pydantic-friendly dataclasses (stdlib only)
    RAGStore                   -- global retrieval index over docs/
"""

from __future__ import annotations
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ._log import log_suppressed

try:
    from flask import Blueprint, jsonify, render_template, request
    from flask_sock import Sock
except Exception:  # pragma: no cover -- document optional dep
    Blueprint = None  # type: ignore
    Sock = None  # type: ignore

# Module-level store; survives across requests in same process.
_STORE: "ChatStore | None" = None
_RAG: "RAGStore | None" = None


def get_store() -> "ChatStore":
    """Lazy-singleton accessor for the global chat store."""
    global _STORE
    if _STORE is None:
        _STORE = ChatStore()
    return _STORE


class RAGStore:
    """Lazy-initialized wrapper around HybridIndex.

    Indexes the sweeper's `docs/` directory (relative to the package root)
    on first use. The corpus is small (~10 markdown files) so reindexing
    on each access is wasteful; we cache the HybridIndex after first build
    and expose a `reload()` method for tests / explicit refresh.
    """

    def __init__(self, docs_dir: str | None = None) -> None:
        self._docs_dir = docs_dir  # None = default to package's docs/ sibling
        self._index = None  # type: ignore[var-annotated]

    def _resolve_docs_dir(self) -> str | None:
        if self._docs_dir:
            return self._docs_dir
        # default: <package>/../../docs (project root docs/)
        try:
            here = Path(__file__).resolve().parent
            candidate = here.parent.parent / "docs"
            if candidate.is_dir():
                return str(candidate)
        except Exception as e:
            log_suppressed("chat_docs_resolve", e)
        return None

    def reload(self) -> int:
        """Force a fresh index build. Returns the doc count.

        A missing or empty docs dir yields 0 (not an exception) so
        that production deploys without docs/ still boot the chat UI
        cleanly — the assistant will simply have no RAG context to
        surface until docs/ is populated.
        """
        from ipracticom_sweeper.chat_rag import load_docs_from_dir
        d = self._resolve_docs_dir()
        if not d:
            self._index = None
            return 0
        try:
            self._index = load_docs_from_dir(d)
        except FileNotFoundError:
            self._index = None
            return 0
        return self._index.doc_count

    def get(self):
        """Lazy accessor returning the underlying HybridIndex (or None)."""
        if self._index is None:
            try:
                self.reload()
            except Exception:
                self._index = None
        return self._index

    def query(self, question: str, top_k: int = 2):
        idx = self.get()
        if idx is None:
            return []
        return idx.query(question, top_k=top_k)


def get_rag() -> "RAGStore":
    """Lazy-singleton accessor for the global RAG store."""
    global _RAG
    if _RAG is None:
        _RAG = RAGStore()
    return _RAG


def _compose_assistant_reply(content: str,
                             rag_top_k: int = 2,
                             max_chars: int = 600) -> str:
    """Build the assistant acknowledgement for slice 3.3 (LLM router).

    Hits the global LLM router (mock by default; OpenAI/Anthropic when
    the matching API key is in env). The router decides whether to call
    tools and emits a final text reply; we surface that to the chat
    store as the assistant message.
    """
    from ipracticom_sweeper.chat_llm import LLMRouter
    rag = get_rag()
    hits = rag.query(content, top_k=rag_top_k)
    reply = LLMRouter().reply(content, hits)
    base = reply.text or ""
    # Append a compact tool-call summary for transparency in the UI.
    if reply.tool_calls:
        tc_lines = ["[tools]"]
        for tc in reply.tool_calls:
            name = tc.get("name", "?")
            args = tc.get("arguments") or {}
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            tc_lines.append(f"• {name}({args_str})")
        # Truncate base reply first if total would overflow.
        tool_block = "\n".join(tc_lines)
        if len(base) + len(tool_block) + 1 > max_chars:
            base = base[:max(0, max_chars - len(tool_block) - 4)] + "...\n"
        return base + "\n" + tool_block
    return base


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    """Monotonic-ish id; uuid4 hex without dashes for compactness."""
    return uuid.uuid4().hex


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant" | "system"
    content: str
    ts_ms: int = field(default_factory=_now_ms)
    msg_id: str = field(default_factory=_new_id)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatSession:
    session_id: str
    title: str
    created_ms: int = field(default_factory=_now_ms)
    creation_seq: int = 0
    messages: list[ChatMessage] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "created_ms": self.created_ms,
            "messages": [m.as_dict() for m in self.messages],
            "message_count": len(self.messages),
        }


class ChatStore:
    """In-memory chat store keyed by session_id. Not thread-safe across procs."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._seq: int = 0

    def list_sessions(self) -> list[ChatSession]:
        # Stable ordering: newest first by creation sequence (a monotonic
        # counter incremented per create_session). Two sessions created in
        # the same millisecond still get a deterministic, creation-order
        # tiebreaker.
        return sorted(self._sessions.values(),
                      key=lambda s: s.creation_seq,
                      reverse=True)

    def create_session(self, title: str = "חדש") -> ChatSession:
        sid = _new_id()[:12]
        self._seq += 1
        session = ChatSession(session_id=sid, title=title,
                              creation_seq=self._seq)
        self._sessions[sid] = session
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        return self._sessions.get(session_id)

    def append(self, session_id: str, role: str, content: str) -> ChatMessage | None:
        sess = self._sessions.get(session_id)
        if sess is None:
            return None
        msg = ChatMessage(role=role, content=content)
        sess.messages.append(msg)
        return msg

    def reset(self) -> None:
        """For tests."""
        self._sessions.clear()
        self._seq = 0


def _seed_demo_session(store: ChatStore) -> ChatSession:
    """One demo session so the UI has something on first visit (Hebrew)."""
    if store.list_sessions():
        # Re-use existing demo if present.
        for s in store.list_sessions():
            if s.title.startswith("שלום"):
                return s
    demo = store.create_session("שלום — דוגמה")
    store.append(demo.session_id, "system",
                 "ברוך הבא לסוכן הצ'אט של הסוויפר. שלח הודעה כדי להתחיל.")
    store.append(demo.session_id, "assistant",
                 "אני כאן כדי לעזור לך לבדוק את שרת ה-FreeSWITCH. "
                 "מה תרצה לבדוק?")
    return demo


def register_chat_routes(app: Any, auth_required: bool = True) -> None:
    """Attach chat routes + websocket to a Flask app. Idempotent.

    v1.5.9 fix: added `auth_required` parameter (default True). When True,
    the /ws WebSocket and all /chat/* HTTP routes require an authenticated
    session (basic auth header for HTTP, header in WS upgrade request).
    Without this, anyone reaching the dashboard box could trigger LLM
    tool calls via /chat/sessions/<sid>/messages or /ws.

    When `auth_required=False`, no check is applied — useful for the
    test suite (which uses Flask test_client and bypasses real auth).
    """
    if Blueprint is None or Sock is None:
        raise RuntimeError(
            "flask + flask-sock required for chat routes; "
            "pip install flask-sock"
        )

    bp = Blueprint("chat", __name__, url_prefix="/chat")
    sock = Sock(app)

    # Seed once at startup; safe across reloads thanks to dedupe-by-title.
    _seed_demo_session(get_store())

    # --- Auth gate ---------------------------------------------------------
    # When auth_required=True, every chat route must verify the basic-auth
    # header. This mirrors dashboard._require_basic_auth but is applied at
    # the blueprint level since chat is a different surface.
    def _check_chat_auth() -> tuple[bool, str]:
        """Return (ok, reason). Reads the same DASHBOARD_USER/PASS env the
        dashboard uses, so operators don't configure auth twice.
        """
        expected_user = os.environ.get("DASHBOARD_USER", "")
        expected_pass = os.environ.get("DASHBOARD_PASS", "")
        if not (expected_user and expected_pass):
            # No auth configured → chat is open only if auth_required=False.
            return not auth_required, "auth_not_configured"
        from flask import request
        auth = request.authorization
        if auth and auth.username == expected_user and auth.password == expected_pass:
            return True, ""
        return False, "bad_credentials"

    @bp.before_request
    def _gate():
        if not auth_required:
            return None
        # GET on /chat/ and /chat/sessions is the chat UI — operators
        # expect to see it after auth, so we let the blueprint return 401
        # via the response below rather than redirecting.
        ok, reason = _check_chat_auth()
        if ok:
            return None
        return jsonify({"error": "unauthorized", "reason": reason}), 401

    @bp.get("/")
    @bp.get("")
    def chat_index():
        store = get_store()
        return render_template(
            "chat.html",
            sessions=[s.as_dict() for s in store.list_sessions()],
        )

    @bp.get("/sessions")
    def chat_sessions_json():
        store = get_store()
        return jsonify(
            sessions=[s.as_dict() for s in store.list_sessions()]
        )

    @bp.post("/sessions")
    def chat_create_session():
        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or "חדש").strip()[:80] or "חדש"
        sess = get_store().create_session(title=title)
        return jsonify(sess.as_dict()), 201

    @bp.get("/sessions/<session_id>")
    def chat_session_detail(session_id: str):
        sess = get_store().get_session(session_id)
        if sess is None:
            return jsonify({"error": "not_found"}), 404
        return jsonify(sess.as_dict())

    @bp.post("/sessions/<session_id>/messages")
    def chat_post_message(session_id: str):
        """HTTP fallback for non-WS clients (and tests)."""
        payload = request.get_json(silent=True) or {}
        content = (payload.get("content") or "").strip()
        if not content:
            return jsonify({"error": "empty"}), 400
        msg = get_store().append(session_id, "user", content)
        if msg is None:
            return jsonify({"error": "not_found"}), 404
        # Echo assistant acknowledgement (LLM wired in slice 3.3).
        ack = get_store().append(
            session_id, "assistant",
            _compose_assistant_reply(content)
        )
        return jsonify({"user": msg.as_dict(), "assistant": ack.as_dict() if ack else None})

    # flask-sock requires routes on the Sock instance at app level, not
    # inside a Blueprint. Register the WS endpoint directly on the app first.
    @sock.route("/ws")
    def chat_ws(ws):
        """Bidirectional WS. Accepts JSON frames: {session_id, content}.

        For slice 3.1 we ignore session_id parameter (always create or pick demo)
        and just echo back. Full session-aware WS lands in slice 3.3.

        v1.5.9 fix: per-IP message rate-limit (default 30 msg/min). Without it,
        a single client can drive up LLM cost by spamming messages.
        """
        # v1.5.9: auth gate (HTTP-equivalent). flask-sock doesn't trigger
        # blueprint before_request, so we check here.
        if auth_required:
            ok, reason = _check_chat_auth()
            if not ok:
                ws.send(json.dumps({"error": "unauthorized", "reason": reason},
                                   ensure_ascii=False))
                return None

        # v1.5.9: per-IP rate-limit using WS upgrade remote_addr.
        from flask import request as _flask_request
        ip = _flask_request.remote_addr or "unknown"
        if not _ws_rate_limit_check(ip, limit_per_min=30):
            ws.send(json.dumps({"error": "rate_limited"}, ensure_ascii=False))
            return None

        store = get_store()
        # Make sure demo session exists.
        demo = _seed_demo_session(store)
        try:
            while True:
                data = ws.receive()
                if data is None:
                    break
                try:
                    payload = json.loads(data)
                    content = (payload.get("content") or "").strip()
                    sid = payload.get("session_id") or demo.session_id
                except (ValueError, AttributeError):
                    content = str(data).strip()
                    sid = demo.session_id
                if not content:
                    ws.send(json.dumps({"error": "empty"}, ensure_ascii=False))
                    continue
                user_msg = store.append(sid, "user", content)
                if user_msg is None:
                    ws.send(json.dumps({"error": "not_found"}, ensure_ascii=False))
                    continue
                ack_msg = store.append(
                    sid, "assistant",
                    _compose_assistant_reply(content)
                )
                from ipracticom_sweeper.chat_llm import LLMRouter
                _router = LLMRouter()
                _rag = get_rag()
                _hits = _rag.query(content, top_k=2)
                _reply = _router.reply(content, _hits)
                ws.send(json.dumps(
                    {"user": user_msg.as_dict(),
                     "assistant": ack_msg.as_dict() if ack_msg else None,
                     "rag_hits": [h.as_dict() for h in _hits],
                     "llm_mode": _reply.mode,
                     "tool_calls": _reply.tool_calls},
                    ensure_ascii=False,
                ))
        except Exception as exc:  # pragma: no cover -- runtime WS errors
            try:
                ws.send(json.dumps({"error": str(exc)}, ensure_ascii=False))
            except Exception as e:
                log_suppressed("chat_ws_error_send", e)

    app.register_blueprint(bp)


# v1.5.9: per-IP rate-limit for the /ws WebSocket. Uses a process-wide
# dict of {ip: [ts, ts, ...]} with a 60s sliding window. Memory-bounded
# because the dict is capped at 1024 entries.
_WS_RATE_BUCKET: dict[str, list[float]] = {}
_WS_RATE_MAX_KEYS = 1024


def _ws_rate_limit_check(ip: str, limit_per_min: int = 30) -> bool:
    """Sliding-window per-IP rate limit. Returns True if under the cap."""
    import time as _time
    now = _time.time()
    window_start = now - 60.0
    bucket = _WS_RATE_BUCKET.get(ip, [])
    bucket = [t for t in bucket if t > window_start]
    if len(bucket) >= limit_per_min:
        _WS_RATE_BUCKET[ip] = bucket
        return False
    bucket.append(now)
    _WS_RATE_BUCKET[ip] = bucket
    # Evict old keys to keep memory bounded.
    if len(_WS_RATE_BUCKET) > _WS_RATE_MAX_KEYS:
        for k in list(_WS_RATE_BUCKET.keys())[:128]:
            _WS_RATE_BUCKET.pop(k, None)
    return True


__all__ = [
    "ChatMessage",
    "ChatSession",
    "ChatStore",
    "RAGStore",
    "get_rag",
    "get_store",
    "register_chat_routes",
]
