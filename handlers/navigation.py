"""
Layer: Handlers — navigation for /start and every "nav_*" screen-jump
callback, plus stale/unrecognized-input recovery (Playbook Part VIII
Step 3, extending Step 2's skeleton).

Real HTML menus (rendering/menus.py) replace Step 2's placeholder text
here. Nothing in this file calls an analysis, scoring, or trading module
(Step 2's constraint still holds — Step 3 only adds rendering + parsing).

Retired from Step 2: `BackHandler` (matched a generic "nav_back"). Every
screen's Back button now carries its own specific target directly
(rendering/menus.py's `_SCREEN_BACK_TARGET`, e.g. "nav_scan",
"nav_welcome") — "nav_back" is never actually produced by any keyboard
anymore, so a handler for it would be dead code. `MenuNavigationHandler`
below covers Back and Home uniformly, since both are just "jump to this
specific screen" once the target is explicit rather than generic.
"""

from __future__ import annotations

from collections.abc import Callable

from aiogram.types import CallbackQuery, Message

from bot.constants import FSMState
from handlers.base import Handler, TelegramEvent, send_rendered
from handlers.callback_parser import parse_callback
from rendering.error_renderer import RECOVERY_HOME, render_error
from rendering.menus import (
    RenderedMessage,
    render_about,
    render_filter_config,
    render_main_menu,
    render_recent_results,
    render_scan_menu,
    render_welcome,
)
from state.fsm import FSMContext, FSMEngine
from state.session_store import SessionStore

_STALE_SESSION_ERROR = "That menu has expired."

# Every screen reachable via a plain "jump there" callback (Part II.5's
# Idle-cluster: no FSM state change needed, just different content). Two
# renderers need `ctx` (Main Menu's status line; Auto-Watch's own entry,
# `nav_watch`, moved to `handlers.watch_handler.WatchHandler` as of the
# custom-roadmap Step 13 pass — it needs a real `AutoWatchStatus` this
# lookup table has no way to supply, same reason `nav_filters` moved
# there too). `nav_settings`/`nav_help` moved to
# `handlers.settings_handler.SettingsHandler` /
# `handlers.help_handler.HelpHandler` in Step 15, for the identical
# reason: both need real per-user state (`UserSettings`, the FAQ
# accordion's own sub-navigation) this table has no way to supply.
# `nav_about` stays here — About has no per-user state at all. Screens
# with live-data prerequisites (Result List/Detail, Trade Staging,
# Scanning) are deliberately absent — their own steps add real entry
# points; nothing here should pre-empt that.
_NAV_TARGETS: dict[str, Callable[[FSMContext], RenderedMessage]] = {
    "nav_welcome": lambda ctx: render_welcome(),
    "nav_main": lambda ctx: render_main_menu(ctx),
    "nav_scan": lambda ctx: render_scan_menu(),
    "nav_custom_filter": lambda ctx: render_filter_config(),
    "nav_recent": lambda ctx: render_recent_results(),
    "nav_about": lambda ctx: render_about(),
}


class StartHandler:
    """Matches `/start`. Always shows Welcome and resets to Idle — `/start`
    is a deliberate "take me to the beginning" action, distinct from the
    Home button (which jumps straight to Main Menu; see rendering/menus.py's
    module docstring, deviation #1, for why those two differ)."""

    def __init__(self, fsm: FSMEngine) -> None:
        self._fsm = fsm

    async def can_handle(self, event: TelegramEvent, ctx: FSMContext) -> bool:
        return isinstance(event, Message) and (event.text or "").strip() == "/start"

    async def handle(self, event: TelegramEvent, ctx: FSMContext) -> None:
        assert isinstance(event, Message) and event.from_user is not None
        self._fsm.transition(event.from_user.id, FSMState.IDLE)
        await send_rendered(event, render_welcome())


class MenuNavigationHandler:
    """
    Matches any callback whose parsed command is a key in `_NAV_TARGETS`.
    `parse_callback` (Step 3's callback parser) runs first, before the
    lookup — this is this playbook's "parser as the first chain link"
    (Part VIII Step 3): parsing genuinely happens before any routing
    decision, even though it lives as this handler's first step rather
    than a separate object, since nothing here needs param-level parsing
    yet (every nav target today is a bare command — see
    handlers/callback_parser.py for where param/uuid decoding would plug
    in once a command that needs it exists, e.g. Step 6's "result_view").

    All current targets are pure Idle-cluster jumps (Part II.4): none of
    them changes FSM state beyond confirming Idle, so one handler covers
    all of them rather than one class per screen.
    """

    def __init__(self, fsm: FSMEngine) -> None:
        self._fsm = fsm

    async def can_handle(self, event: TelegramEvent, ctx: FSMContext) -> bool:
        if not isinstance(event, CallbackQuery):
            return False
        parsed = parse_callback(event.data or "")
        return parsed.command in _NAV_TARGETS

    async def handle(self, event: TelegramEvent, ctx: FSMContext) -> None:
        assert isinstance(event, CallbackQuery) and event.from_user is not None
        parsed = parse_callback(event.data or "")
        render_fn = _NAV_TARGETS[parsed.command]

        new_ctx = self._fsm.transition(event.from_user.id, FSMState.IDLE)
        await send_rendered(event, render_fn(new_ctx))


