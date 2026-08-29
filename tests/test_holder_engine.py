"""
Playbook reference: Unified Developer Playbook, Part VIII Step 8 - Unit
Testing Requirements: one test per Definition-of-Done fixture (low-
concentration/organic, high-concentration HCI>30%, whale-heavy, insider-
bundle, provider outage), plus an explicit boundary test at HCI=30%
(strictly-greater-than, not >=) since a summation landing a hair off a
round threshold is exactly the off-by-one this step calls out by name.

Everything here is executable without aiohttp/aiogram installed, same
reasoning as test_core_engine.py / test_security_engine.py:
`solana_rpc_parser.py` has zero aiohttp dependency, and `HolderEngine`
depends only on the `HolderDataProvider` Protocol, satisfied here by a
plain fake fed from fixture data or built directly in Python.

FREE-TIER PROVIDER CHOICE (this session's explicit requirement, replacing
Step 8's own Solscan assumption): see `analysis/holder_engine.py` and
`analysis/providers/solana_rpc.py`'s module docstrings for the full
reasoning. Nothing below makes a live network call, to either the public
Solana RPC endpoint or Helius - Part V.8's fixture-only rule applies
here exactly as it does to every other engine's tests.
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any

import pytest

from analysis.api_abstraction import FundingRecord, HolderRecord
from analysis.core_engine import CoreResult
from analysis.holder_engine import HolderEngine, HolderResult
from analysis.providers.solana_rpc_parser import resolve_rpc_urls
from analysis.providers.solana_rpc_parser import (
    parse_account_owner,
    parse_funding_from_transaction,
    parse_largest_accounts,
    parse_signatures,
    parse_token_supply,
)
from bot.constants import Chain

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "holders"
_TOTAL_SUPPLY = 1_000_000.0  # matches token_supply_standard.json and every largest_accounts_*.json


def _load(name: str) -> Any:
    """Returns whatever shape the fixture actually is - most are JSON
    objects, but `signatures_for_address.json` is a top-level JSON array
    (matching `getSignaturesForAddress`'s real, unwrapped-array `result`
    shape - see solana_rpc_parser.py's docstring)."""
    return json.loads((_FIXTURES_DIR / name).read_text())


def _holders_from_fixture(name: str, total_supply: float = _TOTAL_SUPPLY) -> list[HolderRecord]:
    """Mirrors what `SolanaRpcHolderProvider.get_holders` does internally
    (parse -> compute pct_of_supply -> HolderRecord), without a network -
    the same "run the real parsing/combination logic against fixture
    data" approach `test_core_engine.py` uses for DexScreener's multi-pool
    fixtures."""
    balances = parse_largest_accounts(_load(name))
    return [
        HolderRecord(
            wallet_address=b.token_account_address,  # fine for engine-level tests: HCI/whale only
            token_account_address=b.token_account_address,  # care about pct_of_supply, not identity
            balance=b.ui_amount,
            pct_of_supply=(b.ui_amount / total_supply) * 100,
        )
        for b in balances
    ]


def _core_result(chain: Chain = Chain.SOL, address: str = "SomeMint1111111111111111111111111111111111") -> CoreResult:
    """Minimal, non-degraded CoreResult - HolderEngine only reads
    .address/.chain/.degraded from it (same Integration Requirement
    Step 5 established for SecurityEngine: consumed by field access
    only)."""
    return CoreResult(
        address=address, chain=chain, primary_pair=None,
        liquidity_usd=0.0, market_cap=0.0, fdv=0.0, dilution_ratio=None,
        volume_24h=0.0, pool_age_days=None, price_change={}, buy_pressure_pct=50.0,
    )


class _FakeProvider:
    def __init__(
        self,
        holders: list[HolderRecord] | None = None,
        funding: list[FundingRecord] | None = None,
        raises_on_holders: Exception | None = None,
        raises_on_funding: Exception | None = None,
    ) -> None:
        self._holders = holders if holders is not None else []
        self._funding = funding if funding is not None else []
        self._raises_on_holders = raises_on_holders
        self._raises_on_funding = raises_on_funding

    async def get_holders(self, address: str, chain: Chain) -> list[HolderRecord]:
        if self._raises_on_holders is not None:
            raise self._raises_on_holders
        return self._holders

    async def get_launch_block_funding(self, address: str, chain: Chain) -> list[FundingRecord]:
        if self._raises_on_funding is not None:
            raise self._raises_on_funding
        return self._funding


# ---------------------------------------------------------------------------
# solana_rpc_parser.py — real fixture-driven parsing tests
# ---------------------------------------------------------------------------


def test_parse_token_supply_prefers_ui_amount_string() -> None:
    supply = parse_token_supply(_load("token_supply_standard.json"))
    assert supply == 1_000_000.0


def test_parse_token_supply_missing_value_defaults_to_zero() -> None:
    assert parse_token_supply({}) == 0.0
    assert parse_token_supply({"value": {}}) == 0.0


def test_parse_largest_accounts_organic_sorted_descending() -> None:
    balances = parse_largest_accounts(_load("largest_accounts_organic.json"))
    assert len(balances) == 20
    assert balances[0].ui_amount == 9000.0
    assert balances[0].token_account_address == "TokenAcctOrganic01111111111111111111111111"
    # strictly descending - re-sort defensiveness actually exercised, not just present
    assert all(balances[i].ui_amount >= balances[i + 1].ui_amount for i in range(len(balances) - 1))


def test_parse_largest_accounts_handles_out_of_order_input() -> None:
    """The parser re-sorts rather than trusting the response's own order
    (module docstring) - fed deliberately out of order here."""
    raw = {
        "value": [
            {"address": "A", "uiAmountString": "10"},
            {"address": "B", "uiAmountString": "999"},
            {"address": "C", "uiAmountString": "500"},
        ]
    }
    balances = parse_largest_accounts(raw)
    assert [b.token_account_address for b in balances] == ["B", "C", "A"]


def test_parse_account_owner_reads_nested_path_not_top_level() -> None:
    """Regression guard for the exact bug this step's module docstring
    warns about by name: `value.owner` (Token Program ID) must NOT be
    returned in place of `value.data.parsed.info.owner` (the real
    holder wallet)."""
    owner = parse_account_owner(_load("account_info_owner.json"))
    assert owner == "HolderWallet1111111111111111111111111111111"
    assert owner != "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # the Token Program ID, not a holder


def test_parse_account_owner_defensive_on_unexpected_shape() -> None:
    """A closed account / non-jsonParsed response must return None, not
    raise or return a wrong-but-present value."""
    assert parse_account_owner(_load("account_info_unexpected_shape.json")) is None
    assert parse_account_owner({}) is None
    assert parse_account_owner({"value": None}) is None


def test_parse_signatures_preserves_newest_first_order() -> None:
    sigs = parse_signatures(_load("signatures_for_address.json"))
    assert len(sigs) == 3
    assert sigs[0].signature.startswith("SigNewest")
    assert sigs[-1].signature.startswith("SigEarliest")
    assert sigs[-1].slot == 300000100
    assert sigs[-1].block_time == 1754800100


def test_parse_funding_from_transaction_sol_transfer_identifies_fee_payer_as_funder() -> None:
    funder, slot, block_time = parse_funding_from_transaction(
        _load("transaction_funding_sol_transfer.json"), "FreshBuyerWallet11111111111111111111111111"
    )
    assert funder == "FunderWallet111111111111111111111111111111"
    assert slot == 300000100
    assert block_time == 1754800100


def test_parse_funding_from_transaction_no_balance_increase_returns_all_none() -> None:
    """The wallet in this fixture PAID sol (its own balance dropped) -
    not a funding-in pattern, must not be misread as one."""
    funder, slot, block_time = parse_funding_from_transaction(
        _load("transaction_no_clear_funding.json"), "SelfFundedWallet111111111111111111111111111"
    )
    assert (funder, slot, block_time) == (None, None, None)


def test_parse_funding_from_transaction_wallet_not_present_returns_all_none() -> None:
    funder, slot, block_time = parse_funding_from_transaction(
        _load("transaction_funding_sol_transfer.json"), "SomeWalletNotInThisTransaction1111111111111"
    )
    assert (funder, slot, block_time) == (None, None, None)


def test_parse_funding_from_transaction_none_input_returns_all_none() -> None:
    assert parse_funding_from_transaction(None, "AnyWallet") == (None, None, None)


# ---------------------------------------------------------------------------
# solana_rpc_parser.py — resolve_rpc_urls (free-tier URL fallback list, no
# network). Moved here from solana_rpc.py, and renamed/extended to build
# an ordered list rather than pick one, during the Step 11 (custom
# roadmap) fallback-RPC pass - same four cases as before, updated to the
# new signature/return shape, plus the new QuickNode/Shyft cases.
# ---------------------------------------------------------------------------


def test_resolve_rpc_urls_no_keys_uses_only_the_free_public_endpoint() -> None:
    assert resolve_rpc_urls(None, None, None) == ["https://api.mainnet-beta.solana.com"]


def test_resolve_rpc_urls_empty_string_keys_treated_as_not_configured() -> None:
    assert resolve_rpc_urls("", "", "") == ["https://api.mainnet-beta.solana.com"]


def test_resolve_rpc_urls_with_helius_key_prepends_helius_free_tier() -> None:
    assert resolve_rpc_urls("my-helius-key", None, None) == [
        "https://mainnet.helius-rpc.com/?api-key=my-helius-key",
        "https://api.mainnet-beta.solana.com",
    ]


def test_resolve_rpc_urls_custom_fallback_overrides_default_public_url() -> None:
    assert resolve_rpc_urls(None, None, None, "https://shyft.example.com/rpc") == ["https://shyft.example.com/rpc"]


def test_resolve_rpc_urls_quicknode_url_used_verbatim_no_template() -> None:
    assert resolve_rpc_urls(None, "https://my-endpoint.solana-mainnet.quiknode.pro/abc123/", None) == [
        "https://my-endpoint.solana-mainnet.quiknode.pro/abc123/",
        "https://api.mainnet-beta.solana.com",
    ]


def test_resolve_rpc_urls_shyft_key_uses_shyft_template() -> None:
    assert resolve_rpc_urls(None, None, "my-shyft-key") == [
        "https://rpc.shyft.to?api_key=my-shyft-key",
        "https://api.mainnet-beta.solana.com",
    ]


def test_resolve_rpc_urls_all_configured_returns_full_priority_order() -> None:
    urls = resolve_rpc_urls("helius-key", "https://my-endpoint.solana-mainnet.quiknode.pro/abc123/", "shyft-key")
    assert urls == [
        "https://mainnet.helius-rpc.com/?api-key=helius-key",
        "https://my-endpoint.solana-mainnet.quiknode.pro/abc123/",
        "https://rpc.shyft.to?api_key=shyft-key",
        "https://api.mainnet-beta.solana.com",
    ]


# ---------------------------------------------------------------------------
# HolderEngine.analyze — fake provider, fixture-backed where useful
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_organic_low_concentration_no_whales() -> None:
    holders = _holders_from_fixture("largest_accounts_organic.json")
    engine = HolderEngine(_FakeProvider(holders=holders))
    result = await engine.analyze(_core_result())

    assert result.degraded is False
    assert result.hci_pct == pytest.approx(6.75)
    assert result.whale_count == 0
    assert result.holder_count == 20
    assert result.holder_count_is_estimate is True


@pytest.mark.asyncio
async def test_analyze_high_concentration_hci_above_30() -> None:
    holders = _holders_from_fixture("largest_accounts_high_concentration.json")
    engine = HolderEngine(_FakeProvider(holders=holders))
    result = await engine.analyze(_core_result())

    assert result.hci_pct == pytest.approx(35.0)
    assert result.hci_pct > 30.0
    assert result.whale_count == 2  # 25% and 6% holders only - the 1.5%-exact holder does NOT count


@pytest.mark.asyncio
async def test_analyze_whale_heavy_counts_each_whale_but_stays_under_hci_threshold() -> None:
    holders = _holders_from_fixture("largest_accounts_whale_heavy.json")
    engine = HolderEngine(_FakeProvider(holders=holders))
    result = await engine.analyze(_core_result())

    assert result.whale_count == 8  # the eight 2.5% holders; the two 1.0% holders don't count
    assert result.hci_pct == pytest.approx(22.0)
    assert result.hci_pct < 30.0  # distinct signal from the high-concentration case above


@pytest.mark.asyncio
async def test_analyze_hci_exactly_at_30_percent_boundary_is_exact_not_drifted() -> None:
    """Step 8's own stated concern, verified directly: ten independently-
    computed 3.0% shares must sum to EXACTLY 30.0, not
    29.999999999999996 - the round() in holder_engine.py exists
    specifically for this. A future >30% check (Step 10) must correctly
    NOT trigger here."""
    holders = _holders_from_fixture("largest_accounts_hci_boundary.json")
    engine = HolderEngine(_FakeProvider(holders=holders))
    result = await engine.analyze(_core_result())

    assert result.hci_pct == 30.0  # exact equality, not approx - this is the whole point of the test
    assert not (result.hci_pct > 30.0)


@pytest.mark.asyncio
async def test_analyze_provider_outage_on_holders_degrades() -> None:
    engine = HolderEngine(_FakeProvider(raises_on_holders=TimeoutError("simulated RPC outage")))
    result = await engine.analyze(_core_result())

    assert result.degraded is True
    assert "provider" in (result.degraded_reason or "").lower()
    assert result.holder_count == 0
    assert result.hci_pct == 0.0


@pytest.mark.asyncio
async def test_analyze_unresolved_core_result_short_circuits_without_calling_provider() -> None:
    degraded_core = CoreResult(
        address="x", chain=None, primary_pair=None, liquidity_usd=0.0, market_cap=0.0,
        fdv=0.0, dilution_ratio=None, volume_24h=0.0, pool_age_days=None,
        price_change={}, buy_pressure_pct=50.0, degraded=True, degraded_reason="test",
    )
    provider = _FakeProvider(raises_on_holders=AssertionError("should never be called"))
    engine = HolderEngine(provider)
    result = await engine.analyze(degraded_core)

    assert result.degraded is True
    assert "no resolved chain" in (result.degraded_reason or "").lower()


@pytest.mark.asyncio
async def test_analyze_non_solana_chain_degrades_without_calling_provider() -> None:
    """Unlike SecurityEngine (lets RugCheck's chain_supported decide),
    HolderEngine gates on Chain.SOL itself before ever touching the
    provider - see holder_engine.py's docstring for why."""
    provider = _FakeProvider(raises_on_holders=AssertionError("should never be called for a non-SOL chain"))
    engine = HolderEngine(provider)
    result = await engine.analyze(_core_result(chain=Chain.ETH))

    assert result.degraded is True
    assert "solana only" in (result.degraded_reason or "").lower()
    assert "eth" in (result.degraded_reason or "").lower()


@pytest.mark.asyncio
async def test_analyze_insider_bundle_detected_at_three_matching_wallets() -> None:
    funding = [
        FundingRecord("W1", "Deployer111111111111111111111111111111111", 500_000, 1_700_000_000),
        FundingRecord("W2", "Deployer111111111111111111111111111111111", 500_000, 1_700_000_050),
        FundingRecord("W3", "Deployer111111111111111111111111111111111", 500_000, 1_700_000_100),
        FundingRecord("W4", "SomeUnrelatedCexWallet11111111111111111111", 500_010, 1_700_000_200),
        FundingRecord("W5", None, None, None),  # unresolved - must not pollute grouping
    ]
    engine = HolderEngine(_FakeProvider(holders=[], funding=funding))
    result = await engine.analyze(_core_result())

    assert result.insider_bundle_detected is True
    assert result.insider_bundle_wallet_count == 3


@pytest.mark.asyncio
async def test_analyze_insider_bundle_not_detected_below_threshold() -> None:
    """Two wallets sharing a (slot, funder) is common and NOT enough on
    its own (holder_engine.py's documented threshold reasoning) -
    verifies both the boolean and that wallet_count stays 0, not 2, when
    unflagged."""
    funding = [
        FundingRecord("W1", "SharedFunder1111111111111111111111111111111", 500_000, 1_700_000_000),
        FundingRecord("W2", "SharedFunder1111111111111111111111111111111", 500_000, 1_700_000_050),
    ]
    engine = HolderEngine(_FakeProvider(holders=[], funding=funding))
    result = await engine.analyze(_core_result())

    assert result.insider_bundle_detected is False
    assert result.insider_bundle_wallet_count == 0


@pytest.mark.asyncio
async def test_analyze_funding_lookup_failure_still_returns_valid_hci_and_whale_data() -> None:
    """Part IV.3's partial-failure rule, applied one level finer than the
    Playbook states it (holder_engine.py's own docstring): the funding
    sub-feature failing must not blank out HCI/whale/classification data
    that already succeeded from the separate get_holders call."""
    holders = _holders_from_fixture("largest_accounts_organic.json")
    engine = HolderEngine(
        _FakeProvider(holders=holders, raises_on_funding=TimeoutError("simulated funding-lookup outage"))
    )
    result = await engine.analyze(_core_result())

    assert result.degraded is False  # NOT degraded overall - HCI/whale data is still good
    assert result.hci_pct == pytest.approx(6.75)
    assert result.holder_count == 20
    assert result.insider_bundle_detected is False  # funding data simply unavailable, not "confirmed none"
    assert result.holder_growth_24h_pct == 0.0


@pytest.mark.asyncio
async def test_analyze_classifies_known_incinerator_address_as_burn() -> None:
    holders = [
        HolderRecord("1nc1nerator11111111111111111111111111111111", "TA1", 50_000.0, 5.0),
        HolderRecord("RegularHolderWallet111111111111111111111111", "TA2", 9_000.0, 0.9),
    ]
    engine = HolderEngine(_FakeProvider(holders=holders))
    result = await engine.analyze(_core_result())

    assert result.classified_wallets == {"1nc1nerator11111111111111111111111111111111": "burn"}
    assert "RegularHolderWallet111111111111111111111111" not in result.classified_wallets


@pytest.mark.asyncio
async def test_analyze_growth_reflects_fraction_of_recently_funded_top_holders() -> None:
    now = time.time()
    funding = [
        FundingRecord("Recent1", "F1", 1, int(now - 3600)),      # 1 hour ago - within 24h
        FundingRecord("Recent2", "F2", 2, int(now - 7200)),      # 2 hours ago - within 24h
        FundingRecord("Old1", "F3", 3, int(now - 400 * 3600)),   # ~16.7 days ago - not within 24h
        FundingRecord("Unknown1", None, None, None),             # excluded from denominator entirely
    ]
    engine = HolderEngine(_FakeProvider(holders=[], funding=funding))
    result = await engine.analyze(_core_result())

    assert result.holder_growth_24h_pct == pytest.approx((2 / 3) * 100)


@pytest.mark.asyncio
async def test_analyze_growth_with_no_timed_funding_data_is_zero_not_none() -> None:
    engine = HolderEngine(_FakeProvider(holders=[], funding=[FundingRecord("W1", None, None, None)]))
    result = await engine.analyze(_core_result())

    assert result.holder_growth_24h_pct == 0.0


@pytest.mark.asyncio
async def test_analyze_empty_holders_is_not_degraded_just_minimal() -> None:
    """A provider genuinely returning zero holders (vs. failing outright)
    is a valid, if unusual, real-world answer - must not be conflated
    with degraded=True, which is reserved for actual failures."""
    engine = HolderEngine(_FakeProvider(holders=[], funding=[]))
    result = await engine.analyze(_core_result())

    assert result.degraded is False
    assert result.holder_count == 0
    assert result.hci_pct == 0.0
    assert result.whale_count == 0
