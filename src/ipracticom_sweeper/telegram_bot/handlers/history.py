"""History handler — time-series catalog + drill-down (v0.4.2).

Owns:
  - menu:history: catalog of metrics with sample counts
  - hist:metric:<name>: drill-down into a metric's range picker
  - hist:range:<metric>:<hours>: render the time-series for that range
"""
from __future__ import annotations

from typing import Any

from ipracticom_sweeper.telegram_bot.formatter import format_error, format_history_catalog
from ipracticom_sweeper.telegram_bot.keyboards import (
    back_to_main,
    history_metric_menu,
    history_overview_menu,
)
from ipracticom_sweeper.telegram_bot.services.agent_client import (
    AgentAPIError,
    AgentClient,
)


def _agent(context) -> AgentClient:
    return context.bot_data["agent"]


# ---------- menu:history ----------

async def history(update, context) -> dict[str, Any]:
    """Show the time-series catalog: every metric with its sample count."""
    try:
        catalog = await _agent(context).get_history_catalog()
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}

    metrics = catalog.get("metrics_with_counts") or [
        {"metric": m, "count": 0} for m in (catalog.get("metrics") or [])
    ]
    return {
        "text": format_history_catalog(catalog),
        "reply_markup": history_overview_menu(metrics),
    }


# ---------- hist:metric:<name> ----------

async def metric_drill(update, context) -> dict[str, Any]:
    """Show the range picker (1h / 24h / 7d) for one metric."""
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    metric = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not metric:
        return {"text": "❌ metric חסר", "reply_markup": back_to_main()}
    return {
        "text": f"📊 בחר טווח עבור <b>{metric}</b>:",
        "reply_markup": history_metric_menu(metric),
    }


# ---------- hist:range:<metric>:<hours> ----------

async def metric_range(update, context) -> dict[str, Any]:
    """Render the actual time-series for one metric + hours range."""
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    parts = data.split(":")
    # hist:range:<metric>:<hours>
    metric = parts[2] if len(parts) >= 4 else ""
    try:
        hours = int(parts[3]) if len(parts) >= 4 else 24
    except ValueError:
        hours = 24

    if not metric:
        return {"text": "❌ metric חסר", "reply_markup": back_to_main()}

    try:
        env = await _agent(context).get_history(metric, hours=hours)
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": history_metric_menu(metric)}

    samples = env.get("samples") or []
    from ipracticom_sweeper.telegram_bot.formatter import format_history
    text = format_history(metric, samples)
    text = f"<i>טווח: {hours} שעות | {len(samples)} דגימות</i>\n\n{text}"
    return {
        "text": text,
        "reply_markup": history_metric_menu(metric),
    }