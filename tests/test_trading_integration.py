"""
Playbook reference: Unified Developer Playbook, Part VIII Step 11;
provider table and hard security boundary in Part IV.1.

Four layers, matching this step's own file split (see
`integrations/trading.py` and `handlers/trade_staging_handler.py`'s own
module docstrings for why the split sits where it does):

1. `integrations.providers` — the six-row table itself (data correctness:
   right chains, right operational flags).
2. `integrations.trading.build_deep_link` — pure string formatting, no
   FSM/SessionStore needed, tested with plain values.
3. `handlers.trade_staging_handler`'s four lifecycle functions — need
   `FSMEngine`/`SessionStore` fixtures but no aiogram/CallbackQuery.
4. `TradeStagingHandler` through the real `Dispatcher` — needs aiogram
   (mocks `CallbackQuery` via `MagicMock(spec=...)`, same technique as
   test_scan_flow_integration.py / test_watch_flow_integration.py).

Run `pytest tests/test_trading_integration.py` after `pip install -r
requirements.txt`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, User

from analysis.api_abstraction import DiscoveryCandidate, PairData, SecurityReport
from analysis.core_engine import CoreEngine, CoreResult
from analysis.holder_engine import HolderEngine, HolderResult
from analysis.momentum_engine import MomentumEngine, MomentumResult
from analysis.security_engine import SecurityEngine, SecurityResult
from analysis.social_engine import SocialEngine, SocialResult
from bot.constants import Chain, FSMState, TradingBot
from bot.types import SessionContext
from handlers.dispatcher import Dispatcher
from handlers.navigation import build_navigation_handlers
from handlers.scan_handler import ScanHandler
from handlers.scan_orchestration import ScoredResult
from handlers.trade_staging_handler import (
    TradeStagingHandler,
    abort_trade_staging,
    approve_and_get_link,
    change_target_bot,
    enter_trade_staging,
)
from integrations.providers import BOT_PROVIDERS, bots_for_chain, default_bot_for_chain
from integrations.trading import UnsupportedChainError, build_deep_link
from rendering.result_renderer import render_watch_alert
from scoring.pipeline import ScoringPipeline, ScoringResult
from scoring.risk_opportunity_matrix import classify
from state.fsm import FSMContext, FSMEngine
from state.session_store import SessionStore

# ---------------------------------------------------------------------------
# Shared fixtures - deliberately minimal (only what chain/address/result_id
# selection actually depends on); full-content rendering snapshots already
# belong to tests/test_result_renderer.py, not duplicated here.
# ---------------------------------------------------------------------------


def _core_result(**overrides) -> CoreResult:
    pair = PairData(
        chain=overrides.get("chain", Chain.SOL), dex_id="raydium", pair_address="pair1",
        base_token_address="Tok1111111111111111111111111111111111111111",
        base_token_symbol="TEST", base_token_name="Test Token", quote_token_symbol="SOL",
        price_usd=0.01, liquidity_usd=250_000.0, fdv=2_000_000.0, market_cap=1_500_000.0,
        volume_5m=100.0, volume_1h=1000.0, volume_6h=5000.0, volume_24h=180_000.0,
        price_change_5m=0.5, price_change_1h=2.0, price_change_6h=4.0, price_change_24h=12.0,
        buys_24h=200, sells_24h=150, pair_created_at_ms=1_700_000_000_000,
    )
    base = dict(
        address=overrides.get("address", "Tok1111111111111111111111111111111111111111"),
        chain=overrides.get("chain", Chain.SOL), primary_pair=pair,
        liquidity_usd=250_000.0, market_cap=1_500_000.0, fdv=2_000_000.0, dilution_ratio=0.75,
        volume_24h=180_000.0, pool_age_days=30.0, price_change={"5m": 0.5, "1h": 2.0, "6h": 4.0, "24h": 12.0},
        buy_pressure_pct=57.1,
    )
    base.update(overrides)
    return CoreResult(**base)


def _scored_result(**overrides) -> ScoredResult:
    core = overrides.get("core", _core_result(chain=overrides.get("chain", Chain.SOL)))
    scoring = ScoringResult(
        score_opportunity=65.0, score_risk=10.0, score_confidence=45.0, score_ai=72.5,
        tier_label="Solid, Monitor", explanation=["fixture"], pipeline_version="v2",
    )
    return ScoredResult(
        core=core,
        security=SecurityResult(
            trust_score=85.0, risk_level="Low Risk", mint_authority_active=False,
            freeze_authority_active=False, buy_tax_pct=0.0, sell_tax_pct=0.0,
            lp_lock_ratio_pct=100.0, lp_lock_duration_days=180.0, ownership_renounced=True,
            scam_flags=[], provider_notes=[],
        ),
        holder=HolderResult(
            holder_count=18, holder_growth_24h_pct=20.0, hci_pct=22.4, whale_count=1,
            classified_wallets={}, insider_bundle_detected=False, insider_bundle_wallet_count=0,
            holder_count_is_estimate=True,
        ),
        momentum=MomentumResult(
            volume_growth_pct=0.0, liquidity_growth_pct=0.0, price_momentum=16.0,
            buy_momentum=60.0, whale_momentum=0.0, social_momentum=0.0, trending_score=12.8,
        ),
        social=overrides.get(
            "social",
            SocialResult(
                x_score=60, verified_follower_ratio=0.5, tweet_frequency_per_day=2.0,
                influencer_mention_count=0, sentiment_ratio=0.0, follower_growth_pct=0.0,
            ),
        ),
        scoring=scoring,
        risk_opportunity=classify(scoring.score_risk, scoring.score_opportunity),
        result_id=overrides.get("result_id", "placeholder"),
    )


def _cached(session_store: SessionStore, **overrides) -> tuple[str, ScoredResult]:
    """Mirrors `handlers.scan_orchestration.run_scan`'s own two-step
    build exactly (construct, cache_put, then set the real result_id) -
    not a shortcut, the actual production pattern."""
    scored = _scored_result(**overrides)
    result_id = session_store.cache_put(scored)
    scored.result_id = result_id
    return result_id, scored


def _seed_state(session_store: SessionStore, user_id: int, state: FSMState) -> None:
    """Places `user_id` directly at `state`, bypassing `FSMEngine
    .transition`'s own adjacency check entirely - the same technique
    `tests/test_fsm.py`'s own `_engine_with_state` helper already
    established for exactly this need (that file's own docstring: hand-
    encoded independently of `state.fsm._ADJACENCY`, so it can actually
    check the map rather than assume it). Used here to reach RESULT_DETAIL/
    AUTO_WATCH_ACTIVE directly rather than re-deriving (and re-risking
    getting wrong) a real IDLE -> ... -> RESULT_DETAIL walk in every test -
    that walk is `test_scan_flow_integration.py`'s own job, not this
    file's; this file only needs a person already there."""
    session_store.set(user_id, SessionContext(user_id=user_id, payload={"fsm": FSMContext(state=state)}))


