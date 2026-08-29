"""
Layer: Analysis — Security Engine (Playbook Part III.2; Part VIII Step 5).

Contract-safety checks: mint/freeze authority, tax simulation, LP lock,
ownership renouncement, scam-pattern flags. Depends only on
`SecurityDataProvider` (Part II.3) — never imports aiohttp, never knows
it's talking to RugCheck specifically. No trading logic, no persistence,
no UI code anywhere in this file (Step 5's explicit constraints).

Mirrors `core_engine.py`'s shape deliberately (protocol -> adapter ->
engine, same degrade-not-raise contract) so Step 6's scoring pipeline can
treat Core and Security as structurally interchangeable inputs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from analysis.api_abstraction import SecurityDataProvider, SecurityReport
from analysis.core_engine import CoreResult
from bot.constants import Chain

logger = logging.getLogger(__name__)

# Part III.2: "combined tax over 10% is high-risk, sell tax >=99% is a
# critical honeypot alert." Named constants, not inline literals, so a
# future tuning pass touches one place.
_HIGH_TAX_COMBINED_THRESHOLD_PCT = 10.0
_HONEYPOT_SELL_TAX_THRESHOLD_PCT = 99.0

_RISK_LEVEL_BANDS: tuple[tuple[float, str], ...] = (
    (80.0, "Low Risk"),
    (50.0, "Moderate Risk"),
    (25.0, "High Risk"),
    (0.0, "Critical Risk"),
)


@dataclass
class SecurityResult:
    """
    Part III.2's normalized output. `degraded=True` covers three distinct
    situations, told apart by `degraded_reason`'s text: the upstream
    `CoreResult` was itself unresolved (nothing to scan), the provider
    genuinely doesn't cover this chain, or a transport failure occurred —
    Part IV.3's partial-failure rule means any of the three should let
    Core's own results still render, never block them.

    `scam_flags` vs. `provider_notes` — two lists, deliberately not
    merged: `scam_flags` is entirely computed by this engine from the
    structured fields below, using Part III.2's own named thresholds —
    it's what Part III.6's explanation text should read from.
    `provider_notes` is the security provider's own raw findings
    (RugCheck's `risks[]`, verbatim), which very often describe the exact
    same underlying facts in the provider's own words (a real RugCheck
    response for a token with an active mint authority typically already
    includes a "Mint authority still enabled" entry in its own `risks[]`,
    on top of the raw `mintAuthority` field this engine checks
    independently). Concatenating the two would mean the same fact gets
    stated twice, worded slightly differently, with no way to tell they're
    the same fact — an actual bug caught by this step's own tests, not a
    hypothetical. Kept separate so a renderer can choose to show one, the
    other, or both clearly labeled, rather than a list with silent
    duplicates.
    """

    trust_score: float  # 0-100, higher = safer
    risk_level: str
    mint_authority_active: bool
    freeze_authority_active: bool
    buy_tax_pct: float
    sell_tax_pct: float
    lp_lock_ratio_pct: float | None
    lp_lock_duration_days: float | None
    ownership_renounced: bool
    scam_flags: list[str] = field(default_factory=list)
    provider_notes: list[str] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None


def _neutral_degraded_result(reason: str) -> SecurityResult:
    """The one place a fully-degraded SecurityResult gets constructed.
    Neutral, not alarmist, defaults: a security check that couldn't run
    is "unknown," not "assume the worst" — Part III.6's scoring pipeline
    is where a caller decides how to weigh an unknown differently from a
    confirmed-safe result, not this function."""
    return SecurityResult(
        trust_score=50.0,
        risk_level="Unknown",
        mint_authority_active=False,
        freeze_authority_active=False,
        buy_tax_pct=0.0,
        sell_tax_pct=0.0,
        lp_lock_ratio_pct=None,
        lp_lock_duration_days=None,
        ownership_renounced=False,
        scam_flags=[],
        provider_notes=[],
        degraded=True,
        degraded_reason=reason,
    )


class SecurityEngine:
    """
    Isolated and independently testable, matching `CoreEngine`'s shape
    exactly. Takes a single provider today; per Step 5's Future
    Compatibility note, a second provider (most plausibly EVM-focused,
    since RugCheck's reliable coverage is Solana-specific — see
    `providers/rugcheck.py`'s docstring) would be reconciled *inside*
    `analyze()` without changing this method's signature. Not built now
    because only one provider exists — the constraint is documented here
    so that future addition doesn't require touching `analyze()`'s
    contract with the rest of the codebase.
    """

    def __init__(self, provider: SecurityDataProvider) -> None:
        self._provider = provider

    async def analyze(self, core_result: CoreResult) -> SecurityResult:
        """
        Consumes `CoreResult` by field access only — never re-fetches or
        re-validates the address (Part VIII Step 5's Integration
        Requirement). Never raises past this method; every failure path
        returns a `SecurityResult` with `degraded=True`.
        """
        if core_result.chain is None or core_result.degraded:
            # Nothing to scan - Core itself couldn't resolve a chain/pair.
            # Not this engine's failure to report as its own.
            return _neutral_degraded_result(
                "No resolved chain/address to run a security scan against."
            )

        try:
            report = await self._provider.scan(core_result.address, core_result.chain)
        except Exception as exc:
            logger.warning(
                "Security data provider failed",
                extra={"address": core_result.address, "chain": core_result.chain.value, "error": str(exc)},
            )
            return _neutral_degraded_result(
                "Couldn't reach the security data provider. Try again shortly."
            )

        if not report.chain_supported:
            return _neutral_degraded_result(
                f"Security scanning isn't available yet for {core_result.chain.value.upper()}."
            )

        return self._build_result(report)

    def _build_result(self, report: SecurityReport) -> SecurityResult:
        scam_flags: list[str] = []

        combined_tax = report.buy_tax_pct + report.sell_tax_pct
        if report.sell_tax_pct >= _HONEYPOT_SELL_TAX_THRESHOLD_PCT:
            scam_flags.append(
                f"Critical: sell tax at or above {_HONEYPOT_SELL_TAX_THRESHOLD_PCT:.0f}% — likely honeypot"
            )
        elif combined_tax > _HIGH_TAX_COMBINED_THRESHOLD_PCT:
            scam_flags.append(f"High combined buy+sell tax: {combined_tax:.1f}%")

        if report.mint_authority_active:
            scam_flags.append("Mint authority is still active — supply can be diluted")
        if report.freeze_authority_active:
            scam_flags.append("Freeze authority is still active — transfers can be blocked")
        if not report.ownership_renounced:
            # For a Solana-sourced report, `ownership_renounced` is
            # itself often derived from mint/freeze authority state
            # (rugcheck_parser.py's docstring — Solana has no separate
            # Ownable-style owner slot the way EVM contracts do), so this
            # can restate the two flags above in different words rather
            # than add new information. Left as-is rather than
            # suppressed: it's still an accurate statement, and a future
            # EVM provider's ownership_renounced IS independent
            # information worth its own flag — deciding "is this
            # redundant" would require this engine to know a fact's
            # provenance, which breaks the abstraction boundary Part II.3
            # exists to enforce.
            scam_flags.append("Ownership has not been renounced")

        return SecurityResult(
            trust_score=report.trust_score,
            risk_level=self._risk_level(report.trust_score),
            mint_authority_active=report.mint_authority_active,
            freeze_authority_active=report.freeze_authority_active,
            buy_tax_pct=report.buy_tax_pct,
            sell_tax_pct=report.sell_tax_pct,
            lp_lock_ratio_pct=report.lp_lock_ratio_pct,
            lp_lock_duration_days=report.lp_lock_duration_days,
            ownership_renounced=report.ownership_renounced,
            scam_flags=scam_flags,
            provider_notes=list(report.raw_risk_flags),
        )

    @staticmethod
    def _risk_level(trust_score: float) -> str:
        """Engine-owned categorization (Part V.2: interpretation is engine
        logic, not provider logic) — not a field RugCheck itself returns
        with confirmed-stable naming. Bands are this engine's own,
        independent of Part III.6's later AI-score tiers (Step 6+), which
        categorize the *combined* verdict, not this one input alone."""
        for threshold, label in _RISK_LEVEL_BANDS:
            if trust_score >= threshold:
                return label
        return _RISK_LEVEL_BANDS[-1][1]
