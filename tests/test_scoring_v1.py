"""
Playbook reference: Unified Developer Playbook, Part VIII Step 6 - Unit
Testing Requirements: one test per Part III.6 tier boundary (85, 70, 50,
30) at and just past each; a test confirming pipeline_version == "v1"
when optional params are omitted; Definition of Done's full fixture
matrix from Steps 4-5 run through the pipeline end-to-end.

Fully executable: formulas.py and pipeline.py are pure stdlib, and
CoreEngine/SecurityEngine (Steps 4-5) are already independently testable
without aiohttp/aiogram — reused here via the same fake-provider pattern.
"""

from __future__ import annotations

import asyncio
import json
import math
import pathlib

import pytest

from analysis.core_engine import CoreEngine, CoreResult
from analysis.providers.dexscreener_parser import parse_pairs_response
from analysis.providers.rugcheck_parser import parse_report
from analysis.security_engine import SecurityEngine, SecurityResult
from bot.constants import Chain
from scoring.formulas import (
    score_ai,
    score_confidence,
    score_opportunity_v1,
    score_risk_v1,
    tier_label,
    vulnerability_penalty,
)
from scoring.pipeline import ScoringPipeline, ScoringResult

_DEXSCREENER_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "dexscreener"
_RUGCHECK_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "rugcheck"


def _dexscreener_pairs(name: str) -> list:
    return parse_pairs_response(json.loads((_DEXSCREENER_FIXTURES / name).read_text()))


def _rugcheck_report(name: str):
    return parse_report(json.loads((_RUGCHECK_FIXTURES / name).read_text()), chain_supported=True)


def _core_result(**overrides) -> CoreResult:
    base = dict(
        address="Tok1111111111111111111111111111111111111111", chain=Chain.SOL, primary_pair=None,
        liquidity_usd=0.0, market_cap=0.0, fdv=0.0, dilution_ratio=None,
        volume_24h=0.0, pool_age_days=None, price_change={}, buy_pressure_pct=50.0,
    )
    base.update(overrides)
    return CoreResult(**base)


def _security_result(**overrides) -> SecurityResult:
    base = dict(
        trust_score=100.0, risk_level="Low Risk", mint_authority_active=False,
        freeze_authority_active=False, buy_tax_pct=0.0, sell_tax_pct=0.0,
        lp_lock_ratio_pct=100.0, lp_lock_duration_days=365.0, ownership_renounced=True,
        scam_flags=[], provider_notes=[],
    )
    base.update(overrides)
    return SecurityResult(**base)


# ---------------------------------------------------------------------------
# vulnerability_penalty — Part III.6's priority-ladder sub-formula
# ---------------------------------------------------------------------------


def test_vulnerability_penalty_clean_token_is_zero() -> None:
    assert vulnerability_penalty(_security_result()) == 0.0


def test_vulnerability_penalty_honeypot_via_sell_tax_is_100() -> None:
    assert vulnerability_penalty(_security_result(sell_tax_pct=99.5)) == 100.0


def test_vulnerability_penalty_freeze_authority_is_100_regardless_of_tax() -> None:
    assert vulnerability_penalty(_security_result(freeze_authority_active=True, sell_tax_pct=0.0)) == 100.0


def test_vulnerability_penalty_mint_authority_only_is_40() -> None:
    assert vulnerability_penalty(_security_result(mint_authority_active=True)) == 40.0


def test_vulnerability_penalty_unrenounced_only_is_20() -> None:
    assert vulnerability_penalty(_security_result(ownership_renounced=False)) == 20.0


def test_vulnerability_penalty_priority_ladder_freeze_beats_mint() -> None:
    """Both active - must land on the 100 band, not 40."""
    result = vulnerability_penalty(
        _security_result(freeze_authority_active=True, mint_authority_active=True)
    )
    assert result == 100.0


def test_vulnerability_penalty_degraded_is_neutral_20_not_0_or_100() -> None:
    degraded = _security_result(degraded=True, degraded_reason="test")
    assert vulnerability_penalty(degraded) == 20.0


# ---------------------------------------------------------------------------
# score_risk_v1 — redistributed weights (0.5/0.5, not the full formula's 0.3/0.4/0.3)
# ---------------------------------------------------------------------------


def test_score_risk_v1_fully_safe_is_zero() -> None:
    assert score_risk_v1(_security_result()) == 0.0


def test_score_risk_v1_worst_case_is_100() -> None:
    worst = _security_result(trust_score=0.0, freeze_authority_active=True)
    assert score_risk_v1(worst) == 100.0


def test_score_risk_v1_matches_hand_computed_redistribution() -> None:
    # trust_score=60 -> 0.5*(100-60)=20; mint active -> vulnerability=40 -> 0.5*40=20; total=40
    mid = _security_result(trust_score=60.0, mint_authority_active=True)
    assert score_risk_v1(mid) == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# score_confidence — final form
# ---------------------------------------------------------------------------


def test_score_confidence_scales_with_pool_age() -> None:
    core = _core_result(pool_age_days=10.0)
    assert score_confidence(core, _security_result()) == pytest.approx(15.0)  # 10*1.5


