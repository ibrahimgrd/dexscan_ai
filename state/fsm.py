"""
Layer: State — FSM engine (Playbook Part VIII Step 2; table in Part II.4).

Encodes the conversation as a static adjacency map: which states each
state may transition to. Built from Part II.4's literal "Exits to" column
plus seven documented deviations (was "four" through Step 7's own patch;
corrected here in passing while adding #6/#7 below, itself a small
instance of the same "flag it explicitly, don't let small drift
accumulate silently" rule this list already exists to demonstrate) —
flagged here rather than silently patched, per the playbook's own "flag
it explicitly" rule (see the "How To Use This Document" section):

1. Part II.4 doesn't literally list IDLE as an exit of AWAITING_ADDRESS,
   SCANNING, or RESULT_DETAIL — but Part II.5's universal Back/Home rule,
   and Part II.9's per-screen table (Scanning's Cancel, an implicit
   Back-out of the paste-address prompt, Result Detail's Home target),
   all require a path back to Idle from each of these three. Added below.
2. Part II.4 states ERROR is "Entered from: Any" — encoded by adding ERROR
   to every other state's exit set, rather than repeating it nine times.
3. ERROR's own exits ("Idle, or back to the failed state") are inherently
   dynamic — which state is "the failed state" depends on per-user
   history, not a fixed pair. Modeled as fully permissive: any other state
   is a valid target *from* Error. It's the calling handler's job (Step 15's
   error-recovery screen, most concretely) to decide whether to send the
   user to Idle or to whatever it reads back from
   `get_state(user_id).payload.get("pre_error_state")` — this module only
   guarantees that decision won't be blocked by the adjacency map.
4. TRADE_STAGING -> RESULT_DETAIL: Part II.9's screen table gives Trade
   Staging a Back target of Result Detail, distinct from the Idle-only
   approve/abort outcomes Part II.4's literal table describes — "let me
   look at the report again" is a softer action than "abort entirely."
5. RESULT_DETAIL -> SCANNING: Step 7's "Rescan" button (built five steps
   after this file) needs this edge and it was never added here — Step 7
   added a UI affordance without circling back to update the FSM map its
   own dispatcher depends on. Found via a real `pytest` run (not this
   sandbox, which can't execute aiogram-dependent handler code at all) -
   the button was live in the rendered UI, completely non-functional in
   practice, and silently swallowed into a generic "something went
   wrong" message by Dispatcher's own exception safety net (Part II
   Step 2) rather than ever surfacing as a visible crash. Exactly the
   failure mode Part VI's execution protocol step 2 ("verify interfaces
   remain compatible") exists to catch — missed at the time because nothing
   in this sandbox could execute the actual handler chain to catch it.
6. TRADE_STAGING -> TRADE_STAGING (self-loop): added during the Step 11
   integration pass — "Change Target Bot" re-renders the same screen
   with a different bot selected without leaving it, the same shape
   AUTO_WATCH_ACTIVE's own self-loop below already has for "status
   refresh / still running." Missing until now for the same root cause
   as deviation #5: nothing before Step 11 ever transitioned INTO Trade
   Staging at all (a placeholder screen, per `rendering/menus.py`'s
   Step-3-era docstring), so no caller had ever exercised this edge.
   Caught this time by a real `pytest`/aiogram run in this sandbox
   (network access confirmed available this session, unlike the
   sandbox deviation #5's own note describes) before shipping, not
   after — see `handlers/trade_staging_handler.py`'s own tests.
7. AUTO_WATCH_ACTIVE -> RESULT_DETAIL: also found via that same Step 11
   test run, same root cause again — `render_watch_alert`'s own
   "View Full Report" button uses the exact same `result_view:{uuid}`
   callback (and the exact same `ScanHandler._show_result_detail`
   handler) a normal Result List's button does, and that handler
   transitions to RESULT_DETAIL unconditionally regardless of where the
   user tapped it from (Step 7's own design — see `handlers/
   scan_handler.py`). Auto-Watch runs as a continuing background task
   independent of FSM navigation (`state.session_store.SessionStore`'s
   watch-task registry, not FSM state, tracks whether it's still
   running) — a person is in AUTO_WATCH_ACTIVE for as long as it's on,
   which is exactly when an alert (and a tap on it) actually happens, so
   this edge is not an edge case: without it, every single real
   Auto-Watch alert's own "View Full Report" button raises
   `InvalidTransitionError` on first tap, caught only by Dispatcher's
   generic safety net. One-directional on purpose — the watch itself
   isn't paused by looking at one alert's detail (no explicit "resume
   watching" action exists to pair a reverse edge with); returning to
   Idle (already valid from Result Detail) and re-opening Auto-Watch
   from the Main Menu shows the same still-running status either way,
   proven in `tests/test_watch_flow_integration.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from bot.constants import FSMState
from bot.types import SessionContext
from state.session_store import SessionStore


class InvalidTransitionError(Exception):
    """
    Raised when `FSMEngine.transition` is asked to move to a target that
    `_ADJACENCY` doesn't allow from the current state. Signals a bug in
    the calling handler, not a recoverable user-facing condition — a
    correctly built handler never offers a button that would trigger
    this (Part VIII Step 2 constraint: "raises a typed
    InvalidTransitionError rather than allowing silent bad state").
    """

    def __init__(self, current: FSMState, target: FSMState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid FSM transition: {current.value} -> {target.value}")


@dataclass
class FSMContext:
    """A user's current FSM position plus whatever payload the active
    state needs (e.g. the chain picked while AwaitingAddress, or
    `pre_error_state`/`error_reason` while in Error)."""

    state: FSMState = FSMState.IDLE
    payload: dict[str, Any] = field(default_factory=dict)


_ADJACENCY: dict[FSMState, set[FSMState]] = {
    FSMState.IDLE: {
        FSMState.IDLE,                 # browsing Main Menu / Settings / Help / About / My Filters
        FSMState.AWAITING_ADDRESS,     # Scan Menu -> Paste Contract Address
        FSMState.CONFIGURING_FILTER,   # Scan Menu -> Custom Filter
        FSMState.SCANNING,             # Scan Menu -> Trending Pairs / New Listings (direct)
        FSMState.AUTO_WATCH_ACTIVE,    # Main Menu -> Auto-Watch -> Start
    },
    FSMState.AWAITING_ADDRESS: {
        FSMState.SCANNING,             # valid address submitted
        FSMState.IDLE,                 # documented addition #1 - Back out to Scan Menu
    },
    FSMState.CONFIGURING_FILTER: {
        FSMState.CONFIGURING_FILTER,   # documented addition #8 (Step 12 revalidation) -
                                        # Advanced Rules' rule_tgl/rule_num buttons stay on
                                        # this same screen after every tap, the same shape
                                        # as TRADE_STAGING's #6 and AUTO_WATCH_ACTIVE's own
                                        # self-loop above. Caught by inspection this time,
                                        # before it shipped, not by a live pytest run - see
                                        # this step's handoff note on why that's still a
                                        # lesser guarantee than the two prior fixes had.
        FSMState.SCANNING,             # filter applied, scan starts
        FSMState.IDLE,                 # cancelled back to Scan Menu / Main Menu
    },
    FSMState.SCANNING: {
        FSMState.RESULT_READY,         # scan completed with at least one match
        FSMState.IDLE,                 # documented addition #1 - user cancelled
    },
    FSMState.RESULT_READY: {
        FSMState.RESULT_DETAIL,        # a result tapped
        FSMState.IDLE,                 # Home
    },
    FSMState.RESULT_DETAIL: {
        FSMState.TRADE_STAGING,        # Buy tapped
        FSMState.RESULT_READY,         # Back to list
        FSMState.IDLE,                 # documented addition #1 - Home
        FSMState.SCANNING,             # documented addition #5 - Rescan (Step 7)
    },
    FSMState.TRADE_STAGING: {
        FSMState.TRADE_STAGING,        # documented addition #6 (Step 11) - "Change Target
                                        # Bot" stays on this screen with a new bot selected.
        FSMState.IDLE,                 # approve (link handed off) or abort - both land here
        FSMState.RESULT_DETAIL,        # Part II.9's screen table: Trade Staging's Back
                                        # target is Result Detail, not just Idle - "let me
                                        # look at the report again" is softer than "abort".
                                        # Missed in an earlier pass; added on Step 2 review.
    },
    FSMState.AUTO_WATCH_ACTIVE: {
        FSMState.AUTO_WATCH_ACTIVE,    # status refresh / still running
        FSMState.IDLE,                 # stopped
        FSMState.RESULT_DETAIL,        # documented addition #7 (Step 11) - alert's own
                                        # "View Full Report" (result_view, same handler
                                        # Result List's button uses) - the watch itself
                                        # keeps running in the background either way.
    },
}

# Documented addition #2 - ERROR is reachable from literally any state.
for _targets in _ADJACENCY.values():
    _targets.add(FSMState.ERROR)

# Documented addition #3 - ERROR's own exits are fully permissive; see
# module docstring for why a fixed subset can't represent "back to the
# failed state" correctly.
_ADJACENCY[FSMState.ERROR] = {s for s in FSMState if s is not FSMState.ERROR}


class FSMEngine:
    """
    Thin wrapper over `SessionStore` (Step 1) — stores each user's
    `FSMContext` at `SessionContext.payload["fsm"]` (see the design note
    in bot/types.py), so `SessionStore` never needs to know the FSM
    exists. No new store is created here (Step 2 constraint: "uses
    SessionStore from Step 1, never a new store").
    """

    def __init__(self, session_store: SessionStore) -> None:
        self._store = session_store

    def get_state(self, user_id: int) -> FSMContext:
        """Returns the user's current FSM context, defaulting to a fresh
        Idle context on first contact — callers never null-check."""
        session_ctx = self._store.get(user_id)
        if session_ctx is None or "fsm" not in session_ctx.payload:
            return FSMContext()
        # payload is dict[str, Any] (Step 1) - explicit cast rather than an
        # implicit Any return, since mypy --strict's warn-return-any would
        # otherwise flag this (Part V.1: "mypy --strict is the target, not
        # an aspiration"). Safe because only this class ever writes the
        # "fsm" key (see _persist below).
        return cast(FSMContext, session_ctx.payload["fsm"])

    def is_valid_transition(self, current: FSMState, target: FSMState) -> bool:
        return target in _ADJACENCY.get(current, set())

    def transition(self, user_id: int, new_state: FSMState, **payload: Any) -> FSMContext:
        """
        Moves `user_id` to `new_state`, merging `payload` into the FSM
        context's existing payload (new keys win on conflict). Raises
        `InvalidTransitionError` if `_ADJACENCY` doesn't allow the move.
        """
        current_ctx = self.get_state(user_id)

        if not self.is_valid_transition(current_ctx.state, new_state):
            raise InvalidTransitionError(current_ctx.state, new_state)

        merged_payload = {**current_ctx.payload, **payload}

        # Once we leave Error, its bookkeeping fields stop being
        # meaningful - drop them so a *future*, unrelated fault doesn't
        # inherit a stale pre_error_state left over from this one.
        if current_ctx.state is FSMState.ERROR and new_state is not FSMState.ERROR:
            merged_payload.pop("pre_error_state", None)
            merged_payload.pop("error_reason", None)

        new_ctx = FSMContext(state=new_state, payload=merged_payload)
        self._persist(user_id, new_ctx)
        return new_ctx

    def _persist(self, user_id: int, fsm_ctx: FSMContext) -> None:
        session_ctx = self._store.get(user_id)
        if session_ctx is None:
            session_ctx = SessionContext(user_id=user_id)
        session_ctx.payload["fsm"] = fsm_ctx
        self._store.set(user_id, session_ctx)
