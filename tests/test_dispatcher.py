"""
Playbook reference: Unified Developer Playbook, Part VIII Step 2 - Unit
Testing Requirements: three fake handlers, only the first matching one
runs; an unknown user_id on a callback still returns a valid Idle-state
recovery response.

Requires aiogram to be installed (handlers.dispatcher imports
aiogram.types for its isinstance check) - see requirements.txt.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery

from bot.constants import FSMState
from handlers.dispatcher import Dispatcher
from state.fsm import FSMContext, FSMEngine
from state.session_store import SessionStore


def _fake_callback_query(user_id: int = 1, data: str = "some_callback") -> CallbackQuery:
    cq = MagicMock(spec=CallbackQuery)
    cq.data = data
    cq.from_user = MagicMock(id=user_id)
    cq.answer = AsyncMock()
    cq.message = MagicMock()
    cq.message.edit_text = AsyncMock()
    return cq


class _RecordingHandler:
    """A fake Handler (the playbook's Handler protocol) that records
    whether it ran, for asserting "only the first match runs"."""

    def __init__(self, matches: bool) -> None:
        self._matches = matches
        self.handled = False

    async def can_handle(self, event: Any, ctx: FSMContext) -> bool:
        return self._matches

    async def handle(self, event: Any, ctx: FSMContext) -> None:
        self.handled = True


@pytest.mark.asyncio
async def test_only_first_matching_handler_runs() -> None:
    fsm = FSMEngine(SessionStore())
    dispatcher = Dispatcher(fsm)

    first_no_match = _RecordingHandler(matches=False)
    second_match = _RecordingHandler(matches=True)
    third_would_also_match = _RecordingHandler(matches=True)

    dispatcher.register(first_no_match)
    dispatcher.register(second_match)
    dispatcher.register(third_would_also_match)

    await dispatcher.dispatch(_fake_callback_query())

    assert first_no_match.handled is False
    assert second_match.handled is True
    assert third_would_also_match.handled is False


@pytest.mark.asyncio
async def test_callback_gets_immediate_acknowledgment() -> None:
    fsm = FSMEngine(SessionStore())
    dispatcher = Dispatcher(fsm)
    dispatcher.register(_RecordingHandler(matches=True))

    cq = _fake_callback_query()
    await dispatcher.dispatch(cq)

    cq.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_user_id_still_gets_idle_recovery_response() -> None:
    """Step 2 stale-session test: an unknown user_id on a callback still
    returns a valid Idle-state recovery response, exercised against the
    real handlers.navigation chain (not a fake), since this specifically
    validates the catch-all's real end-to-end behavior."""
    from handlers.navigation import build_navigation_handlers

    session_store = SessionStore()
    fsm = FSMEngine(session_store)
    dispatcher = Dispatcher(fsm)
    for handler in build_navigation_handlers(fsm, session_store):
        dispatcher.register(handler)

    never_seen_user_id = 424242
    cq = _fake_callback_query(user_id=never_seen_user_id, data="totally_unknown_callback")

    await dispatcher.dispatch(cq)

    assert fsm.get_state(never_seen_user_id).state is FSMState.IDLE
    cq.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_noop_pagination_button_does_not_reset_state_or_show_stale_error() -> None:
    """STEP 16 regression test: "noop" (the pagination page-indicator,
    e.g. "2/5") used to fall through to UnknownInputHandler, which reset
    the user to Idle and rendered a "that menu has expired" error - a
    real bug found during this step's FSM/callback audit (a callback
    string generated in rendering/result_renderer.py with no handler
    anywhere). NoopHandler now claims it and does nothing, so the user's
    actual state and screen must be completely undisturbed."""
    from handlers.navigation import build_navigation_handlers

    session_store = SessionStore()
    fsm = FSMEngine(session_store)
    user_id = 777
    fsm.transition(user_id, FSMState.SCANNING)
    dispatcher = Dispatcher(fsm)
    for handler in build_navigation_handlers(fsm, session_store):
        dispatcher.register(handler)

    cq = _fake_callback_query(user_id=user_id, data="noop")
    await dispatcher.dispatch(cq)

    assert fsm.get_state(user_id).state is FSMState.SCANNING
    cq.answer.assert_awaited_once()  # Part II.8 ack still happens
    cq.message.edit_text.assert_not_awaited()  # but nothing about the screen changes
    class _ExplodingHandler:
        async def can_handle(self, event: Any, ctx: FSMContext) -> bool:
            return True

        async def handle(self, event: Any, ctx: FSMContext) -> None:
            raise RuntimeError("simulated bug in a handler")

    fsm = FSMEngine(SessionStore())
    dispatcher = Dispatcher(fsm)
    dispatcher.register(_ExplodingHandler())

    cq = _fake_callback_query()
    await dispatcher.dispatch(cq)  # must not raise

    cq.message.edit_text.assert_awaited_once()
