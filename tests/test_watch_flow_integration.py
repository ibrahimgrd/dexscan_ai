"""
Playbook reference: Unified Developer Playbook, Part VIII Step 12.
Integration test mirroring test_scan_flow_integration.py's own stated
purpose, applied to this step: proves the WatchHandler wiring itself
(FSM transitions, callback routing, preset selection persisting across
screens) through the real dispatcher chain, not just AutoWatchManager's
own already-tested internal logic (test_auto_watch.py) or
filter_presets.matches (test_filter_presets.py) in isolation.

Requires aiogram (mocks CallbackQuery via `MagicMock(spec=...)`, same
technique as test_scan_flow_integration.py) and aiohttp (AutoWatchManager
itself doesn't need it, but importing handlers.watch_handler pulls in
rendering.menus -> aiogram, and this test's own discovery/engine fakes
mirror test_auto_watch.py's, not real providers - no real network
anywhere in this file either way).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, User

from analysis.api_abstraction import DiscoveryCandidate, PairData, SecurityReport
from analysis.core_engine import CoreEngine
from analysis.holder_engine import HolderEngine
from analysis.momentum_engine import MomentumEngine
from analysis.security_engine import SecurityEngine
from analysis.social_engine import SocialEngine
from bot.constants import Chain, FSMState
from handlers.auto_watch import AutoWatchManager
from handlers.dispatcher import Dispatcher
from handlers.navigation import build_navigation_handlers
from handlers.scan_handler import ScanHandler
from handlers.watch_handler import WatchHandler
from scoring.pipeline import ScoringPipeline
from state.fsm import FSMEngine
from state.session_store import SessionStore

_TEST_INTERVAL_SECONDS = 0.02


class _FakeDiscoveryProvider:
    async def get_new_listings(self, limit: int = 30) -> list[DiscoveryCandidate]:
        return []

    async def get_trending(self, limit: int = 30) -> list[DiscoveryCandidate]:
        return []


class _FakeMarketProvider:
    async def get_pairs(self, address: str) -> list[PairData]:
        return []


class _FakeSecurityProvider:
    async def scan(self, address: str, chain) -> SecurityReport:
        return SecurityReport(
            trust_score=90.0, mint_authority_active=False, freeze_authority_active=False,
            buy_tax_pct=0.0, sell_tax_pct=0.0, lp_lock_ratio_pct=100.0, lp_lock_duration_days=100.0,
            ownership_renounced=True, raw_risk_flags=[], chain_supported=True,
        )


class _FakeHolderProvider:
    async def get_holders(self, address: str, chain) -> list:
        return []

    async def get_launch_block_funding(self, address: str, chain) -> list:
        return []


class _FakeSocialProvider:
    """This file's tests exercise Auto-Watch/Filters, not social scoring
    - always-degrades is enough (same fake as test_scan_orchestration.py
    and test_trading_integration.py each carry their own copy of)."""

    async def lookup_user(self, handle: str) -> object:
        raise RuntimeError("fake social provider - lookup always fails")

    async def list_followers(self, handle: str, limit: int) -> list:
        return []

    async def search_mentions(self, ticker: str) -> list:
        return []


def _fake_user(user_id: int) -> User:
    return MagicMock(spec=User, id=user_id)


def _fake_callback(data: str, user_id: int) -> CallbackQuery:
    cq = MagicMock(spec=CallbackQuery)
    cq.data = data
    cq.from_user = _fake_user(user_id)
    cq.answer = AsyncMock()
    cq.message = MagicMock()
    cq.message.edit_text = AsyncMock()
    return cq


def _build_stack():
    session_store = SessionStore()
    fsm = FSMEngine(session_store)
    core_engine = CoreEngine(_FakeMarketProvider())
    security_engine = SecurityEngine(_FakeSecurityProvider())
    holder_engine = HolderEngine(_FakeHolderProvider())
    momentum_engine = MomentumEngine()
    social_engine = SocialEngine(_FakeSocialProvider())
    scoring_pipeline = ScoringPipeline()

    alerts: list[tuple[int, object]] = []

    async def on_match(user_id: int, scored) -> None:
        alerts.append((user_id, scored))

    auto_watch_manager = AutoWatchManager(
        session_store=session_store,
        discovery_provider=_FakeDiscoveryProvider(),
        core_engine=core_engine, security_engine=security_engine, holder_engine=holder_engine,
        momentum_engine=momentum_engine, social_engine=social_engine, scoring_pipeline=scoring_pipeline,
        on_match=on_match,
        poll_interval_seconds_override=_TEST_INTERVAL_SECONDS,
    )
    scan_handler = ScanHandler(
        fsm, core_engine, security_engine, holder_engine, momentum_engine, social_engine,
        scoring_pipeline, session_store,
    )
    watch_handler = WatchHandler(
        fsm, auto_watch_manager, _FakeDiscoveryProvider(),
        core_engine, security_engine, holder_engine, momentum_engine, social_engine,
        scoring_pipeline, session_store,
    )
    dispatcher = Dispatcher(fsm)
    for handler in build_navigation_handlers(fsm, session_store, extra_handlers=[scan_handler, watch_handler]):
        dispatcher.register(handler)

    return dispatcher, fsm, auto_watch_manager, session_store, alerts


@pytest.mark.asyncio
async def test_selecting_a_preset_then_starting_watch_uses_that_preset() -> None:
    """Full flow through the real dispatcher: tap a preset on Filter
    Config, then tap Start on Auto-Watch - the preset chosen on the
    FIRST screen must be what the SECOND screen's watch actually uses,
    proving the FSM-payload handoff between the two screens
    (WatchHandler._handle_watch_start's own docstring) works for real,
    not just in isolated unit tests of each half."""
    dispatcher, fsm, auto_watch_manager, session_store, alerts = _build_stack()
    user_id = 42

    await dispatcher.dispatch(_fake_callback("rule_preset:aggressive", user_id))
    ctx_after_preset = fsm.get_state(user_id)
    assert ctx_after_preset.payload.get("selected_filter_preset") == "aggressive"

    await dispatcher.dispatch(_fake_callback("watch_start", user_id))

    assert fsm.get_state(user_id).state is FSMState.AUTO_WATCH_ACTIVE
    status = auto_watch_manager.status(user_id)
    assert status is not None
    assert status.profile_name == "aggressive"

    task = session_store.get_watch_task(user_id)
    assert task is not None and not task.done()

    await auto_watch_manager.stop(user_id)  # cleanup - no orphaned task left after this test


@pytest.mark.asyncio
async def test_custom_filter_full_flow_actually_customizes_the_watch() -> None:
    """Step 12 revalidation's core regression test. Under the bug this
    fixes, `NAMED_PRESETS.get("custom", NAMED_PRESETS["balanced"])`
    returned the BALANCED FilterProfile object itself - whose own `.name`
    is "balanced" - so `status.profile_name` would read "balanced" here
    even though the user picked Custom. That's the one symptom this test
    checks for directly, plus the FSM payload actually holding the
    specific values configured, not just Balanced's own numbers under a
    different label."""
    dispatcher, fsm, auto_watch_manager, session_store, alerts = _build_stack()
    user_id = 44

    await dispatcher.dispatch(_fake_callback("rule_advanced", user_id))
    ctx = fsm.get_state(user_id)
    assert ctx.state is FSMState.CONFIGURING_FILTER
    draft = ctx.payload["custom_filter_draft"]
    assert draft.min_liquidity_usd == 15_000.0  # Balanced's own seed value

    # Liquidity ladder is (3_000, 15_000, 50_000, 150_000) - one cycle
    # from Balanced's 15_000 lands on 50_000.
    await dispatcher.dispatch(_fake_callback("rule_num:liquidity", user_id))
    draft = fsm.get_state(user_id).payload["custom_filter_draft"]
    assert draft.min_liquidity_usd == 50_000.0

    # Balanced defaults reject_high_concentration to False; turn it on.
    await dispatcher.dispatch(_fake_callback("rule_tgl:concentration:on", user_id))
    draft = fsm.get_state(user_id).payload["custom_filter_draft"]
    assert draft.reject_high_concentration is True
    assert draft.min_liquidity_usd == 50_000.0  # earlier change survived this second tap

    await dispatcher.dispatch(_fake_callback("rule_save", user_id))
    assert fsm.get_state(user_id).payload.get("selected_filter_preset") == "custom"

    await dispatcher.dispatch(_fake_callback("watch_start", user_id))
    status = auto_watch_manager.status(user_id)
    assert status is not None
    assert status.profile_name == "custom"  # NOT "balanced" - the bug this fixes

    saved_draft = fsm.get_state(user_id).payload["custom_filter_draft"]
    assert saved_draft.min_liquidity_usd == 50_000.0
    assert saved_draft.reject_high_concentration is True

    await auto_watch_manager.stop(user_id)


@pytest.mark.asyncio
async def test_advanced_rules_stale_tap_from_a_different_screen_does_not_crash() -> None:
    """The dispatcher's own broad `except Exception` (Part IV.4 zero-
    dead-ends net) should turn a stale rule_tgl tap into the generic
    recovery reply, not an unhandled InvalidTransitionError - e.g. a user
    who opened Advanced Rules, then separately started a watch (now
    AUTO_WATCH_ACTIVE, which has no edge to CONFIGURING_FILTER), then
    scrolled back up and tapped an old Advanced Rules button still
    sitting in their chat history. Deliberately uses AUTO_WATCH_ACTIVE
    rather than IDLE for the stale state - IDLE *can* legitimately reach
    CONFIGURING_FILTER, so a stale tap from IDLE wouldn't raise anything
    at all, and would silently prove nothing. This does not depend on
    today's CONFIGURING_FILTER self-loop fix; it exercises the
    dispatcher's outermost safety net that already existed for every
    other handler."""
    dispatcher, fsm, auto_watch_manager, session_store, alerts = _build_stack()
    user_id = 45

    await dispatcher.dispatch(_fake_callback("watch_start", user_id))
    assert fsm.get_state(user_id).state is FSMState.AUTO_WATCH_ACTIVE

    cq = _fake_callback("rule_tgl:concentration:on", user_id)
    await dispatcher.dispatch(cq)  # must not raise

    cq.message.edit_text.assert_awaited()
    (sent_text,), _ = cq.message.edit_text.call_args
    assert "went wrong" in sent_text.lower() or "advanced rules" in sent_text.lower()
    await auto_watch_manager.stop(user_id)  # cleanup - watch_start above left a task running


@pytest.mark.asyncio
async def test_start_without_ever_selecting_a_preset_defaults_to_balanced() -> None:
    dispatcher, fsm, auto_watch_manager, session_store, alerts = _build_stack()
    user_id = 43

    await dispatcher.dispatch(_fake_callback("watch_start", user_id))

    status = auto_watch_manager.status(user_id)
    assert status is not None
    assert status.profile_name == "balanced"

    await auto_watch_manager.stop(user_id)


@pytest.mark.asyncio
async def test_stop_via_dispatcher_returns_to_idle_and_leaves_no_orphaned_task() -> None:
    dispatcher, fsm, auto_watch_manager, session_store, alerts = _build_stack()
    user_id = 44

    await dispatcher.dispatch(_fake_callback("watch_start", user_id))
    assert fsm.get_state(user_id).state is FSMState.AUTO_WATCH_ACTIVE

    await dispatcher.dispatch(_fake_callback("watch_stop", user_id))

    assert fsm.get_state(user_id).state is FSMState.IDLE
    assert session_store.get_watch_task(user_id) is None
    assert auto_watch_manager.status(user_id) is None


@pytest.mark.asyncio
async def test_nav_watch_shows_active_status_after_starting() -> None:
    """The screen a person actually sees after tapping Auto-Watch from
    the Main Menu reflects the real running state - not the FSM-only
    signal test_menus.py's own unit test already covers, but the full
    handler wiring supplying a real AutoWatchStatus to it."""
    dispatcher, fsm, auto_watch_manager, session_store, alerts = _build_stack()
    user_id = 45

    await dispatcher.dispatch(_fake_callback("watch_start", user_id))
    cq = _fake_callback("nav_watch", user_id)
    await dispatcher.dispatch(cq)

    rendered_html = cq.message.edit_text.call_args.args[0]
    assert "running" in rendered_html.lower()
    assert "Balanced" in rendered_html

    await auto_watch_manager.stop(user_id)
