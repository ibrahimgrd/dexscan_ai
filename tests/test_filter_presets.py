"""
Playbook reference: Unified Developer Playbook, Part VIII Step 12 - Unit
Testing Requirements: "One test per preset's threshold values."

Pure, provider-free - no aiohttp/aiogram anywhere in this chain
(filter_presets.py's own module docstring), fully executable in any
environment.
"""

from __future__ import annotations

from analysis.core_engine import CoreResult
from analysis.filter_presets import (
    AGGRESSIVE,
    BALANCED,
    CONSERVATIVE,
    MKT_CAP_BAND_LADDER,
    NAMED_PRESETS,
    NUMERIC_FIELD_KEYS,
    TOGGLE_FIELD_KEYS,
    FilterProfile,
    cycle_numeric_field,
    default_custom_profile,
    get_numeric_value,
    get_toggle_value,
    matches,
    set_bool_field,
)
from analysis.holder_engine import HolderResult
from analysis.momentum_engine import MomentumResult
from analysis.security_engine import SecurityResult
from analysis.social_engine import SocialResult
from bot.constants import Chain
from handlers.scan_orchestration import ScoredResult
from scoring.pipeline import ScoringResult
from scoring.risk_opportunity_matrix import classify


def _core(**overrides) -> CoreResult:
    base = dict(
        address="Tok1", chain=Chain.SOL, primary_pair=None,
        liquidity_usd=100_000.0, market_cap=5_000_000.0, fdv=6_000_000.0, dilution_ratio=None,
        volume_24h=200_000.0, pool_age_days=5.0, price_change={}, buy_pressure_pct=55.0,
    )
    base.update(overrides)
    return CoreResult(**base)


def _security(**overrides) -> SecurityResult:
    base = dict(
        trust_score=90.0, risk_level="Low", mint_authority_active=False, freeze_authority_active=False,
        buy_tax_pct=1.0, sell_tax_pct=1.0, lp_lock_ratio_pct=100.0, lp_lock_duration_days=100.0,
        ownership_renounced=True, scam_flags=[], provider_notes=[],
    )
    base.update(overrides)
    return SecurityResult(**base)


def _holder(**overrides) -> HolderResult:
    base = dict(holder_count=50, holder_growth_24h_pct=5.0, hci_pct=15.0, whale_count=1)
    base.update(overrides)
    return HolderResult(**base)


def _momentum(**overrides) -> MomentumResult:
    base = dict(
        volume_growth_pct=0.0, liquidity_growth_pct=0.0, price_momentum=5.0,
        buy_momentum=10.0, whale_momentum=0.0, social_momentum=0.0, trending_score=8.0,
    )
    base.update(overrides)
    return MomentumResult(**base)


def _social(**overrides) -> SocialResult:
    """Step 14 fixture addition. Defaults to a resolved (non-degraded)
    account so every existing `_scored()` caller that doesn't care about
    social keeps its prior behavior — `require_social_presence` used to
    be a no-op (always passed); a non-degraded default here means it
    still passes by default post-fix, and tests below that specifically
    want to exercise the new check override with `_social(degraded=True)`."""
    base = dict(
        x_score=60, verified_follower_ratio=0.5, tweet_frequency_per_day=2.0,
        influencer_mention_count=0, sentiment_ratio=0.0, follower_growth_pct=0.0,
    )
    base.update(overrides)
    return SocialResult(**base)


def _scored(core=None, security=None, holder=None, momentum=None, social=None) -> ScoredResult:
    core = core or _core()
    security = security or _security()
    holder = holder or _holder()
    momentum = momentum or _momentum()
    social = social or _social()
    scoring = ScoringResult(
        score_opportunity=50.0, score_risk=10.0, score_confidence=50.0, score_ai=70.0,
        tier_label="Solid, Monitor", explanation=[], pipeline_version="v3",
    )
    return ScoredResult(
        core=core, security=security, holder=holder, momentum=momentum, social=social, scoring=scoring,
        risk_opportunity=classify(scoring.score_risk, scoring.score_opportunity), result_id="abc",
    )


# ---------------------------------------------------------------------------
# Preset threshold values - one test per preset (Step 12's own requirement)
# ---------------------------------------------------------------------------


