"""Inline keyboards for the iPracticom Sweeper Telegram dashboard.

All keyboards are 1-column-when-long (mobile-first) or 2x2 grid
(when 4 items). The main menu is a 2x2 grid for one-thumb reach.
"""
from __future__ import annotations

from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    """Top-level dashboard: 2x2 grid (mobile-friendly)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 מצב נוכחי", callback_data="menu:status"),
            InlineKeyboardButton("⚠️ בעיות", callback_data="menu:problems"),
        ],
        [
            InlineKeyboardButton("📈 היסטוריה", callback_data="menu:history"),
            InlineKeyboardButton("🔐 אבטחה", callback_data="menu:security"),
        ],
    ])


def status_menu() -> InlineKeyboardMarkup:
    """Status submenu: 3 metrics + back."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖥️ CPU", callback_data="snap:cpu")],
        [InlineKeyboardButton("🧠 זיכרון", callback_data="snap:memory")],
        [InlineKeyboardButton("💾 דיסק", callback_data="snap:disk")],
        [InlineKeyboardButton("🌐 רשת", callback_data="snap:network")],
        [InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")],
    ])


def history_menu() -> InlineKeyboardMarkup:
    """History submenu: 1-col list of metrics + back."""
    metrics = [
        ("defcon", "🎯 DEFCON"),
        ("cpu_percent", "🖥️ CPU %"),
        ("memory_percent", "🧠 זיכרון %"),
        ("disk_percent", "💾 דיסק %"),
    ]
    rows = [
        [InlineKeyboardButton(label, callback_data=f"hist:{metric}")]
        for metric, label in metrics
    ]
    rows.append([InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def back_to_main() -> InlineKeyboardMarkup:
    """Single-button keyboard for inline back navigation."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")]
    ])


# ---------------------------- v0.4.2 keyboards ----------------------------
# These match the dashboard structure: 6 top-level sections, each with its
# own submenu. The main_menu() above was the v0.4.1 menu (4 buttons) —
# the v0.4.2 main menu is full_menu() below. bot.py dispatches menu:main
# to start_handler, which returns full_menu().

def full_menu() -> InlineKeyboardMarkup:
    """v0.4.2 top-level menu: 6 sections in a 2x3 grid.

    Order matches the dashboard's left-rail: Dashboard, History, Approvals,
    Connectors, Fleet, Settings.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏠 לוח בקרה", callback_data="menu:dashboard"),
            InlineKeyboardButton("📚 היסטוריה", callback_data="menu:history"),
        ],
        [
            InlineKeyboardButton("✅ אישורים", callback_data="menu:approvals"),
            InlineKeyboardButton("🔌 מחברים", callback_data="menu:connectors"),
        ],
        [
            InlineKeyboardButton("🖥️ צי", callback_data="menu:fleet"),
            InlineKeyboardButton("⚙️ הגדרות", callback_data="menu:settings"),
        ],
    ])


def dashboard_menu(running: bool = False) -> InlineKeyboardMarkup:
    """Dashboard submenu: run-now + back.

    Per user request, run-now executes immediately — no confirmation.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            "⏳ ריצה מתבצעת..." if running else "▶️ הרץ סריקה עכשיו",
            callback_data="dash:run_now" if not running else "menu:dashboard",
        )],
        [InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(rows)


def history_overview_menu(metrics: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    """History submenu (v0.4.2): list catalog metrics with sample counts.

    Each row is one metric; tapping drills down into its time-series.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for m in metrics:
        name = str(m.get("metric", "?"))
        count = m.get("count", 0)
        label = f"📊 {name}  ({count})"
        # Keep callback compact: hist:metric:<name>
        rows.append([InlineKeyboardButton(label, callback_data=f"hist:metric:{name}")])
    rows.append([InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def history_metric_menu(metric: str) -> InlineKeyboardMarkup:
    """Per-metric drill-down: 1h/24h/7d + back."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱️ שעה אחרונה", callback_data=f"hist:range:{metric}:1")],
        [InlineKeyboardButton("🕐 24 שעות", callback_data=f"hist:range:{metric}:24")],
        [InlineKeyboardButton("📅 7 ימים", callback_data=f"hist:range:{metric}:168")],
        [InlineKeyboardButton("⬅️ חזור", callback_data="menu:history")],
    ])


def approvals_menu(count: int) -> InlineKeyboardMarkup:
    """Approvals submenu: pending count + back."""
    label = f"📋 {count} תיקונים ממתינים" if count else "✅ אין תיקונים ממתינים"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="appr:list")],
        [InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")],
    ])


def approval_action_kb(pid: str) -> InlineKeyboardMarkup:
    """Approve/Reject buttons for one proposal (used in paged lists)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ אשר", callback_data=f"appr:approve:{pid}"),
            InlineKeyboardButton("❌ דחה", callback_data=f"appr:reject:{pid}"),
        ],
    ])


def connectors_menu(connectors: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    """Connectors submenu: list + add-new + back."""
    rows: list[list[InlineKeyboardButton]] = []
    for c in connectors:
        name = str(c.get("name", "?"))
        status = c.get("status", "unknown")
        emoji = "✅" if status == "ok" else ("❌" if status == "error" else "❓")
        rows.append([
            InlineKeyboardButton(f"{emoji} {name}", callback_data=f"conn:view:{name}")
        ])
    rows.append([InlineKeyboardButton("➕ הוסף מחבר", callback_data="conn:add")])
    rows.append([InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def connector_actions_kb(name: str) -> InlineKeyboardMarkup:
    """Per-connector actions: edit, test, delete."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧪 בדוק", callback_data=f"conn:test:{name}")],
        [InlineKeyboardButton("✏️ ערוך", callback_data=f"conn:edit:{name}")],
        [InlineKeyboardButton("🗑️ מחק", callback_data=f"conn:delete:{name}")],
        [InlineKeyboardButton("⬅️ חזור", callback_data="menu:connectors")],
    ])


def fleet_menu(hosts: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    """Fleet submenu: list every host."""
    rows: list[list[InlineKeyboardButton]] = []
    for h in hosts:
        name = str(h.get("name", "?"))
        status = h.get("status", "unknown")
        emoji = "✅" if status == "ok" else ("⚠️" if status == "warn" else (
            "🚨" if status == "crit" else "❓"
        ))
        rows.append([
            InlineKeyboardButton(f"{emoji} {name}", callback_data=f"fleet:host:{name}")
        ])
    rows.append([InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def settings_menu(server_id: str, has_token: bool) -> InlineKeyboardMarkup:
    """Settings submenu: webhook tests + server identity."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧪 בדוק Slack webhook", callback_data="set:test:slack")],
        [InlineKeyboardButton("🧪 בדוק Telegram", callback_data="set:test:tg")],
        [InlineKeyboardButton("🔑 בדוק טוקן Agent API", callback_data="set:test:api")],
        [InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="menu:main")],
    ])


# ---------------------------- Generic factory ----------------------------

def confirm_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    """Yes/No confirmation keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ כן", callback_data=yes_cb),
            InlineKeyboardButton("❌ לא", callback_data=no_cb),
        ],
    ])