# ===========================================================================
# 1. integrations.providers — the six-row table
# ===========================================================================


def test_all_six_trading_bots_are_present() -> None:
    assert set(BOT_PROVIDERS.keys()) == set(TradingBot)


def test_bullx_is_marked_non_operational_with_a_status_note() -> None:
    """See integrations/providers.py's own Verification Status note -
    BullX suspended trading 2026-06-01."""
    provider = BOT_PROVIDERS[TradingBot.BULLX]
    assert provider.is_operational is False
    assert provider.status_note


def test_photon_is_solana_only_and_not_a_telegram_bot() -> None:
    provider = BOT_PROVIDERS[TradingBot.PHOTON]
    assert provider.supported_chains == frozenset({Chain.SOL})
    assert provider.uses_telegram_start_param is False


def test_every_supported_chain_has_at_least_one_operational_default_bot() -> None:
    for chain in Chain:
        assert default_bot_for_chain(chain) is not None, f"no operational bot covers {chain}"


def test_bots_for_chain_excludes_bullx_by_default() -> None:
    assert TradingBot.BULLX not in bots_for_chain(Chain.SOL)
    assert TradingBot.BULLX in bots_for_chain(Chain.SOL, operational_only=False)


def test_bots_for_chain_is_deterministic_enum_order() -> None:
    result = bots_for_chain(Chain.SOL, operational_only=False)
    assert result == [bot for bot in TradingBot if bot in result]


