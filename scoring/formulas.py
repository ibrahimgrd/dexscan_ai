"""
Layer: Scoring — formulas (Playbook Part III.6; Part VIII Step 6; v2
added in Step 10 [custom roadmap: Step 12]; v3 added in Step 14).

READ PART VII.1 BEFORE TOUCHING THIS FILE. The AI Scoring Pipeline was
staged across three versions specifically because `Score_Opportunity`'s
`SentimentScore` term couldn't feed scoring until Social Engine's wiring
existed — building the full formula before every input existed would
have meant either faking a term or shipping a step marked "complete"
with a literal gap in it. Every `_v1`/`_v2`-suffixed function here is a
deliberately staged slice of Part III.6's final formula, not a different
formula; `score_opportunity_v3` (this pass) is that formula's final,
unabridged form — the staging is now complete, not still in progress.

No black-box model anywhere in this file (Step 6's explicit constraint):
every term is a named, independently testable function, and every one of
Part III.6's formulas is implemented exactly as specified once its
inputs actually exist — the exceptions (`score_risk_v1`'s dropped Holder
term, `score_opportunity_v1`/`_v2`'s dropped Sentiment term) use
documented weight redistribution (explained in each function's own
docstring — the exact redistribution RULE differs between v1 and v2, not
just the numbers; see `score_opportunity_v2`'s docstring) rather than
silently changing what "the score" means. `score_opportunity_v3` has no
redistribution left in it at all — see its own docstring.
"""

from __future__ import annotations

import math

from analysis.core_engine import CoreResult
from analysis.holder_engine import HolderResult
from analysis.momentum_engine import MomentumResult
from analysis.security_engine import SecurityResult
from analysis.social_engine import SocialResult

# Part III.6's categorical tiers, in descending order (checked top to
# bottom, first match wins). The *bands* are final form, unchanged across
# v1/v2/v3 — only what feeds the score that lands in one of them changes.
TIER_BANDS: tuple[tuple[float, str], ...] = (
    (85.0, "Strong Profile"),
    (70.0, "Solid, Monitor"),
    (50.0, "Mixed Signals"),
    (30.0, "Elevated Risk"),
    (0.0, "Critical Risk"),
)

