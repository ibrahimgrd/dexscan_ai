"""
Playbook reference: Unified Developer Playbook, Part VIII Step 13 - Unit
Testing Requirements: one test per Definition-of-Done fixture (elite/
high-reputation account, fake-follower-heavy account, zero-mentions case,
provider outage), plus an explicit zero-mentions divide-by-zero guard
test.

Everything here is executable without aiohttp/aiogram installed, same
reasoning as test_holder_engine.py: `twitterapi_io_parser.py` has zero
external dependencies, and `SocialEngine` depends only on the
`SocialDataProvider` Protocol, satisfied here by a plain fake fed from
fixture data - `twitterapi_io.py` itself (the real aiohttp-based adapter)
is never imported by this file.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from analysis.api_abstraction import Tweet, XUserProfile
from analysis.providers.twitterapi_io_parser import (
    parse_followers,
    parse_mentions,
    parse_user_lookup,
)
from analysis.social_engine import SocialEngine, SocialResult

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "social"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text())


def _user(name: str, handle: str = "requested_handle") -> XUserProfile:
    return parse_user_lookup(_load(name), requested_handle=handle)


def _followers(name: str) -> list[XUserProfile]:
    return parse_followers(_load(name))


def _mentions(name: str) -> list[Tweet]:
    return parse_mentions(_load(name))


class _FakeSocialProvider:
    """Satisfies `SocialDataProvider`. Each of the three calls can be
    independently configured to raise (simulating a transport failure)
    via the `*_raises` constructor flags, exercising
    `SocialEngine.analyze`'s per-call partial-failure handling
    (mirroring `HolderEngine`'s own test fakes for the same reason)."""

    def __init__(
        self,
        profile: XUserProfile,
        followers: list[XUserProfile] | None = None,
        mentions: list[Tweet] | None = None,
        lookup_raises: bool = False,
        followers_raises: bool = False,
        mentions_raises: bool = False,
    ) -> None:
        self._profile = profile
        self._followers = followers if followers is not None else []
        self._mentions = mentions if mentions is not None else []
        self._lookup_raises = lookup_raises
        self._followers_raises = followers_raises
        self._mentions_raises = mentions_raises

    async def lookup_user(self, handle: str) -> XUserProfile:
        if self._lookup_raises:
            raise RuntimeError("simulated transport failure")
        return self._profile

    async def list_followers(self, handle: str, limit: int) -> list[XUserProfile]:
        if self._followers_raises:
            raise RuntimeError("simulated transport failure")
        return self._followers[:limit]

    async def search_mentions(self, ticker: str) -> list[Tweet]:
        if self._mentions_raises:
            raise RuntimeError("simulated transport failure")
        return self._mentions


# ---------------------------------------------------------------------------
# Definition of Done fixture cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_elite_high_reputation_account() -> None:
    """Verified, aged, high-follower, healthy-ratio account -> x_score
    should land in the top range; a mostly-verified follower sample ->
    high verified_follower_ratio; bullish mentions -> positive
    sentiment_ratio; the verified 45K-follower author counts as an
    influencer mention."""
    provider = _FakeSocialProvider(
        profile=_user("user_info_elite.json", handle="SolanaFoundation"),
        followers=_followers("followers_mostly_verified.json"),
        mentions=_mentions("mentions_bullish.json"),
    )
    result = await SocialEngine(provider).analyze("SolanaFoundation")

    assert isinstance(result, SocialResult)
    assert result.degraded is False
    assert result.x_score >= 80  # verified + old + huge following + healthy ratio
    assert result.verified_follower_ratio == pytest.approx(0.7)  # 7 of 10 sampled
    assert result.sentiment_ratio > 0.0
    assert result.influencer_mention_count == 1  # only the 45K-follower verified author
    assert result.tweet_frequency_per_day == 3.0


@pytest.mark.asyncio
async def test_fake_follower_heavy_account_flags_low_ratio() -> None:
    """Acceptance Criteria (Step 13): 'verified_follower_ratio correctly
    flags a fixture with an inflated fake-follower pattern' - 9 of 10
    sampled followers are unverified, near-zero-follower, high-following
    accounts (the exact spam shape twitterapi.io's own docs name)."""
    provider = _FakeSocialProvider(
        profile=_user("user_info_moderate.json", handle="SampleMemeCoin"),
        followers=_followers("followers_mostly_fake.json"),
        mentions=[],
    )
    result = await SocialEngine(provider).analyze("SampleMemeCoin")

    assert result.degraded is False
    assert result.verified_follower_ratio == pytest.approx(0.1)  # 1 of 10 sampled
    assert result.verified_follower_ratio < 0.2  # correctly reads as suspicious


@pytest.mark.asyncio
async def test_zero_mentions_sentiment_is_neutral_not_an_error() -> None:
    """Step 13's own named edge case: zero mentions found must not raise
    or return NaN - sentiment_ratio and tweet_frequency_per_day both fall
    back to their documented neutral defaults."""
    provider = _FakeSocialProvider(
        profile=_user("user_info_moderate.json"),
        followers=_followers("followers_mostly_verified.json"),
        mentions=_mentions("mentions_empty.json"),
    )
    result = await SocialEngine(provider).analyze("SampleMemeCoin")

    assert result.degraded is False
    assert result.sentiment_ratio == 0.0
    assert result.tweet_frequency_per_day == 0.0
    assert result.influencer_mention_count == 0
    import math
    assert not math.isnan(result.sentiment_ratio)


@pytest.mark.asyncio
async def test_provider_outage_on_primary_lookup_degrades_fully() -> None:
    """The primary call (lookup_user) failing degrades the whole result
    - mirrors HolderEngine's `get_holders` failure handling exactly."""
    provider = _FakeSocialProvider(
        profile=_user("user_info_elite.json"),  # unused - lookup itself raises first
        lookup_raises=True,
    )
    result = await SocialEngine(provider).analyze("SolanaFoundation")

    assert result.degraded is True
    assert result.degraded_reason is not None
    assert result.x_score == 0
    assert result.sentiment_ratio == 0.0


# ---------------------------------------------------------------------------
# Additional cases beyond the minimum fixture set - real behavior this
# engine needs to get right that the DoD's four named cases don't
# individually isolate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_not_found_degrades_with_specific_reason() -> None:
    """A successful HTTP call that resolves to 'this account doesn't
    exist' (twitterapi.io's own {status: error} shape) is NOT a
    transport failure - still degrades the result (nothing downstream is
    meaningful without a resolved account), but with a distinct,
    specific reason from an actual network failure."""
    provider = _FakeSocialProvider(profile=_user("user_info_not_found.json", handle="ghost_handle"))
    result = await SocialEngine(provider).analyze("ghost_handle")

    assert result.degraded is True
    assert "ghost_handle" in (result.degraded_reason or "")
    assert "provider" not in (result.degraded_reason or "").lower()  # distinct from the transport-failure message


@pytest.mark.asyncio
async def test_bearish_mentions_produce_negative_sentiment() -> None:
    provider = _FakeSocialProvider(
        profile=_user("user_info_moderate.json"),
        followers=[],
        mentions=_mentions("mentions_bearish.json"),
    )
    result = await SocialEngine(provider).analyze("SampleMemeCoin")
    assert result.sentiment_ratio < 0.0


@pytest.mark.asyncio
async def test_mentions_with_no_lexicon_hits_is_neutral_not_positive() -> None:
    """Second, distinct divide-by-zero guard: mentions exist (unlike the
    zero-mentions case above) but none contain any lexicon term either
    way - must not be misread as 'no negativity found, so positive.'"""
    provider = _FakeSocialProvider(
        profile=_user("user_info_moderate.json"),
        followers=[],
        mentions=_mentions("mentions_no_lexicon_hits.json"),
    )
    result = await SocialEngine(provider).analyze("SampleMemeCoin")
    assert result.sentiment_ratio == 0.0
    assert result.tweet_frequency_per_day == 2.0  # still counted, even with neutral sentiment


@pytest.mark.asyncio
async def test_influencer_mention_counted_by_verification_or_follower_threshold() -> None:
    """3 mentions: one verified 250K-follower author (counts twice over
    - verified AND above threshold, but influencer_mention_count counts
    the TWEET once), one 15K-follower unverified author (above the
    10,000 threshold - counts), one 40-follower unverified author
    (neither - doesn't count)."""
    provider = _FakeSocialProvider(
        profile=_user("user_info_moderate.json"),
        followers=[],
        mentions=_mentions("mentions_with_influencer.json"),
    )
    result = await SocialEngine(provider).analyze("SampleMemeCoin")
    assert result.influencer_mention_count == 2


@pytest.mark.asyncio
async def test_empty_follower_sample_gives_neutral_ratio_not_an_error() -> None:
    """A separate divide-by-zero guard from the mentions ones above -
    zero followers sampled (a brand-new account) must not raise."""
    provider = _FakeSocialProvider(profile=_user("user_info_moderate.json"), followers=_followers("followers_empty.json"))
    result = await SocialEngine(provider).analyze("SampleMemeCoin")
    assert result.verified_follower_ratio == 0.0
    assert result.degraded is False  # empty sample isn't a failure, just a real "nothing to sample yet"


@pytest.mark.asyncio
async def test_secondary_call_failure_degrades_only_its_own_fields() -> None:
    """Part IV.3's partial-failure principle at sub-feature granularity
    (Step 8's own precedent, applied here): list_followers failing
    should NOT degrade the whole result or blank out x_score/sentiment,
    which came from lookup_user/search_mentions succeeding independently."""
    provider = _FakeSocialProvider(
        profile=_user("user_info_elite.json"),
        mentions=_mentions("mentions_bullish.json"),
        followers_raises=True,
    )
    result = await SocialEngine(provider).analyze("SolanaFoundation")

    assert result.degraded is False  # NOT a full failure
    assert result.verified_follower_ratio == 0.0  # this one field falls back
    assert result.x_score > 0  # unaffected - came from the successful lookup_user call
    assert result.sentiment_ratio > 0.0  # unaffected - came from the successful search_mentions call


@pytest.mark.asyncio
async def test_mention_search_failure_degrades_only_its_own_fields() -> None:
    """Same principle as above, mirrored for the other secondary call."""
    provider = _FakeSocialProvider(
        profile=_user("user_info_elite.json"),
        followers=_followers("followers_mostly_verified.json"),
        mentions_raises=True,
    )
    result = await SocialEngine(provider).analyze("SolanaFoundation")

    assert result.degraded is False
    assert result.sentiment_ratio == 0.0
    assert result.tweet_frequency_per_day == 0.0
    assert result.influencer_mention_count == 0
    assert result.verified_follower_ratio > 0.0  # unaffected - came from the successful list_followers call
    assert result.x_score > 0  # unaffected


def test_x_score_never_exceeds_100_even_for_an_extreme_profile() -> None:
    """Defensive ceiling check (module docstring's 'round defensively'
    note) - an artificially extreme profile (every component maxed)
    still can't exceed the documented 0-100 range."""
    from analysis.social_engine import _compute_x_score
    from datetime import datetime, timezone, timedelta

    extreme = XUserProfile(
        user_id="1", username="extreme", display_name="Extreme", is_verified=True,
        follower_count=50_000_000, following_count=1,
        tweet_count=100000, created_at=datetime.now(timezone.utc) - timedelta(days=10000),
        description="x", account_exists=True,
    )
    assert 0 <= _compute_x_score(extreme) <= 100


def test_x_score_handles_missing_created_at_without_crashing() -> None:
    """`created_at` is `None` when the provider's own field was
    unparseable (parser's own contract) - the age component must degrade
    to zero contribution, not raise."""
    from analysis.social_engine import _compute_x_score

    profile = XUserProfile(
        user_id="1", username="test", display_name="Test", is_verified=False,
        follower_count=100, following_count=100, tweet_count=10,
        created_at=None, description="", account_exists=True,
    )
    score = _compute_x_score(profile)
    assert isinstance(score, int)
    assert 0 <= score <= 100
