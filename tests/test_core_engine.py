"""
Playbook reference: Unified Developer Playbook, Part VIII Step 4 - Unit
Testing Requirements: fixtures for valid/invalid address per chain
family, a multi-pool response (tests argmax pool selection), a timeout/
failure fixture (tests the degrade path), zero-liquidity pool, FDV == 0
(guards the DilutionRatio divide), address matching no chain.

Everything in this file is executable without aiohttp or aiogram
installed: `dexscreener_parser.py` has zero aiohttp dependency (the
network call lives in the sibling `dexscreener.py`, not exercised here),
and `CoreEngine` depends only on the `MarketDataProvider` Protocol, which
`_FakeProvider` below satisfies with plain fixture data — no network
client needed to test either one for real.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from analysis.api_abstraction import PairData
from analysis.core_engine import CoreEngine, CoreResult
from analysis.providers.dexscreener_parser import parse_pair, parse_pairs_response
from bot.constants import Chain

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "dexscreener"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text())


def _load_pairs(name: str) -> list[PairData]:
    return parse_pairs_response(_load(name))


# ---------------------------------------------------------------------------
# dexscreener_parser.py — real fixture-driven parsing tests
# ---------------------------------------------------------------------------


def test_parse_solana_pair_maps_every_field() -> None:
    pairs = _load_pairs("solana_valid.json")
    assert len(pairs) == 1
    p = pairs[0]
    assert p.chain is Chain.SOL
    assert p.dex_id == "raydium"
    assert p.base_token_symbol == "EXTKN"
    assert p.quote_token_symbol == "SOL"
    assert p.liquidity_usd == 184320.55
    assert p.fdv == 7312000
    assert p.market_cap == 5120000
    assert p.volume_24h == 542300.75
    assert p.volume_1h == 22150.9
    assert p.price_change_24h == 14.2
    assert p.buys_24h == 412
    assert p.sells_24h == 298
    assert p.pair_created_at_ms == 1754000000000


def test_parse_evm_pair_maps_chain_id_correctly() -> None:
    pairs = _load_pairs("evm_valid.json")
    assert len(pairs) == 1
    assert pairs[0].chain is Chain.ETH
    assert pairs[0].base_token_symbol == "EEVM"


def test_parse_ton_pair() -> None:
    pairs = _load_pairs("ton_valid.json")
    assert len(pairs) == 1
    assert pairs[0].chain is Chain.TON


def test_parse_skips_unsupported_chain_but_keeps_supported_ones() -> None:
    """unsupported_chain_mixed.json has one 'polygon' pair (unsupported)
    and one 'ethereum' pair (supported) for the same token address."""
    pairs = _load_pairs("unsupported_chain_mixed.json")
    assert len(pairs) == 1
    assert pairs[0].chain is Chain.ETH


def test_parse_handles_null_pairs_field() -> None:
    """DexScreener returns pairs: null (not []) when nothing matches."""
    pairs = _load_pairs("empty_result.json")
    assert pairs == []


def test_parse_pair_skips_entry_missing_base_token_address() -> None:
    assert parse_pair({"chainId": "solana", "baseToken": {}}) is None


def test_parse_pair_defaults_missing_numeric_fields_to_zero_not_exception() -> None:
    minimal = {
        "chainId": "solana",
        "baseToken": {"address": "SomeAddress1111111111111111111111111111111"},
    }
    result = parse_pair(minimal)
    assert result is not None
    assert result.liquidity_usd == 0.0
    assert result.fdv == 0.0
    assert result.volume_24h == 0.0
    assert result.buys_24h == 0
    assert result.pair_created_at_ms is None


# ---------------------------------------------------------------------------
# CoreEngine.detect_chain — shape-only, no provider needed
# ---------------------------------------------------------------------------

_engine_no_provider = CoreEngine(provider=None)  # detect_chain never touches self._provider


@pytest.mark.parametrize(
    "address,expected",
    [
        ("So11111111111111111111111111111111111111112", Chain.SOL),
        ("1" * 34, Chain.SOL),
        ("EQ" + "A" * 46, Chain.TON),
        ("0x" + "a" * 40, None),  # EVM-shaped - ambiguous by shape alone
        ("not an address", None),
        ("", None),
    ],
)
def test_detect_chain(address: str, expected: Chain | None) -> None:
    assert _engine_no_provider.detect_chain(address) is expected


# ---------------------------------------------------------------------------
# CoreEngine.analyze — fake provider, fixture-backed
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Satisfies MarketDataProvider. Returns whatever was configured,
    or raises whatever was configured — the two behaviors CoreEngine
    needs to handle, with no aiohttp/network involved."""

    def __init__(
        self, pairs: list[PairData] | None = None, raises: Exception | None = None
    ) -> None:
        self._pairs = pairs or []
        self._raises = raises

    async def get_pairs(self, address: str) -> list[PairData]:
        if self._raises is not None:
            raise self._raises
        return self._pairs


@pytest.mark.asyncio
async def test_analyze_solana_happy_path() -> None:
    engine = CoreEngine(_FakeProvider(pairs=_load_pairs("solana_valid.json")))
    result = await engine.analyze("So11111111111111111111111111111111111111112")

    assert result.degraded is False
    assert result.chain is Chain.SOL
    assert result.liquidity_usd == 184320.55
    assert result.price_change == {"5m": 0.42, "1h": -1.85, "6h": 6.7, "24h": 14.2}
    assert result.dilution_ratio == pytest.approx(5120000 / 7312000)
    assert result.buy_pressure_pct == pytest.approx((412 / (412 + 298)) * 100)
    assert result.pool_age_days is not None and result.pool_age_days > 0


