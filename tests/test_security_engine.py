"""
Playbook reference: Unified Developer Playbook, Part VIII Step 5 - Unit
Testing Requirements: one test per Definition-of-Done fixture (fully-safe,
mint-only, freeze-only/critical, honeypot, moderate-tax, unrenounced-
ownership-only, provider outage), asserting the correct flag(s) and no
others, plus a combined-conditions fixture verifying flags don't clobber
each other.

Everything here is executable without aiohttp/aiogram installed, same
reasoning as test_core_engine.py: `rugcheck_parser.py` has zero aiohttp
dependency, and `SecurityEngine` depends only on the `SecurityDataProvider`
Protocol, satisfied here by a plain fake fed from fixture data.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from analysis.core_engine import CoreResult
from analysis.providers.rugcheck_parser import parse_report
from analysis.security_engine import SecurityEngine, SecurityResult
from analysis.api_abstraction import SecurityReport
from bot.constants import Chain

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "rugcheck"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text())


def _parse(name: str) -> SecurityReport:
    return parse_report(_load(name), chain_supported=True)


def _core_result(chain: Chain = Chain.SOL, address: str = "SomeToken111111111111111111111111111111111") -> CoreResult:
    """Minimal, non-degraded CoreResult - SecurityEngine only reads
    .address/.chain/.degraded from it (Integration Requirement: consumed
    by field access only)."""
    return CoreResult(
        address=address, chain=chain, primary_pair=None,
        liquidity_usd=0.0, market_cap=0.0, fdv=0.0, dilution_ratio=None,
        volume_24h=0.0, pool_age_days=None, price_change={}, buy_pressure_pct=50.0,
    )


# ---------------------------------------------------------------------------
# rugcheck_parser.py — real fixture-driven parsing tests
# ---------------------------------------------------------------------------


def test_fully_safe_parses_with_no_authorities_and_full_lp_lock() -> None:
    r = _parse("fully_safe.json")
    assert r.mint_authority_active is False
    assert r.freeze_authority_active is False
    assert r.buy_tax_pct == 0.0 and r.sell_tax_pct == 0.0
    assert r.lp_lock_ratio_pct == 100.0
    assert r.lp_lock_duration_days == 365
    assert r.trust_score == 100.0 - 6  # score_normalised=6, inverted
    assert r.chain_supported is True


def test_mint_authority_active_parsed_correctly() -> None:
    r = _parse("mint_authority_active.json")
    assert r.mint_authority_active is True
    assert r.freeze_authority_active is False
    assert r.trust_score == 100.0 - 38


def test_freeze_authority_active_parsed_correctly() -> None:
    r = _parse("freeze_authority_active.json")
    assert r.freeze_authority_active is True
    assert r.mint_authority_active is False
    assert r.lp_lock_ratio_pct == 0.0


def test_honeypot_transfer_fee_maps_to_both_buy_and_sell_tax() -> None:
    r = _parse("honeypot.json")
    assert r.buy_tax_pct == 99.5
    assert r.sell_tax_pct == 99.5
    assert r.mint_authority_active is True
    assert r.freeze_authority_active is True


def test_moderate_tax_parsed_correctly() -> None:
    r = _parse("moderate_tax.json")
    assert r.buy_tax_pct == 14.0 and r.sell_tax_pct == 14.0


def test_unrenounced_ownership_uses_explicit_field_not_proxy() -> None:
    r = _parse("unrenounced_ownership_only.json")
    assert r.ownership_renounced is False
    assert r.mint_authority_active is False
    assert r.freeze_authority_active is False  # isolates this one condition


def test_ownership_renounced_proxy_when_no_explicit_field() -> None:
    """fully_safe.json has no ownershipRenounced key - both authorities
    null should proxy to renounced=True (rugcheck_parser.py's documented
    fallback)."""
    r = _parse("fully_safe.json")
    assert r.ownership_renounced is True


def test_missing_score_normalised_defaults_to_neutral_trust_score() -> None:
    r = parse_report({"token": {}}, chain_supported=True)
    assert r.trust_score == 50.0


def test_missing_lp_market_data_returns_none_not_zero() -> None:
    """A confirmed 0% lock and 'couldn't find lock data' must stay
    distinguishable - collapsing them to the same 0.0 would be a real
    information loss."""
    r = parse_report({"token": {}, "score_normalised": 50}, chain_supported=True)
    assert r.lp_lock_ratio_pct is None
    assert r.lp_lock_duration_days is None


def test_chain_unsupported_returns_neutral_empty_report() -> None:
    r = parse_report({"some": "payload"}, chain_supported=False)
    assert r.chain_supported is False
    assert r.mint_authority_active is False
    assert r.raw_risk_flags == []


def test_risks_array_extracted_as_named_flags() -> None:
    r = _parse("honeypot.json")
    assert "Extremely high transfer fee" in r.raw_risk_flags
    assert "Mint authority still enabled" in r.raw_risk_flags
    assert "Freeze authority still enabled" in r.raw_risk_flags
    assert len(r.raw_risk_flags) == 3


# ---------------------------------------------------------------------------
# SecurityEngine.analyze — fake provider, fixture-backed
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, report: SecurityReport | None = None, raises: Exception | None = None) -> None:
        self._report = report
        self._raises = raises

    async def scan(self, address: str, chain: Chain) -> SecurityReport:
        if self._raises is not None:
            raise self._raises
        assert self._report is not None
        return self._report


@pytest.mark.asyncio
async def test_analyze_fully_safe_no_flags() -> None:
    engine = SecurityEngine(_FakeProvider(report=_parse("fully_safe.json")))
    result = await engine.analyze(_core_result())

    assert result.degraded is False
    assert result.scam_flags == []
    assert result.risk_level == "Low Risk"


@pytest.mark.asyncio
async def test_analyze_mint_authority_active_flags_only_that() -> None:
    engine = SecurityEngine(_FakeProvider(report=_parse("mint_authority_active.json")))
    result = await engine.analyze(_core_result())

    assert result.degraded is False
    assert any("Mint authority" in f for f in result.scam_flags)
    assert not any("Freeze authority" in f for f in result.scam_flags)
    assert not any("honeypot" in f.lower() for f in result.scam_flags)
    assert not any("combined" in f.lower() for f in result.scam_flags)


@pytest.mark.asyncio
async def test_analyze_freeze_authority_active_flags_only_that() -> None:
    engine = SecurityEngine(_FakeProvider(report=_parse("freeze_authority_active.json")))
    result = await engine.analyze(_core_result())

    assert any("Freeze authority" in f for f in result.scam_flags)
    assert not any("Mint authority" in f for f in result.scam_flags)


@pytest.mark.asyncio
async def test_analyze_honeypot_flags_critical_not_moderate() -> None:
    engine = SecurityEngine(_FakeProvider(report=_parse("honeypot.json")))
    result = await engine.analyze(_core_result())

    assert any("honeypot" in f.lower() for f in result.scam_flags)
    assert not any("High combined" in f for f in result.scam_flags)  # critical, not the lesser band
    assert result.risk_level == "Critical Risk"


@pytest.mark.asyncio
async def test_analyze_moderate_tax_flags_high_combined_not_honeypot() -> None:
    engine = SecurityEngine(_FakeProvider(report=_parse("moderate_tax.json")))
    result = await engine.analyze(_core_result())

    assert any("High combined buy+sell tax: 28.0%" in f for f in result.scam_flags)
    assert not any("honeypot" in f.lower() for f in result.scam_flags)


@pytest.mark.asyncio
async def test_analyze_below_tax_threshold_flags_nothing_tax_related() -> None:
    """5% + 5% = 10%, not > 10% (Part III.2's threshold is strictly
    'over 10%') - must not flag."""
    report = parse_report(
        {"token": {}, "score_normalised": 10, "transferFee": {"pct": 5.0}}, chain_supported=True
    )
    engine = SecurityEngine(_FakeProvider(report=report))
    result = await engine.analyze(_core_result())
    assert not any("tax" in f.lower() or "honeypot" in f.lower() for f in result.scam_flags)


@pytest.mark.asyncio
async def test_analyze_unrenounced_ownership_only_flags_only_that() -> None:
    engine = SecurityEngine(_FakeProvider(report=_parse("unrenounced_ownership_only.json")))
    result = await engine.analyze(_core_result())

    assert result.scam_flags == ["Ownership has not been renounced"]


@pytest.mark.asyncio
async def test_scam_flags_and_provider_notes_stay_separate_not_merged() -> None:
    """The fixture's own risks[] describes the same condition
    ("Program ownership not renounced") this engine also derives
    independently ("Ownership has not been renounced") - regression guard
    for exactly the duplication bug this step's real test run caught:
    the two must never be concatenated into one list."""
    engine = SecurityEngine(_FakeProvider(report=_parse("unrenounced_ownership_only.json")))
    result = await engine.analyze(_core_result())

    assert result.scam_flags == ["Ownership has not been renounced"]
    assert result.provider_notes == ["Program ownership not renounced"]
    assert set(result.scam_flags).isdisjoint(result.provider_notes)


@pytest.mark.asyncio
async def test_analyze_combined_conditions_both_flags_present_not_clobbered() -> None:
    engine = SecurityEngine(_FakeProvider(report=_parse("combined_mint_and_unrenounced.json")))
    result = await engine.analyze(_core_result())

    assert any("Mint authority" in f for f in result.scam_flags)
    assert "Ownership has not been renounced" in result.scam_flags
    assert len(result.scam_flags) == 2  # exactly these two, nothing lost, nothing extra


@pytest.mark.asyncio
async def test_analyze_provider_outage_degrades_core_still_valid() -> None:
    """Part IV.3's partial-failure rule, verified directly: Core's own
    result must remain independently valid while Security degrades."""
    core = _core_result()
    assert core.degraded is False  # Core succeeded

    engine = SecurityEngine(_FakeProvider(raises=TimeoutError("simulated RugCheck outage")))
    result = await engine.analyze(core)

    assert result.degraded is True
    assert "provider" in (result.degraded_reason or "").lower()
    assert core.degraded is False  # still true after Security's failure - unaffected


@pytest.mark.asyncio
async def test_analyze_unresolved_core_result_short_circuits_without_calling_provider() -> None:
    degraded_core = CoreResult(
        address="x", chain=None, primary_pair=None, liquidity_usd=0.0, market_cap=0.0,
        fdv=0.0, dilution_ratio=None, volume_24h=0.0, pool_age_days=None,
        price_change={}, buy_pressure_pct=50.0, degraded=True, degraded_reason="test",
    )
    provider = _FakeProvider(raises=AssertionError("should never be called"))
    engine = SecurityEngine(provider)
    result = await engine.analyze(degraded_core)

    assert result.degraded is True
    assert "no resolved chain" in (result.degraded_reason or "").lower()


@pytest.mark.asyncio
async def test_analyze_unsupported_chain_degrades_with_specific_reason() -> None:
    report = parse_report({}, chain_supported=False)
    engine = SecurityEngine(_FakeProvider(report=report))
    result = await engine.analyze(_core_result(chain=Chain.TON))

    assert result.degraded is True
    assert "ton" in (result.degraded_reason or "").lower()


# ---------------------------------------------------------------------------
# Risk-level banding in isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trust_score,expected_label",
    [(100.0, "Low Risk"), (80.0, "Low Risk"), (79.9, "Moderate Risk"),
     (50.0, "Moderate Risk"), (49.9, "High Risk"), (25.0, "High Risk"),
     (24.9, "Critical Risk"), (0.0, "Critical Risk")],
)
def test_risk_level_bands(trust_score: float, expected_label: str) -> None:
    assert SecurityEngine._risk_level(trust_score) == expected_label
