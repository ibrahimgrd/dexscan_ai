"""Momentum Intelligence Engine — analysis/momentum_engine.py

Layer: Modular Analysis Layer (Part II.3). This is the one analytical engine in the
codebase with no external provider and no I/O of its own (Part VIII Step 9's Scope) —
it only combines outputs CoreEngine (Step 4) and HolderEngine (Step 8) already
produced, plus an optional social signal from the not-yet-built Social Engine
(Step 13). It must never call an external API directly, render UI, or persist anything.

Only CoreResult and HolderResult — plain data types, not the engine classes or
providers behind them — are imported here. That mirrors the precedent SecurityEngine
already set in Step 5 by importing CoreResult directly; Part V.9's "no engine imports
another engine directly" rule is about one engine invoking another engine's
*behavior*, not about sharing a plain result dataclass as a function argument's type.

--- What this engine can and cannot honestly measure ---

Part III.5 frames Momentum's purpose as separating short-term acceleration from
static, point-in-time metrics. But this phase's statelessness (Part I.3 — no
database, nothing persisted between requests) means most of CoreResult's and
HolderResult's fields are themselves single, static snapshots, not deltas. Two of
this engine's six output fields could be computed as genuine rate-of-change signals
from a single snapshot; the other three could not, and are documented, neutral (0.0)
gaps rather than disguised as trends they aren't. This mirrors the precedent Step 6
already set for score_confidence's CodeVerifiedBoolean term: hardcode a neutral
default, and say exactly why in the docstring, rather than fabricate a number nothing
in this phase's data actually supports.

Computed for real, from data already in a single snapshot:
  * price_momentum — CoreResult.price_change already carries four timeframes
    (5m/1h/6h/24h) in one response (Part III.1's Outputs list calls this out as
    "multi-timeframe price change" specifically); comparing the 1h and 6h windows is
    genuine rate-of-change information, no second live call needed. See
    `_price_momentum`.
  * buy_momentum — CoreResult.buy_pressure_pct is a single-snapshot LEVEL (0-100,
    50 = neutral), not a delta. But a sustained buy/sell imbalance is still a
    legitimate directional signal on its own — the same logic Part III.1 already
    relies on for buy_pressure_pct itself (a documented proxy, not a workaround, per
    that section's own note). See `_buy_momentum`.

Documented 0.0 gaps, and why each one is a gap rather than an oversight:
  * volume_growth_pct — CoreResult exposes a single volume_24h figure, not a
    multi-timeframe breakdown the way price_change is (Part III.1's Outputs list
    names "volume" and "multi-timeframe price change" separately — that asymmetry is
    deliberate, not an omission). There is no prior snapshot to diff against and
    nothing else in CoreResult that legitimately proxies a growth *rate* here.
  * liquidity_growth_pct — same structural gap as volume_growth_pct; CoreResult's
    liquidity_usd is also a single point-in-time figure.
  * whale_momentum — HolderResult exposes only a point-in-time whale_count, no
    historical baseline to diff against. Note this is a deliberate omission, not just
    a missing data source: whale *concentration* as a static level (whale_count
    relative to holder_count) IS computable right now without any history, but is
    intentionally not used as a stand-in here, because that signal already lives on
    the Risk axis (HCI / HolderConcentrationPenalty — Part III.3, Steps 8 and 10).
    Folding it into Momentum too would blur Part III.6's explicit rule that Risk and
    Opportunity answer different questions and must never be blended into one signal.

Whichever of the three gaps above is filled in later (a richer provider, a
persistence layer, etc.), only this module's private helpers need to change —
MomentumEngine.compute's signature and MomentumResult's shape stay exactly as Part
VIII Step 9 specifies them.

Score_Trend's formula terms map onto this engine's own output fields one-to-one, not
as sub-weighted blends of several fields: VolumeSurge is volume_growth_pct,
PriceAcceleration is price_momentum, HolderGrowth is holder.holder_growth_24h_pct,
SocialMomentum is social_momentum. liquidity_growth_pct, buy_momentum, and
whale_momentum are all still computed and returned (Part III.5 lists them as engine
outputs), but aren't separately named in the Score_Trend formula itself, so they are
not blended into any of the four terms above — doing so would mean inventing a
sub-weighting the playbook doesn't specify.

--- HolderResult.holder_growth_24h_pct caveats (carried forward from Step 8) ---

holder_engine.py's own module docstring flags two things worth repeating here, since
this engine consumes that field directly as the HolderGrowth term: it is a proxy over
the *observed* top-holder set only (free-tier RPC can't enumerate the full holder
base), and it is derived by re-using funding-lookup data gathered for insider/bundle
detection rather than a second live snapshot (there is no persistence layer to diff
against anyway — Part I.3). This engine treats the figure as directionally useful, not
pristine, and does not attempt to "clean it up" further.

--- Social input (Step 13 not built yet) ---

`social` is typed `Any`, not `SocialResult | None` as Step 9's spec originally
sketched it, because SocialResult does not exist anywhere in the codebase yet — a
forward reference to a genuinely nonexistent name fails mypy --strict. This is the
identical fix Step 6 already applied to ScoringPipeline.score's own `social`
parameter; it is not a new decision made here. See `_social_momentum` for how a real
SocialResult will flow through this function unchanged once Step 13 ships, per this
step's own Integration Requirements ("Step 9's own code does not change when that
happens, only the caller does").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from analysis.core_engine import CoreResult
from analysis.holder_engine import HolderResult

# Score_Trend weights (Part III.5) — named module-level constants, not inline
# literals, so a later phase can promote these to a Settings-driven tunable profile
# (Part III.5's Future Extensibility note) without touching the formula's structure.
# Must sum to 1.0 — see TestTrendingScoreArithmetic.test_score_trend_weights_sum_to_one.
VOLUME_SURGE_WEIGHT: Final[float] = 0.35
PRICE_ACCELERATION_WEIGHT: Final[float] = 0.30
HOLDER_GROWTH_WEIGHT: Final[float] = 0.20
SOCIAL_MOMENTUM_WEIGHT: Final[float] = 0.15


@dataclass
class MomentumResult:
    """Output of MomentumEngine.compute — Part VIII Step 9's exact public interface.

    Unlike CoreResult/SecurityResult/HolderResult, this dataclass carries
    `degraded: bool` with no paired `degraded_reason` field; that matches Step 9's
    literal given interface. This engine has no provider of its own to fail —
    `degraded` here simply mirrors whether either input (core, holder) was itself
    degraded (see MomentumEngine.compute) — so the free-text reason for *why* remains
    available on `core.degraded_reason` / `holder.degraded_reason` rather than being
    duplicated here.
    """

    volume_growth_pct: float
    liquidity_growth_pct: float
    price_momentum: float
    buy_momentum: float
    whale_momentum: float
    social_momentum: float  # 0.0 until Step 13 supplies a real value
    trending_score: float
    degraded: bool = False


class MomentumEngine:
    """Modular Analysis Layer (Part II.3). Computes Part III.5's Momentum
    Intelligence Engine outputs, including the composite Score_Trend
    ("trending_score"). Depends on CoreEngine (Step 4) and HolderEngine (Step 8) only
    through their already-computed result objects — never imports either engine's
    class or provider, and never calls an external API itself (Part V.2 dependency
    inversion; this step's own Scope: "the one engine in this playbook that's purely
    computational").
    """

    def compute(
        self,
        core: CoreResult,
        holder: HolderResult,
        social: Any = None,
    ) -> MomentumResult:
        """Compute one pair's momentum profile.

        Args:
            core: CoreResult from Step 4. Supplies price_change (for price_momentum)
                and buy_pressure_pct (for buy_momentum).
            holder: HolderResult from Step 8. Supplies holder_growth_24h_pct for the
                HolderGrowth term (see this module's docstring for its two
                carried-forward caveats).
            social: Step 13's SocialResult once it exists; typed Any for now (see
                module docstring). None is the expected value until Step 13 ships,
                and must yield social_momentum == 0.0 exactly (this step's
                Acceptance Criteria).

        Returns:
            A fully populated MomentumResult. `degraded` is True whenever either
            input was itself degraded, since every figure here is derived from those
            two inputs and nothing else.
        """
        volume_growth_pct = 0.0  # documented gap — see module docstring
        liquidity_growth_pct = 0.0  # documented gap — see module docstring
        price_momentum = _price_momentum(core.price_change)
        buy_momentum = _buy_momentum(core.buy_pressure_pct)
        whale_momentum = 0.0  # documented gap — see module docstring
        social_momentum = _social_momentum(social)

        # Local aliases matching Part III.5's own term names exactly, so the formula
        # below reads the same as the docstring it implements (Part I.2
        # Explainability: a score should be traceable to the specific factors that
        # produced it).
        volume_surge = volume_growth_pct
        price_acceleration = price_momentum
        holder_growth = holder.holder_growth_24h_pct

        # Score_Trend = 0.35*VolumeSurge + 0.30*PriceAcceleration +
        #               0.20*HolderGrowth + 0.15*SocialMomentum
        # (Part III.5, unabridged — no clamp() here; Part III.5's own formula
        # doesn't clamp this the way Part III.6's higher-level scores clamp
        # themselves to 0-100, so trending_score is left unbounded, exactly as given.)
        trending_score = (
            VOLUME_SURGE_WEIGHT * volume_surge
            + PRICE_ACCELERATION_WEIGHT * price_acceleration
            + HOLDER_GROWTH_WEIGHT * holder_growth
            + SOCIAL_MOMENTUM_WEIGHT * social_momentum
        )

        return MomentumResult(
            volume_growth_pct=volume_growth_pct,
            liquidity_growth_pct=liquidity_growth_pct,
            price_momentum=price_momentum,
            buy_momentum=buy_momentum,
            whale_momentum=whale_momentum,
            social_momentum=social_momentum,
            trending_score=trending_score,
            degraded=core.degraded or holder.degraded,
        )


def _price_momentum(price_change: dict[str, float]) -> float:
    """This engine's operationalization of Part III.5's PriceAcceleration term —
    price_momentum and PriceAcceleration are the same figure here; CoreResult gives
    no separate basis for a raw "momentum" distinct from "acceleration" (see module
    docstring).

    Converts the 1h and 6h windows to a common per-hour rate, then takes the
    difference: positive means the short-term rate is outpacing the longer-term trend
    (price accelerating); negative means it's fading. 5m is skipped — too noisy once
    scaled up to a per-hour rate — and 24h is skipped too, since Part III.5 frames
    this term as separating short-term acceleration *from* static, longer-run levels.
    """
    short_rate_per_hour = price_change.get("1h", 0.0)
    long_rate_per_hour = price_change.get("6h", 0.0) / 6.0
    return short_rate_per_hour - long_rate_per_hour


def _buy_momentum(buy_pressure_pct: float) -> float:
    """Rescales CoreResult.buy_pressure_pct — a 0-100 figure, 50 = neutral
    (Part III.1) — onto a signed -100..100 scale centered on 0, so it behaves like
    this engine's other momentum-flavored terms.

    This is a level, not a true rate-of-change (no prior buy_pressure_pct snapshot
    exists to diff against), but a sustained buy/sell imbalance is a legitimate
    directional signal on its own, consistent with how Part III.1 already treats
    buy_pressure_pct as meaningful without a historical baseline of its own.
    """
    return (buy_pressure_pct - 50.0) * 2.0


def _social_momentum(social: Any) -> float:
    """Returns 0.0 when social is None — Step 9's required graceful-degradation
    default (this step's Acceptance Criteria).

    When social is present, reads a `sentiment_ratio` attribute via getattr rather
    than direct attribute access, since `social` is typed Any (see module
    docstring). Step 13's own spec (Part VIII Step 13) defines
    SocialResult.sentiment_ratio on a -1.0..1.0 scale; scaling by 100 puts it on
    roughly the same -100..100 scale as buy_momentum above. getattr's default means
    this quietly degrades to 0.0 rather than raising if a real SocialResult ends up
    using a different field name — this function, and MomentumEngine.compute above
    it, do not need to change either way (this step's own Integration Requirements).
    """
    if social is None:
        return 0.0
    sentiment_ratio = getattr(social, "sentiment_ratio", 0.0)
    return float(sentiment_ratio) * 100.0
