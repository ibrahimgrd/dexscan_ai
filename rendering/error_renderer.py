"""
Layer: Rendering (Playbook Part VIII Step 15).

The unified error-rendering abstraction Step 15 requires every
degraded/error path to route through, replacing the ad-hoc
`render_plain(<hardcoded string>)` calls scattered across
handlers/navigation.py, handlers/scan_handler.py, handlers/watch_handler.py,
and handlers/trade_staging_handler.py before this pass (each had its own
one-off message constant; none supported a technical-detail toggle; one -
navigation.py's stale-session recovery - had no keyboard AT ALL, a real
Part II.5 "zero dead ends" gap this pass also fixes, not just a style
inconsistency).

All the actual decision logic (what text, what single button) lives in
`rendering/error_copy.py`, which has zero aiogram import and is unit-
tested directly; this file's only job is wrapping that into a real
`RenderedMessage` — mirrors the `analysis/filter_presets.py` (pure) /
`handlers/watch_handler.py` (thin Telegram shell) split used everywhere
else in this codebase (Part V.2).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from rendering.error_copy import build_error_render_plan
from rendering.menus import RenderedMessage


def render_error(
    user_error: str,
    technical_detail: str | None,
    show_technical: bool,
    recovery_action: tuple[str, str],
) -> RenderedMessage:
    """Part VIII Step 15's Public Interface, exact signature. See
    `rendering.error_copy.build_error_render_plan`'s own docstring for
    what each parameter controls — this function only adds the keyboard."""
    plan = build_error_render_plan(user_error, technical_detail, show_technical, recovery_action)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=plan.recovery_label, callback_data=plan.recovery_callback)]]
    )
    return RenderedMessage(html=plan.message_html, keyboard=keyboard)


# Common recovery actions, named so call sites read as intent ("go
# Home") rather than repeating raw ("🏠 Home", "nav_main") tuples at
# every one of the handful of call sites that all want the exact same
# button. Not exhaustive — a call site with a genuinely different
# single recovery action (e.g. Scanning's own Cancel) just passes its
# own tuple directly; this is a convenience, not a closed enum.
RECOVERY_HOME: tuple[str, str] = ("\U0001f3e0 Home", "nav_main")
RECOVERY_RETRY_SCAN: tuple[str, str] = ("\U0001f504 Try Again", "nav_scan")