def test_conservative_thresholds() -> None:
    assert CONSERVATIVE.min_liquidity_usd == 50_000.0
    assert CONSERVATIVE.min_pool_age_hours == 72.0
    assert CONSERVATIVE.mkt_cap_range == (100_000.0, 50_000_000.0)
    assert CONSERVATIVE.max_tax_pct == 5.0
    assert CONSERVATIVE.reject_active_freeze_authority is True
    assert CONSERVATIVE.reject_honeypot_signals is True
    assert CONSERVATIVE.reject_high_concentration is True


def test_balanced_thresholds() -> None:
    assert BALANCED.min_liquidity_usd == 15_000.0
    assert BALANCED.min_pool_age_hours == 24.0
    assert BALANCED.mkt_cap_range == (20_000.0, 200_000_000.0)
    assert BALANCED.max_tax_pct == 10.0
    assert BALANCED.reject_high_concentration is False


def test_aggressive_thresholds() -> None:
    assert AGGRESSIVE.min_liquidity_usd == 3_000.0
    assert AGGRESSIVE.min_pool_age_hours == 1.0
    assert AGGRESSIVE.mkt_cap_range == (1_000.0, 1_000_000_000.0)
    assert AGGRESSIVE.max_tax_pct == 15.0
    assert AGGRESSIVE.reject_active_freeze_authority is False
    assert AGGRESSIVE.reject_honeypot_signals is True  # the one gate that stays on even here


def test_named_presets_dict_maps_all_three() -> None:
    assert NAMED_PRESETS["conservative"] is CONSERVATIVE
    assert NAMED_PRESETS["balanced"] is BALANCED
    assert NAMED_PRESETS["aggressive"] is AGGRESSIVE


def test_custom_preset_supports_the_same_toggle_set() -> None:
    """Step 12's own Acceptance Criteria: "Custom preset supports the
    same toggle set as the Advanced Rules screen" - i.e. FilterProfile's
    full field set is constructible with name="custom" and arbitrary
    values, not a restricted subset."""
    custom = FilterProfile(
        name="custom", min_liquidity_usd=1.0, min_pool_age_hours=0.0,
        mkt_cap_range=(0.0, 1e12), max_tax_pct=100.0,
        reject_active_freeze_authority=False, reject_honeypot_signals=False,
        reject_high_concentration=False, require_social_presence=True,
    )
    assert custom.name == "custom"


# ---------------------------------------------------------------------------
# matches() - the actual predicate
# ---------------------------------------------------------------------------


def test_matches_passes_a_clean_high_quality_token_against_conservative() -> None:
    result = _scored(core=_core(liquidity_usd=200_000.0, pool_age_days=10.0, market_cap=1_000_000.0))
    assert matches(CONSERVATIVE, result) is True


def test_matches_rejects_low_liquidity() -> None:
    result = _scored(core=_core(liquidity_usd=100.0))
    assert matches(CONSERVATIVE, result) is False


def test_matches_rejects_too_young_a_pool() -> None:
    result = _scored(core=_core(pool_age_days=0.1))  # 2.4 hours, below Conservative's 72h
    assert matches(CONSERVATIVE, result) is False


def test_matches_rejects_none_pool_age_as_unknown_not_passing() -> None:
    result = _scored(core=_core(pool_age_days=None))
    assert matches(CONSERVATIVE, result) is False


def test_matches_rejects_market_cap_outside_range() -> None:
    too_small = _scored(core=_core(market_cap=1.0))
    too_large = _scored(core=_core(market_cap=1e12))
    assert matches(CONSERVATIVE, too_small) is False
    assert matches(CONSERVATIVE, too_large) is False


def test_matches_rejects_tax_above_threshold() -> None:
    result = _scored(security=_security(buy_tax_pct=20.0))
    assert matches(BALANCED, result) is False  # BALANCED's max is 10.0


def test_matches_rejects_active_freeze_authority_when_gate_enabled() -> None:
    result = _scored(security=_security(freeze_authority_active=True))
    assert matches(CONSERVATIVE, result) is False


def test_matches_allows_freeze_authority_when_gate_disabled() -> None:
    result = _scored(
        core=_core(liquidity_usd=5_000.0, pool_age_days=2.0, market_cap=10_000.0),
        security=_security(freeze_authority_active=True, buy_tax_pct=1.0, sell_tax_pct=1.0),
    )
    assert matches(AGGRESSIVE, result) is True  # AGGRESSIVE doesn't reject freeze authority


