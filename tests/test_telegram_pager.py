"""Tests for the Telegram bot's pager utility."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ipracticom_sweeper.telegram_bot.services.pager import (
    MAX_ROWS_PER_PAGE,
    chunk,
    paged_keyboard,
    truncate_text,
    with_footer,
)


def _flat_buttons(markup: InlineKeyboardMarkup) -> list[str]:
    """All callback_data values flattened to a single list."""
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def test_chunk_empty_returns_single_empty_page():
    assert chunk([], 3) == [[]]


def test_chunk_even_split():
    assert chunk([1, 2, 3, 4, 5, 6], 2) == [[1, 2], [3, 4], [5, 6]]


def test_chunk_uneven_split():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_chunk_size_one():
    assert chunk([1, 2, 3], 1) == [[1], [2], [3]]


def test_chunk_invalid_size_raises():
    import pytest
    with pytest.raises(ValueError):
        chunk([1, 2], 0)


def test_with_footer_first_page_no_prev():
    rows: list[list[InlineKeyboardButton]] = []
    markup = with_footer(rows, prefix="appr", page=0, total_pages=3, back_callback="menu:main")
    cbs = _flat_buttons(markup)
    assert any(cb and cb.endswith(":page:1") for cb in cbs)  # next only
    assert not any(cb and cb.endswith(":page:-1") for cb in cbs)
    assert "menu:main" in cbs


def test_with_footer_middle_page_has_both():
    rows: list[list[InlineKeyboardButton]] = []
    markup = with_footer(rows, prefix="appr", page=1, total_pages=3, back_callback="menu:main")
    cbs = _flat_buttons(markup)
    assert any(cb and cb.endswith(":page:0") for cb in cbs)  # prev
    assert any(cb and cb.endswith(":page:2") for cb in cbs)  # next


def test_with_footer_last_page_no_next():
    rows: list[list[InlineKeyboardButton]] = []
    markup = with_footer(rows, prefix="appr", page=2, total_pages=3, back_callback="menu:main")
    cbs = _flat_buttons(markup)
    assert any(cb and cb.endswith(":page:1") for cb in cbs)  # prev
    assert not any(cb and cb.endswith(":page:3") for cb in cbs)


def test_with_footer_back_none_skips_back_button():
    rows: list[list[InlineKeyboardButton]] = []
    markup = with_footer(rows, prefix="appr", page=0, total_pages=1, back_callback=None)
    cbs = _flat_buttons(markup)
    assert "menu:main" not in cbs


def test_with_footer_trims_when_too_many_rows():
    # 10 rows → trim to MAX_ROWS_PER_PAGE (keep last footer row).
    rows = [[InlineKeyboardButton(str(i), callback_data=f"x:{i}")] for i in range(10)]
    markup = with_footer(rows, prefix="x", page=0, total_pages=1, back_callback="menu:main")
    assert len(markup.inline_keyboard) <= MAX_ROWS_PER_PAGE


def test_paged_keyboard_first_page_renders_items():
    items = [f"item-{i}" for i in range(10)]

    def render(item, idx):
        return InlineKeyboardButton(item, callback_data=f"appr:item:{idx}")

    markup, total_pages = paged_keyboard(
        items, page=0, page_size=3, prefix="appr", render_item=render
    )
    assert total_pages == 4  # ceil(10/3) but chunk drops empty → 4
    cbs = _flat_buttons(markup)
    # First 3 items on page 0
    assert "appr:item:0" in cbs
    assert "appr:item:1" in cbs
    assert "appr:item:2" in cbs
    # Page navigation footer
    assert any(cb and cb.endswith(":page:1") for cb in cbs)


def test_paged_keyboard_last_page_has_no_next():
    items = [f"item-{i}" for i in range(7)]

    def render(item, idx):
        return InlineKeyboardButton(item, callback_data=f"appr:item:{idx}")

    markup, total_pages = paged_keyboard(
        items, page=3, page_size=2, prefix="appr", render_item=render
    )
    assert total_pages == 4
    cbs = _flat_buttons(markup)
    # Last page has prev but no next
    assert any(cb and cb.endswith(":page:2") for cb in cbs)
    assert not any(cb and cb.endswith(":page:4") for cb in cbs)


def test_paged_keyboard_empty_items_shows_back_only():
    def render(item, idx):
        return InlineKeyboardButton(str(item), callback_data="x:0")

    markup, total_pages = paged_keyboard(
        [], page=0, page_size=3, prefix="x", render_item=render
    )
    assert total_pages == 1
    assert "menu:main" in _flat_buttons(markup)


def test_paged_keyboard_clamps_out_of_range_page():
    items = ["a", "b", "c"]

    def render(item, idx):
        return InlineKeyboardButton(item, callback_data=f"x:{idx}")

    # page=99 should clamp to last page (1, since 3 items / 2 per page = 2 pages)
    markup, total_pages = paged_keyboard(
        items, page=99, page_size=2, prefix="x", render_item=render
    )
    assert total_pages == 2
    cbs = _flat_buttons(markup)
    # Clamped to last page (1) → has prev (:page:0) but no next (:page:2)
    assert any(cb and cb.endswith(":page:0") for cb in cbs)
    assert not any(cb and cb.endswith(":page:2") for cb in cbs)


def test_paged_keyboard_truncates_oversized_callback():
    huge = "x" * 200  # > 64 bytes
    items = [(huge,)]

    def render(item, idx):
        return InlineKeyboardButton(item[0], callback_data=huge)

    markup, total_pages = paged_keyboard(
        items, page=0, page_size=3, prefix="x", render_item=render
    )
    # The item's callback_data should have been truncated
    flat = _flat_buttons(markup)
    for cb in flat:
        if cb and cb.startswith("x" * 50):
            assert len(cb.encode("utf-8")) <= 64


def test_truncate_text_under_limit_passes_through():
    assert truncate_text("hello", max_chars=100) == "hello"


def test_truncate_text_over_limit_adds_marker():
    long = "x" * 1000
    out = truncate_text(long, max_chars=100)
    assert len(out) <= 100
    assert "טקסט קוצר" in out