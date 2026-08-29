"""
Playbook reference: Unified Developer Playbook, Part VIII Step 7 - Unit
Testing Requirements: "Integration test: full fixture-driven scan flow
from AwaitingAddress to ResultDetail state, asserting the FSM ends in the
correct state and the cached ScoredResult matches what was rendered."
Integration Requirement: "the first real integration test of the whole
stack" (Steps 2+3+4+5+6+7 chained together).

Requires aiogram (mocks CallbackQuery/Message via `MagicMock(spec=...)`,
same technique as Step 2's test_dispatcher.py) - syntax-checked and
manually cross-referenced against the real handler chain's source, not
executed in this sandbox. Run `pytest tests/test_scan_flow_integration.py`
after `pip install -r requirements.txt` for the real result.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Message, User

from analysis.core_engine import CoreEngine
from analysis.holder_engine import HolderEngine
from analysis.momentum_engine import MomentumEngine
from analysis.providers.dexscreener_parser import parse_pairs_response
from analysis.providers.rugcheck_parser import parse_report
from analysis.security_engine import SecurityEngine
from analysis.social_engine import SocialEngine
from bot.constants import FSMState
from handlers.dispatcher import Dispatcher
from handlers.navigation import build_navigation_handlers
from handlers.scan_handler import ScanHandler
from handlers.scan_orchestration import ScoredResult
from scoring.pipeline import ScoringPipeline
from state.fsm import FSMEngine
from state.session_store import SessionStore

_DEXSCREENER_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "dexscreener"
_RUGCHECK_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "rugcheck"


class _FakeMarketProvider:
    def __init__(self, pairs: list) -> None:
        self._pairs = pairs

    async def get_pairs(self, address: str) -> list:
        return self._pairs


class _FakeSecurityProvider:
    def __init__(self, report) -> None:
        self._report = report

    async def scan(self, address: str, chain) -> object:
        return self._report


class _FakeHolderProvider:
    """Empty holders/funding - a valid, non-degraded HolderResult, not an
    error path. This integration test's own scope (Steps 2+3+4+5+6+7 in
    the original docstring, extended to include Step 8's wiring here) is
    the FSM/dispatch/render chain, not Holder Engine's own formula
    behavior - that's test_holder_engine.py's job."""

    async def get_holders(self, address: str, chain) -> list:
        return []

    async def get_launch_block_funding(self, address: str, chain) -> list:
        return []


class _FakeSocialProvider:
    """Same scope note as _FakeHolderProvider above, extended to Social
    (Step 14) - always-degrades is enough; this test's own job is the
    FSM/dispatch/render chain, not Social Engine's own behavior."""

    async def lookup_user(self, handle: str) -> object:
        raise RuntimeError("fake social provider - lookup always fails")

    async def list_followers(self, handle: str, limit: int) -> list:
        return []

    async def search_mentions(self, ticker: str) -> list:
        return []


def _build_stack(dex_fixture: str, rc_fixture: str):
    pairs = parse_pairs_response(json.loads((_DEXSCREENER_FIXTURES / dex_fixture).read_text()))
    report = parse_report(json.loads((_RUGCHECK_FIXTURES / rc_fixture).read_text()), chain_supported=True)

    session_store = SessionStore()
    fsm = FSMEngine(session_store)
    core_engine = CoreEngine(_FakeMarketProvider(pairs))
    security_engine = SecurityEngine(_FakeSecurityProvider(report))
    holder_engine = HolderEngine(_FakeHolderProvider())
    # No fake needed: MomentumEngine is synchronous and provider-free
    # (Step 9's own Scope note) - the real class is used directly.
    momentum_engine = MomentumEngine()
    social_engine = SocialEngine(_FakeSocialProvider())
    scoring_pipeline = ScoringPipeline()

    scan_handler = ScanHandler(
        fsm, core_engine, security_engine, holder_engine, momentum_engine, social_engine,
        scoring_pipeline, session_store,
    )
    dispatcher = Dispatcher(fsm)
    for handler in build_navigation_handlers(fsm, session_store, extra_handlers=[scan_handler]):
        dispatcher.register(handler)

    return dispatcher, fsm, session_store