class NoopHandler:
    """
    Matches the "noop" callback: the non-interactive page-indicator label
    (e.g. "2/5") on paginated Result List rows (rendering/result_renderer.py).
    It exists purely so that label can be a real button — Telegram inline
    keyboards have no non-tappable text cell — rather than something a
    user can act on.

    STEP 16 FIX: this command was generated with no handler anywhere, so
    it fell through to `UnknownInputHandler` - every tap on the page
    counter silently reset the user's FSM state to Idle and replaced
    their current screen with a "that menu has expired" error, which is
    wrong on two counts: the menu hadn't expired, and Part II.8's whole
    point of a dedicated no-op button is that tapping it does *nothing*.
    Caught during this step's FSM/callback audit (Part VIII Step 16:
    "callback strings without handlers") — must be registered before
    `UnknownInputHandler` in `build_navigation_handlers`, same as every
    other specific handler.
    """

    async def can_handle(self, event: TelegramEvent, ctx: FSMContext) -> bool:
        return isinstance(event, CallbackQuery) and parse_callback(event.data or "").command == "noop"

    async def handle(self, event: TelegramEvent, ctx: FSMContext) -> None:
        return  # Dispatcher already answered the callback (Part II.8); nothing else to do.


class UnknownInputHandler:
    """
    Catch-all — MUST be registered last. Matches anything no other handler
    claimed: an unrecognized command, an action button for a feature not
    built yet, or an update from a user the SessionStore has never seen.
    Always resolves to a fresh Idle context (Part II.5: stale-session
    recovery returns a fresh home menu) and offers one tappable way out
    rather than just words telling the user to retype something.

    STEP 15 FIX: previously sent `_STALE_OR_UNAVAILABLE_MESSAGE` via bare
    `reply()` with no keyboard at all — zero tappable buttons, relying on
    the user typing `/start` themselves. That's a real Part II.5 "zero
    dead ends" / Part IV.4 UX-checklist gap ("every screen has Back;
    every screen below Main Menu has Home"), not just a style
    inconsistency, caught while building this step's unified error
    renderer. Now routes through `render_error` like every other
    error/degraded path in this codebase, with a real Home button.
    """

    def __init__(self, fsm: FSMEngine, session_store: SessionStore) -> None:
        self._fsm = fsm
        self._session_store = session_store

    async def can_handle(self, event: TelegramEvent, ctx: FSMContext) -> bool:
        return True

    async def handle(self, event: TelegramEvent, ctx: FSMContext) -> None:
        user_id = event.from_user.id if event.from_user else None
        if user_id is not None and ctx.state is not FSMState.IDLE:
            self._fsm.transition(user_id, FSMState.IDLE)
        show_technical = self._session_store.get_settings(user_id).show_technical_errors if user_id is not None else False
        await send_rendered(
            event,
            render_error(_STALE_SESSION_ERROR, technical_detail=None, show_technical=show_technical, recovery_action=RECOVERY_HOME),
        )


def build_navigation_handlers(
    fsm: FSMEngine,
    session_store: SessionStore,
    extra_handlers: list[Handler] | None = None,
) -> list[Handler]:
    """
    Returns the full handler chain in the correct registration order:
    specific matches first, `extra_handlers` (e.g. Step 7's `ScanHandler`)
    next, catch-all always last. `main.py` registers the returned list,
    in this order, onto a `handlers.dispatcher.Dispatcher`.

    `extra_handlers` exists so later steps' handlers (which need engine
    dependencies this function has no reason to know about — `ScanHandler`
    takes `CoreEngine`/`SecurityEngine`/etc.) can be woven into the
    correct position without `main.py` reaching into
    `Dispatcher._handlers` directly, which would both break encapsulation
    and duplicate the "catch-all must be last" rule in a second place.

    `session_store` (Step 15 addition) is threaded through to
    `UnknownInputHandler` alone, for its stale-session error screen's
    `show_technical_errors` check — every other handler here still needs
    none of this file's own state.
    """
    handlers: list[Handler] = [StartHandler(fsm), MenuNavigationHandler(fsm), NoopHandler()]
    handlers.extend(extra_handlers or [])
    handlers.append(UnknownInputHandler(fsm, session_store))
    return handlers
