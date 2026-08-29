"""
Layer: Scoring — Risk x Opportunity Matrix (custom roadmap Step 12 only —
NOT part of the Unified Developer Playbook's Part III.6 formula. Kept in
its own module, deliberately separate from `formulas.py`/`pipeline.py`,
specifically so a future reader can tell at a glance which scoring code
is Playbook-canonical and which is a custom-roadmap addition on top of
it — see README.md's Step 12 entry for the full context).

WHY THIS IS ADDITIVE, NOT A REPLACEMENT — read this before rendering or
extending it. Part III.6 states a design rule "never relax": Risk Level
and AI Score / Opportunity are shown as two separate signals, never
blended into one number, specifically so a token that's safe-but-
unremarkable isn't visually indistinguishable from one that's risky-but-
hyped. A single combined "matrix tier" risks doing exactly the blending
that rule exists to prevent — so `RiskOpportunityTier` is designed to be
rendered ALONGSIDE the existing separate Risk Level (inside
`_security_section`) and AI Score (`_score_section`) lines, never in
place of them, and always carries both raw axis scores next to its
label (`score_risk`/`score_opportunity` below) so the two numbers behind
any combined label stay visible, not hidden behind one verdict. This
module produces an additional cross-reference view, not a compressed
replacement for the two signals it reads.

Band thresholds are Claude's documented assumption (Part VI "on
ambiguity" — nothing prescribes these, since this feature doesn't exist
in the playbook at all): three equal thirds, applied identically to both
axes for simplicity and to avoid an asymmetry that would need its own
separate justification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Band = Literal["Low", "Moderate", "High"]

# Applied to both Score_Risk and Score_Opportunity identically (module
# docstring: equal thirds, same reasoning on both axes, not tuned
# per-axis without evidence either warrants different cut points).
_HIGH_BAND_MIN = 67.0
_MODERATE_BAND_MIN = 34.0


def _band(score: float) -> Band:
    if score >= _HIGH_BAND_MIN:
        return "High"
    if score >= _MODERATE_BAND_MIN:
        return "Moderate"
    return "Low"


@dataclass
class RiskOpportunityTier:
    """One cell of the 3x3 grid. `label`/`description` are categorical
    (Part I.2/IV.3's rule, applied here too even though this whole
    feature is outside the playbook proper) — never a directional call,
    never "Buy"/"Sell"/"Bullish," and every description explicitly
    references BOTH underlying bands rather than reading as a single
    collapsed verdict."""

    risk_band: Band
    opportunity_band: Band
    label: str
    description: str


# 9 cells, (risk_band, opportunity_band) -> (label, description). Every
# description names both bands explicitly - a reader should never need
# to infer which axis drove the label from the label text alone.
_GRID: dict[tuple[Band, Band], tuple[str, str]] = {
    ("Low", "Low"): (
        "Quiet & Contained",
        "Low measured risk, low measured opportunity signal — a calm, low-activity profile.",
    ),
    ("Low", "Moderate"): (
        "Steady Building",
        "Low measured risk with a moderate opportunity signal — gradual activity, not a spike.",
    ),
    ("Low", "High"): (
        "Strong Foundation, High Interest",
        "Low measured risk paired with a high opportunity signal — the combination this matrix "
        "reads most favorably, though 'low risk' is not the same as 'no risk.'",
    ),
    ("Moderate", "Low"): (
        "Caution, Low Signal",
        "Moderate measured risk with a low opportunity signal to weigh against it.",
    ),
    ("Moderate", "Moderate"): (
        "Mixed Profile",
        "Moderate risk, moderate opportunity signal — a genuinely balanced read, not a clear "
        "signal either way.",
    ),
    ("Moderate", "High"): (
        "Hyped, Proceed Carefully",
        "Moderate measured risk with a high opportunity signal — real activity, but the risk "
        "side has not cleared either.",
    ),
    ("High", "Low"): (
        "High Risk, Little Upside Signal",
        "High measured risk with a low opportunity signal offsetting it.",
    ),
    ("High", "Moderate"): (
        "High Risk, Notable Buzz",
        "High measured risk with a moderate opportunity signal — the activity does not offset "
        "the risk read.",
    ),
    ("High", "High"): (
        "High Risk, High Hype",
        "High measured risk AND a high opportunity signal — exactly the profile this matrix "
        "exists to separate from 'Strong Foundation, High Interest' rather than let hype alone "
        "drive the read.",
    ),
}


def classify(score_risk: float, score_opportunity: float) -> RiskOpportunityTier:
    """`score_risk`/`score_opportunity` are the same 0-100
    `ScoringResult.score_risk`/`score_opportunity` values already shown
    elsewhere — this function only classifies them into a labeled grid
    cell, it never recomputes or adjusts either number."""
    risk_band = _band(score_risk)
    opportunity_band = _band(score_opportunity)
    label, description = _GRID[(risk_band, opportunity_band)]
    return RiskOpportunityTier(
        risk_band=risk_band, opportunity_band=opportunity_band, label=label, description=description
    )
