"""
Layer: Analysis — Core Engine (Playbook Part III.1; Part VIII Step 4).

Contract validation, chain detection, primary-pair resolution, and the
normalized metric set every later engine (Security, Holder, Momentum) and
the scoring pipeline build on. Depends only on `MarketDataProvider` (Part
II.3's API Abstraction Layer) — never imports aiohttp, never knows it's
talking to DexScreener specifically. No UI code, no Telegram import
anywhere in this file (Step 4's explicit constraint).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from analysis.api_abstraction import MarketDataProvider, PairData
from bot.constants import (
    EVM_ADDRESS_PATTERN,
    SOLANA_ADDRESS_PATTERN,
    TON_ADDRESS_PATTERN,
    Chain,
)

logger = logging.getLogger(__name__)


@dataclass
class CoreResult:
    """
    Part III.1's normalized output. `chain` and `primary_pair` are `None`
    only in the fully-unresolved degraded case (no chain could be
    determined at all, or the provider returned zero pairs) — every other
    field still gets a defined value (0.0 / empty dict) rather than `None`
    scattered throughout, so a renderer never has to null-check five
    separate fields to show a degraded card.
    """

    address: str
    chain: Chain | None
    primary_pair: PairData | None
    liquidity_usd: float
    market_cap: float
    fdv: float
    dilution_ratio: float | None  # None when fdv == 0 - Part III.1's divide, guarded
    volume_24h: float
    pool_age_days: float | None  # None when the provider omitted pair_created_at_ms
    price_change: dict[str, float]  # keys: "5m", "1h", "6h", "24h"
    buy_pressure_pct: float
    ambiguous_chain_candidates: list[Chain] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None


def _empty_result(address: str, reason: str) -> CoreResult:
    """The one place a fully-degraded CoreResult gets constructed, so
    every degraded field defaults identically regardless of which failure
    path produced it."""
    return CoreResult(
        address=address,
        chain=None,
        primary_pair=None,
        liquidity_usd=0.0,
        market_cap=0.0,
        fdv=0.0,
        dilution_ratio=None,
        volume_24h=0.0,
        pool_age_days=None,
        price_change={},
        buy_pressure_pct=50.0,  # neutral, not "0% buy pressure" which would misleadingly read as all-selling
        degraded=True,
        degraded_reason=reason,
    )


class CoreEngine:
    """Isolated and independently testable: constructed with a
    `MarketDataProvider`, has zero other collaborators. `main.py`'s
    composition root injects the concrete `DexScreenerProvider` — this
    class itself never imports it."""

    def __init__(self, provider: MarketDataProvider) -> None:
        self._provider = provider

    def detect_chain(self, address: str) -> Chain | None:
        """
        Shape-only (Part II.6) — no network call. Solana and TON shapes
        are unambiguous (their length ranges never overlap: 32-44 chars
        vs. exactly 48) and returned directly. An EVM-shaped (`0x...`)
        address deliberately returns `None`: shape alone can't distinguish
        ETH/BSC/BASE/ARB, and — unlike this playbook's original
        assumption — DexScreener's real API doesn't expose a per-chain
        lookup to disambiguate against either. `analyze()` resolves this
        from live data instead (which chain(s) the address actually has
        indexed pairs on), not from a second guess-and-check regex pass.
        """
        stripped = address.strip()
        if re.match(SOLANA_ADDRESS_PATTERN, stripped):
            return Chain.SOL
        if re.match(TON_ADDRESS_PATTERN, stripped):
            return Chain.TON
        return None  # EVM-shaped or genuinely unrecognized - analyze() tells these apart

    async def analyze(self, raw_address: str, chain_hint: Chain | None = None) -> CoreResult:
        """
        Part III.1's main entry point. Never raises past this method —
        every failure path (unrecognized shape, provider timeout, zero
        pairs found, ambiguous EVM match with no hint) returns a
        `CoreResult` with `degraded=True` and a specific
        `degraded_reason`, per Step 4's constraint that failures degrade
        rather than propagate.
        """
        address = raw_address.strip()
        shape_chain = self.detect_chain(address)

        if shape_chain is None and not re.match(EVM_ADDRESS_PATTERN, address):
            # Not Solana-shaped, not TON-shaped, not even EVM-shaped -
            # nothing to look up. No network call spent on this.
            return _empty_result(address, "This doesn't match a known address format.")

        try:
            pairs = await self._provider.get_pairs(address)
        except Exception as exc:
            # Broad on purpose: the provider's contract (api_abstraction.py's
            # docstring) is "raise on any transport failure" - timeout,
            # connection error, 5xx, malformed response - and this is the
            # one place Core catches all of them and degrades (Part V.5),
            # rather than each call site re-implementing the same guard.
            logger.warning("Market data provider failed", extra={"address": address, "error": str(exc)})
            return _empty_result(address, "Couldn't reach the market data provider. Try again shortly.")

        if not pairs:
            return _empty_result(address, "No trading pairs found for this address.")

        resolved_chain, candidate_pairs, ambiguous_candidates = self._resolve_chain(
            pairs, shape_chain, chain_hint
        )

        if resolved_chain is None:
            # Genuinely ambiguous EVM match with no hint to break the tie,
            # and more than one candidate chain's pairs are present.
            return CoreResult(
                address=address,
                chain=None,
                primary_pair=None,
                liquidity_usd=0.0,
                market_cap=0.0,
                fdv=0.0,
                dilution_ratio=None,
                volume_24h=0.0,
                pool_age_days=None,
                price_change={},
                buy_pressure_pct=50.0,
                ambiguous_chain_candidates=ambiguous_candidates,
                degraded=True,
                degraded_reason="This address exists on more than one chain — please pick one.",
            )

        primary_pair = max(candidate_pairs, key=lambda p: p.liquidity_usd)  # Part III.1: PrimaryPair = argmax(Liquidity_USD)

        return CoreResult(
            address=address,
            chain=resolved_chain,
            primary_pair=primary_pair,
            liquidity_usd=primary_pair.liquidity_usd,
            market_cap=primary_pair.market_cap,
            fdv=primary_pair.fdv,
            dilution_ratio=self._dilution_ratio(primary_pair.market_cap, primary_pair.fdv),
            volume_24h=primary_pair.volume_24h,
            pool_age_days=self._pool_age_days(primary_pair.pair_created_at_ms),
            price_change={
                "5m": primary_pair.price_change_5m,
                "1h": primary_pair.price_change_1h,
                "6h": primary_pair.price_change_6h,
                "24h": primary_pair.price_change_24h,
            },
            buy_pressure_pct=self._buy_pressure(primary_pair.buys_24h, primary_pair.sells_24h),
        )

    def _resolve_chain(
        self,
        pairs: list[PairData],
        shape_chain: Chain | None,
        chain_hint: Chain | None,
    ) -> tuple[Chain | None, list[PairData], list[Chain]]:
        """
        Returns (resolved_chain_or_None, pairs_on_that_chain, all_candidate_chains).

        Precedence, most to least authoritative:
        1. `shape_chain` (Solana/TON) — the address's own shape is
           deterministic for these two families; a live result on a
           different chain would mean the provider is confused, not the
           user, so shape wins.
        2. `chain_hint` — used only when shape was ambiguous (EVM), to
           pick among the chains the provider actually returned pairs on.
        3. Single-chain result — if every returned pair is on the same
           chain regardless of hint, that's unambiguous on its own.
        4. Otherwise: genuinely ambiguous: return None and the full
           candidate list, for `analyze()` to surface to the user.
        """
        candidate_chains = sorted({p.chain for p in pairs}, key=lambda c: c.value)

        if shape_chain is not None:
            on_shape_chain = [p for p in pairs if p.chain is shape_chain]
            # Shape said Solana/TON; if the provider genuinely has nothing
            # on that chain, that's a "no pairs" case, not ambiguity.
            return (shape_chain, on_shape_chain, candidate_chains) if on_shape_chain else (
                shape_chain,
                [],
                candidate_chains,
            )

        if len(candidate_chains) == 1:
            only = candidate_chains[0]
            return only, [p for p in pairs if p.chain is only], candidate_chains

        if chain_hint is not None and chain_hint in candidate_chains:
            return chain_hint, [p for p in pairs if p.chain is chain_hint], candidate_chains

        return None, [], candidate_chains

    @staticmethod
    def _dilution_ratio(market_cap: float, fdv: float) -> float | None:
        """Part III.1: DilutionRatio = MarketCap / FDV. Guarded against
        FDV == 0 (a real, if rare, provider value for a brand-new or
        malformed pair) rather than letting a ZeroDivisionError escape."""
        if fdv == 0:
            return None
        return market_cap / fdv

    @staticmethod
    def _buy_pressure(buys_24h: int, sells_24h: int) -> float:
        """
        Part III.1 defines BuyPressure as a volume-based ratio
        (Volume_Buy_24h / Volume_Total_24h). DexScreener's actual API
        does not expose a buy/sell VOLUME split — only 24h transaction
        COUNTS (`txns.h24.buys` / `.sells`). Implemented here as the
        transaction-count ratio instead: BuyPressure =
        buys_24h / (buys_24h + sells_24h) x 100. This is a documented,
        deliberate substitution for missing data (Part VI: flag rather
        than silently deviate), not a silent reinterpretation - a
        buy-count-share is a widely-used, legitimate proxy for the same
        underlying "is this mostly being bought or sold" question, just
        not literally the formula as originally written.

        Zero total transactions returns a neutral 50.0 rather than
        dividing by zero or implying "all selling."
        """
        total = buys_24h + sells_24h
        if total == 0:
            return 50.0
        return (buys_24h / total) * 100

    @staticmethod
    def _pool_age_days(pair_created_at_ms: int | None) -> float | None:
        if pair_created_at_ms is None:
            return None
        age_seconds = time.time() - (pair_created_at_ms / 1000)
        return max(age_seconds / 86400, 0.0)  # never negative, e.g. from minor clock skew
