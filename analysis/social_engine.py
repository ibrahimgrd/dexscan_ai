"""
Layer: Analysis engine — Social Intelligence (Playbook Part III.4; Part
VIII Step 13).

Isolated and independently testable, matching `CoreEngine`/
`SecurityEngine`/`HolderEngine`'s shape exactly: a single injected
provider satisfying `SocialDataProvider` (Part V.2, dependency
inversion), never a concrete `TwitterApiIoProvider` import in this file.

Scope note (matching Steps 8/9's own precedent): this module builds and
tests the engine in isolation only. Wiring a constructed `SocialEngine`
into `main.py`'s composition root, `scan_orchestration.run_scan`, and
`result_renderer.py`'s placeholder is deliberately deferred — Part
VIII Step 13's own Integration Requirements say as much explicitly
("Step 14's wiring passes a real SocialResult into both
ScoringPipeline.score and MomentumEngine.compute"), and both of those
call sites already accept `social` as an optional, currently-`None`
parameter for exactly this reason (Steps 6 and 9's forward-compatible
design).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from analysis.api_abstraction import SocialDataProvider, Tweet, XUserProfile
from bot.constants import INFLUENCER_FOLLOWER_THRESHOLD

logger = logging.getLogger(__name__)

# How many of the most recent followers to sample for
# `verified_follower_ratio` - twitterapi.io's own followers endpoint caps
# a single page at 200 (confirmed against its published reference while
# implementing this step); this project has no stored-history layer to
# amortize the cost of paginating further (Part I.3), so one page is the
# whole sample - see `SocialDataProvider.list_followers`'s docstring.
_FOLLOWER_SAMPLE_SIZE = 200

# --- x_score composite formula (Part VI "on ambiguity": Part III.4 names
# "reputation via twitterapi.io's user-lookup endpoint" as the check but
# gives no formula - this is Claude's documented assumption, not a
# playbook-specified figure). Four real, independently-meaningful
# signals from a single `XUserProfile`, weighted to sum to 100:
#   - Verified badge: flat 25 pts.
#   - Account age: up to 20 pts, linear to a 3-year cap - an account
#     that's survived 3+ years of crypto-Twitter churn is about as much
#     "age credit" as this signal should reasonably grant.
#   - Follower count: up to 35 pts, log10-scaled (raw counts are
#     extremely right-skewed - treating a 10M-follower account as
#     "1000x more reputable" than a 10K-follower one would be absurd on
#     a bounded 0-100 scale). 5 pts per order of magnitude: ~17 pts at
#     10K followers, ~30 at 1M, capped at 35 (~10M+).
#   - Follower:following ratio: up to 20 pts, 4 pts per whole ratio
#     point, capped at ratio>=5 - directly reflects the real spam
#     heuristic twitterapi.io's own documentation names ("very low
#     follower + high following... = likely spam").
_X_SCORE_VERIFIED_POINTS = 25
_X_SCORE_AGE_MAX_POINTS = 20
_X_SCORE_AGE_CAP_DAYS = 3 * 365
_X_SCORE_FOLLOWER_MAX_POINTS = 35
_X_SCORE_FOLLOWER_POINTS_PER_DECADE = 5
_X_SCORE_RATIO_MAX_POINTS = 20
_X_SCORE_RATIO_POINTS_PER_UNIT = 4

# Sentiment lexicon (Part III.4: "a lightweight sentiment pass (a simple
# lexicon-based or small-model classifier...)" - lexicon chosen over a
# model: zero external dependency, deterministic, fully unit-testable
# with no network/inference call, consistent with this whole project's
# stateless/low-dependency ethos (Part I.3). Crypto-Twitter-specific
# vocabulary, not a general-purpose sentiment dictionary - "moon" and
# "rug" carry no sentiment at all outside this domain, and a general
# lexicon would miss both.
_BULLISH_TERMS: frozenset[str] = frozenset({
    "moon", "mooning", "bullish", "pump", "pumping", "gem", "based",
    "lfg", "wagmi", "diamond hands", "accumulate", "undervalued",
    "breakout", "ath", "printing", "send it", "up only", "100x", "early",
    "🚀", "💎",
})
_BEARISH_TERMS: frozenset[str] = frozenset({
    "rug", "rugpull", "rug pull", "scam", "honeypot", "dump", "dumping",
    "bearish", "dead", "avoid", "ngmi", "paper hands", "exit liquidity",
    "ponzi", "rekt", "bagholders", "worthless", "crash", "rugged",
    "🚩", "💀",
})


@dataclass
class SocialResult:
    """
    Part VIII Step 13's output shape (Part III.4's five signals plus
    `follower_growth_pct`). `sentiment_ratio` is -1.0 (all negative
    lexicon hits) .. 1.0 (all positive), 0.0 for "no mentions" or "no
    lexicon hits at all" (both are a real neutral outcome, not an error -
    `_compute_sentiment_ratio`'s docstring covers both guard cases).

    `follower_growth_pct` is a documented, permanent 0.0 in this build -
    NOT a "coming later" gap the way the primary account simply not
    existing is, and structurally different from `MomentumResult`'s own
    permanent-zero fields (Step 9): those exist because this project has
    no stored history to diff against (Part I.3); this one is that PLUS
    twitterapi.io's `/twitter/user/followers` response has no per-
    follower "date followed" field at all (confirmed against its
    published schema while implementing this step) - unlike
    `HolderEngine`'s `holder_growth_24h_pct`, which reuses each holder's
    own funding timestamp as a growth proxy, there's no analogous
    per-follower timestamp here to reuse. A future provider exposing
    that field could close this gap without changing this dataclass's
    shape.
    """

    x_score: int
    verified_follower_ratio: float  # 0.0-1.0, not a percentage - see module docstring's naming convention note
    tweet_frequency_per_day: float
    influencer_mention_count: int
    sentiment_ratio: float  # -1.0 .. 1.0
    follower_growth_pct: float
    degraded: bool = False
    degraded_reason: str | None = None


def _degraded_result(reason: str) -> SocialResult:
    """The one place a fully-degraded SocialResult gets constructed -
    same pattern as core_engine.py/security_engine.py/holder_engine.py's
    own `_degraded_result`/`_empty_result` helpers. Neutral defaults: an
    unmeasured signal is "unknown," not "assume the worst" or "assume
    the best" - the same reasoning those modules document for their own
    degrade cases."""
    return SocialResult(
        x_score=0,
        verified_follower_ratio=0.0,
        tweet_frequency_per_day=0.0,
        influencer_mention_count=0,
        sentiment_ratio=0.0,
        follower_growth_pct=0.0,
        degraded=True,
        degraded_reason=reason,
    )


def _compute_x_score(profile: XUserProfile) -> int:
    """See module-level constants block above for the full, documented
    weight derivation. Returns an int 0-100 (each component individually
    capped before summing, so the total can't exceed 100 through
    floating-point accumulation - the same "round defensively" caution
    `holder_engine.py`'s HCI sum documents)."""
    score = 0.0

    if profile.is_verified:
        score += _X_SCORE_VERIFIED_POINTS

    if profile.created_at is not None:
        age_days = (datetime.now(timezone.utc) - profile.created_at).days
        score += min(_X_SCORE_AGE_MAX_POINTS, max(0, age_days) / _X_SCORE_AGE_CAP_DAYS * _X_SCORE_AGE_MAX_POINTS)

    score += min(
        _X_SCORE_FOLLOWER_MAX_POINTS,
        math.log10(profile.follower_count + 1) * _X_SCORE_FOLLOWER_POINTS_PER_DECADE,
    )

    follower_following_ratio = profile.follower_count / max(profile.following_count, 1)
    score += min(_X_SCORE_RATIO_MAX_POINTS, follower_following_ratio * _X_SCORE_RATIO_POINTS_PER_UNIT)

    return round(min(100.0, score))


def _compute_verified_follower_ratio(followers: list[XUserProfile]) -> float:
    """Fraction (0.0-1.0) of the sampled followers (see
    `_FOLLOWER_SAMPLE_SIZE`) that are themselves verified - Part III.4's
    named fake-follower-inflation signal. `0.0` for an empty sample
    (divide-by-zero guard), same "neutral, not negative" default as
    every other zero-division case in this module."""
    if not followers:
        return 0.0
    return sum(1 for f in followers if f.is_verified) / len(followers)


def _tweet_sentiment_hits(text: str) -> tuple[int, int]:
    """(bullish_hits, bearish_hits) for one tweet's text against the
    lexicon above - substring matching on the lowercased text, so
    multi-word terms ("rug pull") and single tokens ("rug") both match
    correctly, and hashtag/cashtag punctuation around a term doesn't
    prevent a match."""
    lowered = text.lower()
    bullish = sum(1 for term in _BULLISH_TERMS if term in lowered)
    bearish = sum(1 for term in _BEARISH_TERMS if term in lowered)
    return bullish, bearish


def _compute_sentiment_ratio(mentions: list[Tweet]) -> float:
    """
    (total_bullish - total_bearish) / (total_bullish + total_bearish)
    across every mention, clamped to [-1.0, 1.0] (defensive - the ratio
    formula can't mathematically exceed that range, but Step 8's own
    "sum of independently-computed floats" caution applies equally here).

    Two distinct zero-division guards, both returning the documented
    neutral `0.0` (Part VIII Step 13's own Acceptance Criteria names
    this exact case): zero mentions found at all, AND mentions found but
    none contain any lexicon term either way. Collapsing "no signal
    found" into "assume positive" or "assume negative" would fabricate a
    verdict this method never actually detected.
    """
    if not mentions:
        return 0.0

    total_bullish = 0
    total_bearish = 0
    for tweet in mentions:
        bullish, bearish = _tweet_sentiment_hits(tweet.text)
        total_bullish += bullish
        total_bearish += bearish

    if total_bullish + total_bearish == 0:
        return 0.0

    ratio = (total_bullish - total_bearish) / (total_bullish + total_bearish)
    return max(-1.0, min(1.0, ratio))


class SocialEngine:
    def __init__(self, provider: SocialDataProvider) -> None:
        self._provider = provider

    async def analyze(self, handle_or_ticker: str) -> SocialResult:
        """
        Never raises past this method; every failure path returns a
        `SocialResult` with `degraded=True` (Steps 4/5/8's own contract,
        applied here too).

        Primary vs. secondary calls, mirroring `HolderEngine.analyze`'s
        exact structure (Step 8): `lookup_user` is the primary call - its
        failure, OR a successful-but-"this account doesn't exist"
        response, fully degrades the result, since nothing downstream is
        meaningful without a resolved account. `list_followers` and
        `search_mentions` are secondary, independent sub-features (Part
        IV.3's partial-failure principle applied at sub-feature
        granularity, same as Step 8's funding-lookup call) - either
        failing degrades only the fields it feeds, logged as a warning,
        while the other's fields (and x_score, from the profile lookup
        alone) stay valid. Sequential, not gathered concurrently -
        Step 8's own two secondary calls (`get_holders`/
        `get_launch_block_funding`) made the same choice for the same
        reason (Part V.2, KISS: two independent calls with no shared
        latency-sensitive UI moment don't need `asyncio.gather`'s added
        complexity to be correct).
        """
        try:
            profile = await self._provider.lookup_user(handle_or_ticker)
        except Exception as exc:
            logger.warning(
                "Social data provider failed on user lookup",
                extra={"handle": handle_or_ticker, "error": str(exc)},
            )
            return _degraded_result("Couldn't reach the social data provider. Try again shortly.")

        if not profile.account_exists:
            return _degraded_result(f"No X/Twitter account found for @{handle_or_ticker}.")

        followers: list[XUserProfile] = []
        try:
            followers = await self._provider.list_followers(handle_or_ticker, limit=_FOLLOWER_SAMPLE_SIZE)
        except Exception as exc:
            logger.warning(
                "Follower sample lookup failed; verified_follower_ratio falls back to its neutral default",
                extra={"handle": handle_or_ticker, "error": str(exc)},
            )

        mentions: list[Tweet] = []
        try:
            mentions = await self._provider.search_mentions(handle_or_ticker)
        except Exception as exc:
            logger.warning(
                "Mention search failed; sentiment/frequency/influencer data fall back to their neutral defaults",
                extra={"handle": handle_or_ticker, "error": str(exc)},
            )

        influencer_count = sum(
            1
            for tweet in mentions
            if tweet.author_is_verified or tweet.author_follower_count >= INFLUENCER_FOLLOWER_THRESHOLD
        )

        return SocialResult(
            x_score=_compute_x_score(profile),
            verified_follower_ratio=_compute_verified_follower_ratio(followers),
            # Already a 24h-bounded count by construction - the provider's
            # own query includes a `since_time:` cutoff (see
            # TwitterApiIoProvider.search_mentions's docstring), so no
            # further division by elapsed days is needed here.
            tweet_frequency_per_day=float(len(mentions)),
            influencer_mention_count=influencer_count,
            sentiment_ratio=_compute_sentiment_ratio(mentions),
            follower_growth_pct=0.0,  # documented permanent gap - see SocialResult's own docstring
        )
