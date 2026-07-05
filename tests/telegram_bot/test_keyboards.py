"""Tests for telegram_bot.keyboards — inline keyboard builders.

The dashboard is a menu tree: main → status/problems/history/security →
submenus. Each menu is a single column of buttons (1-2-3-4) to keep it
mobile-friendly; the main menu fits in one row of 4 wide.
"""
import pytest

from ipracticom_sweeper.telegram_bot.keyboards import (
    main_menu,
    status_menu,
    history_menu,
    back_to_main,
)


def test_main_menu_has_four_buttons():
    """Main menu has 4 buttons: status, problems, history, security."""
    kb = main_menu()
    # InlineKeyboardMarkup has 1 row per list inside `inline_keyboard`
    rows = kb.inline_keyboard
    flat = [b for row in rows for b in row]
    assert len(flat) == 4


def test_main_menu_callbacks_unique():
    """All main menu callbacks are unique."""
    kb = main_menu()
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert len(callbacks) == len(set(callbacks))


def test_main_menu_uses_hebrew_labels():
    """Main menu uses Hebrew labels."""
    kb = main_menu()
    labels = [b.text for row in kb.inline_keyboard for b in row]
    hebrew_chars = sum(1 for c in "".join(labels) if "\u0590" <= c <= "\u05ff")
    assert hebrew_chars > 0  # at least one Hebrew character


def test_status_menu_has_back_button():
    """Status submenu has a back button."""
    kb = status_menu()
    flat = [b for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for b in flat]
    assert "menu:main" in callbacks


def test_history_menu_lists_known_metrics():
    """History menu shows the known metrics as buttons."""
    kb = history_menu()
    flat = [b for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for b in flat]
    # Known metrics
    for metric in ("defcon", "cpu_percent", "memory_percent"):
        assert f"hist:{metric}" in callbacks
    # Back button
    assert "menu:main" in callbacks


def test_back_to_main_single_button():
    """back_to_main is a single button (1 col x 1 row)."""
    kb = back_to_main()
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 1
    assert kb.inline_keyboard[0][0].callback_data == "menu:main"
