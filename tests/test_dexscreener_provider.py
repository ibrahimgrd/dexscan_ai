"""
Step 12 (custom roadmap: Step 13) - real execution proof for
`DexScreenerProvider`'s network methods, using this repo's own
FakeSession/FakeResponse test doubles (tests/_aiohttp_test_doubles.py -
see that module's docstring for why they replaced this file's previous
aiohttp._ScriptedResponse / url_behaviors= usage, which referenced
aiohttp internals that don't exist). Covers the two discovery methods
(`get_new_listings`/`get_trending`) and `get_pairs`.
"""

from __future__ import annotations

import aiohttp
import pytest

from analysis.providers.dexscreener import DexScreenerProvider
from tests._aiohttp_test_doubles import FakeResponse, FakeSession

_PAIR_RESPONSE = {
    "pairs": [
        {
            "chainId": "solana", "dexId": "raydium", "pairAddress": "pair1",
            "baseToken": {"address": "Tok1", "name": "Test Token", "symbol": "TEST"},
            "quoteToken": {"symbol": "SOL"},
            "priceUsd": "0.01", "liquidity": {"usd": 100000.0},
            "fdv": 1000000.0, "marketCap": 800000.0,
            "volume": {"m5": 100.0, "h1": 1000.0, "h6": 5000.0, "h24": 50000.0},
            "priceChange": {"m5": 0.1, "h1": 1.0, "h6": 2.0, "h24": 5.0},
            "txns": {"h24": {"buys": 100, "sells": 80}},
            "pairCreatedAt": 1700000000000,
        }
    ]
}

_DISCOVERY_RESPONSE = [
    {"url": "https://x.com", "chainId": "solana", "tokenAddress": "Tok1", "description": "test"},
    {"url": "https://x.com", "chainId": "ethereum", "tokenAddress": "Tok2", "description": "test2"},
]


@pytest.mark.asyncio
async def test_get_pairs_success() -> None:
    url = "https://api.dexscreener.com/latest/dex/tokens/Tok1"
    session = FakeSession({url: FakeResponse(status=200, json_body=_PAIR_RESPONSE)})
    provider = DexScreenerProvider(session)

    pairs = await provider.get_pairs("Tok1")

    assert len(pairs) == 1
    assert pairs[0].base_token_symbol == "TEST"
    assert session.requested_urls == [url]


@pytest.mark.asyncio
async def test_get_pairs_transport_failure_raises() -> None:
    url = "https://api.dexscreener.com/latest/dex/tokens/Tok1"
    session = FakeSession({url: aiohttp.ClientError("down")})
    provider = DexScreenerProvider(session)

    with pytest.raises(RuntimeError, match="DexScreener request failed"):
        await provider.get_pairs("Tok1")


@pytest.mark.asyncio
async def test_get_new_listings_hits_the_correct_endpoint_and_parses_candidates() -> None:
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    session = FakeSession({url: FakeResponse(status=200, json_body=_DISCOVERY_RESPONSE)})
    provider = DexScreenerProvider(session)

    candidates = await provider.get_new_listings()

    assert session.requested_urls == [url]
    assert len(candidates) == 2
    assert candidates[0].source == "new_listings"
    assert candidates[0].token_address == "Tok1"


@pytest.mark.asyncio
async def test_get_trending_hits_the_boosts_endpoint_not_the_profiles_one() -> None:
    """Trending and New Listings must never silently share an endpoint -
    that would make TokenDiscoveryProvider's own documented distinction
    (paid boosts vs. profile recency) meaningless."""
    profiles_url = "https://api.dexscreener.com/token-profiles/latest/v1"
    boosts_url = "https://api.dexscreener.com/token-boosts/latest/v1"
    session = FakeSession({
        profiles_url: FakeResponse(status=200, json_body=[]),
        boosts_url: FakeResponse(status=200, json_body=_DISCOVERY_RESPONSE),
    })
    provider = DexScreenerProvider(session)

    candidates = await provider.get_trending()

    assert session.requested_urls == [boosts_url]
    assert profiles_url not in session.requested_urls
    assert all(c.source == "boosted" for c in candidates)


@pytest.mark.asyncio
async def test_get_new_listings_respects_limit_after_parsing() -> None:
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    many = [{"url": "x", "chainId": "solana", "tokenAddress": f"Tok{i}"} for i in range(10)]
    session = FakeSession({url: FakeResponse(status=200, json_body=many)})
    provider = DexScreenerProvider(session)

    candidates = await provider.get_new_listings(limit=3)

    assert len(candidates) == 3


@pytest.mark.asyncio
async def test_discovery_feed_transport_failure_raises_not_silently_empty() -> None:
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    session = FakeSession({url: aiohttp.ClientError("down")})
    provider = DexScreenerProvider(session)

    with pytest.raises(RuntimeError, match="DexScreener request failed"):
        await provider.get_new_listings()
