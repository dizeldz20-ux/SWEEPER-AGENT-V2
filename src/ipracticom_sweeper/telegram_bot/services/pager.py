"""Pagination utility for Telegram inline keyboards.

Telegram limits inline keyboards to ~8 rows per message and message text
to 4096 chars. When we have more items than fit, we slice them into
pages and emit a small "prev/next" footer with callback_data of the
form ``<prefix>:page:<n>``.

We also enforce Telegram's 64-byte callback_data limit, since the prefix
+ page suffix plus the item index can easily blow past it on long
metric names.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Telegram hard limits (kept here so tests can pin them).
MAX_ROWS_PER_PAGE = 8
MAX_CALLBACK_BYTES = 64


def chunk(items: list[Any], size: int) -> list[list[Any]]:
    """Split ``items`` into chunks of ``size``.

    Returns a list of lists. Empty input → ``[[]]`` so callers can
    always treat the result as at least one page.
    """
    if size <= 0:
        raise ValueError("chunk size must be positive")
    if not items:
        return [[]]
    return [items[i : i + size] for i in range(0, len(items), size)]


def make_row(
    buttons: list[InlineKeyboardButton],
) -> list[InlineKeyboardButton]:
    """Pass-through so callers can build one row ergonomically."""
    return list(buttons)


def with_footer(
    rows: list[list[InlineKeyboardButton]],
    prefix: str,
    page: int,
    total_pages: int,
    back_callback: str | None = "menu:main",
    back_label: str = "⬅️ חזור לתפריט",
) -> InlineKeyboardMarkup:
    """Append a navigation footer (prev/next + optional back).

    Prefixes callback_data with ``f"{prefix}:page:{n}"`` so the bot's
    dispatcher can match a generic ``^{prefix}:page:(\\d+)$`` regex.
    """
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton("◀️ הקודם", callback_data=f"{prefix}:page:{page - 1}")
        )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton("הבא ▶️", callback_data=f"{prefix}:page:{page + 1}")
        )
    if nav_row:
        rows.append(nav_row)

    if back_callback:
        rows.append([InlineKeyboardButton(back_label, callback_data=back_callback)])

    # Defensive: warn if we exceeded Telegram limits. We don't truncate
    # silently — the caller should reduce their rows.
    if len(rows) > MAX_ROWS_PER_PAGE:
        # Trim from the bottom (keep header rows, drop footer rows).
        rows = rows[: MAX_ROWS_PER_PAGE - 1] + [rows[-1]]
    return InlineKeyboardMarkup(rows)


def paged_keyboard(
    items: Iterable[Any],
    *,
    page: int,
    page_size: int,
    prefix: str,
    render_item: Callable[[Any, int], InlineKeyboardButton],
    back_callback: str | None = "menu:main",
    back_label: str = "⬅️ חזור לתפריט",
) -> tuple[InlineKeyboardMarkup, int]:
    """Build a complete paged keyboard for ``items``.

    Returns ``(markup, total_pages)``. ``render_item`` is given the item
    and its absolute index (across all pages) so callback_data can
    include identifiers (e.g. proposal id).
    """
    items_list = list(items)
    pages = chunk(items_list, page_size)
    total_pages = len(pages)

    if total_pages == 0:
        # Empty state: one page, no items, just a back button.
        return with_footer([], prefix, 0, 1, back_callback, back_label), 1

    page = max(0, min(page, total_pages - 1))
    current = pages[page]

    rows: list[list[InlineKeyboardButton]] = [
        [render_item(item, page * page_size + idx)]
        for idx, item in enumerate(current)
    ]

    # Validate callback_data lengths (skip empty/None for safety).
    # InlineKeyboardButton is frozen — we have to rebuild it rather
    # than mutate the field.
    safe_rows: list[list[InlineKeyboardButton]] = []
    for row in rows:
        safe_row: list[InlineKeyboardButton] = []
        for btn in row:
            cb: str = btn.callback_data or ""
            if len(cb.encode("utf-8")) > MAX_CALLBACK_BYTES:
                safe_row.append(
                    InlineKeyboardButton(
                        btn.text,
                        callback_data=cb[: MAX_CALLBACK_BYTES - 1],
                    )
                )
            else:
                safe_row.append(btn)
        safe_rows.append(safe_row)
    rows = safe_rows

    return with_footer(rows, prefix, page, total_pages, back_callback, back_label), total_pages


def truncate_text(text: str, max_chars: int = 4000) -> str:
    """Truncate ``text`` to fit Telegram's 4096-char message limit.

    Leaves room for a single trailing truncation marker.
    """
    if len(text) <= max_chars:
        return text
    suffix = "\n\n<i>... (טקסט קוצר)</i>"
    return text[: max_chars - len(suffix)] + suffix