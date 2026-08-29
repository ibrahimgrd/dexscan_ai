"""
Layer: Handlers — Trade Staging lifecycle (Playbook Part VIII Step 11;
functions named in Part IV.1: `enter_trade_staging`, `approve_and_get_link`,
`abort_trade_staging`; `change_target_bot` is this project's own addition
for the "Change Target Bot" button `rendering/menus.py`'s Step-3
placeholder already shipped with).

Deliberately Telegram-free (zero aiogram import) below `TradeStagingHandler`
itself — same split as `handlers/auto_watch.py`'s `AutoWatchManager` (see
that module's own docstring): these four functions need
`state.fsm.FSMEngine` and `state.session_store.SessionStore`, which is why
they live in `handlers/` rather than `integrations/`, but they never touch
`aiogram`, which is why they're independently unit-testable without a
fake Telegram event. `TradeStagingHandler` is the thin Telegram-I/O shell
around them — same relationship `ScanHandler` has to `scan_orchestration
.run_scan` and `WatchHandler` has to `AutoWatchManager`. See
`integrations/trading.py`'s own module docstring for the reasoning on
why `build_deep_link` itself lives there and not here.

Staged-flow state (which result, which bot) lives in
`state.fsm.FSMContext.payload` under `trade_result_id`/`trade_bot` while
`ctx.state is FSMState.TRADE_STAGING` — the same "small, per-user,
session-scoped value that payload already exists for" pattern
`WatchHandler._handle_preset_selected`'s own docstring already
established for `selected_filter_preset`, not a new store.

HARD SECURITY BOUNDARY (Part I.3, restated once more at this layer):
`approve_and_get_link` is the ONLY function in this entire codebase that
returns a usable external trading URL, and it only runs in direct
response to a user's own explicit tap on Trade Staging's "Approve & Open
Bot" button — never automatically, never as a side effect of anything
else. See `integrations/trading.py`'s module docstring for the same
statement from the pure deep-link-building side.
"""

from __future__ import annotations

from aiogram.types import CallbackQuery

from bot.constants import FSMState, TradingBot
from handlers.base import TelegramEvent, send_rendered
from handlers.callback_parser import parse_callback
from handlers.scan_orchestration import ScoredResult
from integrations.providers import BOT_PROVIDERS, bots_for_chain, default_bot_for_chain
from integrations.trading import UnsupportedChainError, build_deep_link
from rendering.error_renderer import RECOVERY_HOME, render_error
from rendering.menus import render_plain
from rendering.result_renderer import render_result_detail, render_trade_link_ready, render_trade_staging
from state.fsm import FSMContext, FSMEngine
from state.session_store import SessionStore

_OWNED_CALLBACK_COMMANDS = frozenset({"exec_stage", "exec_approve", "exec_change_bot", "exec_abort", "result_back_detail"})

_UNAVAILABLE_ERROR = (
    "That result isn't available for trading right now — it may have expired, or its chain "
    "couldn't be determined."
)


def enter_trade_staging(
    fsm: FSMEngine, session_store: SessionStore, user_id: int, result_id: str
) -> tuple[FSMContext, ScoredResult, TradingBot] | None:
    """Result Detail's Buy button -> Trade Staging (Part II.4). `None`
    (no FSM transition performed) if `result_id` doesn't resolve to a
    cached `ScoredResult` (Part II.5's stale-session recovery — the same
    contract `handlers.scan_handler.ScanHandler._lookup_result` already
    holds itself to for every other UUID-cache lookup) or if the
    result's own `core.chain` is `None` (an undetected chain — nothing
    in `integrations.providers.BOT_PROVIDERS` can be offered for a chain
    that was never resolved in the first place; Result Detail itself
    already shows "Unknown Chain" for this same case, so a person
    reaching this branch has already seen that before ever tapping Buy).
    Picks `default_bot_for_chain` as the starting selection — "Change
    Target Bot" is how a person moves off it.
    """
    scored = session_store.cache_get(result_id)
    if not isinstance(scored, ScoredResult) or scored.core.chain is None:
        return None

    bot = default_bot_for_chain(scored.core.chain)
    if bot is None:
        return None

    new_ctx = fsm.transition(user_id, FSMState.TRADE_STAGING, trade_result_id=result_id, trade_bot=bot.value)
    return new_ctx, scored, bot


