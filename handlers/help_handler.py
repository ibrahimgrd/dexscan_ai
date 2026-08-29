"""
Layer: Handlers — Help, FAQ, Tutorial, Security Basics (Playbook Part
VIII Step 15).

Owns `nav_help` (moved here from `handlers.navigation.MenuNavigationHandler`'s
`_NAV_TARGETS`, same reasoning as `SettingsHandler` taking `nav_settings`
— Help's own sub-navigation, not per-user state, is what this table
can't express) plus every screen nested under the Help hub in the site
map (Part II.5): FAQ accordion entries, Tutorial, Security Basics. All
of it is a single-message-edit tree — tapping a question, opening the
Tutorial, and going Back all edit the SAME message
(`handlers.base.send_rendered` already does this uniformly for any
`CallbackQuery`; nothing here needs special-casing beyond picking the
right screen), satisfying Step 15's "FAQ edits the same message"
Definition of Done item.
"""

from __future__ import annotations

from aiogram.types import CallbackQuery

from bot.constants import FSMState
from handlers.base import TelegramEvent, send_rendered
from handlers.callback_parser import parse_callback
from rendering.menus import render_faq_answer, render_help, render_security_basics, render_tutorial
from state.fsm import FSMContext, FSMEngine

_OWNED_COMMANDS = frozenset({"nav_help", "help_faq", "help_tutorial", "help_security"})


class HelpHandler:
    def __init__(self, fsm: FSMEngine) -> None:
        self._fsm = fsm

    async def can_handle(self, event: TelegramEvent, ctx: FSMContext) -> bool:
        if not isinstance(event, CallbackQuery):
            return False
        parsed = parse_callback(event.data or "")
        return parsed.command in _OWNED_COMMANDS

    async def handle(self, event: TelegramEvent, ctx: FSMContext) -> None:
        assert isinstance(event, CallbackQuery) and event.from_user is not None
        self._fsm.transition(event.from_user.id, FSMState.IDLE)
        parsed = parse_callback(event.data or "")

        if parsed.command == "nav_help":
            await send_rendered(event, render_help())
            return

        if parsed.command == "help_faq":
            entry_id = parsed.params[0] if parsed.params else ""
            await send_rendered(event, render_faq_answer(entry_id))
            return

        if parsed.command == "help_tutorial":
            await send_rendered(event, render_tutorial())
            return

        if parsed.command == "help_security":
            await send_rendered(event, render_security_basics())
            return
