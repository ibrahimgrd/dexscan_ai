"""
Layer: Handlers — shared contract (Playbook Part VIII Step 2; extended
Step 3 for real HTML/keyboard rendering).

`Handler` is the interface every chain-of-responsibility link implements.
`handlers.dispatcher.Dispatcher` walks a list of these in registration
order.

Documented deviation from the playbook's literal `Update` type: aiogram
3.x's own Dispatcher hands typed `Message`/`CallbackQuery` objects to
registered observers, not a raw `Update` wrapper. Forcing the literal
`Update` type here would mean fabricating a synthetic `Update` (with a
fake `update_id`) at the integration point in main.py — worse than using
the union type aiogram actually gives us. `TelegramEvent` is functionally
equivalent to what the spec intended; only the exact type differs.
"""

from __future__ import annotations

from typing import Protocol

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from rendering.menus import RenderedMessage
from state.fsm import FSMContext

TelegramEvent = Message | CallbackQuery


class Handler(Protocol):
    """
    One link in the dispatcher's chain-of-responsibility (Part II.1).
    `can_handle` must be side-effect-free and fast — it only decides
    whether this handler owns the event, never performs the actual work.
    """

    async def can_handle(self, event: TelegramEvent, ctx: FSMContext) -> bool:
        """Return True if this handler should process `event` given the
        user's current FSM context. Must not mutate state."""
        ...

    async def handle(self, event: TelegramEvent, ctx: FSMContext) -> None:
        """Do the actual work. Only called if `can_handle` returned True
        for this same (event, ctx) pair."""
        ...


async def reply(
    event: TelegramEvent,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Sends `text` (Telegram HTML mode — Part II.8's standing default) back
    regardless of whether `event` is a fresh Message (answer) or a tapped
    button on an existing one (edit in place — Part II.8's single-
    evolving-message rule). `keyboard` becomes the message's inline
    keyboard when given. Step 2's call sites (bare text, no keyboard) keep
    working unchanged, since `keyboard` defaults to None.

    parse_mode is passed explicitly on every call. main.py also sets a
    Bot-level `DefaultBotProperties(parse_mode=ParseMode.HTML)` default —
    the two aren't in conflict (an explicit call-level value always wins
    over the Bot default when both are set), so this is intentional
    defense-in-depth: the explicit value here means this file is correct
    on its own even if someone reads it in isolation, and the Bot-level
    default means any future call site that forgets to pass parse_mode
    still renders HTML correctly instead of leaking literal `<b>` tags.
    """
    if isinstance(event, CallbackQuery):
        if event.message is not None:
            await event.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await event.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def send_rendered(event: TelegramEvent, rendered: RenderedMessage) -> None:
    """Convenience wrapper unpacking a `rendering.menus.RenderedMessage`
    into a `reply()` call — the one place a handler converts "which screen
    to show" into an actual Telegram API call."""
    await reply(event, rendered.html, keyboard=rendered.keyboard)