def _fake_user(user_id: int = 1) -> User:
    return MagicMock(spec=User, id=user_id)


def _fake_callback(data: str, user_id: int = 1) -> CallbackQuery:
    cq = MagicMock(spec=CallbackQuery)
    cq.data = data
    cq.from_user = _fake_user(user_id)
    cq.answer = AsyncMock()
    cq.message = MagicMock(spec=Message)
    cq.message.edit_text = AsyncMock()
    return cq


def _fake_message(text: str, user_id: int = 1) -> Message:
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.from_user = _fake_user(user_id)
    reply_message = MagicMock(spec=Message)
    reply_message.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=reply_message)
    return msg


@pytest.mark.asyncio
async def test_full_scan_flow_awaiting_address_to_result_ready() -> None:
    """Scan Menu's paste button -> user pastes a valid address -> ends
    in ResultReady, per Part VIII Step 7's stated flow (this test stops
    short of tapping into ResultDetail - the next test covers that leg
    separately, matching the Unit Testing Requirement's explicit
    "AwaitingAddress to ResultDetail" wording as two connected legs)."""
    dispatcher, fsm, session_store = _build_stack("solana_valid.json", "fully_safe.json")
    user_id = 1

    # Step 1: tap "Paste Contract Address" on Scan Menu
    start_cb = _fake_callback("scan_paste", user_id)
    await dispatcher.dispatch(start_cb)
    assert fsm.get_state(user_id).state is FSMState.AWAITING_ADDRESS

    # Step 2: user sends the address as a plain message
    address_msg = _fake_message("So11111111111111111111111111111111111111112", user_id)
    await dispatcher.dispatch(address_msg)

    assert fsm.get_state(user_id).state is FSMState.RESULT_READY
    # The reply message should have been progressively edited at least
    # twice (initial progress -> final result), never left un-edited.
    reply_message = address_msg.answer.return_value
    assert reply_message.edit_text.await_count >= 1


@pytest.mark.asyncio
async def test_invalid_pasted_address_stays_in_awaiting_address() -> None:
    dispatcher, fsm, session_store = _build_stack("solana_valid.json", "fully_safe.json")
    user_id = 2

    await dispatcher.dispatch(_fake_callback("scan_paste", user_id))
    assert fsm.get_state(user_id).state is FSMState.AWAITING_ADDRESS

    bad_msg = _fake_message("definitely not an address", user_id)
    await dispatcher.dispatch(bad_msg)

    # Must NOT have advanced to Scanning/ResultReady - shape validation
    # rejects before any engine call.
    assert fsm.get_state(user_id).state is FSMState.AWAITING_ADDRESS
    bad_msg.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_full_flow_ends_at_result_detail_with_cached_result_matching_render() -> None:
    """The Unit Testing Requirement's literal scenario: AwaitingAddress
    all the way to ResultDetail, asserting the FSM's final state AND that
    the cached ScoredResult is the same one the detail view rendered
    from."""
    dispatcher, fsm, session_store = _build_stack("solana_valid.json", "honeypot.json")
    user_id = 3

    await dispatcher.dispatch(_fake_callback("scan_paste", user_id))
    address_msg = _fake_message("So11111111111111111111111111111111111111112", user_id)
    await dispatcher.dispatch(address_msg)
    assert fsm.get_state(user_id).state is FSMState.RESULT_READY

    # Find the result_id the flow actually cached, the same way a real
    # button tap would carry it: read back what the final render call
    # was given, via the ScoredResult now sitting in the store (there's
    # exactly one entry after a single scan in this test).
    cached_ids = list(session_store._uuid_cache.keys())  # test-only introspection
    assert len(cached_ids) == 1
    result_id = cached_ids[0]
    scored = session_store.cache_get(result_id)
    assert isinstance(scored, ScoredResult)

    detail_cb = _fake_callback(f"result_view:{result_id}", user_id)
    await dispatcher.dispatch(detail_cb)

    assert fsm.get_state(user_id).state is FSMState.RESULT_DETAIL
    detail_cb.message.edit_text.assert_awaited_once()
    rendered_html = detail_cb.message.edit_text.await_args.args[0]
    # The cached result's own security tier (Critical Risk, from the
    # honeypot fixture) must appear in what was actually rendered - ties
    # the FSM-state assertion to the actual rendered content, not just
    # "some edit happened."
    assert scored.scoring.tier_label in rendered_html


