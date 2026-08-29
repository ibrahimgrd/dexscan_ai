"""
Layer: Provider adapter — DexScreener network I/O (Playbook Part II.3;
Part VIII Step 4; discovery-feed methods added in the custom roadmap
Step 12/13 Auto-Watch pass).

Just the HTTP call: constructs the request, checks the status, hands the
JSON body to `dexscreener_parser`'s matching parse function. All schema
knowledge lives in that sibling module (deliberately split out — see its
own docstring for why); this file's only job is talking to the network
and turning transport failures into a clean raised exception for the
caller (`CoreEngine`, or now `AutoWatchManager`) to catch.

Endpoints verified against DexScreener's own published API reference
(docs.dexscreener.com/api/reference) while implementing this step —
`get_new_listings`/`get_trending` specifically confirmed against that
same reference during the custom-roadmap Auto-Watch pass, not assumed to
exist just because a third-party wrapper library mentioned them.
"""

from __future__ import annotations

import aiohttp

from analysis.api_abstraction import DiscoveryCandidate, PairData
from analysis.providers.dexscreener_parser import parse_discovery_feed, parse_pairs_response

_BASE_URL = "https://api.dexscreener.com"  # verified
_REQUEST_TIMEOUT_SECONDS = 10


class DexScreenerProvider:
    """Satisfies both `analysis.api_abstraction.MarketDataProvider` AND
    `TokenDiscoveryProvider` (Part V.2: one concrete adapter can satisfy
    more than one Protocol when it's genuinely the same API/session
    behind both — `AutoWatchManager` depends only on the narrower
    `TokenDiscoveryProvider` view of this same class, never on this
    concrete type directly). Holds its own `aiohttp.ClientSession`
    rather than requiring the caller to manage one — `main.py`'s
    composition root constructs one instance for the life of the process
    (Part II.3: single process-wide instances).

    `base_url` defaults to the real DexScreener API but is a constructor
    parameter, not hardcoded inline — `config.Settings.dexscreener_base_url`
    is the value Step 7's composition root will actually pass (Part VIII
    Step 4's own note: "add dexscreener_base_url if needed" — it's needed
    for exactly this: pointing at a mock/staging server in an integration
    test without touching this class's code)."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str = _BASE_URL) -> None:
        self._session = session
        self._base_url = base_url

    async def get_pairs(self, address: str) -> list[PairData]:
        """
        GET /latest/dex/tokens/{address} (verified) — returns every pair
        DexScreener has indexed for this exact address string, across
        every chain, in one call. No `chain` parameter: this endpoint
        doesn't take one (see api_abstraction.py's docstring for why
        that's a deliberate correction to this playbook's original
        per-chain-lookup assumption).

        Raises on any transport-level failure (timeout, connection error,
        non-200 status) — `CoreEngine` is the layer that catches and
        degrades (Part V.5); this method's job is only to raise a clear,
        logged-by-the-caller exception when something's actually wrong,
        never to swallow a real failure into a quietly-empty list.
        """
        url = f"{self._base_url}/latest/dex/tokens/{address}"

        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
            ) as response:
                if response.status != 200:
                    body_preview = (await response.text())[:200]
                    raise RuntimeError(
                        f"DexScreener returned HTTP {response.status}: {body_preview}"
                    )
                payload = await response.json()
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"DexScreener request failed: {exc}") from exc

        return parse_pairs_response(payload)

    async def _get_discovery_feed(self, path: str, source: str, limit: int) -> list[DiscoveryCandidate]:
        """Shared GET-and-parse for both discovery endpoints below —
        same error contract as `get_pairs`. `limit` truncates client-side
        after parsing: neither discovery endpoint accepts a limit/page
        parameter of its own (confirmed against the published reference —
        both return their full current list every call), so asking for
        fewer candidates doesn't reduce what DexScreener itself sends."""
        url = f"{self._base_url}{path}"

        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
            ) as response:
                if response.status != 200:
                    body_preview = (await response.text())[:200]
                    raise RuntimeError(f"DexScreener returned HTTP {response.status}: {body_preview}")
                payload = await response.json()
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"DexScreener request failed: {exc}") from exc

        return parse_discovery_feed(payload, source=source)[:limit]

    async def get_new_listings(self, limit: int = 30) -> list[DiscoveryCandidate]:
        """GET /token-profiles/latest/v1 (verified) — see
        `TokenDiscoveryProvider`'s own docstring for exactly what "new"
        means here (profile-recency, not confirmed pool-creation-recency)."""
        return await self._get_discovery_feed("/token-profiles/latest/v1", source="new_listings", limit=limit)

    async def get_trending(self, limit: int = 30) -> list[DiscoveryCandidate]:
        """GET /token-boosts/latest/v1 (verified) — see
        `TokenDiscoveryProvider`'s own docstring for why this is paid
        promotion, not an organic-interest signal."""
        return await self._get_discovery_feed("/token-boosts/latest/v1", source="boosted", limit=limit)
