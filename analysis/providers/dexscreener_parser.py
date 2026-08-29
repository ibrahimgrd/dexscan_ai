"""
Layer: Provider adapter — DexScreener response parsing (Playbook Part
II.3; Part VIII Step 4).

Pure data transformation, deliberately split out of `dexscreener.py` into
its own file with zero `aiohttp` import: this is the one part of the
whole provider that translates DexScreener's actual JSON field names into
this project's normalized `PairData`, and it's also the part with the
most uncertainty (see module docstring in `dexscreener.py` for which
fields were verified against a live doc fetch vs. carried over from
training-data recall). Splitting it out means it can be executed and
tested directly, with fixture JSON, in any environment — including one
without `aiohttp` installed — rather than only syntax-checked.

`dexscreener.py` imports `parse_pair` and the chain-id maps from here and
adds nothing but the actual HTTP call around them.
"""

from __future__ import annotations

import logging
from typing import Any

from analysis.api_abstraction import DiscoveryCandidate, PairData
from bot.constants import Chain

logger = logging.getLogger(__name__)

# DexScreener's chainId strings (verified: the docs' own example shows
# chainId: "solana") do not match this project's short Chain enum values
# (Step 1's Chain.SOL == "sol", chosen before this provider was built) -
# this map is the one place that translation happens. "bsc" and "base"
# happen to already match; the rest don't.
DEXSCREENER_CHAIN_ID: dict[Chain, str] = {
    Chain.SOL: "solana",
    Chain.ETH: "ethereum",
    Chain.BSC: "bsc",
    Chain.BASE: "base",
    Chain.ARB: "arbitrum",
    Chain.TON: "ton",  # assumption - not separately confirmed; TON pairs are a
                        # small fraction of DexScreener's indexed volume, so
                        # this is the one chain in the map worth re-checking
                        # against a live response before relying on it.
}
CHAIN_FROM_DEXSCREENER_ID: dict[str, Chain] = {v: k for k, v in DEXSCREENER_CHAIN_ID.items()}


def parse_pair(raw: dict[str, Any]) -> PairData | None:
    """
    Returns `None` (skip this one pair, not the whole response) for a
    chainId this project doesn't support, or an entry missing the one
    field (`baseToken.address`) everything else is keyed on — one
    malformed or foreign-chain pair in a multi-pair response shouldn't
    fail the other, valid ones.

    Every other field uses `.get(..., default)` throughout, so a live
    response missing a field this module expected degrades to a sensible
    default (0.0 / 0 / empty string) instead of raising `KeyError`.
    """
    chain_id = raw.get("chainId")
    chain = CHAIN_FROM_DEXSCREENER_ID.get(chain_id)
    if chain is None:
        logger.debug("Skipping pair on unsupported/unrecognized chain", extra={"chainId": chain_id})
        return None

    base_token = raw.get("baseToken") or {}
    if not base_token.get("address"):
        logger.debug("Skipping pair with no baseToken.address")
        return None

    liquidity = raw.get("liquidity") or {}
    volume = raw.get("volume") or {}
    price_change = raw.get("priceChange") or {}
    txns_24h = (raw.get("txns") or {}).get("h24") or {}

    try:
        price_usd = float(raw.get("priceUsd") or 0.0)
    except (TypeError, ValueError):
        price_usd = 0.0

    return PairData(
        chain=chain,
        dex_id=str(raw.get("dexId") or "unknown"),
        pair_address=str(raw.get("pairAddress") or ""),
        base_token_address=str(base_token.get("address") or ""),
        base_token_symbol=str(base_token.get("symbol") or "?"),
        base_token_name=str(base_token.get("name") or "Unknown Token"),
        quote_token_symbol=str((raw.get("quoteToken") or {}).get("symbol") or "?"),
        price_usd=price_usd,
        liquidity_usd=float(liquidity.get("usd") or 0.0),
        fdv=float(raw.get("fdv") or 0.0),
        market_cap=float(raw.get("marketCap") or 0.0),
        volume_5m=float(volume.get("m5") or 0.0),
        volume_1h=float(volume.get("h1") or 0.0),
        volume_6h=float(volume.get("h6") or 0.0),
        volume_24h=float(volume.get("h24") or 0.0),
        price_change_5m=float(price_change.get("m5") or 0.0),
        price_change_1h=float(price_change.get("h1") or 0.0),
        price_change_6h=float(price_change.get("h6") or 0.0),
        price_change_24h=float(price_change.get("h24") or 0.0),
        buys_24h=int(txns_24h.get("buys") or 0),
        sells_24h=int(txns_24h.get("sells") or 0),
        pair_created_at_ms=raw.get("pairCreatedAt"),
    )


def parse_pairs_response(payload: dict[str, Any]) -> list[PairData]:
    """Parses a full `/latest/dex/tokens/{address}` response body.
    `payload["pairs"]` is `None` (not `[]`) when DexScreener has nothing
    for the address — handled here so callers never need to know that."""
    raw_pairs = payload.get("pairs") or []
    parsed = [parse_pair(raw) for raw in raw_pairs]
    return [p for p in parsed if p is not None]


# --- Discovery feeds (custom roadmap Step 12/13's Auto-Watch) --------------
#
# `/token-profiles/latest/v1` and `/token-boosts/latest/v1` share the
# exact same `TokenProfile` response shape (confirmed against
# DexScreener's own published OpenAPI schema while implementing this
# step: {url, chainId, tokenAddress, icon, header, description, links[]}
# for both) - one shared parser handles both, with the caller supplying
# which `source` label to stamp on the result (see
# `api_abstraction.DiscoveryCandidate`'s own docstring for why that label
# matters and is never dropped).

# Reverse of DEXSCREENER_CHAIN_ID above - discovery responses report
# chainId as a string ("solana"), same translation this file already
# needed one direction for `parse_pair`.
_CHAIN_ID_TO_CHAIN: dict[str, Chain] = {v: k for k, v in DEXSCREENER_CHAIN_ID.items()}


def parse_discovery_feed(payload: list[dict[str, Any]], source: str) -> list[DiscoveryCandidate]:
    """`payload` is the raw JSON array both `/token-profiles/latest/v1`
    and `/token-boosts/latest/v1` return - a list of `TokenProfile`-shaped
    dicts. An entry whose `chainId` doesn't map to a chain this project
    tracks (Step 1's six) is skipped, not raised on - a discovery feed
    covering more chains than this project scans yet is an expected,
    ordinary mismatch, not malformed data."""

    candidates: list[DiscoveryCandidate] = []
    for entry in payload:
        chain = _CHAIN_ID_TO_CHAIN.get(str(entry.get("chainId", "")))
        token_address = entry.get("tokenAddress")
        if chain is None or not token_address:
            continue
        candidates.append(DiscoveryCandidate(chain=chain, token_address=str(token_address), source=source))
    return candidates
