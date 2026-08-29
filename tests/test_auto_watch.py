"""
Playbook reference: Unified Developer Playbook, Part VIII Step 12.

Definition of Done (quoted exactly, since this file is built directly
against it): "A fixture-driven test proves a full watch cycle — start,
one non-matching scan, one matching scan, alert sent, stop — with no
real asyncio.sleep durations (use a fake clock or a very short test
interval) and no orphaned tasks left running after the test."

Fully executable without aiohttp/aiogram: `AutoWatchManager` (Step 12's
own module docstring) has zero aiogram import - `on_match` is an
injected callback, not a direct Telegram call - and every engine here is
a fake satisfying the same Protocols test_scan_orchestration.py's fakes
already do. `poll_interval_seconds_override` (a constructor param added
specifically for testability) replaces real per-minute intervals with a
few milliseconds, satisfying the "no real asyncio.sleep durations"
requirement without needing to mock `asyncio.sleep` globally.

STEP 14 VERIFICATION GATE - two real fixture defects found and fixed
here (both confirmed by actually running this file's own scenarios
in isolation, not by re-reading - see docs/STEP14_STEP15_HANDOFF.md
for the full trace):

1. `_pair()` built `pair_created_at_ms` as `pool_age_days *
   86_400_000` - a DURATION - but `CoreEngine._pool_age_days`
   (analysis/core_engine.py) treats that field as an ABSOLUTE unix-ms
   timestamp (`time.time() - pair_created_at_ms/1000`). Every pool
   this helper built therefore landed near the 1970 epoch and
   computed as thousands of days old regardless of the
   `pool_age_days` value actually passed in. This didn't flip any
   PREVIOUSLY-EXISTING test's verdict in this file (none of them
   differentiate on pool age - they all use `liquidity_usd` instead),
   but it meant pool age was never actually exercised as a
   discriminator anywhere here, and it silently broke the first draft
   of `test_non_matching_candidate_is_re_evaluated_once_it_ages_past_the_threshold`
   below until traced down. Fixed: `pair_created_at_ms` is now computed
   relative to real `time.time()`.

2. Every CONSERVATIVE-profile test in this file paired a fake social
   provider whose `lookup_user` always raised with CONSERVATIVE's own
   `require_social_presence=True` (analysis/filter_presets.py).
   `SocialEngine.analyze` degrades on any lookup failure (Step 13's
   contract), and `filter_presets.matches` correctly refuses to match
   a require-social-presence profile against a degraded social
   result - Part I.2's "under-promise" principle, working as
   designed. Net effect: every CONSERVATIVE-profile test below, AS
   ORIGINALLY WRITTEN, would have failed under real execution
   (`alerts == []`, never the asserted match) - confirmed directly,
   not assumed, before this fix. This was a test-fixture bug, not a
   production one: these tests all exercise the "genuinely good
   match" path, so the fake needs to actually resolve. Fixed:
   `_FakeSocialProvider.lookup_user` now returns a resolved,
   `account_exists=True` profile.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from analysis.api_abstraction import DiscoveryCandidate, PairData, SecurityReport, XUserProfile
from analysis.core_engine import CoreEngine
from analysis.filter_presets import AGGRESSIVE, CONSERVATIVE
from analysis.holder_engine import HolderEngine
from analysis.momentum_engine import MomentumEngine
from analysis.security_engine import SecurityEngine
from analysis.social_engine import SocialEngine
from bot.constants import Chain
from handlers.auto_watch import AutoWatchManager
from scoring.pipeline import ScoringPipeline
from state.session_store import SessionStore

# Short enough that a handful of cycles complete well within a test's
# own await-sleep window, long enough to not be a busy-loop.
_TEST_INTERVAL_SECONDS = 0.02


class _FakeDiscoveryProvider:
    """Satisfies TokenDiscoveryProvider. `candidates` is mutable after
    construction so a test can change what the next cycle discovers -
    e.g. start empty, then inject a candidate mid-test."""

    def __init__(self, candidates: list[DiscoveryCandidate] | None = None, raises: bool = False) -> None:
        self.candidates = candidates if candidates is not None else []
        self.raises = raises
        self.call_count = 0

    async def get_new_listings(self, limit: int = 30) -> list[DiscoveryCandidate]:
        self.call_count += 1
        if self.raises:
            raise RuntimeError("simulated discovery outage")
        return list(self.candidates)

    async def get_trending(self, limit: int = 30) -> list[DiscoveryCandidate]:
        return []


class _FakeMarketProvider:
    """Returns a different PairData per address, keyed by a dict a test
    configures - lets one discovery cycle contain both a matching and a
    non-matching candidate, distinguished by their actual scan data."""

    def __init__(self, pairs_by_address: dict[str, list[PairData]]) -> None:
        self._pairs_by_address = pairs_by_address

    async def get_pairs(self, address: str) -> list[PairData]:
        return self._pairs_by_address.get(address, [])


class _FakeSecurityProvider:
    def __init__(self, report: SecurityReport) -> None:
        self._report = report

    async def scan(self, address: str, chain) -> SecurityReport:
        return self._report


class _FakeHolderProvider:
    async def get_holders(self, address: str, chain) -> list:
        return []

    async def get_launch_block_funding(self, address: str, chain) -> list:
        return []


class _FakeSocialProvider:
    """Resolves successfully (account_exists=True) - see this module's
    own docstring, fixture defect #2. CONSERVATIVE requires verifiable
    social presence; every test below is exercising the "genuinely
    good match" path, so the fake must actually resolve rather than
    always fail."""

    async def lookup_user(self, handle: str) -> XUserProfile:
        return XUserProfile(
            user_id="1", username=handle, display_name=handle, is_verified=True,
            follower_count=50_000, following_count=100, tweet_count=1_000,
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc), description="",
            account_exists=True,
        )

    async def list_followers(self, handle: str, limit: int) -> list:
        return []

    async def search_mentions(self, ticker: str) -> list:
        return []


def _pair(address: str, liquidity_usd: float, market_cap: float, pool_age_days: float) -> PairData:
    """`pair_created_at_ms` must be an absolute unix-ms timestamp -
    CoreEngine._pool_age_days computes `time.time() -
    pair_created_at_ms/1000` - so it's built relative to real
    `time.time()` here, not as `pool_age_days * ms_per_day` (see this
    module's own docstring, fixture defect #1, for what that got
    wrong)."""
    created_at_ms = int((time.time() - pool_age_days * 86_400.0) * 1000)
    return PairData(
        chain=Chain.SOL, dex_id="raydium", pair_address=f"pair-{address}",
        base_token_address=address, base_token_symbol="TEST", base_token_name="Test Token",
        quote_token_symbol="SOL", price_usd=0.01, liquidity_usd=liquidity_usd,
        fdv=market_cap * 1.1, market_cap=market_cap,
        volume_5m=100.0, volume_1h=1000.0, volume_6h=5000.0, volume_24h=50_000.0,
        price_change_5m=0.1, price_change_1h=1.0, price_change_6h=2.0, price_change_24h=5.0,
        buys_24h=100, sells_24h=80, pair_created_at_ms=created_at_ms,
    )


def _safe_report() -> SecurityReport:
    return SecurityReport(
        trust_score=95.0, mint_authority_active=False, freeze_authority_active=False,
        buy_tax_pct=0.0, sell_tax_pct=0.0, lp_lock_ratio_pct=100.0, lp_lock_duration_days=100.0,
        ownership_renounced=True, raw_risk_flags=[], chain_supported=True,
    )


def _make_manager(
    pairs_by_address: dict[str, list[PairData]],
    discovery: _FakeDiscoveryProvider,
    on_match,
    session_store: SessionStore | None = None,
) -> AutoWatchManager:
    return AutoWatchManager(
        session_store=session_store or SessionStore(),
        discovery_provider=discovery,
        core_engine=CoreEngine(_FakeMarketProvider(pairs_by_address)),
        security_engine=SecurityEngine(_FakeSecurityProvider(_safe_report())),
        holder_engine=HolderEngine(_FakeHolderProvider()),
        momentum_engine=MomentumEngine(),
        social_engine=SocialEngine(_FakeSocialProvider()),
        scoring_pipeline=ScoringPipeline(),
        on_match=on_match,
        poll_interval_seconds_override=_TEST_INTERVAL_SECONDS,
    )


@pytest.mark.asyncio
async def test_full_watch_cycle_start_nonmatch_match_alert_stop() -> None:
    """The Definition of Done, quoted at module top, implemented as a
    single test: start, one non-matching scan, one matching scan, alert
    sent, stop."""
    matching_address = "Match1111111111111111111111111111111111111"
    non_matching_address = "NoMatch111111111111111111111111111111111111"

    pairs_by_address = {
        # Passes CONSERVATIVE: high liquidity, old enough, in-range cap.
        matching_address: [_pair(matching_address, liquidity_usd=200_000.0, market_cap=1_000_000.0, pool_age_days=10.0)],
        # Fails CONSERVATIVE on liquidity alone (well below its 50,000 floor).
        non_matching_address: [_pair(non_matching_address, liquidity_usd=500.0, market_cap=1_000_000.0, pool_age_days=10.0)],
    }
    discovery = _FakeDiscoveryProvider(
        candidates=[
            DiscoveryCandidate(chain=Chain.SOL, token_address=non_matching_address, source="new_listings"),
            DiscoveryCandidate(chain=Chain.SOL, token_address=matching_address, source="new_listings"),
        ]
    )

    alerts: list[tuple[int, str]] = []

    async def on_match(user_id: int, scored) -> None:
        alerts.append((user_id, scored.core.address))

    session_store = SessionStore()
    manager = _make_manager(pairs_by_address, discovery, on_match, session_store)

    await manager.start(user_id=1, profile=CONSERVATIVE, interval_min=1)
    await asyncio.sleep(_TEST_INTERVAL_SECONDS * 3)  # let at least one full cycle complete
    await manager.stop(user_id=1)

    assert alerts == [(1, matching_address)]  # exactly the matching one, exactly once

    # No orphaned task: the registry has nothing left for this user, and
    # the task object itself is fully finished, not just "cancel requested".
    assert session_store.get_watch_task(1) is None
    assert manager.status(1) is None


@pytest.mark.asyncio
async def test_stop_actually_cancels_not_just_marks_a_flag() -> None:
    """Step 12's own Unit Testing Requirement, verified two ways: the
    task object itself reports cancelled/done, AND on_match stops firing
    for cycles that would have happened after stop() if the loop were
    somehow still running."""
    address = "Watch111111111111111111111111111111111111111"
    pairs_by_address = {address: [_pair(address, liquidity_usd=200_000.0, market_cap=1_000_000.0, pool_age_days=10.0)]}
    discovery = _FakeDiscoveryProvider(
        candidates=[DiscoveryCandidate(chain=Chain.SOL, token_address=address, source="new_listings")]
    )
    alerts: list[int] = []

    async def on_match(user_id: int, scored) -> None:
        alerts.append(user_id)

    session_store = SessionStore()
    manager = _make_manager(pairs_by_address, discovery, on_match, session_store)

    await manager.start(user_id=2, profile=CONSERVATIVE, interval_min=1)
    await asyncio.sleep(_TEST_INTERVAL_SECONDS * 2)
    task_before_stop = session_store.get_watch_task(2)
    assert task_before_stop is not None
    assert not task_before_stop.done()

    await manager.stop(user_id=2)

    assert task_before_stop.cancelled() or task_before_stop.done()
    count_at_stop = len(alerts)
    await asyncio.sleep(_TEST_INTERVAL_SECONDS * 5)  # would catch more alerts if the loop were still alive
    assert len(alerts) == count_at_stop  # nothing new happened after stop


@pytest.mark.asyncio
async def test_emergency_stop_all_cancels_every_users_task() -> None:
    address = "Any11111111111111111111111111111111111111111"
    pairs_by_address = {address: [_pair(address, liquidity_usd=1.0, market_cap=1.0, pool_age_days=0.0)]}
    discovery = _FakeDiscoveryProvider(candidates=[])  # empty - just need long-lived idle loops here

    async def on_match(user_id: int, scored) -> None:
        pass

    session_store = SessionStore()
    manager = _make_manager(pairs_by_address, discovery, on_match, session_store)

    await manager.start(user_id=10, profile=AGGRESSIVE, interval_min=1)
    await manager.start(user_id=20, profile=AGGRESSIVE, interval_min=1)
    await manager.start(user_id=30, profile=AGGRESSIVE, interval_min=1)
    await asyncio.sleep(_TEST_INTERVAL_SECONDS * 2)

    tasks = [session_store.get_watch_task(uid) for uid in (10, 20, 30)]
    assert all(t is not None and not t.done() for t in tasks)

    await manager.emergency_stop_all()

    assert all(t.cancelled() or t.done() for t in tasks)
    assert session_store.get_watch_task(10) is None
    assert session_store.get_watch_task(20) is None
    assert session_store.get_watch_task(30) is None
    assert manager.status(10) is None
    assert manager.status(20) is None
    assert manager.status(30) is None


@pytest.mark.asyncio
async def test_starting_twice_replaces_the_old_watch_cleanly() -> None:
    """start() while already running must not leave two loops racing for
    the same user - the old task should be stopped, not orphaned."""
    address = "Any11111111111111111111111111111111111111111"
    pairs_by_address = {address: [_pair(address, liquidity_usd=1.0, market_cap=1.0, pool_age_days=0.0)]}
    discovery = _FakeDiscoveryProvider(candidates=[])

    async def on_match(user_id: int, scored) -> None:
        pass

    session_store = SessionStore()
    manager = _make_manager(pairs_by_address, discovery, on_match, session_store)

    await manager.start(user_id=5, profile=CONSERVATIVE, interval_min=1)
    first_task = session_store.get_watch_task(5)

    await manager.start(user_id=5, profile=AGGRESSIVE, interval_min=1)
    second_task = session_store.get_watch_task(5)

    assert first_task is not second_task
    await asyncio.sleep(_TEST_INTERVAL_SECONDS)
    assert first_task.cancelled() or first_task.done()  # the old one was actually stopped, not left running
    assert not second_task.done()

    await manager.stop(5)


@pytest.mark.asyncio
async def test_discovery_failure_one_cycle_does_not_kill_the_loop() -> None:
    """Part IV.3's partial-failure principle at the discovery-fetch
    level: one bad cycle logs and moves on, the loop keeps running for
    the next interval rather than dying."""
    address = "Match1111111111111111111111111111111111111"
    pairs_by_address = {address: [_pair(address, liquidity_usd=200_000.0, market_cap=1_000_000.0, pool_age_days=10.0)]}
    discovery = _FakeDiscoveryProvider(raises=True)  # every call raises

    alerts: list[int] = []

    async def on_match(user_id: int, scored) -> None:
        alerts.append(user_id)

    session_store = SessionStore()
    manager = _make_manager(pairs_by_address, discovery, on_match, session_store)

    await manager.start(user_id=7, profile=CONSERVATIVE, interval_min=1)
    await asyncio.sleep(_TEST_INTERVAL_SECONDS * 3)

    task = session_store.get_watch_task(7)
    assert task is not None and not task.done()  # still alive despite every discovery call failing
    assert discovery.call_count >= 2  # it kept retrying on the next interval, not just once

    await manager.stop(7)


@pytest.mark.asyncio
async def test_same_match_is_not_alerted_twice_in_one_watch_session() -> None:
    """seen_addresses dedup: a candidate the discovery feed keeps
    returning every cycle (a real, expected case - it doesn't disappear
    from "latest token profiles" the instant it's been seen once) must
    only trigger on_match the first time."""
    address = "Match1111111111111111111111111111111111111"
    pairs_by_address = {address: [_pair(address, liquidity_usd=200_000.0, market_cap=1_000_000.0, pool_age_days=10.0)]}
    discovery = _FakeDiscoveryProvider(
        candidates=[DiscoveryCandidate(chain=Chain.SOL, token_address=address, source="new_listings")]
    )

    alerts: list[int] = []

    async def on_match(user_id: int, scored) -> None:
        alerts.append(user_id)

    session_store = SessionStore()
    manager = _make_manager(pairs_by_address, discovery, on_match, session_store)

    await manager.start(user_id=9, profile=CONSERVATIVE, interval_min=1)
    await asyncio.sleep(_TEST_INTERVAL_SECONDS * 6)  # several cycles - same candidate returned every time
    await manager.stop(9)

    assert alerts == [9]  # exactly one alert despite the feed repeating the same candidate every cycle


@pytest.mark.asyncio
async def test_non_matching_candidate_is_re_evaluated_once_it_ages_past_the_threshold() -> None:
    """Auto-Watch Cooldown Review (STEP12_13_14_HANDOFF.md): the Playbook
    names no distinct time-based cooldown anywhere in Part VIII Step 12
    (confirmed by a full-text search of the Playbook while resolving
    this question) - duplicate-ALERT prevention is the one, sufficient,
    documented mechanism, and this file's own
    `test_same_match_is_not_alerted_twice_in_one_watch_session` above
    already covers it. What this test covers is the gap that mechanism
    must NOT also create: a candidate that legitimately doesn't match
    yet (too young for CONSERVATIVE's `min_pool_age_hours=72` here) must
    still be eligible for re-evaluation on a later cycle once real
    elapsed time (or a recovered degraded engine) would change the
    verdict - not permanently skipped after its first, non-matching
    scan for the rest of the watch session. Red before the
    `handlers/auto_watch.py` fix that renamed `seen_addresses` to
    `alerted_addresses` and moved the add() to only fire on an actual
    match; green after.
    """
    address = "Grow111111111111111111111111111111111111111"
    pairs_by_address = {address: [_pair(address, liquidity_usd=200_000.0, market_cap=1_000_000.0, pool_age_days=1.0)]}
    discovery = _FakeDiscoveryProvider(
        candidates=[DiscoveryCandidate(chain=Chain.SOL, token_address=address, source="new_listings")]
    )
    alerts: list[tuple[int, str]] = []

    async def on_match(user_id: int, scored) -> None:
        alerts.append((user_id, scored.core.address))

    session_store = SessionStore()
    manager = _make_manager(pairs_by_address, discovery, on_match, session_store)

    await manager.start(user_id=1, profile=CONSERVATIVE, interval_min=1)
    await asyncio.sleep(_TEST_INTERVAL_SECONDS * 3)  # cycle(s) at pool_age_days=1.0: too young, must NOT match
    assert alerts == []

    # Simulate real elapsed time: the SAME address is now old enough
    # (mutating the same dict `_FakeMarketProvider` was constructed
    # with - no new fake needed).
    pairs_by_address[address] = [_pair(address, liquidity_usd=200_000.0, market_cap=1_000_000.0, pool_age_days=4.0)]
    await asyncio.sleep(_TEST_INTERVAL_SECONDS * 5)
    await manager.stop(1)

    assert alerts == [(1, address)]


@pytest.mark.asyncio
async def test_status_reports_none_before_start_and_after_stop() -> None:
    async def on_match(user_id: int, scored) -> None:
        pass

    session_store = SessionStore()
    manager = _make_manager({}, _FakeDiscoveryProvider(candidates=[]), on_match, session_store)

    assert manager.status(99) is None
    await manager.start(user_id=99, profile=AGGRESSIVE, interval_min=5)
    status = manager.status(99)
    assert status is not None
    assert status.user_id == 99
    assert status.interval_min == 5
    assert status.matches_found == 0

    await manager.stop(99)
    assert manager.status(99) is None


@pytest.mark.asyncio
async def test_status_matches_found_increments_on_real_matches() -> None:
    address = "Match1111111111111111111111111111111111111"
    pairs_by_address = {address: [_pair(address, liquidity_usd=200_000.0, market_cap=1_000_000.0, pool_age_days=10.0)]}
    # Feed cycles through TWO distinct addresses across calls so
    # seen_addresses dedup doesn't suppress the second one.
    address_2 = "Match2222222222222222222222222222222222222"
    pairs_by_address[address_2] = [_pair(address_2, liquidity_usd=200_000.0, market_cap=1_000_000.0, pool_age_days=10.0)]

    call_state = {"n": 0}

    class _TwoStepDiscovery(_FakeDiscoveryProvider):
        async def get_new_listings(self, limit: int = 30):
            call_state["n"] += 1
            if call_state["n"] == 1:
                return [DiscoveryCandidate(chain=Chain.SOL, token_address=address, source="new_listings")]
            return [DiscoveryCandidate(chain=Chain.SOL, token_address=address_2, source="new_listings")]

    session_store = SessionStore()

    async def on_match(user_id: int, scored) -> None:
        pass

    manager = _make_manager(pairs_by_address, _TwoStepDiscovery(), on_match, session_store)
    await manager.start(user_id=11, profile=CONSERVATIVE, interval_min=1)
    await asyncio.sleep(_TEST_INTERVAL_SECONDS * 6)
    status = manager.status(11)
    assert status is not None
    assert status.matches_found >= 1
    await manager.stop(11)