def test_score_confidence_caps_at_100_for_old_pools() -> None:
    core = _core_result(pool_age_days=1000.0)  # 1000*1.5=1500, well over 100
    assert score_confidence(core, _security_result()) == 100.0


def test_score_confidence_none_pool_age_treated_as_zero_not_favorable() -> None:
    core = _core_result(pool_age_days=None)
    assert score_confidence(core, _security_result()) == 0.0


def test_score_confidence_boundary_at_100() -> None:
    core = _core_result(pool_age_days=100.0 / 1.5)  # exactly the boundary
    assert score_confidence(core, _security_result()) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# score_opportunity_v1 — the corrected 1.0 coefficient (not the spec's original 0.7)
# ---------------------------------------------------------------------------


def test_score_opportunity_v1_zero_volume_is_zero() -> None:
    assert score_opportunity_v1(_core_result(volume_24h=0.0)) == 0.0


def test_score_opportunity_v1_uses_corrected_coefficient_of_one_not_point_seven() -> None:
    """Regression guard for the arithmetic error found and fixed while
    implementing this step (formulas.py's docstring): proportional
    redistribution of the single surviving 0.3-weighted term to fill the
    full 1.0 weight gives a coefficient of 1.0, not 0.7."""
    core = _core_result(volume_24h=1_000_000.0)
    expected_with_correct_coefficient = 1.0 * math.log(1_000_000.0)
    expected_with_original_spec_error = 0.7 * math.log(1_000_000.0)

    result = score_opportunity_v1(core)

    assert result == pytest.approx(expected_with_correct_coefficient)
    assert result != pytest.approx(expected_with_original_spec_error)


def test_score_opportunity_v1_clamps_at_100_for_extreme_volume() -> None:
    core = _core_result(volume_24h=math.exp(150))  # ln(x)=150, unrealistic but a clean clamp test
    assert score_opportunity_v1(core) == 100.0


# ---------------------------------------------------------------------------
# score_ai — the final combination
# ---------------------------------------------------------------------------


def test_score_ai_perfect_inputs_zero_risk() -> None:
    assert score_ai(100.0, 100.0, 0.0) == 100.0


def test_score_ai_clamps_negative_to_zero() -> None:
    assert score_ai(0.0, 0.0, 100.0) == 0.0


def test_score_ai_matches_hand_computation() -> None:
    # 0.7*50 + 0.3*50 - 20 = 35+15-20 = 30
    assert score_ai(50.0, 50.0, 20.0) == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# tier_label — all four Part III.6 boundaries, at and just past each
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ai_score,expected_label",
    [
        (100.0, "Strong Profile"), (85.0, "Strong Profile"), (84.9, "Solid, Monitor"),
        (70.0, "Solid, Monitor"), (69.9, "Mixed Signals"),
        (50.0, "Mixed Signals"), (49.9, "Elevated Risk"),
        (30.0, "Elevated Risk"), (29.9, "Critical Risk"),
        (0.0, "Critical Risk"),
    ],
)
def test_tier_label_boundaries(ai_score: float, expected_label: str) -> None:
    assert tier_label(ai_score) == expected_label


# ---------------------------------------------------------------------------
# ScoringPipeline — dispatch, pipeline_version, explanation
# ---------------------------------------------------------------------------


def test_pipeline_version_is_v1_when_optional_inputs_omitted() -> None:
    pipeline = ScoringPipeline()
    result = pipeline.score(_core_result(), _security_result())
    assert result.pipeline_version == "v1"


def test_pipeline_v2_with_only_one_of_holder_momentum_raises_value_error() -> None:
    """v2 is implemented as of Step 10 [custom roadmap: Step 12] - see
    test_scoring_v2.py for its own full test suite. This file keeps only
    the one case that's still genuinely about v1/dispatch behavior:
    passing just one of holder/momentum (not both) is a caller error,
    not a silent v1 fallback or a crash deep inside a formula."""
    pipeline = ScoringPipeline()
    with pytest.raises(ValueError, match="needs both holder and momentum"):
        pipeline.score(_core_result(), _security_result(), holder=object())


# STEP 14 VERIFICATION: this file used to also carry
# test_pipeline_v3_trigger_raises_not_implemented, written back when v3
# was a future stub expected to raise NotImplementedError. Now that
# Step 14 actually implements v3, ScoringPipeline.score's real
# dispatch-validation behavior for a partial (social-without-holder/
# momentum) call is a specific ValueError, not NotImplementedError -
# and that behavior has its own correct, current coverage in
# test_scoring_v3.py (test_pipeline_social_with_only_one_of_holder_or_momentum_raises,
# test_pipeline_social_without_holder_and_momentum_raises). The stale
# test was asserting a contract this codebase deliberately no longer
# has, confirmed failing under real execution, and is removed here
# rather than "fixed" into a duplicate of v3's own suite.