def test_matches_rejects_honeypot_sell_tax_even_on_aggressive() -> None:
    """The one gate that stays on regardless of preset (Part I.2's trust
    principle - see AGGRESSIVE's own inline comment)."""
    result = _scored(security=_security(sell_tax_pct=99.0))
    assert matches(AGGRESSIVE, result) is False


def test_matches_rejects_high_concentration_only_when_gate_enabled() -> None:
    concentrated = _scored(holder=_holder(hci_pct=45.0))
    assert matches(CONSERVATIVE, concentrated) is False  # gate ON
    assert matches(BALANCED, concentrated) is True  # gate OFF, same underlying data


def test_matches_require_social_presence_now_actually_enforces() -> None:
    """Step 14 regression test. Before this pass, `require_social_presence`
    was a documented no-op — this exact scenario (Conservative, social
    degraded) would have passed, silently. `social=None` isn't tested
    here (it would be a caller bug, not a real runtime state — every
    `_scored()` caller now gets a real SocialResult, per `_social()`'s
    own default), only `degraded=True`, which is the real runtime state
    `SocialEngine.analyze` produces for both "lookup failed" and "no
    account found"."""
    no_social = _scored(social=_social(degraded=True, degraded_reason="no resolvable account"))
    assert matches(CONSERVATIVE, no_social) is False  # gate ON, no verifiable presence -> fails
    assert matches(BALANCED, no_social) is True  # gate OFF (Balanced's own default), same underlying data

    has_social = _scored(social=_social(degraded=False))
    assert matches(CONSERVATIVE, has_social) is True  # gate ON, presence verified -> passes


def test_matches_degraded_social_only_fails_when_gate_enabled() -> None:
    """Mirrors test_matches_rejects_high_concentration_only_when_gate_enabled's
    own gate-on/gate-off shape, for the social gate specifically — a
    degraded SocialResult should never fail a profile that never asked
    for social presence in the first place."""
    degraded_social = _social(degraded=True, degraded_reason="rate limited")
    assert matches(AGGRESSIVE, _scored(social=degraded_social)) is True  # gate OFF on Aggressive
    assert matches(CONSERVATIVE, _scored(social=degraded_social)) is False  # gate ON


def test_matches_degraded_core_always_fails() -> None:
    degraded_core = CoreResult(
        address="x", chain=None, primary_pair=None, liquidity_usd=0.0, market_cap=0.0, fdv=0.0,
        dilution_ratio=None, volume_24h=0.0, pool_age_days=None, price_change={}, buy_pressure_pct=0.0,
        degraded=True, degraded_reason="no pairs found",
    )
    result = _scored(core=degraded_core)
    assert matches(AGGRESSIVE, result) is False  # can't verify anything - fails, doesn't skip


def test_matches_degraded_security_always_fails() -> None:
    degraded_security = SecurityResult(
        trust_score=0.0, risk_level="Unknown", mint_authority_active=False, freeze_authority_active=False,
        buy_tax_pct=0.0, sell_tax_pct=0.0, lp_lock_ratio_pct=None, lp_lock_duration_days=None,
        ownership_renounced=False, scam_flags=[], provider_notes=[],
        degraded=True, degraded_reason="provider outage",
    )
    result = _scored(security=degraded_security)
    assert matches(AGGRESSIVE, result) is False


def test_matches_degraded_holder_only_fails_when_concentration_gate_enabled() -> None:
    degraded_holder = HolderResult(
        holder_count=0, holder_growth_24h_pct=0.0, hci_pct=0.0, whale_count=0,
        degraded=True, degraded_reason="chain not supported",
    )
    strict = _scored(holder=degraded_holder)
    loose = _scored(
        core=_core(liquidity_usd=5_000.0, pool_age_days=2.0, market_cap=10_000.0),
        holder=degraded_holder,
    )
    assert matches(CONSERVATIVE, strict) is False  # concentration gate ON - can't verify, fails
    assert matches(AGGRESSIVE, loose) is True  # concentration gate OFF - degraded holder is irrelevant here
