"""
Layer: Scoring — pipeline (Playbook Part III.6; Part VIII Step 6; v2
wired in during Step 10 [custom roadmap: Step 12]; v3 wired in during
Step 14).

`ScoringPipeline.score` was written in Step 6 to already accept all five
possible engine outputs as parameters (Core/Security required; Holder/
Momentum/Social optional), dispatching internally to whichever formula
version the actually-present inputs support. This is Part III.6's "one
new formula term per new engine" plugin principle, implemented as real
dispatch logic rather than asserted in prose (Part VII.1) — this pass is
that dispatch logic's THIRD and final branch landing, exactly as Step
6's own docstring said it eventually would, and Step 10's docstring said
the pattern would repeat once more to get here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from analysis.core_engine import CoreResult
from analysis.holder_engine import HolderResult
from analysis.momentum_engine import MomentumResult
from analysis.security_engine import SecurityResult
from analysis.social_engine import SocialResult
from scoring.formulas import (
    score_ai,
    score_confidence,
    score_opportunity_v1,
    score_opportunity_v2,
    score_opportunity_v3,
    score_risk_v1,
    score_risk_v2,
    tier_label,
)


@dataclass
class ScoringResult:
    """Part VIII Step 6's output shape. `pipeline_version` records which
    formula slice actually produced this result — never guessed at by a
    caller, always read directly off this field."""

    score_opportunity: float
    score_risk: float
    score_confidence: float
    score_ai: float
    tier_label: str
    explanation: list[str] = field(default_factory=list)
    pipeline_version: Literal["v1", "v2", "v3"] = "v1"


class ScoringPipeline:
    """No black-box model (Step 6's constraint): every number this class
    returns traces back to a named function in `formulas.py`, and
    `explanation` is generated from the specific inputs that actually
    fired, never a static template with items filled in or crossed out."""

    def score(
        self,
        core: CoreResult,
        security: SecurityResult,
        holder: HolderResult | None = None,
        momentum: MomentumResult | None = None,
        social: SocialResult | None = None,
    ) -> ScoringResult:
        """
        Dispatches on which optional engine outputs are present. v3 is
        implemented as of this pass (Step 14) — `social` is narrowed from
        `Any` to its real `SocialResult | None` type now that Social
        Engine exists, the same narrowing `holder`/`momentum` already
        went through in the Step 10 pass.

        v3 requires `holder`, `momentum`, AND `social` together, not just
        `social` alone: `score_opportunity_v3` needs `momentum`,
        `score_risk_v2` (reused unchanged — see `_score_v3`) needs
        `holder`. `scan_orchestration.run_scan` (this pipeline's only
        real caller) always produces all three from the same scan, so in
        practice this is never a partial call — handled explicitly
        anyway (a clear `ValueError` rather than a `None`-attribute crash
        deep inside a formula function), same reasoning `_score_v2`'s own
        holder+momentum check already established.
        """
        if social is not None:
            if holder is None or momentum is None:
                raise ValueError(
                    "Scoring v3 needs holder, momentum, and social together "
                    "(score_opportunity_v3 needs momentum; score_risk_v2 needs holder) - "
                    "got social without both of the others."
                )
            return self._score_v3(core, security, holder, momentum, social)
        if holder is not None and momentum is not None:
            return self._score_v2(core, security, holder, momentum)
        if holder is not None or momentum is not None:
            raise ValueError(
                "Scoring v2 needs both holder and momentum together (score_risk_v2 needs holder; "
                "score_opportunity_v2 needs momentum) - got only one."
            )
        return self._score_v1(core, security)

    def _score_v1(self, core: CoreResult, security: SecurityResult) -> ScoringResult:
        opportunity = score_opportunity_v1(core)
        risk = score_risk_v1(security)
        confidence = score_confidence(core, security)
        ai = score_ai(opportunity, confidence, risk)

        return ScoringResult(
            score_opportunity=opportunity,
            score_risk=risk,
            score_confidence=confidence,
            score_ai=ai,
            tier_label=tier_label(ai),
            explanation=_explain_v1(core, security),
            pipeline_version="v1",
        )

    def _score_v2(
        self, core: CoreResult, security: SecurityResult, holder: HolderResult, momentum: MomentumResult
    ) -> ScoringResult:
        opportunity = score_opportunity_v2(core, momentum)
        risk = score_risk_v2(security, holder)
        confidence = score_confidence(core, security)
        ai = score_ai(opportunity, confidence, risk)

        return ScoringResult(
            score_opportunity=opportunity,
            score_risk=risk,
            score_confidence=confidence,
            score_ai=ai,
            tier_label=tier_label(ai),
            explanation=_explain_v2(core, security, holder, momentum),
            pipeline_version="v2",
        )

    def _score_v3(
        self,
        core: CoreResult,
        security: SecurityResult,
        holder: HolderResult,
        momentum: MomentumResult,
        social: SocialResult,
    ) -> ScoringResult:
        """`score_risk_v2` is reused UNCHANGED here, not a new
        `score_risk_v3` — Part III.6's `Score_Risk` only ever has three
        terms (TrustScore, HolderConcentrationPenalty,
        VulnerabilityPenalty) and none of them is Social; `formulas.py`'s
        own `score_risk_v2` docstring already says as much ("no
        `score_risk_v3` waiting in a later step"). Only `Score_Opportunity`
        gains a third stage."""
        opportunity = score_opportunity_v3(core, momentum, social)
        risk = score_risk_v2(security, holder)
        confidence = score_confidence(core, security)
        ai = score_ai(opportunity, confidence, risk)

        return ScoringResult(
            score_opportunity=opportunity,
            score_risk=risk,
            score_confidence=confidence,
            score_ai=ai,
            tier_label=tier_label(ai),
            explanation=_explain_v3(core, security, holder, momentum, social),
            pipeline_version="v3",
        )


def _explain_v1(core: CoreResult, security: SecurityResult) -> list[str]:
    """
    Every line traces to a specific field that was actually read (Part
    I.2's Explainability principle) — nothing here is a static template.
    Security's own `scam_flags` (already specific, factual findings from
    Step 5) are included directly rather than re-derived.
    """
    lines: list[str] = []

    if security.degraded:
        lines.append(
            f"Security data unavailable ({security.degraded_reason or 'unknown reason'}) "
            "— risk score reflects that uncertainty rather than a confirmed finding."
        )
    elif security.scam_flags:
        lines.extend(security.scam_flags)
    else:
        lines.append("No mint/freeze authority, tax, or ownership red flags detected.")

    if core.pool_age_days is not None:
        lines.append(f"Pool age: {core.pool_age_days:.1f} days.")
    else:
        lines.append("Pool age could not be determined.")

    if core.volume_24h > 0:
        lines.append(f"24h volume: ${core.volume_24h:,.0f}.")
    else:
        lines.append("No 24h trading volume recorded.")

    lines.append(
        "This is a staged v1 score: momentum and social signals aren't factored in yet "
        "(they land in later build steps) — only contract safety, pool age, and volume."
    )

    return lines


def _explain_v2(core: CoreResult, security: SecurityResult, holder: HolderResult, momentum: MomentumResult) -> list[str]:
    """
    Same Explainability contract as `_explain_v1`, extended with the two
    new v2 inputs. Holder/Momentum lines are added rather than replacing
    v1's own lines — a v2 score is v1's same contract-safety/pool-age/
    volume read PLUS two new factors, not a different read of the old
    ones.
    """
    lines: list[str] = []

    if security.degraded:
        lines.append(
            f"Security data unavailable ({security.degraded_reason or 'unknown reason'}) "
            "— risk score reflects that uncertainty rather than a confirmed finding."
        )
    elif security.scam_flags:
        lines.extend(security.scam_flags)
    else:
        lines.append("No mint/freeze authority, tax, or ownership red flags detected.")

    if holder.degraded:
        lines.append(
            f"Holder data unavailable ({holder.degraded_reason or 'unknown reason'}) "
            "— concentration penalty reflects that uncertainty rather than a confirmed reading."
        )
    elif holder.hci_pct > 30.0:
        lines.append(f"Top-10 holder concentration is {holder.hci_pct:.1f}% (above the 30% flag threshold).")
    else:
        lines.append(f"Top-10 holder concentration is {holder.hci_pct:.1f}% (below the 30% flag threshold).")

    if holder.insider_bundle_detected:
        lines.append(f"Insider/bundle pattern detected: {holder.insider_bundle_wallet_count} wallets funded together at launch.")

    if core.pool_age_days is not None:
        lines.append(f"Pool age: {core.pool_age_days:.1f} days.")
    else:
        lines.append("Pool age could not be determined.")

    if core.volume_24h > 0:
        lines.append(f"24h volume: ${core.volume_24h:,.0f}.")
    else:
        lines.append("No 24h trading volume recorded.")

    trend_direction = "positive" if momentum.trending_score > 0 else "negative" if momentum.trending_score < 0 else "flat"
    lines.append(f"Momentum trend is {trend_direction} ({momentum.trending_score:+.1f}).")

    lines.append(
        "This is a v2 score: holder concentration and momentum now factor in directly; "
        "social sentiment still doesn't (it lands in a later build step)."
    )

    return lines


def _explain_v3(
    core: CoreResult, security: SecurityResult, holder: HolderResult, momentum: MomentumResult, social: SocialResult
) -> list[str]:
    """Same Explainability contract as `_explain_v1`/`_explain_v2`,
    extended with `social`. Reuses `_explain_v2`'s own lines rather than
    rebuilding them (DRY) but drops its closing sentence specifically —
    "social sentiment still doesn't [factor in]" is now factually wrong,
    not merely incomplete, so it is the one line every other `_explain_v*`
    function's own "extend, never replace" convention makes an exception
    for; every other line is additive, exactly as v1->v2 already was."""
    lines = _explain_v2(core, security, holder, momentum)[:-1]

    if social.degraded:
        lines.append(
            f"Social data unavailable ({social.degraded_reason or 'unknown reason'}) "
            "— sentiment score reflects that uncertainty rather than a confirmed reading."
        )
    else:
        sentiment_word = (
            "positive" if social.sentiment_ratio > 0 else "negative" if social.sentiment_ratio < 0 else "neutral"
        )
        lines.append(f"Social sentiment is {sentiment_word} ({social.sentiment_ratio:+.2f}).")

    lines.append(
        "This is a v3 score: the full five-engine formula now applies, including social sentiment."
    )

    return lines