# ===========================================================================
# 2. integrations.trading.build_deep_link — pure function
# ===========================================================================


def test_build_deep_link_telegram_bot_happy_path() -> None:
    url = build_deep_link(TradingBot.TROJAN, Chain.SOL, "Tok111")
    assert url == "https://t.me/solana_trojanbot?start=dexscan_Tok111"


def test_build_deep_link_non_telegram_bot_happy_path() -> None:
    """Photon - see integrations/providers.py module docstring finding #2."""
    url = build_deep_link(TradingBot.PHOTON, Chain.SOL, "Tok111")
    assert url == "https://photon-sol.tinyastro.io/en/lp/Tok111"
    assert "start=" not in url


def test_build_deep_link_raises_for_unsupported_chain() -> None:
    """Trojan is Solana-only (integrations/providers.py) - an ETH result
    must never silently produce a link nobody can actually use."""
    with pytest.raises(UnsupportedChainError):
        build_deep_link(TradingBot.TROJAN, Chain.ETH, "0xabc")


@pytest.mark.parametrize(
    ("bot", "chain"),
    [(bot, chain) for bot in TradingBot for chain in BOT_PROVIDERS[bot].supported_chains],
)
def test_build_deep_link_never_raises_for_any_declared_supported_pair(bot: TradingBot, chain: Chain) -> None:
    """Every (bot, chain) pair the table itself claims to support must
    actually build without raising - a table/function mismatch here
    would be worse than a missing feature, since it would look
    supported right up until a real user hit it."""
    build_deep_link(bot, chain, "SomeAddress123")


def test_build_deep_link_includes_slippage_only_when_supported() -> None:
    with_slippage = build_deep_link(TradingBot.TROJAN, Chain.SOL, "Tok111", slippage_pct=2.5)
    assert "slippage=2.5" in with_slippage

    photon_ignores_it = build_deep_link(TradingBot.PHOTON, Chain.SOL, "Tok111", slippage_pct=2.5)
    assert "slippage" not in photon_ignores_it


def test_build_deep_link_includes_anti_mev_only_when_supported() -> None:
    """GMGN supports slippage but not anti-MEV (integrations/providers.py)
    - the two flags must be independent, not both-or-nothing."""
    gmgn_link = build_deep_link(TradingBot.GMGN, Chain.SOL, "Tok111", slippage_pct=1.0, anti_mev=True)
    assert "slippage=1" in gmgn_link
    assert "anti_mev" not in gmgn_link

    trojan_link = build_deep_link(TradingBot.TROJAN, Chain.SOL, "Tok111", anti_mev=True)
    assert "anti_mev=on" in trojan_link


def test_build_deep_link_drops_referral_prefix_rather_than_truncate_a_long_address() -> None:
    """Documented edge case (integrations/trading.py's own docstring):
    not reachable with today's three real address families, exercised
    here with a synthetic one so the fallback branch is actually tested,
    not just asserted-safe in prose."""
    long_address = "A" * 60
    url = build_deep_link(TradingBot.TROJAN, Chain.SOL, long_address)
    assert f"start={long_address}" in url
    assert "dexscan_" not in url


@pytest.mark.parametrize("weird_address", ["", "has spaces", "semi;colon&amp=x", "\U0001f680emoji\U0001f680"])
def test_build_deep_link_never_crashes_on_malformed_address_input(weird_address: str) -> None:
    """build_deep_link's own contract (module docstring) is pure string
    formatting, not validation - Part II.6's address-family regex is
    what actually gates what reaches this function in real use, long
    before a ScoredResult (let alone a staged trade) exists. What this
    function itself owes the caller is: never raise on a weird string
    (the parametrize itself proves that - a crash here would fail
    collection), and never let it leak unescaped into the URL, since a
    raw space or "&" from the address could otherwise break the link or
    inject an unintended second query parameter."""
    url = build_deep_link(TradingBot.TROJAN, Chain.SOL, weird_address)
    assert url.startswith("https://t.me/solana_trojanbot?start=")
    assert " " not in url
    assert url.count("&") == 0  # only one query param (start) here - a literal "&" would mean it leaked unescaped


