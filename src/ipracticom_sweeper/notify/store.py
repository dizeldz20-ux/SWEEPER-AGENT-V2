"""Persistent store for MULTIPLE notification bots (Telegram + Slack App).

This is *additive* to the legacy single-channel ``notifications.env`` used by
:mod:`ipracticom_sweeper.dashboard_notify`. It holds a **list** of bots per
platform in a JSON file co-located with ``notifications.env``:

    {
      "telegram": [{"id", "name", "bot_token", "chat_id"}],
      "slack":    [{"id", "name", "bot_token", "channel"}]
    }

Design contract (matches the legacy env helpers):

- **Never raises on read.** Missing/unreadable/corrupt file → empty lists. This
  keeps the safety-critical notify dispatch path from ever crashing because of a
  config file. When the file is absent (the state in every test that doesn't
  opt in), :func:`telegram_bots`/:func:`slack_bots` return ``[]`` and the
  fan-out in ``notify/legacy.py`` becomes a no-op — existing behaviour is byte
  for byte unchanged.
- **Secrets at rest** are written with ``0600`` perms via an atomic replace.
- The legacy env target is NOT stored here. The API layer merges the two views
  for display; dispatch sends to the env target *and* to this store's entries,
  which are disjoint by construction (no double-send).

Path: defaults to ``<state-dir>/bots.json`` (``paths.ROOT()``, i.e.
``/var/lib/ipracticom-sweeper`` in prod). The state dir is chosen over
``/etc`` because it is the agent's canonical *writable* location in every
environment — the systemd service owns it in prod and it is user-owned in the
WSL dev setup, so persistence works without root. Overridable via
``IPRACTICOM_SWEEPER_BOTS_FILE`` (used by tests / flexible deployment).
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

import structlog

_log = structlog.get_logger("ipracticom.notify.store")

_PLATFORMS = ("telegram", "slack")

# Serialises the read-modify-write in add_bot/delete_bot. _read()/_write() are
# each atomic on their own, but the RMW cycle around them is not: two threads
# (Flask dev server is single-process, threaded) would both read the same list,
# both append, and the second _write() would clobber the first — a silently
# lost bot. The dashboard is the only writer and runs in one process, so an
# in-process Lock is sufficient (no cross-process flock needed); this mirrors
# dashboard_helpers._write_lock for /run/now.
_STORE_LOCK = threading.Lock()


def bots_file() -> Path:
    """Resolve the bots.json path (env-overridable, read fresh each call)."""
    override = os.environ.get("IPRACTICOM_SWEEPER_BOTS_FILE")
    if override:
        return Path(override)
    # Lazy import (matches the codebase's lazy-import style) + keeps this module
    # importable even if config wiring changes.
    from ipracticom_sweeper.config import paths

    return paths.ROOT() / "bots.json"


def _empty() -> dict[str, list]:
    return {"telegram": [], "slack": []}


def _read() -> dict[str, list]:
    """Read bots.json. Never raises — any problem yields empty lists."""
    f = bots_file()
    if not f.exists():
        return _empty()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _log.warning("bots_read_failed", error=str(e))
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    return {
        "telegram": [b for b in data.get("telegram", []) if isinstance(b, dict)],
        "slack": [b for b in data.get("slack", []) if isinstance(b, dict)],
    }


def _write(data: dict[str, list]) -> tuple[bool, str | None]:
    """Atomically replace bots.json (0600). Returns (ok, error_message)."""
    f = bots_file()
    if not f.parent.exists():
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"cannot create dir: {e}"
    tmp = f.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(f)
    except OSError as e:
        return False, f"cannot write: {e}"
    return True, None


# --- Read accessors (used by the dispatch path) ------------------------------


def telegram_bots() -> list[dict]:
    """All configured Telegram bots (with secrets — dispatch use only)."""
    return _read()["telegram"]


def slack_bots() -> list[dict]:
    """All configured Slack App bots (with secrets — dispatch use only)."""
    return _read()["slack"]


def has_any_bot() -> bool:
    """True if at least one bot exists in the store (either platform)."""
    d = _read()
    return bool(d["telegram"] or d["slack"])


def find_bot(bot_id: str) -> tuple[str | None, dict | None]:
    """Return (platform, bot) for ``bot_id`` or (None, None)."""
    d = _read()
    for platform in _PLATFORMS:
        for b in d[platform]:
            if b.get("id") == bot_id:
                return platform, b
    return None, None


# --- Validation + mutations (used by the API) --------------------------------

# Accept the three shapes Slack actually uses: "#channel-name", a channel/group/
# DM id (C…/G…/D… + upper-alnum), or a bare lowercase channel name. Reject only
# clear mistakes — spaces, or a pasted webhook URL — rather than risk blocking a
# legitimate channel the operator typed.
_SLACK_CHANNEL_RE = re.compile(r"^(#[\w.-]+|[CGD][A-Z0-9]+|[a-z0-9][a-z0-9._-]*)$")


def _valid_slack_channel(channel: str) -> bool:
    return bool(_SLACK_CHANNEL_RE.match(channel))


def _validate(platform: str, fields: dict) -> tuple[dict | None, str | None]:
    """Validate + normalise an incoming bot. Returns (entry, error)."""
    name = str(fields.get("name") or "").strip()
    if platform == "telegram":
        token = str(fields.get("bot_token") or "").strip()
        chat_id = str(fields.get("chat_id") or "").strip()
        if not token:
            return None, "טוקן הבוט חסר"
        if not chat_id:
            return None, "מזהה הצ'אט (chat_id) חסר"
        return {
            "id": uuid.uuid4().hex,
            "name": name or "Telegram bot",
            "bot_token": token,
            "chat_id": chat_id,
        }, None
    if platform == "slack":
        token = str(fields.get("bot_token") or "").strip()
        channel = str(fields.get("channel") or "").strip()
        if not token:
            return None, "Bot Token של Slack חסר"
        if not token.startswith("xox"):
            return None, "Bot Token של Slack חייב להתחיל ב-xox (למשל xoxb-...)"
        if not channel:
            return None, "ערוץ Slack (channel) חסר"
        if not _valid_slack_channel(channel):
            return None, (
                "ערוץ Slack לא תקין — הזן #שם-ערוץ, מזהה ערוץ (C…/G…/D…), "
                "או שם ערוץ פשוט (אותיות קטנות/ספרות/נקודה/מקף). לא כתובת URL או רווחים."
            )
        return {
            "id": uuid.uuid4().hex,
            "name": name or "Slack app",
            "bot_token": token,
            "channel": channel,
        }, None
    return None, f"פלטפורמה לא נתמכת: {platform!r}"


def add_bot(platform: str, fields: dict) -> tuple[dict | None, str | None]:
    """Validate + append a bot. Returns (masked_entry, error)."""
    platform = (platform or "").strip()
    if platform not in _PLATFORMS:
        return None, f"פלטפורמה לא נתמכת: {platform!r}"
    entry, err = _validate(platform, fields)
    if err or entry is None:
        return None, err
    # Validation is pure (no file I/O), so keep it out of the lock; serialise
    # only the read-modify-write so a concurrent add can't clobber this one.
    with _STORE_LOCK:
        data = _read()
        data[platform].append(entry)
        ok, werr = _write(data)
    if not ok:
        return None, werr or "write failed"
    return mask(platform, entry), None


def delete_bot(bot_id: str) -> bool:
    """Remove a bot by id. Returns True if something was removed."""
    with _STORE_LOCK:
        data = _read()
        removed = False
        for platform in _PLATFORMS:
            before = len(data[platform])
            data[platform] = [b for b in data[platform] if b.get("id") != bot_id]
            if len(data[platform]) != before:
                removed = True
        if removed:
            _write(data)
    return removed


# --- Masking (secrets never leave the backend) -------------------------------


def mask(platform: str, bot: dict) -> dict:
    """Public (secret-free) view of a stored bot for the SPA."""
    out = {
        "id": bot.get("id", ""),
        "name": bot.get("name", ""),
        "platform": platform,
        "token_set": bool(bot.get("bot_token")),
        "legacy": False,
    }
    if platform == "telegram":
        out["chat_id"] = bot.get("chat_id", "")
    elif platform == "slack":
        out["channel"] = bot.get("channel", "")
        out["kind"] = "app"
    return out


def masked_list() -> dict[str, list]:
    """Secret-free lists for the SPA: {telegram: [...], slack: [...]}."""
    d = _read()
    return {
        "telegram": [mask("telegram", b) for b in d["telegram"]],
        "slack": [mask("slack", b) for b in d["slack"]],
    }