def test_pipeline_result_fields_are_internally_consistent() -> None:
    pipeline = ScoringPipeline()
    core = _core_result(volume_24h=500_000.0, pool_age_days=45.0)
    security = _security_result(trust_score=80.0, mint_authority_active=True)

    result = pipeline.score(core, security)

    assert result.score_opportunity == pytest.approx(score_opportunity_v1(core))
    assert result.score_risk == pytest.approx(score_risk_v1(security))
    assert result.score_confidence == pytest.approx(score_confidence(core, security))
    assert result.score_ai == pytest.approx(
        score_ai(result.score_opportunity, result.score_confidence, result.score_risk)
    )
    assert result.tier_label == tier_label(result.score_ai)


def test_explanation_includes_scam_flags_when_present() -> None:
    pipeline = ScoringPipeline()
    security = _security_result(mint_authority_active=True, scam_flags=["Mint authority is still active — supply can be diluted"])
    result = pipeline.score(_core_result(), security)

    assert "Mint authority is still active — supply can be diluted" in result.explanation


def test_explanation_notes_clean_result_when_no_flags() -> None:
    pipeline = ScoringPipeline()
    result = pipeline.score(_core_result(), _security_result())
    assert any("no mint/freeze authority" in line.lower() for line in result.explanation)


def test_explanation_notes_degraded_security_distinctly() -> None:
    pipeline = ScoringPipeline()
    degraded = _security_result(degraded=True, degraded_reason="provider outage")
    result = pipeline.score(_core_result(), degraded)
    assert any("unavailable" in line.lower() and "provider outage" in line for line in result.explanation)


def test_explanation_always_notes_v1_staging_caveat() -> None:
    pipeline = ScoringPipeline()
    result = pipeline.score(_core_result(), _security_result())
    assert any("momentum and social" in line.lower() for line in result.explanation)


# ---------------------------------------------------------------------------
# Definition of Done: full Step 4/5 fixture matrix through the pipeline
# end-to-end, each landing in a tier that's checked against a hand
# computation, not just "some plausible label."
# ---------------------------------------------------------------------------


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


@pytest.mark.asyncio
async def test_fully_safe_fixture_lands_in_expected_tier() -> None:
    core_engine = CoreEngine(_FakeMarketProvider(_dexscreener_pairs("solana_valid.json")))
    security_engine = SecurityEngine(_FakeSecurityProvider(_rugcheck_report("fully_safe.json")))

    core = await core_engine.analyze("So11111111111111111111111111111111111111112")
    security = await security_engine.analyze(core)
    result = ScoringPipeline().score(core, security)

    expected_ai = score_ai(
        score_opportunity_v1(core), score_confidence(core, security), score_risk_v1(security)
    )
    assert result.score_ai == pytest.approx(expected_ai)
    assert result.tier_label == tier_label(expected_ai)
    # fully_safe.json's score_normalised=6 -> trust_score=94.0 (not a
    # perfect 100) -> score_risk_v1 = 0.5*(100-94) + 0.5*0 = 3.0
    assert result.score_risk == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_honeypot_fixture_lands_in_critical_risk() -> None:
    core_engine = CoreEngine(_FakeMarketProvider(_dexscreener_pairs("solana_valid.json")))
    security_engine = SecurityEngine(_FakeSecurityProvider(_rugcheck_report("honeypot.json")))

    core = await core_engine.analyze("So11111111111111111111111111111111111111112")
    security = await security_engine.analyze(core)
    result = ScoringPipeline().score(core, security)

    assert result.tier_label == "Critical Risk"
    # NOT exactly 100.0: vulnerability_penalty=100 (freeze active + honeypot
    # tax) does dominate, but score_risk_v1 = 0.5*(100-trust_score) +
    # 0.5*vulnerability_penalty still adds the trust_score term.
    # honeypot.json's score_normalised=97 -> trust_score=3.0 ->
    # 0.5*(100-3) + 0.5*100 = 48.5 + 50 = 98.5. An earlier draft of this
    # test asserted exactly 100.0 - a wrong assumption on my part (that a
    # maxed vulnerability_penalty alone forces the ceiling), caught by
    # actually running this rather than eyeballing the formula.
    assert security.mint_authority_active is True
    assert security.freeze_authority_active is True
    assert result.score_risk == pytest.approx(98.5)


@pytest.mark.asyncio
async def test_provider_outage_still_produces_a_complete_scoring_result() -> None:
    """Part IV.3's partial-failure rule, verified all the way through the
    pipeline: a degraded SecurityResult must still produce a complete,
    non-crashing ScoringResult."""
    core_engine = CoreEngine(_FakeMarketProvider(_dexscreener_pairs("solana_valid.json")))

    class _RaisingProvider:
        async def scan(self, address: str, chain) -> object:
            raise TimeoutError("simulated outage")

    core = await core_engine.analyze("So11111111111111111111111111111111111111112")
    security = await SecurityEngine(_RaisingProvider()).analyze(core)
    result = ScoringPipeline().score(core, security)

    assert security.degraded is True
    assert isinstance(result, ScoringResult)
    assert 0.0 <= result.score_ai <= 100.0
    valid_labels = {"Strong Profile", "Solid, Monitor", "Mixed Signals", "Elevated Risk", "Critical Risk"}
    assert result.tier_label in valid_labels