# ===========================================================================
# 3. handlers.trade_staging_handler — lifecycle functions (FSM/SessionStore,
#    no aiogram)
# ===========================================================================


def _stack() -> tuple[FSMEngine, SessionStore]:
    session_store = SessionStore()
    return FSMEngine(session_store), session_store


def test_enter_trade_staging_happy_path_transitions_and_seeds_payload() -> None:
    fsm, session_store = _stack()
    user_id = 1
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, scored = _cached(session_store)

    outcome = enter_trade_staging(fsm, session_store, user_id, result_id)

    assert outcome is not None
    ctx, returned_scored, bot = outcome
    assert ctx.state is FSMState.TRADE_STAGING
    assert ctx.payload["trade_result_id"] == result_id
    assert ctx.payload["trade_bot"] == bot.value
    assert returned_scored is scored
    assert bot in bots_for_chain(Chain.SOL)


def test_enter_trade_staging_unknown_result_id_returns_none_and_does_not_transition() -> None:
    fsm, session_store = _stack()
    user_id = 2
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)

    outcome = enter_trade_staging(fsm, session_store, user_id, "does-not-exist")

    assert outcome is None
    assert fsm.get_state(user_id).state is FSMState.RESULT_DETAIL  # unchanged


def test_enter_trade_staging_unresolved_chain_returns_none() -> None:
    fsm, session_store = _stack()
    user_id = 3
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, _ = _cached(session_store, core=_core_result(chain=None, primary_pair=None))

    assert enter_trade_staging(fsm, session_store, user_id, result_id) is None


def test_change_target_bot_cycles_and_wraps_around() -> None:
    fsm, session_store = _stack()
    user_id = 4
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, _ = _cached(session_store)
    enter_trade_staging(fsm, session_store, user_id, result_id)

    candidates = bots_for_chain(Chain.SOL)
    seen = [fsm.get_state(user_id).payload["trade_bot"]]
    for _ in range(len(candidates)):
        outcome = change_target_bot(fsm, session_store, user_id)
        assert outcome is not None
        seen.append(outcome[2].value)

    # after cycling exactly len(candidates) times we're back to the start
    assert seen[0] == seen[-1]
    assert len(set(seen[:-1])) == len(candidates)  # every candidate appeared exactly once


def test_change_target_bot_never_offers_bullx() -> None:
    fsm, session_store = _stack()
    user_id = 5
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, _ = _cached(session_store)
    enter_trade_staging(fsm, session_store, user_id, result_id)

    for _ in range(len(bots_for_chain(Chain.SOL)) * 2):  # two full laps
        outcome = change_target_bot(fsm, session_store, user_id)
        assert outcome is not None
        assert outcome[2] is not TradingBot.BULLX


def test_change_target_bot_with_nothing_staged_returns_none() -> None:
    fsm, session_store = _stack()
    user_id = 6
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)  # never entered staging

    assert change_target_bot(fsm, session_store, user_id) is None


def test_approve_and_get_link_happy_path_returns_matching_link_and_lands_on_idle() -> None:
    fsm, session_store = _stack()
    user_id = 7
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, scored = _cached(session_store)
    enter_trade_staging(fsm, session_store, user_id, result_id)
    staged_bot = TradingBot(fsm.get_state(user_id).payload["trade_bot"])

    outcome = approve_and_get_link(fsm, session_store, user_id)

    assert outcome is not None
    ctx, returned_scored, bot, deep_link = outcome
    assert ctx.state is FSMState.IDLE
    assert bot is staged_bot
    assert deep_link == build_deep_link(staged_bot, Chain.SOL, scored.core.address)


def test_approve_and_get_link_with_nothing_staged_returns_none_and_no_link() -> None:
    fsm, session_store = _stack()
    user_id = 8
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)

    assert approve_and_get_link(fsm, session_store, user_id) is None


