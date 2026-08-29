"""
Playbook reference: Unified Developer Playbook, Part VIII Step 14 - Unit
Testing Requirements: "v3 input normalization; five-engine integration;
missing/degraded engine behavior; social input; risk score; opportunity
score; confidence; explainability; deterministic scoring; boundary
conditions; malformed inputs; regression against v2."

Regression against v1/v2 is re-run in test_scoring_v1.py/test_scoring_v2.py
UNMODIFIED (this step touched neither file) - per those steps' own
Definition of Done ("re-run unmodified to confirm no regression"), not
duplicated here.

Landed as Step 14 - Social Engine (Step 13) exists by this point,
matching Part VII.1's staged-rollout rationale for why v3 couldn't land
any earlier: SentimentScore has nothing to read until now.
"""

from __future__ import annotations

import math

import pytest

from analysis.core_engine import CoreResult
from analysis.holder_engine import HolderResult
from analysis.momentum_engine import MomentumResult
from analysis.security_engine import SecurityResult
from analysis.social_engine import SocialResult
from bot.constants import Chain
from scoring.formulas import (
    score_ai,
    score_confidence,
    score_opportunity_v3,
    score_risk_v2,
    sentiment_score,
    tier_label,
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


def _social_result(**overrides) -> SocialResult:
    base = dict(
        x_score=60, verified_follower_ratio=0.5, tweet_frequency_per_day=2.0,
        influencer_mention_count=0, sentiment_ratio=0.0, follower_growth_pct=0.0,
    )
    base.update(overrides)
    return SocialResult(**base)


# ---------------------------------------------------------------------------
# sentiment_score
# ---------------------------------------------------------------------------


def test_sentiment_score_neutral_ratio_is_midpoint() -> None:
    assert sentiment_score(_social_result(sentiment_ratio=0.0)) == pytest.approx(50.0)


def test_sentiment_score_matches_hand_computed_linear_mapping() -> None:
    assert sentiment_score(_social_result(sentiment_ratio=0.4)) == pytest.approx((0.4 + 1.0) * 50.0)
    assert sentiment_score(_social_result(sentiment_ratio=-0.4)) == pytest.approx((-0.4 + 1.0) * 50.0)


def test_sentiment_score_extremes_hit_the_documented_bounds() -> None:
    assert sentiment_score(_social_result(sentiment_ratio=1.0)) == pytest.approx(100.0)
    assert sentiment_score(_social_result(sentiment_ratio=-1.0)) == pytest.approx(0.0)


def test_sentiment_score_degraded_social_contributes_neutral_not_zero() -> None:
    """The one case this formula is most likely to get wrong by
    accident: a degraded result must NOT read as sentiment_ratio's own
    default (0.0 happens to equal the neutral midpoint here, so this
    test deliberately uses a non-zero sentiment_ratio alongside
    degraded=True to prove the degraded check runs before the ratio is
    ever read, not that they coincidentally agree)."""
    degraded = _social_result(sentiment_ratio=-0.9, degraded=True, degraded_reason="account not found")
    assert sentiment_score(degraded) == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# score_opportunity_v3 - Part VII.1's own named requirement: this is the
# formula stage where redistribution logic finally disappears entirely.
# ---------------------------------------------------------------------------


def test_score_opportunity_v3_matches_hand_computed_formula() -> None:
    core = _core_result(volume_24h=250_000.0)
    momentum = _momentum_result(trending_score=20.0)
    social = _social_result(sentiment_ratio=0.5)
    expected = max(0.0, min(100.0, 0.4 * 20.0 + 0.3 * 75.0 + 0.3 * math.log(250_000.0)))
    assert score_opportunity_v3(core, momentum, social) == pytest.approx(expected)


def test_score_opportunity_v3_zero_volume_guards_log_domain() -> None:
    core = _core_result(volume_24h=0.0)
    momentum = _momentum_result(trending_score=15.0)
    social = _social_result(sentiment_ratio=0.2)
    expected = max(0.0, min(100.0, 0.4 * 15.0 + 0.3 * 60.0))
    assert score_opportunity_v3(core, momentum, social) == pytest.approx(expected)


def test_score_opportunity_v3_positive_sentiment_beats_negative_all_else_equal() -> None:
    core = _core_result(volume_24h=100_000.0)
    momentum = _momentum_result(trending_score=10.0)
    positive = score_opportunity_v3(core, momentum, _social_result(sentiment_ratio=0.8))
    negative = score_opportunity_v3(core, momentum, _social_result(sentiment_ratio=-0.8))
    assert positive > negative
    # only the 0.3-weighted SentimentScore term moved; isolate its exact contribution
    assert positive - negative == pytest.approx(0.3 * (sentiment_score(_social_result(sentiment_ratio=0.8))
                                                         - sentiment_score(_social_result(sentiment_ratio=-0.8))))


def test_score_opportunity_v3_degraded_social_matches_neutral_sentiment_case() -> None:
    """Degraded social should score identically to a real neutral
    reading, not to the worst or best case - proves the pipeline-level
    wiring reaches sentiment_score's own degraded branch rather than
    bypassing it."""
    core = _core_result(volume_24h=50_000.0)
    momentum = _momentum_result(trending_score=5.0)
    degraded = score_opportunity_v3(core, momentum, _social_result(degraded=True))
    neutral = score_opportunity_v3(core, momentum, _social_result(sentiment_ratio=0.0, degraded=False))
    assert degraded == pytest.approx(neutral)


def test_score_opportunity_v3_caps_at_100() -> None:
    core = _core_result(volume_24h=10_000_000_000.0)
    momentum = _momentum_result(trending_score=1000.0)
    social = _social_result(sentiment_ratio=1.0)
    assert score_opportunity_v3(core, momentum, social) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# ScoringPipeline v3 dispatch
# ---------------------------------------------------------------------------


def test_pipeline_dispatches_to_v3_when_all_three_optional_inputs_given() -> None:
    pipeline = ScoringPipeline()
    result = pipeline.score(
        _core_result(), _security_result(),
        holder=_holder_result(), momentum=_momentum_result(), social=_social_result(),
    )
    assert result.pipeline_version == "v3"


def test_pipeline_v1_fallback_still_works_when_everything_optional_omitted() -> None:
    pipeline = ScoringPipeline()
    result = pipeline.score(_core_result(), _security_result())
    assert result.pipeline_version == "v1"


def test_pipeline_v2_fallback_still_works_when_social_omitted() -> None:
    """Step 6/10's own dispatch pattern extended one more time (Step 14's
    "no new architectural pattern needs to be invented" note) - social
    alone is the new optional input; omitting only it must still reach
    v2 exactly as it did before this step existed."""
    pipeline = ScoringPipeline()
    result = pipeline.score(_core_result(), _security_result(), holder=_holder_result(), momentum=_momentum_result())
    assert result.pipeline_version == "v2"


def test_pipeline_social_without_holder_and_momentum_raises() -> None:
    pipeline = ScoringPipeline()
    with pytest.raises(ValueError, match="holder, momentum, and social together"):
        pipeline.score(_core_result(), _security_result(), social=_social_result())


def test_pipeline_social_with_only_one_of_holder_or_momentum_raises() -> None:
    pipeline = ScoringPipeline()
    with pytest.raises(ValueError, match="holder, momentum, and social together"):
        pipeline.score(_core_result(), _security_result(), holder=_holder_result(), social=_social_result())
    with pytest.raises(ValueError, match="holder, momentum, and social together"):
        pipeline.score(_core_result(), _security_result(), momentum=_momentum_result(), social=_social_result())


def test_pipeline_v3_result_fields_are_internally_consistent() -> None:
    pipeline = ScoringPipeline()
    core = _core_result(volume_24h=500_000.0, pool_age_days=45.0)
    security = _security_result(trust_score=80.0, mint_authority_active=True)
    holder = _holder_result(hci_pct=35.0)
    momentum = _momentum_result(trending_score=12.0)
    social = _social_result(sentiment_ratio=0.3)

    result = pipeline.score(core, security, holder=holder, momentum=momentum, social=social)

    assert result.score_opportunity == pytest.approx(score_opportunity_v3(core, momentum, social))
    # Score_Risk is UNCHANGED from v2 - Part III.6 gives Risk only three
    # terms and none of them is Social (formulas.score_risk_v2's own
    # docstring: "no score_risk_v3 waiting in a later step").
    assert result.score_risk == pytest.approx(score_risk_v2(security, holder))
    assert result.score_confidence == pytest.approx(score_confidence(core, security))
    expected_ai = score_ai(result.score_opportunity, result.score_confidence, result.score_risk)
    assert result.score_ai == pytest.approx(expected_ai)
    assert result.tier_label == tier_label(expected_ai)


def test_pipeline_v3_is_deterministic() -> None:
    """Same five inputs, called twice, must produce bit-for-bit identical
    output - Part V.8's coverage target singles out the scoring pipeline
    specifically because a silent nondeterminism here is this platform's
    highest-stakes bug class."""
    pipeline = ScoringPipeline()
    args = (_core_result(volume_24h=42_000.0), _security_result())
    kwargs = dict(holder=_holder_result(), momentum=_momentum_result(), social=_social_result(sentiment_ratio=0.15))
    first = pipeline.score(*args, **kwargs)
    second = pipeline.score(*args, **kwargs)
    assert first == second


# ---------------------------------------------------------------------------
# Explainability (Part I.2: "a number without a reason is not a finished
# feature") - the one place v3 legitimately REPLACES a v2 line instead of
# only adding to it (see _explain_v3's own docstring for why).
# ---------------------------------------------------------------------------


def test_v3_explanation_states_sentiment_and_drops_the_stale_v2_closing_line() -> None:
    pipeline = ScoringPipeline()
    result = pipeline.score(
        _core_result(), _security_result(),
        holder=_holder_result(), momentum=_momentum_result(),
        social=_social_result(sentiment_ratio=0.7),
    )
    explanation_text = " ".join(result.explanation)
    assert "social sentiment still doesn't" not in explanation_text  # v2's own closing line - now false, must be gone
    assert "positive" in explanation_text.lower()
    assert "v3 score" in explanation_text.lower()


def test_v3_explanation_states_degraded_social_honestly() -> None:
    pipeline = ScoringPipeline()
    result = pipeline.score(
        _core_result(), _security_result(),
        holder=_holder_result(), momentum=_momentum_result(),
        social=_social_result(degraded=True, degraded_reason="rate limited"),
    )
    explanation_text = " ".join(result.explanation)
    assert "rate limited" in explanation_text
    assert "uncertainty" in explanation_text.lower()


def test_v3_explanation_carries_forward_v2s_own_lines_unmodified() -> None:
    """_explain_v3 reuses _explain_v2's lines (DRY) rather than
    rebuilding them - every non-social line must be identical between
    the two, not just similar."""
    from scoring.pipeline import _explain_v2

    core, security = _core_result(), _security_result(mint_authority_active=False)
    holder, momentum = _holder_result(), _momentum_result()
    v2_lines = _explain_v2(core, security, holder, momentum)

    pipeline = ScoringPipeline()
    result = pipeline.score(core, security, holder=holder, momentum=momentum, social=_social_result())

    # result.explanation = v2_lines[:-1] + [one sentiment/degraded line] +
    # [one v3 closing line] - drop both trailing v3-only lines to compare
    # against v2_lines with its own closing line dropped.
    assert result.explanation[:-2] == v2_lines[:-1]
