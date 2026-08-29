"""
Playbook reference: Unified Developer Playbook, Part VIII Step 2 - Unit
Testing Requirements: parametrized test over every (current, target) pair
in Part II.4's table. Valid pairs succeed; all others raise
InvalidTransitionError.

The expected-valid table below is hand-encoded independently from
`state.fsm._ADJACENCY`, so this test actually checks the implementation
against the playbook's Part II.4 table (plus the seven documented
additions justified by Part II.5/II.9 - see state/fsm.py's module
docstring) rather than checking the map against itself.

Pure stdlib + this project's own modules only - no pydantic/aiogram
dependency, so this file can run even before those packages are installed.
"""

from __future__ import annotations

import itertools

import pytest

from bot.constants import FSMState
from bot.types import SessionContext
from state.fsm import FSMContext, FSMEngine, InvalidTransitionError
from state.session_store import SessionStore

_EXPECTED_VALID: dict[FSMState, set[FSMState]] = {
    FSMState.IDLE: {
        FSMState.IDLE, FSMState.AWAITING_ADDRESS, FSMState.CONFIGURING_FILTER,
        FSMState.SCANNING, FSMState.AUTO_WATCH_ACTIVE, FSMState.ERROR,
    },
    FSMState.AWAITING_ADDRESS: {FSMState.SCANNING, FSMState.IDLE, FSMState.ERROR},
    # documented addition #8 (Step 12 revalidation) - Advanced Rules'
    # rule_tgl/rule_num buttons stay on this same screen after every tap.
    FSMState.CONFIGURING_FILTER: {
        FSMState.CONFIGURING_FILTER, FSMState.SCANNING, FSMState.IDLE, FSMState.ERROR,
    },
    FSMState.SCANNING: {FSMState.RESULT_READY, FSMState.IDLE, FSMState.ERROR},
    FSMState.RESULT_READY: {FSMState.RESULT_DETAIL, FSMState.IDLE, FSMState.ERROR},
    # RESULT_DETAIL: documented addition #4 - Trade Staging's Back target
    # (Part II.9) is Result Detail, not just the approve/abort-to-Idle
    # outcomes Part II.4's literal table shows. Documented addition #5 -
    # SCANNING, for Step 7's Rescan button (found missing via a real
    # pytest run; this sandbox can't execute the handler code that
    # actually needs this edge).
    FSMState.RESULT_DETAIL: {
        FSMState.TRADE_STAGING, FSMState.RESULT_READY, FSMState.IDLE,
        FSMState.SCANNING, FSMState.ERROR,
    },
    FSMState.TRADE_STAGING: {
        # documented addition #6 (Step 11) - "Change Target Bot" stays here.
        FSMState.TRADE_STAGING, FSMState.IDLE, FSMState.RESULT_DETAIL, FSMState.ERROR,
    },
    # documented addition #7 (Step 11) - AUTO_WATCH_ACTIVE -> RESULT_DETAIL,
    # for an alert's own "View Full Report" (see state/fsm.py's docstring).
    FSMState.AUTO_WATCH_ACTIVE: {
        FSMState.AUTO_WATCH_ACTIVE, FSMState.IDLE, FSMState.RESULT_DETAIL, FSMState.ERROR,
    },
    # Documented addition #3 (state/fsm.py): fully permissive, since "the
    # failed state" is dynamic per user, not a fixed pair.
    FSMState.ERROR: {s for s in FSMState if s is not FSMState.ERROR},
}

_ALL_PAIRS = list(itertools.product(FSMState, FSMState))


def _engine_with_state(current: FSMState) -> tuple[FSMEngine, int]:
    store = SessionStore()
    user_id = 1
    store.set(user_id, SessionContext(user_id=user_id, payload={"fsm": FSMContext(state=current)}))
    return FSMEngine(store), user_id


@pytest.mark.parametrize("current,target", _ALL_PAIRS)
def test_transition_matrix(current: FSMState, target: FSMState) -> None:
    engine, user_id = _engine_with_state(current)
    expected_valid = target in _EXPECTED_VALID[current]

    assert engine.is_valid_transition(current, target) is expected_valid

    if expected_valid:
        new_ctx = engine.transition(user_id, target)
        assert new_ctx.state is target
    else:
        with pytest.raises(InvalidTransitionError):
            engine.transition(user_id, target)


def test_every_state_is_reachable_as_a_target() -> None:
    """Definition of Done: all nine FSMState values must be reachable via
    at least one valid transition somewhere in the table."""
    reachable = {target for targets in _EXPECTED_VALID.values() for target in targets}
    assert reachable == set(FSMState)


def test_get_state_defaults_to_idle_for_unknown_user() -> None:
    engine = FSMEngine(SessionStore())
    ctx = engine.get_state(user_id=999)
    assert ctx.state is FSMState.IDLE
    assert ctx.payload == {}


def test_transition_persists_across_get_state_calls() -> None:
    engine = FSMEngine(SessionStore())
    engine.transition(user_id=7, new_state=FSMState.AWAITING_ADDRESS)
    assert engine.get_state(7).state is FSMState.AWAITING_ADDRESS


def test_transition_merges_payload_without_dropping_existing_keys() -> None:
    engine = FSMEngine(SessionStore())
    engine.transition(user_id=7, new_state=FSMState.AWAITING_ADDRESS, chain="sol")
    ctx = engine.transition(user_id=7, new_state=FSMState.SCANNING, query_id="abc")
    assert ctx.payload["chain"] == "sol"
    assert ctx.payload["query_id"] == "abc"


def test_error_bookkeeping_is_dropped_once_left() -> None:
    engine = FSMEngine(SessionStore())
    engine.transition(
        user_id=7, new_state=FSMState.ERROR,
        pre_error_state=FSMState.SCANNING.value, error_reason="timeout",
    )
    recovered = engine.transition(user_id=7, new_state=FSMState.IDLE)
    assert "pre_error_state" not in recovered.payload
    assert "error_reason" not in recovered.payload
