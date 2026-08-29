"""
Playbook reference: Unified Developer Playbook, Part VIII Step 10 - Unit
Testing Requirements: "All of Step 6's boundary tests re-run and
passing; new boundary test at HCI=30% flowing all the way through to
Score_Risk; a test confirming v1 fallback still functions when
holder/momentum are omitted." (Step 6's own boundary tests stay in
test_scoring_v1.py, unmodified, per that requirement - re-run there, not
duplicated here.)

Landed as Step 10 [custom roadmap: Step 12] - Holder Engine (Step 8) and
Momentum Engine (Step 9) both exist by this point, matching Part VII.1's
staged-rollout rationale for why v2 couldn't land any earlier.
"""

from __future__ import annotations

import math

import pytest

from analysis.core_engine import CoreResult
from analysis.holder_engine import HolderResult
from analysis.momentum_engine import MomentumResult
from analysis.security_engine import SecurityResult
from bot.constants import Chain
from scoring.formulas import (
    holder_concentration_penalty,
    score_ai,
    score_confidence,
    score_opportunity_v2,
    score_risk_v2,
    tier_label,
    vulnerability_penalty,
)
from scoring.pipeline import ScoringPipeline


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
        trust_score=90.0, risk_level="Low", mint_authority_active=False, freeze_authority_active=False,
        buy_tax_pct=0.0, sell_tax_pct=0.0, lp_lock_ratio_pct=100.0, lp_lock_duration_days=100.0,
        ownership_renounced=True, scam_flags=[], provider_notes=[],
    )
    base.update(overrides)
    return SecurityResult(**base)


def _holder_result(**overrides) -> HolderResult:
    base = dict(
        holder_count=20, holder_growth_24h_pct=10.0, hci_pct=15.0, whale_count=1,
        classified_wallets={}, insider_bundle_detected=False, insider_bundle_wallet_count=0,
    )
    base.update(overrides)
    return HolderResult(**base)


def _momentum_result(**overrides) -> MomentumResult:
    base = dict(
        volume_growth_pct=0.0, liquidity_growth_pct=0.0, price_momentum=5.0,
        buy_momentum=10.0, whale_momentum=0.0, social_momentum=0.0, trending_score=8.0,
    )
    base.update(overrides)
    return MomentumResult(**base)


# ---------------------------------------------------------------------------
# holder_concentration_penalty
# ---------------------------------------------------------------------------


def test_holder_concentration_penalty_is_direct_passthrough() -> None:
    assert holder_concentration_penalty(_holder_result(hci_pct=42.3)) == pytest.approx(42.3)


def test_holder_concentration_penalty_degraded_holder_contributes_zero() -> None:
    degraded = HolderResult(
        holder_count=0, holder_growth_24h_pct=0.0, hci_pct=0.0, whale_count=0,
        degraded=True, degraded_reason="unavailable",
    )
    assert holder_concentration_penalty(degraded) == 0.0


# ---------------------------------------------------------------------------
# score_risk_v2 - Part VII.1's own named requirement: HCI=30% boundary
# flowing all the way through to Score_Risk.
# ---------------------------------------------------------------------------


def test_score_risk_v2_matches_hand_computed_formula() -> None:
    security = _security_result(trust_score=70.0, mint_authority_active=True)
    holder = _holder_result(hci_pct=50.0)
    expected = 0.3 * (100.0 - 70.0) + 0.4 * 50.0 + 0.3 * vulnerability_penalty(security)
    assert score_risk_v2(security, holder) == pytest.approx(expected)


def test_score_risk_v2_hci_30_percent_boundary_flows_through() -> None:
    """Part III.3's own >30% flag threshold, traced all the way to
    Score_Risk - a 1-point difference either side of 30% must produce a
    correspondingly different (not identical, not wildly different)
    Score_Risk, per holder_concentration_penalty's direct-passthrough
    design."""
    security = _security_result()
    just_below = score_risk_v2(security, _holder_result(hci_pct=29.9))
    just_above = score_risk_v2(security, _holder_result(hci_pct=30.1))
    assert just_above > just_below
    assert just_above - just_below == pytest.approx(0.4 * 0.2, abs=1e-6)  # only the 0.4-weighted term moved


def test_score_risk_v2_full_concentration_and_full_vulnerability_caps_at_100() -> None:
    worst_security = _security_result(freeze_authority_active=True, trust_score=0.0)
    worst_holder = _holder_result(hci_pct=100.0)
    assert score_risk_v2(worst_security, worst_holder) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# score_opportunity_v2