def test_abort_trade_staging_always_lands_on_idle_and_returns_only_fsm_context() -> None:
    """Part IV.1's Acceptance Criteria literally: abort produces no
    external action. `abort_trade_staging`'s own signature makes this
    structural, not just behavioral - it doesn't take a SessionStore, so
    it has no way to look up a result or build a link even in principle."""
    fsm, session_store = _stack()
    user_id = 9
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, _ = _cached(session_store)
    enter_trade_staging(fsm, session_store, user_id, result_id)
    assert fsm.get_state(user_id).state is FSMState.TRADE_STAGING

    ctx = abort_trade_staging(fsm, user_id)

    assert ctx.state is FSMState.IDLE


# ===========================================================================
# 4. TradeStagingHandler through the real Dispatcher
# ===========================================================================


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
    """This file's tests exercise Trading Integration / Trade Staging,
    not social scoring - always-degrades is enough (same reasoning as
    test_scan_orchestration.py's own copy of this fake)."""

    async def lookup_user(self, handle: str) -> object:
        raise RuntimeError("fake social provider - lookup always fails")

    async def list_followers(self, handle: str, limit: int) -> list:
        return []

    async def search_mentions(self, ticker: str) -> list:
        return []


def _fake_callback(data: str, user_id: int) -> CallbackQuery:
    cq = MagicMock(spec=CallbackQuery)
    cq.data = data
    cq.from_user = MagicMock(spec=User, id=user_id)
    cq.answer = AsyncMock()
    cq.message = MagicMock()
    cq.message.edit_text = AsyncMock()
    return cq


def _build_dispatcher_stack() -> tuple[Dispatcher, FSMEngine, SessionStore]:
    session_store = SessionStore()
    fsm = FSMEngine(session_store)
    scan_handler = ScanHandler(
        fsm,
        CoreEngine(_FakeMarketProvider()),
        SecurityEngine(_FakeSecurityProvider()),
        HolderEngine(_FakeHolderProvider()),
        MomentumEngine(),
        SocialEngine(_FakeSocialProvider()),
        ScoringPipeline(),
        session_store,
    )
    trade_staging_handler = TradeStagingHandler(fsm, session_store)
    dispatcher = Dispatcher(fsm)
    for handler in build_navigation_handlers(fsm, session_store, extra_handlers=[scan_handler, trade_staging_handler]):
        dispatcher.register(handler)
    return dispatcher, fsm, session_store


def _rendered_html(cq: CallbackQuery) -> str:
    return cq.message.edit_text.call_args.args[0]


def _rendered_callbacks(cq: CallbackQuery) -> set[str]:
    markup = cq.message.edit_text.call_args.kwargs["reply_markup"]
    return {b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data is not None}


@pytest.mark.asyncio
async def test_result_detail_buy_button_reaches_trade_staging() -> None:
    dispatcher, fsm, session_store = _build_dispatcher_stack()
    user_id = 100
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, _ = _cached(session_store)

    await dispatcher.dispatch(_fake_callback(f"exec_stage:{result_id}", user_id))

    assert fsm.get_state(user_id).state is FSMState.TRADE_STAGING


@pytest.mark.asyncio
async def test_trade_staging_approve_returns_link_ready_screen_and_lands_on_idle() -> None:
    dispatcher, fsm, session_store = _build_dispatcher_stack()
    user_id = 101
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, scored = _cached(session_store)
    await dispatcher.dispatch(_fake_callback(f"exec_stage:{result_id}", user_id))
    staged_bot = TradingBot(fsm.get_state(user_id).payload["trade_bot"])

    cq = _fake_callback("exec_approve", user_id)
    await dispatcher.dispatch(cq)

    assert fsm.get_state(user_id).state is FSMState.IDLE
    expected_link = build_deep_link(staged_bot, Chain.SOL, scored.core.address)
    urls = {b.url for row in cq.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard for b in row if b.url}
    assert expected_link in urls


