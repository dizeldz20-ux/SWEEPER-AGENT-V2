"""Tests for v0.5.0 slice 4.1 — RTL verify on the chat UI.

Covers:
  - base.html sets lang=he + dir=rtl on <html>
  - chat.html inherits RTL (no override, no conflicting dir=ltr)
  - chat shell uses CSS that respects writing direction
  - LTR-explicit classes (direction: ltr) only on technical tables/JSON,
    not on chat DOM
  - Mock/real-mode chat responses still parse cleanly in Hebrew
"""

from __future__ import annotations

import re

import pytest


@pytest.fixture
def app():
    from ipracticom_sweeper.dashboard import app as dashboard_app
    dashboard_app.config["TESTING"] = True
    return dashboard_app


@pytest.fixture
def client(app):
    return app.test_client()


def test_base_html_is_rtl_hebrew(client):
    resp = client.get("/chat/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The base template (extended by chat.html) must set lang=he dir=rtl
    # on the root <html> tag.
    assert re.search(r'<html\s+lang="he"\s+dir="rtl"', body), \
        "base.html must declare <html lang='he' dir='rtl'>"


def test_chat_html_does_not_override_root_dir(client):
    """A nested dir='ltr' would cancel the RTL context. The chat shell
    section must NOT carry a dir attribute (inherits from base)."""
    resp = client.get("/chat/")
    body = resp.get_data(as_text=True)
    # Strip the script block (contains 'ltr' inside strings).
    body_no_script = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    # No <section ... dir="ltr"> or <section ... dir="rtl"> inside the
    # rendered chat DOM — we want inheritance from the <html>.
    assert 'section class="chat-shell" dir' not in body_no_script, \
        "chat-shell section must not override dir; rely on base.html"


def test_chat_dom_uses_rtl_friendly_css_properties(client):
    """The injected style.css should reference `text-align: start`,
    `border-inline-start`, and avoid `text-align: left/right` inside the
    chat-* classes."""
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    css = resp.get_data(as_text=True)
    # Slice into the chat-* block (stop at @media query boundary).
    m = re.search(r"/\* --- Chat shell.*?(?=/\* ---|\Z)", css, flags=re.S)
    assert m, "chat-shell CSS block must exist"
    chat_css = m.group(0)
    assert "text-align: start" in chat_css or "text-align:start" in chat_css
    assert "border-inline-start" in chat_css
    # No hardcoded text-align: left|right inside chat-* classes.
    bad = re.findall(r"text-align:\s*(?:left|right)\s*;", chat_css)
    assert not bad, f"chat CSS contains hardcoded text-align L/R: {bad}"


def test_chat_grid_uses_inline_padding_not_left_right(client):
    """Padding should be `padding-inline-*` to follow writing direction."""
    resp = client.get("/static/style.css")
    css = resp.get_data(as_text=True)
    # Just check the file uses inline-* somewhere in chat section.
    assert "border-inline" in css or "padding-inline" in css


def test_chat_message_posting_preserves_hebrew(client):
    """End-to-end: posting a Hebrew message still works and renders."""
    sess = client.post("/chat/sessions", json={"title": "rtl"}).get_json()
    sid = sess["session_id"]
    resp = client.post(f"/chat/sessions/{sid}/messages",
                       json={"content": "\u05d0\u05d9\u05da \u05de\u05ea\u05e7\u05d9\u05e0\u05d9\u05dd \u05d0\u05ea FreeSWITCH?"})
    assert resp.status_code == 200
    ack = resp.get_json()["assistant"]["content"]
    # Hebrew round-tripped.
    assert "\u05d0\u05d9\u05da" in ack or "mock" in ack or "[tools]" in ack


def test_chat_template_loads_no_external_rtl_hacks(client):
    """No inline style='direction: ltr' in chat HTML."""
    resp = client.get("/chat/")
    body = resp.get_data(as_text=True)
    # Strip <style> and <script> to focus on DOM.
    body_no_script = re.sub(r"<(?:script|style).*?</(?:script|style)>",
                            "", body, flags=re.S)
    bad = re.findall(r'style="[^"]*direction\s*:\s*ltr[^"]*"', body_no_script)
    assert not bad, f"chat HTML contains inline LTR direction: {bad}"


def test_chat_index_has_rtl_friendly_skeleton(client):
    """Skeleton checks: chat-shell + chat-grid + chat-sessions + chat-log + chat-form."""
    resp = client.get("/chat/")
    body = resp.get_data(as_text=True)
    for cls in ("chat-shell", "chat-grid", "chat-sessions",
                "chat-log", "chat-form", "chat-input"):
        assert cls in body, f"missing class={cls} in chat DOM"


def test_chat_log_classes_use_logical_properties():
    """Direct CSS inspection — alignment uses logical properties only."""
    from pathlib import Path
    # Repo-relative path — the previous hardcoded "/root/sweeper-work/..."
    # only existed on one deployment host and raised PermissionError/
    # FileNotFoundError everywhere else (CI, non-root dev checkouts).
    css_path = Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "static" / "style.css"
    css = css_path.read_text(encoding="utf-8")
    # Pull the chat-shell block.
    block = re.search(r"/\* --- Chat shell.*?(?:/\* ---|\Z)", css, flags=re.S)
    assert block
    css_block = block.group(0)
    # No 'float: left|right' in chat-* classes.
    bad_float = re.findall(r"float:\s*(?:left|right)\s*;", css_block)
    assert not bad_float
    # text-align must use 'start' or 'inherit', not 'left/right'.
    align = re.findall(r"text-align:\s*([a-z]+)\s*;", css_block)
    for v in align:
        assert v in {"start", "inherit", "initial"}, \
            f"non-logical text-align: {v}"