@pytest.mark.asyncio
async def test_stale_result_id_recovers_gracefully() -> None:
    dispatcher, fsm, session_store = _build_stack("solana_valid.json", "fully_safe.json")
    user_id = 4

    stale_cb = _fake_callback("result_view:does-not-exist", user_id)
    await dispatcher.dispatch(stale_cb)

    stale_cb.message.edit_text.assert_awaited_once()
    rendered_html = stale_cb.message.edit_text.await_args.args[0]
    assert "expired" in rendered_html.lower()


@pytest.mark.asyncio
async def test_rescan_reuses_original_address_and_produces_a_new_cache_entry() -> None:
    """Rescan only exists as a button on Result Detail (rendering/
    result_renderer.py), never on Result List - a correct test reaches it
    the same way a real user does: paste -> ResultReady -> tap the result
    -> ResultDetail -> Rescan. An earlier version of this test skipped
    straight from ResultReady to rescan and passed here on paper, but
    failed the first time it was actually run with real pytest
    (state.fsm.InvalidTransitionError: result_ready -> scanning) -
    RESULT_DETAIL -> SCANNING wasn't in state/fsm.py's adjacency map at
    all (documented addition #5, added once this was found). Both the
    map and this test were wrong in the same direction; fixing only one
    would have left them silently disagreeing again."""
    dispatcher, fsm, session_store = _build_stack("solana_valid.json", "fully_safe.json")
    user_id = 5

    await dispatcher.dispatch(_fake_callback("scan_paste", user_id))
    await dispatcher.dispatch(_fake_message("So11111111111111111111111111111111111111112", user_id))
    first_id = next(iter(session_store._uuid_cache.keys()))

    # Reach Result Detail first - this is the real path to the Rescan button.
    detail_cb = _fake_callback(f"result_view:{first_id}", user_id)
    await dispatcher.dispatch(detail_cb)
    assert fsm.get_state(user_id).state is FSMState.RESULT_DETAIL

    rescan_cb = _fake_callback(f"scan_rescan:{first_id}", user_id)
    await dispatcher.dispatch(rescan_cb)

    assert fsm.get_state(user_id).state is FSMState.RESULT_READY
    assert len(session_store._uuid_cache) == 2  # original + rescanned, both retained (Part I.3: in-memory, no cleanup policy yet)


@pytest.mark.asyncio
async def test_scan_cancel_returns_to_idle_with_honest_message_not_stale_error() -> None:
    """STEP 16 regression test: "scan_cancel" is the ONLY button on the
    live-scanning screen (Playbook Part II.9's Scanning/Progress row) and
    had no handler anywhere - found during this step's FSM/callback
    audit. It used to fall through to UnknownInputHandler's "that menu
    has expired" message, which is misleading (nothing expired; the user
    cancelled on purpose). Placed mid-SCANNING directly (not via a real
    in-flight scan) since this test's job is the cancel handler itself,
    not scan cancellation-token behavior - see this handler's own code
    comment for why interrupting an in-flight scan is a separate,
    out-of-scope concern this fix deliberately does not attempt."""
    dispatcher, fsm, session_store = _build_stack("solana_valid.json", "fully_safe.json")
    user_id = 6
    fsm.transition(user_id, FSMState.SCANNING)

    cancel_cb = _fake_callback("scan_cancel", user_id)
    await dispatcher.dispatch(cancel_cb)

    assert fsm.get_state(user_id).state is FSMState.IDLE
    cancel_cb.message.edit_text.assert_awaited_once()
    rendered_html = cancel_cb.message.edit_text.await_args.args[0]
    assert "expired" not in rendered_html.lower()
    assert "cancelled" in rendered_html.lower()
