"""
Playbook reference: Unified Developer Playbook, Part VIII Step 7; Holder
Engine wired in concurrently with Security in the Step 8 integration
pass; Momentum Engine wired in sequentially after both in this Step 9
integration pass.

Tests `handlers.scan_orchestration.run_scan` — the pure engine-chaining
logic, deliberately split from `scan_handler.py`'s Telegram I/O (same
rationale as Steps 4-5's provider/parser splits). Fully executable
without aiohttp/aiogram: fake engines satisfy `CoreEngine`/
`SecurityEngine`/`HolderEngine`'s own dependency-inverted providers,
exactly as in Steps 4-5-8's own test suites; `MomentumEngine` needs no
fake at all - Step 9's Scope calls it out as "the one engine in this
playbook that's purely computational," so the real class is used
directly everywhere below.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from analysis.core_engine import CoreEngine
from analysis.holder_engine import HolderEngine
from analysis.momentum_engine import MomentumEngine
from analysis.security_engine import SecurityEngine
from analysis.social_engine import SocialEngine
from bot.constants import Chain
from handlers.scan_orchestration import ScoredResult, run_scan
from scoring.pipeline import ScoringPipeline
from state.session_store import SessionStore


class _FakeMarketProvider:
    def __init__(self, pairs: list) -> None:
        self._pairs = pairs

    async def get_pairs(self, address: str) -> list:
        return self._pairs


class _FakeSecurityProvider:
    def __init__(self, report, delay_seconds: float = 0.0) -> None:
        self._report = report
        self._delay_seconds = delay_seconds

    async def scan(self, address: str, chain) -> object:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        return self._report


class _FakeHolderProvider:
    """Satisfies `HolderDataProvider`. Empty holders/funding by default -
    a valid, non-degraded `HolderResult` (holder_count=0 etc.), not an
    error path; individual tests override via the constructor when they
    need specific holder/funding data."""

    def __init__(self, holders: list | None = None, funding: list | None = None, delay_seconds: float = 0.0) -> None:
        self._holders = holders if holders is not None else []
        self._funding = funding if funding is not None else []
        self._delay_seconds = delay_seconds

    async def get_holders(self, address: str, chain) -> list:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        return self._holders

    async def get_launch_block_funding(self, address: str, chain) -> list:
        return self._funding


class _FakeSocialProvider:
    """Simplest possible fake satisfying `SocialDataProvider`: always
    fails the primary lookup, so `SocialEngine.analyze` degrades cleanly
    (Step 13's own contract) without this file needing realistic
    XUserProfile/Tweet fixture data it has no use for — none of the
    tests in this file assert anything about social/sentiment content;
    they only need `run_scan` to accept a real `SocialEngine` and finish
    without raising."""

    async def lookup_user(self, handle: str) -> object:
        raise RuntimeError("fake social provider - lookup always fails")

    async def list_followers(self, handle: str, limit: int) -> list:
        return []

    async def search_mentions(self, ticker: str) -> list:
        return []


def _make_engines(pairs: list, report, security_delay: float = 0.0, holder_delay: float = 0.0):
    core_engine = CoreEngine(_FakeMarketProvider(pairs))
    security_engine = SecurityEngine(_FakeSecurityProvider(report, delay_seconds=security_delay))
    holder_engine = HolderEngine(_FakeHolderProvider(delay_seconds=holder_delay))
    momentum_engine = MomentumEngine()
    social_engine = SocialEngine(_FakeSocialProvider())
    return core_engine, security_engine, holder_engine, momentum_engine, social_engine


def _solana_pairs():
    from analysis.api_abstraction import PairData

    return [
        PairData(
            chain=Chain.SOL, dex_id="raydium", pair_address="pair1",
            base_token_address="So11111111111111111111111111111111111111112",
            base_token_symbol="TEST", base_token_name="Test Token", quote_token_symbol="SOL",
            price_usd=0.01, liquidity_usd=100_000.0, fdv=1_000_000.0, market_cap=800_000.0,
            volume_5m=100.0, volume_1h=1000.0, volume_6h=5000.0, volume_24h=50_000.0,
            price_change_5m=0.1, price_change_1h=1.0, price_change_6h=2.0, price_change_24h=5.0,
            buys_24h=100, sells_24h=80, pair_created_at_ms=1_700_000_000_000,
        )
    ]


def _safe_report():
    from analysis.api_abstraction import SecurityReport

    return SecurityReport(
        trust_score=90.0, mint_authority_active=False, freeze_authority_active=False,
        buy_tax_pct=0.0, sell_tax_pct=0.0, lp_lock_ratio_pct=100.0, lp_lock_duration_days=100.0,
        ownership_renounced=True, raw_risk_flags=[], chain_supported=True,
    )


@pytest.mark.asyncio
async def test_run_scan_returns_complete_scored_result() -> None:
    core_engine, security_engine, holder_engine, momentum_engine, social_engine = _make_engines(_solana_pairs(), _safe_report())
    store = SessionStore()

    result = await run_scan(
        "So11111111111111111111111111111111111111112",
        core_engine, security_engine, holder_engine, momentum_engine, social_engine, ScoringPipeline(), store,
    )

    assert isinstance(result, ScoredResult)
    assert result.core.degraded is False
    assert result.security.degraded is False
    assert result.holder.degraded is False
    assert result.momentum.degraded is False
    assert result.scoring.pipeline_version == "v3"  # Step 14: social_engine is now always supplied, so run_scan always reaches v3
    assert result.risk_opportunity is not None
    assert result.result_id != ""


@pytest.mark.asyncio
async def test_run_scan_result_is_actually_cached_under_its_own_id() -> None:
    """The two-step result_id build (construct, cache, mutate) must
    actually leave the SAME object retrievable by its own id - not a
    copy, not a stale pre-mutation version."""
    core_engine, security_engine, holder_engine, momentum_engine, social_engine = _make_engines(_solana_pairs(), _safe_report())
    store = SessionStore()

    result = await run_scan(
        "So11111111111111111111111111111111111111112",
        core_engine, security_engine, holder_engine, momentum_engine, social_engine, ScoringPipeline(), store,
    )

    cached = store.cache_get(result.result_id)
    assert cached is result  # same object, not merely equal


@pytest.mark.asyncio
async def test_run_scan_calls_progress_hooks_in_order() -> None:
    """Core's hook must fire before the concurrent pair starts. Security
    and Holder's own hooks fire independently as each finishes (they run
    via `asyncio.gather`, not sequentially - see run_scan's docstring),
    so only "both happened, after core" is asserted for that pair, not a
    fixed order between them. Momentum's hook is asserted LAST and
    singly, not just "somewhere after core" - unlike Security/Holder, it
    structurally cannot fire until the gather above it has fully
    resolved (MomentumEngine.compute requires a real HolderResult), so
    this ordering is a real guarantee, not an artifact of scheduling."""
    calls: list[str] = []

    async def on_core() -> None:
        calls.append("core")

    async def on_security() -> None:
        calls.append("security")

    async def on_holder() -> None:
        calls.append("holder")

    async def on_momentum() -> None:
        calls.append("momentum")

    core_engine, security_engine, holder_engine, momentum_engine, social_engine = _make_engines(_solana_pairs(), _safe_report())
    store = SessionStore()

    await run_scan(
        "So11111111111111111111111111111111111111112",
        core_engine, security_engine, holder_engine, momentum_engine, social_engine, ScoringPipeline(), store,
        on_core_complete=on_core, on_security_complete=on_security,
        on_holder_complete=on_holder, on_momentum_complete=on_momentum,
    )

    assert calls[0] == "core"
    assert set(calls[1:3]) == {"security", "holder"}
    assert calls[3] == "momentum"
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_run_scan_works_with_no_hooks_provided() -> None:
    core_engine, security_engine, holder_engine, momentum_engine, social_engine = _make_engines(_solana_pairs(), _safe_report())
    store = SessionStore()
    result = await run_scan(
        "So11111111111111111111111111111111111111112",
        core_engine, security_engine, holder_engine, momentum_engine, social_engine, ScoringPipeline(), store,
    )
    assert result.result_id != ""


@pytest.mark.asyncio
async def test_run_scan_degraded_core_still_produces_complete_result() -> None:
    """No engine raises for a bad/unresolvable address (Steps 4-5-8's
    own contracts) - run_scan must not add a new failure mode on top.
    Holder degrades too (via its own "no resolved chain" path, same as
    Security), not because HolderEngine.analyze itself was skipped.
    Momentum degrades in turn, per its own contract of mirroring
    core-or-holder degradation (Step 9)."""
    core_engine, security_engine, holder_engine, momentum_engine, social_engine = _make_engines([], _safe_report())  # no pairs -> Core degrades
    store = SessionStore()

    result = await run_scan(
        "garbage-not-an-address", core_engine, security_engine, holder_engine, momentum_engine, social_engine,
        ScoringPipeline(), store,
    )

    assert result.core.degraded is True
    assert result.holder.degraded is True
    assert result.momentum.degraded is True
    assert isinstance(result.scoring.score_ai, float)
    assert 0.0 <= result.scoring.score_ai <= 100.0
    assert result.risk_opportunity is not None  # v3 scoring + classification both handle an all-degraded input cleanly
    assert result.result_id != ""


@pytest.mark.asyncio
async def test_run_scan_risk_opportunity_matches_the_actual_scoring_result() -> None:
    """End-to-end proof that risk_opportunity isn't computed from stale
    or placeholder numbers - it must classify the SAME score_risk/
    score_opportunity values actually on result.scoring, not a
    recomputation (scoring/risk_opportunity_matrix.py's own "never
    recomputes" contract, checked here at the orchestration seam)."""
    from scoring.risk_opportunity_matrix import classify

    core_engine, security_engine, holder_engine, momentum_engine, social_engine = _make_engines(_solana_pairs(), _safe_report())
    store = SessionStore()

    result = await run_scan(
        "So11111111111111111111111111111111111111112",
        core_engine, security_engine, holder_engine, momentum_engine, social_engine, ScoringPipeline(), store,
    )

    expected = classify(result.scoring.score_risk, result.scoring.score_opportunity)
    assert result.risk_opportunity == expected


@pytest.mark.asyncio
async def test_run_scan_passes_chain_hint_through_to_core_engine() -> None:
    received_hints: list[Chain | None] = []

    class _RecordingMarketProvider:
        async def get_pairs(self, address: str) -> list:
            return _solana_pairs()

    class _RecordingCoreEngine(CoreEngine):
        async def analyze(self, raw_address: str, chain_hint: Chain | None = None):
            received_hints.append(chain_hint)
            return await super().analyze(raw_address, chain_hint=chain_hint)

    core_engine = _RecordingCoreEngine(_RecordingMarketProvider())
    security_engine = SecurityEngine(_FakeSecurityProvider(_safe_report()))
    holder_engine = HolderEngine(_FakeHolderProvider())
    momentum_engine = MomentumEngine()
    social_engine = SocialEngine(_FakeSocialProvider())  # STEP 14 fix: was missing, causing a NameError
    store = SessionStore()

    await run_scan(
        "So11111111111111111111111111111111111111112",
        core_engine, security_engine, holder_engine, momentum_engine, social_engine, ScoringPipeline(), store, chain_hint=Chain.SOL,
    )

    assert received_hints == [Chain.SOL]


@pytest.mark.asyncio
async def test_run_scan_runs_security_and_holder_concurrently_not_sequentially() -> None:
    """Step 8's original claim ("fully executed concurrently... using
    asyncio.gather") is a timing claim, not just a structural one - this
    proves it rather than just exercising the code path. Each fake
    provider sleeps independently; if Security and Holder ran back-to-
    back this would take >= security_delay + holder_delay. Gathered
    concurrently, wall-clock time should track max(), not sum() - the
    generous ceiling below (sum minus half the smaller delay) comfortably
    separates "concurrent" from "sequential" without being a flaky exact-
    timing assertion. Momentum's addition (Step 9) doesn't change this -
    it's a synchronous, instant computation that runs after the gather,
    contributing negligible wall-clock time of its own."""
    security_delay = 0.15
    holder_delay = 0.12
    core_engine, security_engine, holder_engine, momentum_engine, social_engine = _make_engines(
        _solana_pairs(), _safe_report(), security_delay=security_delay, holder_delay=holder_delay
    )
    store = SessionStore()

    started = time.monotonic()
    await run_scan(
        "So11111111111111111111111111111111111111112",
        core_engine, security_engine, holder_engine, momentum_engine, social_engine, ScoringPipeline(), store,
    )
    elapsed = time.monotonic() - started

    sequential_worst_case = security_delay + holder_delay
    assert elapsed < sequential_worst_case - (min(security_delay, holder_delay) / 2)


@pytest.mark.asyncio
async def test_run_scan_momentum_receives_the_real_holder_result_not_a_placeholder() -> None:
    """Structural proof that Momentum is sequenced correctly - not just
    "called with something," but called with the SAME HolderResult the
    concurrent gather actually produced. Two runs differing only in
    holder_growth_24h_pct must produce different trending_score values
    (MomentumEngine.compute's own weighted-sum formula: HOLDER_GROWTH_WEIGHT
    * holder.holder_growth_24h_pct is one of its four additive terms) -
    if run_scan accidentally passed a stale/default HolderResult instead
    of the real one, this would fail by producing identical scores."""
    store_a, store_b = SessionStore(), SessionStore()

    core_engine_a = CoreEngine(_FakeMarketProvider(_solana_pairs()))
    security_engine_a = SecurityEngine(_FakeSecurityProvider(_safe_report()))
    holder_engine_a = HolderEngine(_FakeHolderProvider(holders=[]))  # holder_growth_24h_pct == 0.0

    core_engine_b = CoreEngine(_FakeMarketProvider(_solana_pairs()))
    security_engine_b = SecurityEngine(_FakeSecurityProvider(_safe_report()))

    from analysis.api_abstraction import FundingRecord, HolderRecord

    # Three of five observed wallets funded within the last 24h -
    # HolderEngine._growth_from_funding (Step 8) computes
    # holder_growth_24h_pct as (recent / len(timed)) * 100 = 60.0, well
    # above holder_engine_a's 0.0 (no funding records at all).
    now = time.time()
    holders_b = [
        HolderRecord(wallet_address=f"wallet{i}", token_account_address=f"acct{i}", balance=1000.0, pct_of_supply=1.0)
        for i in range(5)
    ]
    funding_b = [
        FundingRecord(
            wallet_address=f"wallet{i}", funding_source_address="funder1",
            funded_at_slot=100, funded_at_block_time=int(now - 3600),
        )
        for i in range(3)
    ] + [
        FundingRecord(
            wallet_address=f"wallet{i}", funding_source_address="funder2",
            funded_at_slot=50, funded_at_block_time=int(now - (10 * 86400)),
        )
        for i in range(3, 5)
    ]
    holder_engine_b = HolderEngine(_FakeHolderProvider(holders=holders_b, funding=funding_b))

    result_a = await run_scan(
        "So11111111111111111111111111111111111111112",
        core_engine_a, security_engine_a, holder_engine_a, MomentumEngine(), SocialEngine(_FakeSocialProvider()),
        ScoringPipeline(), store_a,
    )
    result_b = await run_scan(
        "So11111111111111111111111111111111111111112",
        core_engine_b, security_engine_b, holder_engine_b, MomentumEngine(), SocialEngine(_FakeSocialProvider()),
        ScoringPipeline(), store_b,
    )

    assert result_a.holder.holder_growth_24h_pct != result_b.holder.holder_growth_24h_pct
    assert result_a.momentum.trending_score != result_b.momentum.trending_score
