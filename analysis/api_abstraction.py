"""
Layer: API Abstraction (Playbook Part II.3; Part VIII Steps 4-5).

Normalizes every external data call behind a Protocol so the engines in
`analysis/` never import a concrete HTTP client or know a provider's
specific field names/scales (Part V.2: dependency inversion). A second
provider for either data type, or a breaking change in an existing one's
schema, is a new/updated adapter satisfying the relevant Protocol — the
engine that consumes it doesn't change.

Deliberately pure stdlib: no aiohttp/requests import anywhere in this
file. Both Protocols only declare a signature; a test can satisfy either
with a plain fake object and exercise the corresponding engine's real
logic without ever touching a network call. That's not an incidental
property — it's the actual point of this layer existing as a separate
module instead of each engine calling its provider directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from bot.constants import Chain


@dataclass
class PairData:
    """
    One normalized trading pair. Field names are chain/provider-agnostic
    on purpose — DexScreener's own field names (`priceUsd`, `fdv`,
    `txns.h24.buys`, ...) are mapped onto this shape entirely inside
    `providers/dexscreener.py`; nothing outside that one file should ever
    need to know DexScreener's actual JSON shape.
    """

    chain: Chain
    dex_id: str
    pair_address: str
    base_token_address: str
    base_token_symbol: str
    base_token_name: str
    quote_token_symbol: str
    price_usd: float
    liquidity_usd: float
    fdv: float
    market_cap: float
    volume_5m: float
    volume_1h: float
    volume_6h: float
    volume_24h: float
    price_change_5m: float
    price_change_1h: float
    price_change_6h: float
    price_change_24h: float
    buys_24h: int
    sells_24h: int
    pair_created_at_ms: int | None  # unix ms; None if the provider omitted it


class MarketDataProvider(Protocol):
    """Satisfied today by `providers.dexscreener.DexScreenerProvider`; any
    future provider, or a fake/mock in tests, implements this same shape."""

    async def get_pairs(self, address: str) -> list[PairData]:
        """
        Returns every pair the provider knows about for `address`, across
        every chain it has indexed that exact address string on. No
        `chain` parameter: DexScreener's real token-lookup endpoint
        doesn't take one — it returns whatever chains it has data for in
        a single call, and `CoreEngine.analyze` does the chain filtering/
        disambiguation from that full set (Part II.6). This is a
        corrected departure from this playbook's original
        `get_pairs(address, chain)` signature — verified against
        DexScreener's actual documented endpoint shape while implementing
        this step, not guessed at. One query per candidate EVM chain
        (the original design) would have been both slower and simply not
        how the real API works — it doesn't expose a per-chain lookup at
        all for this endpoint.

        An address with zero indexed pairs returns an empty list — that's
        a normal, expected outcome, not a reason to raise. Only transport-
        level failures (timeout, connection error, 5xx) raise, for
        `CoreEngine` to catch and degrade.
        """
        ...


@dataclass
class DiscoveryCandidate:
    """One candidate address surfaced by a discovery feed (Part VIII
    Step 12/custom-roadmap Step 13's Auto-Watch) — deliberately thin
    (just enough to feed `CoreEngine.analyze` on the next pass, which
    re-fetches full live data itself rather than trusting a discovery
    snapshot). `source` records exactly which feed produced this
    candidate — see `TokenDiscoveryProvider`'s own docstring for why the
    two feeds are NOT interchangeable and should never be presented to a
    person without that distinction."""

    chain: Chain
    token_address: str
    source: str  # "new_listings" | "boosted" — see TokenDiscoveryProvider


class TokenDiscoveryProvider(Protocol):
    """Satisfied today by `providers.dexscreener.DexScreenerProvider`
    (same class, same session, as `MarketDataProvider` — a single
    provider instance can satisfy both Protocols; nothing requires a
    second HTTP client). Separated into its own Protocol rather than
    folded into `MarketDataProvider` (Part V.2, Dependency Inversion):
    `CoreEngine` only ever needs `get_pairs`; nothing about scanning one
    already-known address requires discovery at all. Only
    `AutoWatchManager` depends on this Protocol.

    Two feeds, NOT interchangeable, confirmed against DexScreener's own
    published API reference (docs.dexscreener.com/api/reference) while
    implementing this step:

    - `get_new_listings` reads `/token-profiles/latest/v1` — tokens
      whose PROFILE (description/links/icon) was most recently
      submitted or updated. This is a real, honest proxy for "new and
      notable," but it is profile-recency, not pool-creation-recency —
      a token that's existed for months and just added a Twitter link
      would appear here too. Documented explicitly rather than silently
      presented as "brand new pools," which it isn't guaranteed to be.
    - `get_trending` reads `/token-boosts/latest/v1` — tokens whose
      project PAID for a visibility boost (DexScreener's own Boosting
      docs: "purchasing a Boost pack... temporarily increase a token's
      Trending Score"). This is paid promotion, not an organic-interest
      signal — rendered and documented as exactly that (see
      `rendering/menus.py`'s own copy for this screen), never presented
      as if it meant "the market is organically excited about this."
      This project's own trust principle (Part I.2: "Trust is the
      product") is exactly why this distinction isn't glossed over.

    Both raise on transport-level failure; both return an empty list
    (never an error) when the feed itself has nothing to report.
    """

    async def get_new_listings(self, limit: int) -> list[DiscoveryCandidate]: ...

    async def get_trending(self, limit: int) -> list[DiscoveryCandidate]: ...


@dataclass
class SecurityReport:
    """
    One normalized security scan (Part VIII Step 5). Field names and
    scale are provider-agnostic and already normalized to this project's
    conventions — RugCheck's own field names, and its own (inverted, much
    larger than 0-100) raw score scale, are translated entirely inside
    `providers/rugcheck_parser.py`; nothing outside that file should ever
    need to know RugCheck's actual response shape.

    `trust_score` is 0-100, higher = safer — the OPPOSITE polarity from
    RugCheck's own raw `score` field (RugCheck: higher = riskier). This
    matters beyond just this file: Part III.6's `Score_Risk` formula
    computes `0.3*(100-TrustScore)`, which is only dimensionally correct
    if TrustScore already means "higher = safer" — so the inversion has
    to happen here, once, not be re-derived by every future caller.

    `risk_level` is deliberately NOT part of this dataclass: it's a
    categorization SecurityEngine computes from `trust_score` using this
    project's own thresholds (Part V.2 — interpretation is engine logic,
    not provider logic), the same way CoreEngine computes DilutionRatio
    from PairData's raw facts rather than a provider pre-computing it.
    """

    trust_score: float  # 0-100, higher = safer
    mint_authority_active: bool
    freeze_authority_active: bool
    buy_tax_pct: float
    sell_tax_pct: float
    lp_lock_ratio_pct: float | None  # None when the provider's lock data couldn't be confidently located
    lp_lock_duration_days: float | None
    ownership_renounced: bool
    raw_risk_flags: list[str]  # provider's own named findings (e.g. RugCheck's risks[]), for display/explanation
    chain_supported: bool  # False when this provider has no meaningful coverage for the requested chain


class SecurityDataProvider(Protocol):
    """Satisfied today by `providers.rugcheck.RugCheckProvider`; a second
    security provider (Part III.2's Future Extensibility note — most
    plausibly an EVM-focused scanner, since RugCheck's real, reliable
    coverage is Solana-specific despite its endpoint accepting a `chain`
    parameter) implements this same shape."""

    async def scan(self, address: str, chain: Chain) -> SecurityReport:
        """
        Raises on transport-level failure, same contract as
        `MarketDataProvider.get_pairs` — `SecurityEngine` is the layer
        that catches and degrades. A chain this provider doesn't
        meaningfully cover is NOT a transport failure — it returns a
        `SecurityReport` with `chain_supported=False` and neutral/empty
        values for everything else, so `SecurityEngine` can distinguish
        "asked and got told no data exists for this chain" from "asked
        and the network failed."
        """
        ...


@dataclass
class HolderRecord:
    """
    One resolved token holder (Part VIII Step 8). `wallet_address` is the
    OWNING wallet, already resolved from whatever account-level identifier
    the underlying provider actually indexes by — on Solana specifically,
    `getTokenLargestAccounts` returns SPL *token account* addresses, not
    wallets, and `providers/solana_rpc.py` resolves the real owner before
    this object is ever built, precisely so `HolderEngine` (and anything
    that consumes `HolderResult` later) never has to know that distinction
    exists. `token_account_address` is kept alongside for traceability
    (e.g. a future renderer linking out to an explorer), not because
    anything in this codebase keys off it.
    """

    wallet_address: str
    token_account_address: str
    balance: float  # decimal-adjusted (UI) token amount
    pct_of_supply: float  # 0-100


@dataclass
class FundingRecord:
    """
    One holder wallet's earliest identifiable on-chain funding (Part VIII
    Step 8) — the raw material `HolderEngine` groups for insider/bundle
    detection (Part III.3) and derives `HolderResult.holder_growth_24h_pct`
    from (see `holder_engine.py`'s docstring for why growth reuses this
    data rather than a separate call).

    `funding_source_address`, `funded_at_slot`, and `funded_at_block_time`
    are `None` together when no clear funding transaction could be
    identified for this wallet (`providers/solana_rpc_parser.py`'s
    docstring explains the heuristic and its limits) — "couldn't confirm"
    is preserved as its own state, not collapsed into a default that
    would misread as "confirmed this wallet has no funder."
    """

    wallet_address: str
    funding_source_address: str | None
    funded_at_slot: int | None
    funded_at_block_time: int | None  # unix seconds


class HolderDataProvider(Protocol):
    """Satisfied today by `providers.solana_rpc.SolanaRpcHolderProvider`.
    Deliberately Solana-only in this build — see that module's docstring
    for the free-tier data-source reasoning, and `HolderEngine`'s own
    docstring for why the chain gate lives in the engine rather than a
    `chain_supported`-style flag here (unlike `SecurityDataProvider`, a
    Solana RPC endpoint can't meaningfully answer for a different chain
    at all, so there's no "best effort, unconfirmed" middle ground to
    model). A second, EVM-focused provider would satisfy this same
    Protocol and be reconciled inside `HolderEngine.analyze()` — the same
    extension pattern `SecurityDataProvider` documents for its own future
    second provider."""

    async def get_holders(self, address: str, chain: Chain) -> list[HolderRecord]:
        """
        Raises on transport-level failure — `HolderEngine` catches and
        degrades, same contract as `MarketDataProvider.get_pairs` /
        `SecurityDataProvider.scan`. An address with no meaningful holder
        data returns an empty list, not an error.
        """
        ...

    async def get_launch_block_funding(self, address: str, chain: Chain) -> list[FundingRecord]:
        """
        Earliest-known funding for a provider-chosen subset of
        significant holders (Part III.3 frames insider/bundle detection
        around large, coordinated holders specifically, not the long
        tail — see the concrete provider's own docstring for its exact
        scope and RPC-economy reasoning). Raises only on a failure of the
        lookup as a whole; a single wallet's lookup failing is omitted
        from the result rather than raised, since the other wallets'
        results are still meaningful on their own.
        """
        ...


@dataclass
class XUserProfile:
    """
    One X/Twitter account's profile (Part VIII Step 13) — used both for
    the project's own account (`SocialDataProvider.lookup_user`) and for
    each entry in a follower page (`list_followers`) / a mentioning
    tweet's author (`Tweet.author_*` fields below use only a subset of
    this same data, inlined rather than nesting this whole dataclass —
    see `Tweet`'s own docstring for why).

    Field names are twitterapi.io-agnostic on purpose, same convention as
    `PairData`/`SecurityReport`: twitterapi.io's own JSON field names
    (`userName`, `isBlueVerified`, `followers`, `createdAt` as a fixed-
    format string, ...) are mapped onto this shape entirely inside
    `providers/twitterapi_io_parser.py`; nothing outside that file should
    ever need to know twitterapi.io's actual response shape.

    `created_at` is an already-parsed `datetime`, not the raw fixed-format
    string twitterapi.io returns (e.g. "Thu Dec 13 08:41:26 +0000 2007")
    — same principle as `HolderRecord.wallet_address` already being
    pre-resolved rather than left as a provider-specific raw value.
    `None` only if that one field was present but unparseable, not as a
    stand-in for "account doesn't exist" — see `account_exists` for that.
    """

    user_id: str
    username: str
    display_name: str
    is_verified: bool
    follower_count: int
    following_count: int
    tweet_count: int
    created_at: datetime | None
    description: str
    account_exists: bool = True  # False for a 404/suspended/deleted lookup — see SocialEngine's usage


@dataclass
class Tweet:
    """
    One tweet returned by a mention search (Part VIII Step 13). Carries
    only the author fields `SocialEngine` actually consumes (follower
    count and verification status, for influencer detection) rather than
    a full nested `XUserProfile`, since a search result's `author` is a
    much lighter read than a real profile lookup and this codebase's
    convention (Part V.2, KISS) is not to model fields nothing consumes
    yet. twitterapi.io's real tweet object carries several more
    (retweetCount, viewCount, quoted_tweet, ...) — add them here if a
    future step needs them, per this layer's own extension pattern.
    """

    tweet_id: str
    text: str
    author_username: str
    author_follower_count: int
    author_is_verified: bool
    created_at: datetime | None


class SocialDataProvider(Protocol):
    """Satisfied today by `providers.twitterapi_io.TwitterApiIoProvider`.

    Three methods, not the two (`lookup_user`/`search_mentions`) this
    playbook step's own Public Interface names — `verified_follower_ratio`
    (Part III.4) can only be computed from the primary account's actual
    followers, and neither of those two methods returns that;
    twitterapi.io only exposes it via a separate `/twitter/user/followers`
    endpoint (confirmed against the provider's own published API
    reference while implementing this step, not guessed at) — the same
    class of documented, necessary interface correction as Step 4's
    `get_pairs` losing its originally-assumed `chain` parameter.
    `list_followers` is spelled out here rather than left implicit so a
    fake in tests has an explicit contract to satisfy, same as the other
    two.
    """

    async def lookup_user(self, handle: str) -> XUserProfile:
        """
        Raises on transport-level failure, same contract as every other
        provider method in this layer. A handle that resolves to nothing
        (never existed, suspended, deleted) is NOT a transport failure —
        returns an `XUserProfile` with `account_exists=False` and neutral
        values everywhere else, so `SocialEngine` can distinguish "asked
        and got told this account doesn't exist" from "asked and the
        network failed" (same shape as `SecurityDataProvider.scan`'s
        `chain_supported` distinction).
        """
        ...

    async def list_followers(self, handle: str, limit: int) -> list[XUserProfile]:
        """
        The most recent `limit` followers of `handle` — twitterapi.io's
        `/twitter/user/followers` is cursor-paginated and explicitly
        returns newest-follower-first, and this method fetches one page,
        not the full paginated list, deliberately: `verified_follower_ratio`
        is a sampled estimate from the most recent N followers, not an
        exhaustive account-wide count. An account with hundreds of
        thousands of followers makes an exhaustive fetch both slow and
        costly (twitterapi.io bills per follower returned) for a project
        with no stored-history layer to amortize that cost against (Part
        I.3) — the same "documented sample, not fabricated precision"
        tradeoff `HolderEngine.analyze` already makes for its own top-N
        holder set. Returns an empty list, not an error, for zero
        followers or a nonexistent account.
        """
        ...

    async def search_mentions(self, ticker: str) -> list[Tweet]:
        """
        Tweets mentioning `ticker` from roughly the last 24h — the exact
        query the concrete provider builds is its own concern (see that
        module's docstring for the operator used). Raises on transport-
        level failure, same contract as every other method in this
        layer. Zero matching tweets is a normal outcome, not an error —
        returns an empty list (drives `SocialResult.sentiment_ratio` and
        `tweet_frequency_per_day` to their documented neutral defaults),
        never raised for "nothing found."
        """
        ...