_HONEYPOT_SELL_TAX_THRESHOLD_PCT = 99.0  # matches Step 5's own threshold - not re-derived differently here


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Every Part III.6 formula ends in `clamp(0,100, ...)` - one shared
    implementation rather than four inline `max(0, min(100, ...))`s."""
    return max(lo, min(hi, value))


def vulnerability_penalty(security: SecurityResult) -> float:
    """
    Part III.6's `VulnerabilityPenalty` sub-formula — unchanged across
    v1/v2/v3, used by `score_risk_v1` here and by `score_risk_v2`/`_v3`
    once those exist (Steps 10/14 import this function, they don't
    reimplement it):

        100  if Honeypot, active Freeze Authority, or upgradeable proxy detected
        40   if Mint Authority still active
        20   if Ownership not renounced
        0    otherwise

    Read as a priority ladder (first match wins), not additive — a token
    that's both a honeypot and has an active mint authority is exactly
    as bad as one that's "just" a honeypot; the formula caps at 100, it
    doesn't go to 140.

    "Upgradeable proxy" is an EVM-specific concept (a Solidity proxy
    contract whose implementation can be swapped by whoever controls it)
    with no dedicated field in `SecurityResult` yet — Step 5's only
    provider (RugCheck) doesn't surface this, since Solana's own
    upgradeability model works differently. Not checked here; flagged as
    a known gap for whatever future EVM-focused security provider Part
    III.2's "Future Extensibility" note anticipates.

    A `degraded` SecurityResult (couldn't scan at all) is deliberately
    NOT treated as "assume the worst" (100) or "assume the best" (0) —
    both would misrepresent genuine uncertainty as a confirmed finding.
    Returns 20: the same magnitude as "ownership not renounced," a real
    but moderate, non-alarmist penalty for "we don't actually know."
    """
    if security.degraded:
        return 20.0
    if security.sell_tax_pct >= _HONEYPOT_SELL_TAX_THRESHOLD_PCT or security.freeze_authority_active:
        return 100.0
    if security.mint_authority_active:
        return 40.0
    if not security.ownership_renounced:
        return 20.0
    return 0.0


def score_risk_v1(security: SecurityResult) -> float:
    """
    v1 slice of `Score_Risk`. Full formula (Part III.6):
        0.3*(100-TrustScore) + 0.4*HolderConcentrationPenalty + 0.3*VulnerabilityPenalty

    Holder Engine doesn't exist until Step 8, so `HolderConcentrationPenalty`
    (weight 0.4) is unavailable. Proportional redistribution: the two
    surviving terms (TrustScore 0.3, Vulnerability 0.3) summed to 0.6 of
    the original 1.0 — scaling each by (1.0/0.6) so they again sum to 1.0
    preserves their original 1:1 ratio to each other:
        0.3 * (1.0/0.6) = 0.5   for both terms

        Score_Risk_v1 = clamp(0,100, 0.5*(100-TrustScore) + 0.5*VulnerabilityPenalty)

    Max case check: TrustScore=0, VulnerabilityPenalty=100 ->
    0.5*100 + 0.5*100 = 100. Still correctly reaches the full 0-100 range.
    """
    return _clamp(0.5 * (100.0 - security.trust_score) + 0.5 * vulnerability_penalty(security))


def score_confidence(core: CoreResult, security: SecurityResult) -> float:
    """
    Final form (Part III.6), unchanged in v2/v3:
        clamp(0,100, PoolAgeDays*1.5 + 40*CodeVerifiedBoolean)

    KNOWN GAP, not an oversight: `CodeVerifiedBoolean` has no source yet.
    It's an EVM concept (verified contract source matched against
    deployed bytecode, e.g. on an Etherscan-style explorer) that neither
    `CoreResult` nor `SecurityResult` currently expose — Step 5's only
    provider (RugCheck/Solana) doesn't have a direct analog, and adding
    a field for it is outside Step 6's declared Scope (new: scoring
    files only). Hardcoded `False` (contributes 0) here, with this
    docstring as the marker for whoever later adds real source-
    verification data (most plausibly alongside a future EVM security
    provider) to come update this function's one hardcoded line.

    `pool_age_days=None` (Step 4: the provider omitted a timestamp) is
    treated as 0 days — "we don't even know how old this pool is" is
    itself a low-confidence signal, not a reason to guess a favorable
    number.
    """
    pool_age_days = core.pool_age_days if core.pool_age_days is not None else 0.0
    code_verified = False  # see docstring - no data source for this yet
    return _clamp(pool_age_days * 1.5 + 40.0 * code_verified)


def score_opportunity_v1(core: CoreResult) -> float:
    """
    v1 slice of `Score_Opportunity`. Full formula (Part III.6):
        0.4*Score_Trend + 0.3*SentimentScore + 0.3*ln(Volume_24h)

    Momentum Engine (Score_Trend) doesn't exist until Step 9; Social
    Engine (SentimentScore) doesn't exist until Step 13. Only the
    0.3-weighted volume term survives. Proportional redistribution to
    fill the model's full 1.0 weight with the one remaining term:
        0.3 * (1.0/0.3) = 1.0

        Score_Opportunity_v1 = clamp(0,100, 1.0 * ln(Volume_24h))

    CORRECTION from this playbook's own Step 6 draft, found while
    implementing this step: the original spec text read
    "0.7*ln(Volume_24h)" for this redistribution. 0.7 doesn't satisfy
    "scaled to fill the weight" — it's neither the original 0.3 nor the
    fully-redistributed 1.0, and doesn't match the same proportional-
    redistribution method `score_risk_v1` above actually uses. 1.0 is
    the value consistent with the stated principle; implemented as such
    per Part VI's instruction to correct a detected error rather than
    silently follow it.

    Note this preserves a real property of Part III.6's original design,
    not a defect this function introduces: `ln` compresses volume so
    heavily that even an aggressively-redistributed coefficient of 1.0
    yields fairly modest scores across realistic volume ranges (ln of
    $1M is ~13.8; of $100M, ~18.4) — Score_Opportunity_v1 alone rarely
    approaches 100 on volume alone, by design of the underlying formula,
    not a bug in this redistribution.

    volume_24h <= 0 guards ln's undefined domain — a pair can genuinely
    report zero 24h volume (Step 4's CoreResult allows this); treated as
    contributing 0 rather than raising.
    """
    if core.volume_24h <= 0:
        return 0.0
    return _clamp(math.log(core.volume_24h))


def holder_concentration_penalty(holder: HolderResult) -> float:
    """
    Part III.6's `HolderConcentrationPenalty` sub-formula — named as a
    formula slot ("0.4*HolderConcentrationPenalty") but never given an
    exact HCI%-to-penalty mapping anywhere in the playbook; Part III.3
    only names the underlying metric and its 30% *flag* threshold, not a
    scoring curve. Claude's documented assumption (Part VI "on
    ambiguity"): direct 1:1 passthrough,

        HolderConcentrationPenalty = clamp(0, 100, HCI_pct)

    chosen over a stepped/curved mapping for the same reason
    `score_opportunity_v1`'s docstring favors a plain redistribution over
    an invented curve: a number a person can sanity-check directly
    ("62% concentration -> a 62-point penalty contribution") beats an
    opaque curve that would need its own separate justification, and
    Part III.3's own 30% "flag" framing already does the job of marking
    where this term starts being a *meaningful* contributor without this
    formula needing a matching kink to represent that.

    A degraded `HolderResult` contributes 0, not a guess — "unmeasured"
    isn't "assume concentrated," same principle `vulnerability_penalty`
    applies to a degraded `SecurityResult` (at 0 rather than a moderate
    default specifically because, unlike Security's several independent
    binary checks, there's no comparably-moderate "some evidence of a
    problem" reading available for a single missing percentage).
    """
    if holder.degraded:
        return 0.0
    return _clamp(holder.hci_pct)


def score_risk_v2(security: SecurityResult, holder: HolderResult) -> float:
    """
    Full, FINAL Part III.6 `Score_Risk` formula — unlike
    `Score_Opportunity`, `Score_Risk` only ever has three terms
    (TrustScore, HolderConcentrationPenalty, VulnerabilityPenalty), and
    Holder Engine (Step 8) supplies the one that was still missing. No
    redistribution needed, and no `score_risk_v3` waiting in a later
    step the way `score_opportunity` has one — this is the formula from
    here forward.

        Score_Risk = clamp(0,100, 0.3*(100-TrustScore)
                            + 0.4*HolderConcentrationPenalty
                            + 0.3*VulnerabilityPenalty)
    """
    return _clamp(
        0.3 * (100.0 - security.trust_score)
        + 0.4 * holder_concentration_penalty(holder)
        + 0.3 * vulnerability_penalty(security)
    )


def score_opportunity_v2(core: CoreResult, momentum: MomentumResult) -> float:
    """
    v2 slice of `Score_Opportunity`. Full formula (Part III.6):
        0.4*Score_Trend + 0.3*SentimentScore + 0.3*ln(Volume_24h)

    Momentum Engine (Step 9) now supplies `Score_Trend` directly as
    `MomentumResult.trending_score` — same figure, no rescaling of the
    figure itself. `SentimentScore` still doesn't feed scoring (Social
    Engine was built in Step 10 [custom roadmap]/Playbook Step 13, but
    that step's own Integration Requirements deliberately deferred
    wiring it into scoring — see `scan_orchestration.py`'s docstring for
    the same deferral applied one layer up).

    Redistribution rule change from v1, worth stating explicitly since
    it's NOT "rescale every surviving term proportionally" again: Trend
    returns to its own true, final 0.4 weight immediately, because real
    data now backs it — it doesn't need to borrow room from anything.
    Volume absorbs the entirety of Sentiment's STILL-missing 0.3 weight
    on top of its own original 0.3, landing at 0.6 (0.3 original + 0.3
    still-uncovered). The governing principle isn't "always split
    evenly" — it's "a term with real data behind it settles at its true
    final weight the moment that data exists; only whatever's STILL
    missing keeps needing to be covered by whichever term was already
    covering for it":

        Score_Opportunity_v2 = clamp(0,100, 0.4*Score_Trend + 0.6*ln(Volume_24h))

    This is also the one place a future v3 pass has an easy correctness
    check: when Sentiment finally arrives, Volume should drop back down
    to exactly its original 0.3 (not stay inflated), and Trend's 0.4
    should be unchanged from what's already here — v3 isn't a new
    redistribution, just Sentiment's own weight finally landing in the
    one slot that's been borrowed this whole time.

    volume_24h <= 0 guard: same reasoning as score_opportunity_v1.
    """
    volume_term = math.log(core.volume_24h) if core.volume_24h > 0 else 0.0
    return _clamp(0.4 * momentum.trending_score + 0.6 * volume_term)


_SENTIMENT_NEUTRAL_SCORE = 50.0  # sentiment_ratio == 0.0's own midpoint


def sentiment_score(social: SocialResult) -> float:
    """
    Part III.6's `SentimentScore` sub-formula — like
    `holder_concentration_penalty` before it, never given an exact
    mapping from `SocialResult.sentiment_ratio`'s -1.0..1.0 scale onto
    whatever scale `Score_Opportunity`'s own 0.3-weighted term expects.
    Claude's documented assumption (Part VI "on ambiguity"): a plain
    linear rescale onto Score_Opportunity's own 0-100 scale, the same
    kind of directly sanity-checkable choice `holder_concentration_penalty`
    already made over an invented curve:

        SentimentScore = clamp(0, 100, (sentiment_ratio + 1.0) * 50.0)

    sentiment_ratio = -1.0 (uniformly negative) -> 0; 0.0 (neutral) -> 50;
    +1.0 (uniformly positive) -> 100.

    This is a separate use of the same underlying sentiment_ratio from
    `momentum_engine._social_momentum`'s — that function feeds a much
    smaller (0.15-weighted) SocialMomentum term inside Score_Trend
    itself; this one feeds SentimentScore directly at its own full 0.3
    weight inside Score_Opportunity. Part III.6 genuinely uses the same
    underlying signal through two different lenses at two different
    weights — this is that design working as specified, not duplicated
    logic to deduplicate.

    A degraded SocialResult (couldn't resolve an account, or nothing
    resolvable at all) contributes the neutral midpoint (50.0), not 0 —
    same "unmeasured isn't a confirmed finding in either direction"
    principle `vulnerability_penalty`/`holder_concentration_penalty`
    already apply to their own degraded inputs. 0 here would read as
    "confirmed uniformly negative sentiment," which is not what a
    degraded result means.
    """
    if social.degraded:
        return _SENTIMENT_NEUTRAL_SCORE
    return _clamp((social.sentiment_ratio + 1.0) * 50.0)


def score_opportunity_v3(core: CoreResult, momentum: MomentumResult, social: SocialResult) -> float:
    """
    Full, FINAL Part III.6 `Score_Opportunity` formula — the last of the
    three staged versions (Part VII.1). No redistribution logic left in
    this function at all, unlike `_v1`/`_v2`: every term is at its own
    true final weight, because every input now genuinely exists.
    `score_opportunity_v2`'s own docstring predicted exactly this moment:
    Volume drops back down from v2's inflated 0.6 to its original 0.3;
    Trend's 0.4 is unchanged from v2; Sentiment finally lands in the one
    slot Volume had been borrowing.

        Score_Opportunity = clamp(0,100, 0.4*Score_Trend + 0.3*SentimentScore
                                    + 0.3*ln(Volume_24h))

    volume_24h <= 0 guard: same reasoning as `score_opportunity_v1`/`_v2`.
    """
    volume_term = math.log(core.volume_24h) if core.volume_24h > 0 else 0.0
    return _clamp(0.4 * momentum.trending_score + 0.3 * sentiment_score(social) + 0.3 * volume_term)


def score_ai(score_opportunity: float, score_confidence: float, score_risk: float) -> float:
    """Final form (Part III.6), unchanged across v1/v2/v3 — only the three
    inputs' own staging changes, never this combination:
        clamp(0,100, (0.70*Score_Opportunity + 0.30*Score_Confidence) - Score_Risk)
    """
    return _clamp((0.70 * score_opportunity + 0.30 * score_confidence) - score_risk)


def tier_label(ai_score: float) -> str:
    """Part III.6's categorical tiers. Checked highest-to-lowest, first
    match wins; the final (0.0, "Critical Risk") entry always matches
    for any value >= 0, so this never falls through without a label."""
    for threshold, label in TIER_BANDS:
        if ai_score >= threshold:
            return label
    return TIER_BANDS[-1][1]  # unreachable given clamp(0,100) upstream, kept as an explicit safety net
