"""
Layer: Analysis — Holder Engine (Playbook Part III.3; Part VIII Step 8).

Concentration and coordination-risk checks: Holder Concentration Index
(HCI), whale mapping, launch-slot insider/bundle detection. Depends only
on `HolderDataProvider` (Part II.3) — never imports aiohttp, never knows
it's talking to a Solana JSON-RPC endpoint specifically. No trading logic,
no persistence, no UI code anywhere in this file — mirrors Step 4/5's
constraints on `CoreEngine` / `SecurityEngine`, whose degrade-not-raise
shape this class matches deliberately.

FREE-TIER PROVIDER CHOICE (per this session's explicit requirement — a
documented departure from Step 8's own "Assumption stated explicitly"
text, which named Solscan): Solscan's holder-data endpoints sit behind
its paid Pro tier (reported around $199/mo). This build instead uses
`providers.solana_rpc.SolanaRpcHolderProvider`, which speaks the
*standard* Solana JSON-RPC surface — free on both the public cluster
endpoint (no key at all) and Helius's free tier (1M credits/mo, no card).
See that module's docstring for the full reasoning and verified current
pricing/limits.

Known, documented scope limits of this free-tier approach — flagged here
the same way Step 6 flags `score_confidence`'s missing `CodeVerifiedBoolean`
input as a known gap, not a silent omission:

- `holder_count` reflects the top 20 accounts `getTokenLargestAccounts`
  returns — a cap Solana's own RPC method imposes on every caller, free
  or paid; a bigger free-tier plan doesn't remove it, only a fundamentally
  different indexing product (full historical enumeration) would.
  `HolderResult.holder_count_is_estimate` is `True` whenever this cap is
  in effect, which — for this provider — is always.
- `classified_wallets` can only ever produce a `"burn"` label in this
  build (a small, named set of known dead/incinerator addresses).
  `"team"` and `"smart_money"` both need an off-chain labeled-wallet data
  source no free (or paid) RPC endpoint exposes on its own — left
  unclassified rather than guessed at.
- `holder_growth_24h_pct` is derived from the SAME top-holder funding
  data already gathered for insider/bundle detection below (no extra RPC
  cost), not a second live snapshot compared against a first — Part I.3
  forbids the persistent storage a true historical diff would need
  anyway. It is honestly scoped to the observed top-holder set, not the
  full holder base this free-tier build never fully enumerates.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from analysis.api_abstraction import FundingRecord, HolderDataProvider, HolderRecord
from analysis.core_engine import CoreResult
from bot.constants import WHALE_HOLDER_THRESHOLD_PCT, Chain

logger = logging.getLogger(__name__)

# Part III.3: "HCI = sum of top-10 holders' % of supply." Kept local to
# this module (not bot/constants.py) - it's a formula constant, same
# convention security_engine.py uses for its own tax thresholds, not a
# cross-cutting value other modules need to reference.
_HCI_TOP_N_HOLDERS = 10

# Part III.3 names the *pattern* (same-slot, same-funder wallets) but not
# an exact group-size threshold - a documented assumption per Part VI's
# "on ambiguity" rule: 3 independently-appearing top holders funded by
# the same wallet in the same slot is a strong enough coincidence to be
# worth surfacing; 2 is common on its own (a single CEX/OTC withdrawal
# wallet funding two unrelated buyers happens constantly) and would flag
# too often to be a useful signal.
_INSIDER_BUNDLE_MIN_WALLETS = 3

# Solana's de facto incinerator/dead address - no known private key,
# confirmed via multiple independent sources (sol-incinerator.com, Solana
# Explorer, Solscan) while implementing this step. Deliberately a small,
# non-exhaustive list: Solana has no single protocol-level "burn address"
# standard the way Ethereum has 0x000...dEaD (a real SPL Token `burn`
# instruction destroys supply directly and never produces a holder to
# classify here at all) - this catches the one overwhelmingly common
# community convention, not every address a project might informally
# treat as a burn sink.
_KNOWN_BURN_ADDRESSES: frozenset[str] = frozenset({"1nc1nerator11111111111111111111111111111111"})

_SECONDS_PER_DAY = 86400


@dataclass
class HolderResult:
    """
    Part III.3's normalized output, extended with two fields the
    Playbook's own dataclass sketch didn't name (both default in a way
    that preserves the original shape's meaning for any caller that
    doesn't know about them yet - Part V.2's Open/Closed principle):

    `holder_count_is_estimate` - `True` whenever `holder_count` is the
    top-20 cap rather than a confirmed total (always `True` for this
    free-tier provider today - module docstring).
    `insider_bundle_wallet_count` - how many wallets triggered
    `insider_bundle_detected`, so a future renderer can say "3 wallets
    funded together" instead of a bare boolean (Part I.2: Explainability
    over black-box verdicts applies here, not only to the AI Score).
    """

    holder_count: int
    holder_growth_24h_pct: float
    hci_pct: float
    whale_count: int
    classified_wallets: dict[str, str] = field(default_factory=dict)
    insider_bundle_detected: bool = False
    holder_count_is_estimate: bool = True
    insider_bundle_wallet_count: int = 0
    degraded: bool = False
    degraded_reason: str | None = None


def _degraded_result(reason: str) -> HolderResult:
    """The one place a fully-degraded HolderResult gets constructed -
    same pattern as core_engine.py's `_empty_result` / security_engine
    .py's `_neutral_degraded_result`. Neutral defaults: an unmeasured
    concentration is "unknown," not "assume the worst" - the same
    reasoning security_engine.py documents for its own neutral degrade
    case."""
    return HolderResult(
        holder_count=0,
        holder_growth_24h_pct=0.0,
        hci_pct=0.0,
        whale_count=0,
        classified_wallets={},
        insider_bundle_detected=False,
        holder_count_is_estimate=True,
        insider_bundle_wallet_count=0,
        degraded=True,
        degraded_reason=reason,
    )


class HolderEngine:
    """
    Isolated and independently testable, matching `CoreEngine` /
    `SecurityEngine`'s shape. Takes a single provider; a second, EVM-
    focused provider (this step's Future Compatibility note) would be
    reconciled inside `analyze()` — the same extension pattern
    `SecurityEngine` documents for its own eventual second provider — not
    built now because this free-tier implementation only has a Solana
    data source (module docstring).
    """

    def __init__(self, provider: HolderDataProvider) -> None:
        self._provider = provider

    async def analyze(self, core_result: CoreResult) -> HolderResult:
        """
        Consumes `CoreResult` by field access only, same Integration
        Requirement Step 5 established for `SecurityEngine` — never
        re-fetches or re-validates the address. Never raises past this
        method; every failure path returns a `HolderResult` with
        `degraded=True`.

        Gates on `chain is Chain.SOL` *before* calling the provider at
        all — unlike `SecurityEngine`, which lets RugCheck's own
        `chain_supported` flag decide case by case. A Solana JSON-RPC
        endpoint isn't "unreliable" for an EVM address the way RugCheck's
        non-Solana coverage was merely unconfirmed (Part III.2) — it is
        the wrong network, categorically, so there's nothing worth
        spending a request on. See module docstring's Future
        Compatibility note for how a second, EVM-focused provider would
        change this later.
        """
        if core_result.degraded or core_result.chain is None:
            return _degraded_result("No resolved chain/address to run a holder scan against.")

        if core_result.chain is not Chain.SOL:
            return _degraded_result(
                f"Holder analysis is currently available for Solana only, not {core_result.chain.value.upper()}."
            )

        try:
            holders = await self._provider.get_holders(core_result.address, core_result.chain)
        except Exception as exc:
            logger.warning(
                "Holder data provider failed",
                extra={"address": core_result.address, "error": str(exc)},
            )
            return _degraded_result("Couldn't reach the holder data provider. Try again shortly.")

        holders = sorted(holders, key=lambda h: h.pct_of_supply, reverse=True)

        # round(): summing up to 10 independently-computed floats can
        # land a value that is *mathematically* exactly on a threshold
        # (e.g. 30.0%) a hair off it instead (e.g. 29.999999999999996)
        # purely from IEEE-754 accumulation - the precise off-by-one Step
        # 8 flags by name as a risk. 6 decimal places is far finer than
        # this figure is ever meaningfully precise to, so nothing real is
        # lost; what's gained is that a later `> 30.0` comparison (Step
        # 10's scoring pipeline, not this engine) sees the number a
        # person computing the same sum by hand would.
        hci_pct = round(sum(h.pct_of_supply for h in holders[:_HCI_TOP_N_HOLDERS]), 6)
        whale_count = sum(1 for h in holders if h.pct_of_supply > WHALE_HOLDER_THRESHOLD_PCT)
        classified = self._classify(holders)

        # Funding lookups are a separate provider call that fails
        # independently of the holder list above. Part IV.3's
        # partial-failure principle ("one engine timing out degrades
        # that section only") applied one level finer than the Playbook
        # states it: a sub-feature failing inside ONE engine shouldn't
        # blank out sibling sub-features that already succeeded. HCI/
        # whale/classification above stay valid even if this fails.
        funding_records: list[FundingRecord] = []
        try:
            funding_records = await self._provider.get_launch_block_funding(
                core_result.address, core_result.chain
            )
        except Exception as exc:
            logger.warning(
                "Holder funding lookup failed; HCI/whale/classification data is still valid",
                extra={"address": core_result.address, "error": str(exc)},
            )

        insider_detected, bundle_size = self._detect_insider_bundle(funding_records)
        growth_pct = self._growth_from_funding(funding_records)

        return HolderResult(
            holder_count=len(holders),
            holder_growth_24h_pct=growth_pct,
            hci_pct=hci_pct,
            whale_count=whale_count,
            classified_wallets=classified,
            insider_bundle_detected=insider_detected,
            holder_count_is_estimate=True,  # module docstring: top-20 cap, always, for this provider
            insider_bundle_wallet_count=bundle_size,
        )

    @staticmethod
    def _classify(holders: list[HolderRecord]) -> dict[str, str]:
        """Only ever produces `"burn"` labels in this build - module
        docstring explains why `"team"` / `"smart_money"` aren't
        attempted here."""
        return {h.wallet_address: "burn" for h in holders if h.wallet_address in _KNOWN_BURN_ADDRESSES}

    @staticmethod
    def _detect_insider_bundle(records: list[FundingRecord]) -> tuple[bool, int]:
        """
        Part III.3: "wallets funded in the same launch-block slot by the
        same deployer address." Groups by `(slot, funder)`. Solana has
        slots, not the discrete per-transaction "block" Part III.3's
        prose borrows from an EVM mental model — slot is the direct
        analog (a documented substitution, the same kind
        `core_engine.py`'s buy-pressure proxy and `security_engine.py`'s
        transfer-fee mapping already make elsewhere in this codebase for
        the same underlying reason: an EVM-shaped formula meeting a
        Solana-shaped reality).

        Records with an unresolved funder/slot are excluded from grouping
        entirely (`continue`) rather than grouped under a `(None, None)`
        key — "couldn't confirm a funder" is not evidence of coordination
        and must never accidentally count toward the threshold.
        """
        groups: dict[tuple[int, str], int] = {}
        for r in records:
            if r.funding_source_address is None or r.funded_at_slot is None:
                continue
            key = (r.funded_at_slot, r.funding_source_address)
            groups[key] = groups.get(key, 0) + 1

        if not groups:
            return False, 0

        largest_group_size = max(groups.values())
        if largest_group_size >= _INSIDER_BUNDLE_MIN_WALLETS:
            return True, largest_group_size
        return False, 0

    @staticmethod
    def _growth_from_funding(records: list[FundingRecord]) -> float:
        """See module docstring: a proxy over the observed top-holder set
        (the only wallets this free-tier build has funding timestamps
        for), not the full holder base - reuses data already fetched for
        insider/bundle detection rather than issuing extra RPC calls.
        `0.0` (not `None`) when no funding timestamps are available at
        all - `holder_growth_24h_pct` is a non-optional `float` in
        `HolderResult`, and "no fresh top holders observed" is itself a
        meaningful, honest zero, not a missing value."""
        timed = [r.funded_at_block_time for r in records if r.funded_at_block_time is not None]
        if not timed:
            return 0.0
        cutoff = time.time() - _SECONDS_PER_DAY
        recent = sum(1 for block_time in timed if block_time >= cutoff)
        return (recent / len(timed)) * 100
