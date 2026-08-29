"""Tests for analysis/momentum_engine.py — Part VIII Step 9.

CoreResult and HolderResult are built with small local factory functions rather than
JSON fixtures under tests/fixtures/, because this engine has no provider (Step 9's
Scope) — per Part V.8, fixtures live under tests/fixtures/ specifically for
*external provider* data, and there is no external provider response to fix in a
JSON file here. The "fixtures" in this file are just plain dataclass instances one
layer up the pipeline.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from analysis.core_engine import CoreResult
from analysis.holder_engine import HolderResult
from analysis.momentum_engine import (
    HOLDER_GROWTH_WEIGHT,
    PRICE_ACCELERATION_WEIGHT,
    SOCIAL_MOMENTUM_WEIGHT,
    VOLUME_SURGE_WEIGHT,
    MomentumEngine,
)
from bot.constants import Chain


def _core_result(
    *,
    price_change: dict[str, float],
    buy_pressure_pct: float,
    volume_24h: float = 50_000.0,
    degraded: bool = False,
) -> CoreResult:
    """Minimal-but-valid CoreResult for Momentum tests. Fields this engine never
    reads (address, chain, primary_pair, liquidity_usd, market_cap, fdv,
    dilution_ratio, pool_age_days) get simple, valid placeholders; primary_pair is
    left None since PairData's shape is never needed here.
    """
    return CoreResult(
        address="So11111111111111111111111111111111111111112",
        chain=Chain.SOL,
        primary_pair=None,
        liquidity_usd=100_000.0,
        market_cap=1_000_000.0,
        fdv=1_200_000.0,
        dilution_ratio=1_000_000.0 / 1_200_000.0,
        volume_24h=volume_24h,
        pool_age_days=3.0,
        price_change=price_change,
        buy_pressure_pct=buy_pressure_pct,
        degraded=degraded,
    )


def _holder_result(
    *,
    holder_growth_24h_pct: float,
    degraded: bool = False,
) -> HolderResult:
    """Minimal-but-valid HolderResult for Momentum tests."""
    return HolderResult(
        holder_count=500,
        holder_growth_24h_pct=holder_growth_24h_pct,
        hci_pct=18.0,
        whale_count=3,
        degraded=degraded,
    )


class TestMomentumScenarios:
    """Definition of Done: strong-momentum, flat/neutral, and negative-momentum
    fixture coverage."""

    def test_strong_momentum_case(self) -> None:
        """Accelerating price, buy-dominated pressure, strong holder growth.
        price_momentum = 21.0 - 30.0/6.0 = 16.0;
        buy_momentum = (80.0-50.0)*2.0 = 60.0;
        trending_score = 0.30*16.0 + 0.20*40.0 = 4.8 + 8.0 = 12.8
        (volume_surge and social_momentum are both 0.0 here)."""
        core = _core_result(
            price_change={"5m": 6.0, "1h": 21.0, "6h": 30.0, "24h": 50.0},
            buy_pressure_pct=80.0,
        )
        holder = _holder_result(holder_growth_24h_pct=40.0)

        result = MomentumEngine().compute(core, holder)

        assert result.price_momentum == pytest.approx(16.0)
        assert result.buy_momentum == pytest.approx(60.0)
        assert result.trending_score == pytest.approx(12.8)

    def test_flat_neutral_case(self) -> None:
        """No price movement, perfectly balanced buy/sell pressure, no holder
        growth, no social input — every term is neutral, so trending_score must be
        exactly 0.0."""
        core = _core_result(
            price_change={"5m": 0.0, "1h": 0.0, "6h": 0.0, "24h": 0.0},
            buy_pressure_pct=50.0,
        )
        holder = _holder_result(holder_growth_24h_pct=0.0)

        result = MomentumEngine().compute(core, holder)

        assert result.price_momentum == pytest.approx(0.0)
        assert result.buy_momentum == pytest.approx(0.0)
        assert result.social_momentum == pytest.approx(0.0)
        assert result.trending_score == pytest.approx(0.0)

    def test_negative_momentum_case(self) -> None:
        """Declining price and sell-dominated pressure, holders leaving. volume_24h
        is also set low here to narratively match "declining volume/price" from this
        step's Definition of Done — but volume_growth_pct itself is a documented 0.0
        gap (see module docstring): it isn't derived from volume_24h at all yet, so
        this test doesn't assert against it.
        price_momentum = -18.0 - (-24.0/6.0) = -18.0 - (-4.0) = -14.0;
        buy_momentum = (25.0-50.0)*2.0 = -50.0;
        trending_score = 0.30*(-14.0) + 0.20*(-20.0) = -4.2 + -4.0 = -8.2
        """
        core = _core_result(
            price_change={"5m": -4.0, "1h": -18.0, "6h": -24.0, "24h": -30.0},
            buy_pressure_pct=25.0,
            volume_24h=8_000.0,
        )
        holder = _holder_result(holder_growth_24h_pct=-20.0)

        result = MomentumEngine().compute(core, holder)

        assert result.price_momentum == pytest.approx(-14.0)
        assert result.buy_momentum == pytest.approx(-50.0)
        assert result.trending_score == pytest.approx(-8.2)


class TestSocialMomentumDegradation:
    """Definition of Done: social=None confirming the graceful-degradation path,
    plus coverage of the duck-typed extraction that runs once a real social argument
    is supplied."""

    def test_social_none_defaults_to_zero(self) -> None:
        """Required graceful-degradation path (Step 9 Acceptance Criteria):
        social_momentum must default to exactly 0.0, not None, when social is
        omitted."""
        core = _core_result(
            price_change={"5m": 1.0, "1h": 5.0, "6h": 6.0, "24h": 10.0},
            buy_pressure_pct=55.0,
        )
        holder = _holder_result(holder_growth_24h_pct=5.0)

        result = MomentumEngine().compute(core, holder, social=None)

        assert result.social_momentum == 0.0

    def test_social_present_contributes_to_trending_score(self) -> None:
        """Once a social-like object is supplied, its sentiment_ratio should flow
        into social_momentum (scaled x100) and into trending_score via
        SOCIAL_MOMENTUM_WEIGHT — the behavior Step 13/14 will rely on without
        needing to change this engine's code (this step's Integration
        Requirements)."""
        core = _core_result(
            price_change={"5m": 1.0, "1h": 5.0, "6h": 6.0, "24h": 10.0},
            buy_pressure_pct=55.0,
        )
        holder = _holder_result(holder_growth_24h_pct=5.0)
        social_stub = SimpleNamespace(sentiment_ratio=0.5)

        without_social = MomentumEngine().compute(core, holder, social=None)
        with_social = MomentumEngine().compute(core, holder, social=social_stub)

        assert with_social.social_momentum == pytest.approx(50.0)
        assert with_social.trending_score > without_social.trending_score
        assert with_social.trending_score - without_social.trending_score == pytest.approx(
            SOCIAL_MOMENTUM_WEIGHT * 50.0
        )

    def test_social_missing_sentiment_ratio_falls_back_to_zero(self) -> None:
        """A social-like object that doesn't (yet) expose sentiment_ratio must not
        raise — getattr's fallback keeps this engine working even if Step 13 ends up
        naming the field differently."""
        core = _core_result(
            price_change={"5m": 0.0, "1h": 0.0, "6h": 0.0, "24h": 0.0},
            buy_pressure_pct=50.0,
        )
        holder = _holder_result(holder_growth_24h_pct=0.0)
        social_stub_without_field = SimpleNamespace(x_score=42)

        result = MomentumEngine().compute(
            core, holder, social=social_stub_without_field
        )

        assert result.social_momentum == 0.0


class TestTrendingScoreArithmetic:
    """Unit Testing Requirement: confirms the weighted-sum arithmetic exactly, not
    just that it "produces a plausible number"."""

    def test_weighted_sum_matches_hand_computed_value(self) -> None:
        """
        price_momentum  = price_change["1h"] - price_change["6h"] / 6
                         = 10.0 - 12.0 / 6 = 10.0 - 2.0 = 8.0
        volume_surge    = volume_growth_pct = 0.0   (documented gap)
        holder_growth   = 15.0
        social_momentum = 0.0                        (social=None)

        trending_score = 0.35*0.0 + 0.30*8.0 + 0.20*15.0 + 0.15*0.0
                        = 0.0 + 2.4 + 3.0 + 0.0
                        = 5.4
        """
        core = _core_result(
            price_change={"5m": 2.0, "1h": 10.0, "6h": 12.0, "24h": 20.0},
            buy_pressure_pct=60.0,
        )
        holder = _holder_result(holder_growth_24h_pct=15.0)

        result = MomentumEngine().compute(core, holder, social=None)

        assert result.trending_score == pytest.approx(5.4)

    def test_weighted_sum_with_social_present(self) -> None:
        """Same base case as above, plus a social stub contributing
        sentiment_ratio=0.5 -> social_momentum=50.0.

        trending_score = 0.35*0.0 + 0.30*8.0 + 0.20*15.0 + 0.15*50.0
                        = 0.0 + 2.4 + 3.0 + 7.5
                        = 12.9
        """
        core = _core_result(
            price_change={"5m": 2.0, "1h": 10.0, "6h": 12.0, "24h": 20.0},
            buy_pressure_pct=60.0,
        )
        holder = _holder_result(holder_growth_24h_pct=15.0)
        social_stub = SimpleNamespace(sentiment_ratio=0.5)

        result = MomentumEngine().compute(core, holder, social=social_stub)

        assert result.trending_score == pytest.approx(12.9)

    def test_score_trend_weights_sum_to_one(self) -> None:
        """Guards the module's own documented invariant — a later phase promoting
        these to a Settings-driven profile (Future Compatibility) must preserve
        this."""
        total = (
            VOLUME_SURGE_WEIGHT
            + PRICE_ACCELERATION_WEIGHT
            + HOLDER_GROWTH_WEIGHT
            + SOCIAL_MOMENTUM_WEIGHT
        )
        assert total == pytest.approx(1.0)


class TestDocumentedGapFields:
    """Regression guard for the three fields this step deliberately defaults to 0.0
    (see module docstring) — forces a conscious test update if a future step starts
    computing real values for these."""

    def test_gap_fields_are_always_zero(self) -> None:
        core = _core_result(
            price_change={"5m": 9.0, "1h": 40.0, "6h": 12.0, "24h": 3.0},
            buy_pressure_pct=95.0,
            volume_24h=999_999.0,
        )
        holder = _holder_result(holder_growth_24h_pct=77.0)

        result = MomentumEngine().compute(core, holder)

        assert result.volume_growth_pct == 0.0
        assert result.liquidity_growth_pct == 0.0
        assert result.whale_momentum == 0.0


class TestDegradedPropagation:
    def test_degraded_true_when_core_degraded(self) -> None:
        core = _core_result(
            price_change={"5m": 0.0, "1h": 0.0, "6h": 0.0, "24h": 0.0},
            buy_pressure_pct=50.0,
            degraded=True,
        )
        holder = _holder_result(holder_growth_24h_pct=0.0)

        result = MomentumEngine().compute(core, holder)

        assert result.degraded is True

    def test_degraded_true_when_holder_degraded(self) -> None:
        core = _core_result(
            price_change={"5m": 0.0, "1h": 0.0, "6h": 0.0, "24h": 0.0},
            buy_pressure_pct=50.0,
        )
        holder = _holder_result(holder_growth_24h_pct=0.0, degraded=True)

        result = MomentumEngine().compute(core, holder)

        assert result.degraded is True

    def test_not_degraded_when_both_inputs_healthy(self) -> None:
        core = _core_result(
            price_change={"5m": 0.0, "1h": 0.0, "6h": 0.0, "24h": 0.0},
            buy_pressure_pct=50.0,
        )
        holder = _holder_result(holder_growth_24h_pct=0.0)

        result = MomentumEngine().compute(core, holder)

        assert result.degraded is False