# ---------------------------------------------------------------------------


def test_score_opportunity_v2_matches_hand_computed_formula() -> None:
    core = _core_result(volume_24h=250_000.0)
    momentum = _momentum_result(trending_score=20.0)
    expected = max(0.0, min(100.0, 0.4 * 20.0 + 0.6 * math.log(250_000.0)))
    assert score_opportunity_v2(core, momentum) == pytest.approx(expected)


def test_score_opportunity_v2_zero_volume_guards_log_domain() -> None:
    core = _core_result(volume_24h=0.0)
    momentum = _momentum_result(trending_score=15.0)
    expected = max(0.0, min(100.0, 0.4 * 15.0))
    assert score_opportunity_v2(core, momentum) == pytest.approx(expected)


def test_score_opportunity_v2_negative_trending_score_pulls_score_down() -> None:
    core = _core_result(volume_24h=100_000.0)
    positive = score_opportunity_v2(core, _momentum_result(trending_score=30.0))
    negative = score_opportunity_v2(core, _momentum_result(trending_score=-30.0))
    assert negative < positive


# ---------------------------------------------------------------------------
# ScoringPipeline v2 dispatch
# ---------------------------------------------------------------------------


def test_pipeline_dispatches_to_v2_when_both_holder_and_momentum_given() -> None:
    pipeline = ScoringPipeline()
    result = pipeline.score(_core_result(), _security_result(), holder=_holder_result(), momentum=_momentum_result())
    assert result.pipeline_version == "v2"


def test_pipeline_v1_fallback_still_works_when_holder_momentum_omitted() -> None:
    """Step 10's own named requirement: v1 must still function
    unmodified when the newer optional inputs aren't supplied."""
    pipeline = ScoringPipeline()
    result = pipeline.score(_core_result(), _security_result())
    assert result.pipeline_version == "v1"


def test_pipeline_v2_result_fields_are_internally_consistent() -> None:
    pipeline = ScoringPipeline()
    core = _core_result(volume_24h=500_000.0, pool_age_days=45.0)
    security = _security_result(trust_score=80.0, mint_authority_active=True)
    holder = _holder_result(hci_pct=35.0)
    momentum = _momentum_result(trending_score=12.0)

    result = pipeline.score(core, security, holder=holder, momentum=momentum)

    assert result.score_opportunity == pytest.approx(score_opportunity_v2(core, momentum))
    assert result.score_risk == pytest.approx(score_risk_v2(security, holder))
    assert result.score_confidence == pytest.approx(score_confidence(core, security))
    assert result.score_ai == pytest.approx(
        score_ai(result.score_opportunity, result.score_confidence, result.score_risk)
    )
    assert result.tier_label == tier_label(result.score_ai)


def test_pipeline_v2_explanation_mentions_holder_concentration_and_momentum() -> None:
    pipeline = ScoringPipeline()
    result = pipeline.score(
        _core_result(), _security_result(), holder=_holder_result(hci_pct=45.0), momentum=_momentum_result(trending_score=7.5)
    )
    joined = " ".join(result.explanation).lower()
    assert "45.0%" in " ".join(result.explanation)
    assert "momentum trend" in joined


def test_pipeline_v2_explanation_notes_insider_bundle_when_detected() -> None:
    pipeline = ScoringPipeline()
    holder = _holder_result(insider_bundle_detected=True, insider_bundle_wallet_count=4)
    result = pipeline.score(_core_result(), _security_result(), holder=holder, momentum=_momentum_result())
    assert any("insider" in line.lower() and "4 wallets" in line for line in result.explanation)


def test_pipeline_v2_explanation_notes_degraded_holder_distinctly() -> None:
    pipeline = ScoringPipeline()
    degraded_holder = HolderResult(
        holder_count=0, holder_growth_24h_pct=0.0, hci_pct=0.0, whale_count=0,
        degraded=True, degraded_reason="chain not supported",
    )
    result = pipeline.score(_core_result(), _security_result(), holder=degraded_holder, momentum=_momentum_result())
    assert any("holder data unavailable" in line.lower() and "chain not supported" in line for line in result.explanation)


def test_pipeline_v2_explanation_still_notes_social_not_yet_wired() -> None:
    pipeline = ScoringPipeline()
    result = pipeline.score(_core_result(), _security_result(), holder=_holder_result(), momentum=_momentum_result())
    assert any("social" in line.lower() and "later" in line.lower() for line in result.explanation)