@pytest.mark.asyncio
async def test_trade_staging_abort_lands_on_idle_with_no_url_anywhere_in_the_response() -> None:
    dispatcher, fsm, session_store = _build_dispatcher_stack()
    user_id = 102
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, _ = _cached(session_store)
    await dispatcher.dispatch(_fake_callback(f"exec_stage:{result_id}", user_id))

    cq = _fake_callback("exec_abort", user_id)
    await dispatcher.dispatch(cq)

    assert fsm.get_state(user_id).state is FSMState.IDLE
    markup = cq.message.edit_text.call_args.kwargs.get("reply_markup")
    urls = {b.url for row in markup.inline_keyboard for b in row if b.url} if markup else set()
    assert urls == set()  # Part IV.1: abort produces no external action - not even a dead link


@pytest.mark.asyncio
async def test_trade_staging_back_returns_to_result_detail() -> None:
    dispatcher, fsm, session_store = _build_dispatcher_stack()
    user_id = 103
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, _ = _cached(session_store)
    await dispatcher.dispatch(_fake_callback(f"exec_stage:{result_id}", user_id))
    assert fsm.get_state(user_id).state is FSMState.TRADE_STAGING

    cq = _fake_callback("result_back_detail", user_id)
    await dispatcher.dispatch(cq)

    assert fsm.get_state(user_id).state is FSMState.RESULT_DETAIL
    assert "Trade Staging" not in _rendered_html(cq)


@pytest.mark.asyncio
async def test_change_bot_button_re_renders_trade_staging_with_a_different_bot() -> None:
    dispatcher, fsm, session_store = _build_dispatcher_stack()
    user_id = 104
    _seed_state(session_store, user_id, FSMState.RESULT_DETAIL)
    result_id, _ = _cached(session_store)
    await dispatcher.dispatch(_fake_callback(f"exec_stage:{result_id}", user_id))
    first_bot = fsm.get_state(user_id).payload["trade_bot"]

    cq = _fake_callback("exec_change_bot", user_id)
    await dispatcher.dispatch(cq)

    assert fsm.get_state(user_id).state is FSMState.TRADE_STAGING  # stayed on the same screen
    second_bot = fsm.get_state(user_id).payload["trade_bot"]
    assert second_bot != first_bot
    assert BOT_PROVIDERS[TradingBot(second_bot)].display_name in _rendered_html(cq)


@pytest.mark.asyncio
async def test_watch_alert_result_view_and_buy_button_all_reach_the_same_trade_staging(
) -> None:
    """Document-of-record for Part VIII Step 11's own integration
    requirement, restated in this session's own resume instructions:
    Auto-Watch match -> alert -> result_view:{uuid} -> Result Detail ->
    Trade Staging -> approval, with no second execution path anywhere.
    Proven here by actually walking that exact chain through the real
    Dispatcher, starting from `render_watch_alert`'s own callback_data -
    not asserted, read directly off the rendered alert. Also the
    regression test for documented adjacency addition #7
    (state/fsm.py): seeds AUTO_WATCH_ACTIVE - where a real alert
    recipient actually is - rather than Idle, so a revert of that
    addition fails this test with the exact InvalidTransitionError a
    real user would have hit."""
    dispatcher, fsm, session_store = _build_dispatcher_stack()
    user_id = 105
    result_id, scored = _cached(session_store)

    alert = render_watch_alert(scored)
    alert_callbacks = {b.callback_data for row in alert.keyboard.inline_keyboard for b in row if b.callback_data}
    assert f"result_view:{result_id}" in alert_callbacks

    _seed_state(session_store, user_id, FSMState.AUTO_WATCH_ACTIVE)  # where a real alert recipient would be
    await dispatcher.dispatch(_fake_callback(f"result_view:{result_id}", user_id))
    assert fsm.get_state(user_id).state is FSMState.RESULT_DETAIL

    await dispatcher.dispatch(_fake_callback(f"exec_stage:{result_id}", user_id))
    assert fsm.get_state(user_id).state is FSMState.TRADE_STAGING

    cq = _fake_callback("exec_approve", user_id)
    await dispatcher.dispatch(cq)
    assert fsm.get_state(user_id).state is FSMState.IDLE
    markup = cq.message.edit_text.call_args.kwargs["reply_markup"]
    assert any(b.url for row in markup.inline_keyboard for b in row)  # a real, single link - one path, one outcome
