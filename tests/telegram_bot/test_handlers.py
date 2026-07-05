"""Tests for telegram_bot handlers (v0.4.2).

Each handler is a thin async function: takes (update, context) like
python-telegram-bot, uses the bot_data['agent'] to talk to agent_api,
formats the response, and replies.

We test the handler's *return value* (a dict describing what to send)
rather than mocking the entire Telegram API. This keeps tests fast and
focused on the contract.

In v0.4.2 handlers are split across modules under
`ipracticom_sweeper.telegram_bot.handlers`. v0.4.1's flat handlers.py
exported {start, status, problems, history, security}; in v0.4.2:
  - start     → handlers.dashboard.start
  - status    → handlers.dashboard.dashboard (renamed; takes a snapshot
                and shows DEFCON + run-now)
  - problems  → merged into dashboard.dashboard (active problems shown
                together with DEFCON)
  - history   → handlers.history.history (now uses the catalog
                endpoint; range drill-down lives in handlers.history)
  - security  → still uses /api/snapshot's security_baseline module;
                we test the formatter directly in test_telegram_formatter

We import the v0.4.2 names so the tests survive the refactor.
"""
import pytest
from types import SimpleNamespace

from ipracticom_sweeper.telegram_bot.config import BotConfig
from ipracticom_sweeper.telegram_bot.services.agent_client import AgentAPIError
from ipracticom_sweeper.telegram_bot.handlers.dashboard import start, dashboard
from ipracticom_sweeper.telegram_bot.handlers.history import history as history_handler


# ---------- fakes ----------

def _update(chat_id: int = 42, callback_data: str | None = None, text: str | None = None):
    """Build a fake Update-like object with the right shape."""
    chat = SimpleNamespace(id=chat_id)
    message = SimpleNamespace(text=text) if text is not None else None
    if callback_data is None:
        return SimpleNamespace(effective_chat=chat, message=message, callback_query=None)
    cq = SimpleNamespace(data=callback_data, from_user=SimpleNamespace(id=chat_id), message=None)
    return SimpleNamespace(effective_chat=chat, callback_query=cq, message=message)


def _ctx(cfg: BotConfig, agent):
    """Build a fake Context with bot_data populated."""
    return SimpleNamespace(bot_data={"config": cfg, "agent": agent})


# ---------- start ----------

@pytest.mark.asyncio
async def test_start_returns_welcome_with_six_section_menu():
    """v0.4.2 /start shows 6-section menu (full_menu)."""
    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, agent=None)
    update = _update(chat_id=42)
    result = await start(update, ctx)
    assert "ברוך הבא" in result["text"] or "sweeper" in result["text"].lower()
    assert "reply_markup" in result
    # Verify 6 sections are present
    cbs = [
        btn.callback_data
        for row in result["reply_markup"].inline_keyboard
        for btn in row
    ]
    for required in ("menu:dashboard", "menu:history", "menu:approvals",
                     "menu:connectors", "menu:fleet", "menu:settings"):
        assert required in cbs, f"missing v0.4.2 section: {required}"


# ---------- dashboard ----------

@pytest.mark.asyncio
async def test_dashboard_uses_snapshot():
    """dashboard handler pulls /api/snapshot and shows DEFCON + run-now."""
    class FakeAgent:
        async def get_snapshot(self):
            return {"defcon": 3, "modules": {"cpu": {"status": "ok"}}}

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="menu:dashboard")
    result = await dashboard(update, ctx)
    assert "DEFCON 3" in result["text"]
    # Has run-now button
    cbs = [
        btn.callback_data
        for row in result["reply_markup"].inline_keyboard
        for btn in row
    ]
    assert "dash:run_now" in cbs


@pytest.mark.asyncio
async def test_dashboard_handles_agent_error():
    """dashboard handler returns Hebrew error when agent_api is down."""
    class BrokenAgent:
        async def get_snapshot(self):
            raise AgentAPIError(503, "service unavailable")

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, BrokenAgent())
    update = _update(chat_id=42, callback_data="menu:dashboard")
    result = await dashboard(update, ctx)
    assert "שגיאה" in result["text"] or "error" in result["text"].lower()


# ---------- history (v0.4.2 catalog-based) ----------

@pytest.mark.asyncio
async def test_history_lists_metrics_from_catalog():
    """history handler reads /api/history (catalog) and lists metrics."""

    class FakeAgent:
        async def get_history_catalog(self):
            return {
                "metrics": ["defcon", "cpu_percent"],
                "hosts": ["localhost"],
                "metrics_with_counts": [
                    {"metric": "defcon", "count": 50},
                    {"metric": "cpu_percent", "count": 1440},
                ],
                "hosts_with_counts": [],
            }

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="menu:history")
    result = await history_handler(update, ctx)
    assert "defcon" in result["text"]
    assert "cpu_percent" in result["text"]
    # Drill-down buttons present
    cbs = [
        btn.callback_data
        for row in result["reply_markup"].inline_keyboard
        for btn in row
    ]
    assert any(cb and cb.startswith("hist:metric:") for cb in cbs)


@pytest.mark.asyncio
async def test_history_empty_catalog():
    """history handler handles empty catalog gracefully."""
    class FakeAgent:
        async def get_history_catalog(self):
            return {"metrics": [], "hosts": []}

    cfg = BotConfig(bot_token="t", allowed_chat_ids={42})
    ctx = _ctx(cfg, FakeAgent())
    update = _update(chat_id=42, callback_data="menu:history")
    result = await history_handler(update, ctx)
    assert "אין מטריקות" in result["text"]