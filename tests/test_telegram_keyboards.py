"""Tests for v0.4.2 inline keyboards."""
from __future__ import annotations

from telegram import InlineKeyboardMarkup

from ipracticom_sweeper.telegram_bot.keyboards import (
    approval_action_kb,
    approvals_menu,
    back_to_main,
    confirm_kb,
    connector_actions_kb,
    connectors_menu,
    dashboard_menu,
    fleet_menu,
    full_menu,
    history_metric_menu,
    history_overview_menu,
    main_menu,
    settings_menu,
    status_menu,
    history_menu,
)


def _flat(markup: InlineKeyboardMarkup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


# ---------------------------- backwards-compat ----------------------------

def test_main_menu_v041_still_has_4_buttons():
    """The v0.4.1 main_menu() (4 buttons) is still exported for back-compat."""
    cbs = _flat(main_menu())
    assert "menu:status" in cbs
    assert "menu:problems" in cbs
    assert "menu:history" in cbs
    assert "menu:security" in cbs


# ---------------------------- v0.4.2 new keyboards ----------------------------

def test_full_menu_has_six_sections():
    cbs = _flat(full_menu())
    for required in ("menu:dashboard", "menu:history", "menu:approvals",
                     "menu:connectors", "menu:fleet", "menu:settings"):
        assert required in cbs, f"missing section: {required}"


def test_dashboard_menu_has_run_now():
    cbs = _flat(dashboard_menu())
    assert "dash:run_now" in cbs
    assert "menu:main" in cbs


def test_dashboard_menu_running_state_disables_run_now():
    """While a sweep is in progress, run_now becomes a no-op button."""
    cbs = _flat(dashboard_menu(running=True))
    assert "dash:run_now" not in cbs
    assert "menu:dashboard" in cbs


def test_history_overview_menu_renders_metrics_with_counts():
    metrics = [
        {"metric": "cpu_percent", "count": 142},
        {"metric": "memory_percent", "count": 138},
    ]
    cbs = _flat(history_overview_menu(metrics))
    assert "hist:metric:cpu_percent" in cbs
    assert "hist:metric:memory_percent" in cbs


def test_history_metric_menu_has_three_ranges():
    cbs = _flat(history_metric_menu("cpu_percent"))
    assert any(cb and cb.startswith("hist:range:cpu_percent:") for cb in cbs)
    # Three ranges: 1h, 24h, 168h
    ranges = [cb for cb in cbs if cb and cb.startswith("hist:range:cpu_percent:")]
    assert len(ranges) == 3


def test_approvals_menu_pending_count():
    markup = approvals_menu(5)
    flat_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("5" in t for t in flat_texts)


def test_approvals_menu_empty_shows_zero():
    markup = approvals_menu(0)
    flat_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("0" in t or "אין" in t for t in flat_texts)


def test_approval_action_kb_uses_proposal_id():
    cbs = _flat(approval_action_kb("abc123"))
    assert "appr:approve:abc123" in cbs
    assert "appr:reject:abc123" in cbs


def test_connectors_menu_renders_each_connector():
    connectors = [
        {"name": "prod-web", "status": "ok"},
        {"name": "prod-db", "status": "error"},
    ]
    cbs = _flat(connectors_menu(connectors))
    assert "conn:view:prod-web" in cbs
    assert "conn:view:prod-db" in cbs
    assert "conn:add" in cbs


def test_connector_actions_kb_has_test_edit_delete():
    cbs = _flat(connector_actions_kb("prod-web"))
    assert "conn:test:prod-web" in cbs
    assert "conn:edit:prod-web" in cbs
    assert "conn:delete:prod-web" in cbs


def test_fleet_menu_uses_status_emoji():
    hosts = [
        {"name": "local", "status": "ok"},
        {"name": "prod-1", "status": "warn"},
        {"name": "prod-2", "status": "crit"},
    ]
    markup = fleet_menu(hosts)
    flat = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("✅" in t and "local" in t for t in flat)
    assert any("⚠️" in t and "prod-1" in t for t in flat)
    assert any("🚨" in t and "prod-2" in t for t in flat)


def test_settings_menu_has_all_test_buttons():
    cbs = _flat(settings_menu("server-1", has_token=True))
    assert "set:test:slack" in cbs
    assert "set:test:tg" in cbs
    assert "set:test:api" in cbs


def test_confirm_kb_uses_provided_callbacks():
    cbs = _flat(confirm_kb("yes:1", "no:1"))
    assert "yes:1" in cbs
    assert "no:1" in cbs


def test_back_to_main_single_button():
    assert _flat(back_to_main()) == ["menu:main"]


# Existing keyboards still work
def test_status_menu_has_back():
    cbs = _flat(status_menu())
    assert "menu:main" in cbs


def test_history_menu_has_metric_callbacks():
    cbs = _flat(history_menu())
    assert any(cb and cb.startswith("hist:") for cb in cbs)