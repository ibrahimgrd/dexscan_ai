"""
Layer: Handlers — Settings (Playbook Part VIII Step 15).

The Telegram-I/O shell around `bot.settings_logic`'s pure cycle/toggle/
reset functions and `state.session_store.SessionStore`'s new
`get_settings`/`set_settings` slot — same split as
`handlers.watch_handler.WatchHandler` over `analysis.filter_presets`
(Part V.2): the mutation logic stays aiogram-free and independently
testable; this file only reads callback params, mutates via that logic,
stores the result, and re-renders.

Takes over `nav_settings` from `handlers.navigation.MenuNavigationHandler`'s
`_NAV_TARGETS`, for the same reason `nav_watch`/`nav_filters` moved to
`WatchHandler` in Step 12: it needs a real per-user `UserSettings` that
lookup table has no way to supply.
"""

from __future__ import annotations

from aiogram.types import CallbackQuery

from bot.constants import FSMState
from bot.settings_logic import CYCLE_FIELD_KEYS, TOGGLE_FIELD_KEYS, cycle_field, reset_settings, toggle_field
from handlers.base import TelegramEvent, send_rendered
from handlers.callback_parser import parse_callback
from rendering.menus import render_settings
from state.fsm import FSMContext, FSMEngine
from state.session_store import SessionStore

_OWNED_COMMANDS = frozenset({"nav_settings", "settings_cycle", "settings_toggle", "settings_reset"})


class SettingsHandler:
    def __init__(self, fsm: FSMEngine, session_store: SessionStore) -> None:
        self._fsm = fsm
        self._session_store = session_store

    async def can_handle(self, event: TelegramEvent, ctx: FSMContext) -> bool:
        if not isinstance(event, CallbackQuery):
            return False
        parsed = parse_callback(event.data or "")
        return parsed.command in _OWNED_COMMANDS

    async def handle(self, event: TelegramEvent, ctx: FSMContext) -> None:
        assert isinstance(event, CallbackQuery) and event.from_user is not None
        user_id = event.from_user.id
        parsed = parse_callback(event.data or "")

        if parsed.command == "nav_settings":
            self._fsm.transition(user_id, FSMState.IDLE)
            await send_rendered(event, render_settings(self._session_store.get_settings(user_id)))
            return

        if parsed.command == "settings_cycle":
            if parsed.params and parsed.params[0] in CYCLE_FIELD_KEYS:
                updated = cycle_field(self._session_store.get_settings(user_id), parsed.params[0])
                self._session_store.set_settings(user_id, updated)
            await send_rendered(event, render_settings(self._session_store.get_settings(user_id)))
            return

        if parsed.command == "settings_toggle":
            if parsed.params and parsed.params[0] in TOGGLE_FIELD_KEYS:
                updated = toggle_field(self._session_store.get_settings(user_id), parsed.params[0])
                self._session_store.set_settings(user_id, updated)
            await send_rendered(event, render_settings(self._session_store.get_settings(user_id)))
            return

        if parsed.command == "settings_reset":
            self._session_store.set_settings(user_id, reset_settings())
            await send_rendered(event, render_settings(self._session_store.get_settings(user_id)))
            return