def change_target_bot(
    fsm: FSMEngine, session_store: SessionStore, user_id: int
) -> tuple[FSMContext, ScoredResult, TradingBot] | None:
    """Cycles to the next operational bot supporting the currently-staged
    result's own chain (`TradingBot` enum declaration order, via
    `integrations.providers.bots_for_chain`, wrapping around past the
    last one) — never re-offers BullX while
    `BOT_PROVIDERS[TradingBot.BULLX].is_operational` stays `False` (see
    that module's own Verification Status note), and never offers a bot
    that doesn't cover this result's chain in the first place. `None`
    under the same "nothing currently staged" conditions
    `approve_and_get_link` below also returns `None` for — this project
    has one shared, honest way of saying "that flow isn't active
    anymore," not a different message per function.
    """
    ctx = fsm.get_state(user_id)
    result_id = ctx.payload.get("trade_result_id")
    if not isinstance(result_id, str):
        return None

    scored = session_store.cache_get(result_id)
    if not isinstance(scored, ScoredResult) or scored.core.chain is None:
        return None

    candidates = bots_for_chain(scored.core.chain, operational_only=True)
    if not candidates:
        return None

    current_raw = ctx.payload.get("trade_bot")
    try:
        current = TradingBot(current_raw) if current_raw is not None else candidates[0]
    except ValueError:
        current = candidates[0]

    next_index = (candidates.index(current) + 1) % len(candidates) if current in candidates else 0
    next_bot = candidates[next_index]

    new_ctx = fsm.transition(user_id, FSMState.TRADE_STAGING, trade_result_id=result_id, trade_bot=next_bot.value)
    return new_ctx, scored, next_bot


def approve_and_get_link(
    fsm: FSMEngine, session_store: SessionStore, user_id: int
) -> tuple[FSMContext, ScoredResult, TradingBot, str] | None:
    """THE ONLY function in this codebase that returns a usable external
    trading URL (module docstring's Hard Security Boundary) — and even
    this one only after `enter_trade_staging`/`change_target_bot` have
    already put a real (result, bot) pair in the FSM payload and the
    user has themselves tapped "Approve & Open Bot" on a screen that
    already showed the non-custodial disclaimer. Transitions to Idle
    unconditionally on success (Part II.4: "approve or abort — both land
    [in Idle]") — approval is a terminal action for this flow, same as
    abort. `None` (no transition, no link) for the same "nothing
    currently staged" conditions `change_target_bot` returns `None` for,
    plus the (currently unreachable in practice — see
    `integrations/providers.py`) case where `build_deep_link` itself
    raises `UnsupportedChainError`, treated identically rather than
    letting that exception escape to the Dispatcher's generic handler.
    """
    ctx = fsm.get_state(user_id)
    result_id = ctx.payload.get("trade_result_id")
    if not isinstance(result_id, str):
        return None

    scored = session_store.cache_get(result_id)
    if not isinstance(scored, ScoredResult) or scored.core.chain is None:
        return None

    bot_raw = ctx.payload.get("trade_bot")
    bot: TradingBot | None
    try:
        bot = TradingBot(bot_raw) if bot_raw is not None else None
    except ValueError:
        bot = None
    if bot is None:
        bot = default_bot_for_chain(scored.core.chain)
    if bot is None:
        return None

    try:
        deep_link = build_deep_link(bot, scored.core.chain, scored.core.address)
    except UnsupportedChainError:
        return None

    new_ctx = fsm.transition(user_id, FSMState.IDLE)
    return new_ctx, scored, bot, deep_link


def abort_trade_staging(fsm: FSMEngine, user_id: int) -> FSMContext:
    """Part IV.1's Acceptance Criteria, implemented literally: "abort at
    any point in Trade Staging returns to Idle with no URL ever
    generated." No lookup, no session access, no way for this function
    to produce anything other than an Idle transition — there is
    nothing here that COULD leak into a trading action even by future
    accident, which is the point."""
    return fsm.transition(user_id, FSMState.IDLE)