@pytest.mark.asyncio
async def test_analyze_unrecognized_shape_degrades_without_calling_provider() -> None:
    provider = _FakeProvider(raises=AssertionError("should never be called"))
    engine = CoreEngine(provider)
    result = await engine.analyze("definitely not an address")

    assert result.degraded is True
    assert result.chain is None
    assert "known address format" in (result.degraded_reason or "")


@pytest.mark.asyncio
async def test_analyze_provider_failure_degrades_not_raises() -> None:
    engine = CoreEngine(_FakeProvider(raises=TimeoutError("simulated network timeout")))
    result = await engine.analyze("So11111111111111111111111111111111111111112")

    assert result.degraded is True
    assert result.chain is None
    assert "provider" in (result.degraded_reason or "").lower()


@pytest.mark.asyncio
async def test_analyze_zero_pairs_found_degrades_with_specific_reason() -> None:
    engine = CoreEngine(_FakeProvider(pairs=[]))
    result = await engine.analyze("So11111111111111111111111111111111111111112")

    assert result.degraded is True
    assert "no trading pairs" in (result.degraded_reason or "").lower()


@pytest.mark.asyncio
async def test_analyze_guards_zero_fdv_dilution_ratio() -> None:
    engine = CoreEngine(_FakeProvider(pairs=_load_pairs("zero_fdv_and_zero_liquidity.json")))
    result = await engine.analyze("ZeroFdvToken111111111111111111111111111111")

    assert result.degraded is False  # a real, if degenerate, pair - not a failure
    assert result.dilution_ratio is None
    assert result.liquidity_usd == 0.0
    assert result.buy_pressure_pct == 50.0  # 0 buys + 0 sells -> neutral, not divide-by-zero


@pytest.mark.asyncio
async def test_analyze_evm_single_chain_resolves_without_hint() -> None:
    engine = CoreEngine(_FakeProvider(pairs=_load_pairs("evm_valid.json")))
    result = await engine.analyze("0x" + "b" * 40)

    assert result.degraded is False
    assert result.chain is Chain.ETH


@pytest.mark.asyncio
async def test_analyze_evm_ambiguous_without_hint_returns_candidates() -> None:
    engine = CoreEngine(_FakeProvider(pairs=_load_pairs("ambiguous_evm.json")))
    result = await engine.analyze("0x" + "d" * 40)

    assert result.degraded is True
    assert result.chain is None
    assert set(result.ambiguous_chain_candidates) == {Chain.ETH, Chain.BASE}
    assert "more than one chain" in (result.degraded_reason or "").lower()


@pytest.mark.asyncio
async def test_analyze_evm_ambiguous_resolved_by_hint_and_picks_highest_liquidity_pool() -> None:
    """ambiguous_evm.json has two pools on 'base': $120k and $80k
    liquidity. Given chain_hint=BASE, the engine should resolve to base
    AND pick the $120k pool as primary (Part III.1's argmax)."""
    engine = CoreEngine(_FakeProvider(pairs=_load_pairs("ambiguous_evm.json")))
    result = await engine.analyze("0x" + "d" * 40, chain_hint=Chain.BASE)

    assert result.degraded is False
    assert result.chain is Chain.BASE
    assert result.liquidity_usd == 120000.0
    assert result.primary_pair is not None and result.primary_pair.dex_id == "aerodrome"


@pytest.mark.asyncio
async def test_analyze_chain_hint_ignored_when_shape_is_unambiguous() -> None:
    """A Solana-shaped address with a (nonsensical) ETH hint should still
    resolve via shape, not the hint - shape is deterministic for Solana/
    TON; a hint is only meaningful for breaking an EVM tie."""
    engine = CoreEngine(_FakeProvider(pairs=_load_pairs("solana_valid.json")))
    result = await engine.analyze(
        "So11111111111111111111111111111111111111112", chain_hint=Chain.ETH
    )
    assert result.chain is Chain.SOL


# ---------------------------------------------------------------------------
# Formulas in isolation (Part III.1) - Acceptance Criteria: exact match
# ---------------------------------------------------------------------------


def test_dilution_ratio_formula() -> None:
    assert CoreEngine._dilution_ratio(5000.0, 10000.0) == 0.5
    assert CoreEngine._dilution_ratio(0.0, 0.0) is None
    assert CoreEngine._dilution_ratio(100.0, 0.0) is None


def test_buy_pressure_formula() -> None:
    assert CoreEngine._buy_pressure(75, 25) == 75.0
    assert CoreEngine._buy_pressure(0, 0) == 50.0
    assert CoreEngine._buy_pressure(0, 100) == 0.0
    assert CoreEngine._buy_pressure(100, 0) == 100.0


def test_pool_age_days_formula() -> None:
    import time

    thirty_days_ago_ms = int((time.time() - 30 * 86400) * 1000)
    age = CoreEngine._pool_age_days(thirty_days_ago_ms)
    assert age is not None
    assert 29.9 < age < 30.1  # small tolerance for wall-clock time elapsed during the test
    assert CoreEngine._pool_age_days(None) is None
