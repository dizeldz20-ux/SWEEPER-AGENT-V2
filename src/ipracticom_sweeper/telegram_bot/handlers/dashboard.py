"""Dashboard + start + main menu handlers (v0.4.2).

Owns:
  - /start and /help: full 6-section menu
  - menu:main: back to main
  - menu:dashboard: dashboard view (DEFCON + problems + run-now)
  - dash:run_now: trigger a sweep
"""
from __future__ import annotations

from typing import Any

from ipracticom_sweeper.telegram_bot.formatter import format_dashboard, format_error
from ipracticom_sweeper.telegram_bot.keyboards import (
    dashboard_menu,
    full_menu,
)
from ipracticom_sweeper.telegram_bot.services.agent_client import (
    AgentAPIError,
    AgentClient,
)


def _agent(context) -> AgentClient:
    return context.bot_data["agent"]


# ---------- /start and /help ----------

async def start(update, context) -> dict[str, Any]:
    """Welcome message + full v0.4.2 6-section menu."""
    return {
        "text": (
            "🛡️ <b>iPracticom Sweeper</b>\n"
            "\n"
            "ברוך הבא. בחר פעולה מהתפריט:\n"
            "<i>כל הנתונים נמשכים מה-agent_api המקומי</i>"
        ),
        "reply_markup": full_menu(),
    }


async def back_to_main(update, context) -> dict[str, Any]:
    """Re-render the main menu (used by 'menu:main' callback)."""
    return await start(update, context)


# ---------- menu:dashboard ----------

async def dashboard(update, context) -> dict[str, Any]:
    """Show the dashboard: DEFCON + active problems + run-now button."""
    try:
        snap = await _agent(context).get_snapshot()
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": full_menu()}
    return {
        "text": format_dashboard(snap),
        "reply_markup": dashboard_menu(running=False),
    }


# ---------- dash:run_now ----------

async def run_now(update, context) -> dict[str, Any]:
    """Trigger a fresh sweep. Per user request, no confirmation — direct execute."""
    try:
        result = await _agent(context).trigger_run()
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": dashboard_menu()}

    defcon = result.get("defcon", "?")
    problems = result.get("problems_found", 0)
    text = (
        f"✅ <b>סריקה הושלמה</b>\n"
        f"  DEFCON: <b>{defcon}</b>\n"
        f"  בעיות שנמצאו: <b>{problems}</b>\n"
        f"\n"
        f"<i>{result.get('server', '')}</i>"
    )
    return {"text": text, "reply_markup": dashboard_menu()}