class TradeStagingHandler:
    """The Telegram-I/O shell — sends/edits messages, reads callback
    params, delegates every actual decision to the four functions above.
    Owns `exec_stage`/`exec_approve`/`exec_change_bot`/`exec_abort`
    (Part V.6's `exec_` prefix, reserved for this handler since Step 1 —
    see `bot/constants.py`'s `CALLBACK_PREFIXES` comment) plus
    `result_back_detail` (reserved since Step 3 — see
    `rendering/menus.py`'s module docstring, deviation #2)."""

    def __init__(self, fsm: FSMEngine, session_store: SessionStore) -> None:
        self._fsm = fsm
        self._session_store = session_store

    async def can_handle(self, event: TelegramEvent, ctx: FSMContext) -> bool:
        if not isinstance(event, CallbackQuery):
            return False
        parsed = parse_callback(event.data or "")
        return parsed.command in _OWNED_CALLBACK_COMMANDS

    async def _show_unavailable(self, event: CallbackQuery, user_id: int) -> None:
        """STEP 15: the four call sites below all reached for the same
        `render_plain(_UNAVAILABLE_MESSAGE)` independently - exactly the
        "duplicate ad-hoc error rendering" Step 15's Error Integration
        section asks to remove. One shared, unified call now, with a
        real technical-detail slot none of the four had before."""
        show_technical = self._session_store.get_settings(user_id).show_technical_errors
        await send_rendered(
            event,
            render_error(
                _UNAVAILABLE_ERROR,
                technical_detail="No cached ScoredResult for this trade-staging result id (expired uuid, process restart, or an unresolved chain).",
                show_technical=show_technical,
                recovery_action=RECOVERY_HOME,
            ),
        )

    async def handle(self, event: TelegramEvent, ctx: FSMContext) -> None:
        assert isinstance(event, CallbackQuery) and event.from_user is not None
        user_id = event.from_user.id
        parsed = parse_callback(event.data or "")

        if parsed.command == "exec_stage":
            await self._handle_stage(event, user_id, parsed.params)
        elif parsed.command == "exec_change_bot":
            await self._handle_change_bot(event, user_id)
        elif parsed.command == "exec_approve":
            await self._handle_approve(event, user_id)
        elif parsed.command == "exec_abort":
            await self._handle_abort(event, user_id)
        elif parsed.command == "result_back_detail":
            await self._handle_back_to_detail(event, user_id)

    async def _handle_stage(self, event: CallbackQuery, user_id: int, params: list[str]) -> None:
        result_id = params[0] if params else ""
        outcome = enter_trade_staging(self._fsm, self._session_store, user_id, result_id)
        if outcome is None:
            await self._show_unavailable(event, user_id)
            return
        _ctx, scored, bot = outcome
        await send_rendered(event, render_trade_staging(scored, bot))

    async def _handle_change_bot(self, event: CallbackQuery, user_id: int) -> None:
        outcome = change_target_bot(self._fsm, self._session_store, user_id)
        if outcome is None:
            await self._show_unavailable(event, user_id)
            return
        _ctx, scored, bot = outcome
        await send_rendered(event, render_trade_staging(scored, bot))

    async def _handle_approve(self, event: CallbackQuery, user_id: int) -> None:
        outcome = approve_and_get_link(self._fsm, self._session_store, user_id)
        if outcome is None:
            await self._show_unavailable(event, user_id)
            return
        _ctx, scored, bot, deep_link = outcome
        await send_rendered(event, render_trade_link_ready(scored, bot, deep_link))

    async def _handle_abort(self, event: CallbackQuery, user_id: int) -> None:
        abort_trade_staging(self._fsm, user_id)
        await send_rendered(event, render_plain("\u274c Trade staging aborted. No external action was taken."))

    async def _handle_back_to_detail(self, event: CallbackQuery, user_id: int) -> None:
        ctx = self._fsm.get_state(user_id)
        result_id = ctx.payload.get("trade_result_id")
        scored = self._session_store.cache_get(result_id) if isinstance(result_id, str) else None
        if not isinstance(scored, ScoredResult):
            await self._show_unavailable(event, user_id)
            return
        self._fsm.transition(user_id, FSMState.RESULT_DETAIL)
        await send_rendered(event, render_result_detail(scored))
