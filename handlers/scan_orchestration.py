"""
Layer: Handlers — scan orchestration (Playbook Part VIII Step 7; Holder
Engine wired in concurrently with Security in the Step 8 integration
pass; Momentum Engine wired in sequentially after both in the Step 9
pass; Scoring v2 + the Risk x Opportunity Matrix wired in during the
Step 10 [custom roadmap: Step 12] pass; Social Engine + Scoring v3
wired in during Step 14).

The actual "call Core, then (Security + Holder + Social), then Momentum,
then Scoring" logic, deliberately split out of `scan_handler.py` with
zero Telegram/aiogram import — same rationale as Steps 4-5's provider/
parser splits: this is pure input-to-output orchestration, and keeping
it that way means it's testable with plain fakes, not just syntax-
checked. `scan_handler.py` wraps this with the actual Telegram
message-editing calls around it, via the optional progress hooks below.

Dependency direction (checked explicitly during the Step 8 integration
pass, still true here): this module imports nothing from `rendering.*`,
and never will — `rendering/result_renderer.py` imports `ScoredResult`
FROM here, which is a one-way dependency (rendering depends on the
handler layer's data shape, not the reverse), not a cycle. See that
module's docstring for the same note from its side.

Scoring is v3 as of this pass: `scoring_pipeline.score(core, security,
holder=holder_result, momentum=momentum_result, social=social_result)`
now supplies every one of the five engine outputs Step 6's own signature
always accepted. Deriving `handle_or_ticker` for `SocialEngine.analyze`:
`PairData` (Step 4) exposes `base_token_symbol`/`quote_token_symbol` but
no dedicated social-link field, so this uses `base_token_symbol` — the
scanned token's own ticker, not the pairing currency — as the best-
effort handle/ticker per Step 13's own spec ("sourced from CoreResult's
pair metadata where DexScreener exposes a social link, falling back to
a ticker-based search... document whichever fallback path is actually
available"). Imperfect (a project's ticker and its real X handle aren't
always identical) but the only field this phase's data actually
supports; `SocialEngine.analyze` degrades gracefully (Step 13's own
contract) rather than crashing when the guess doesn't resolve to a real
account.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from analysis.core_engine import CoreEngine, CoreResult
from analysis.holder_engine import HolderEngine, HolderResult
from analysis.momentum_engine import MomentumEngine, MomentumResult
from analysis.security_engine import SecurityEngine, SecurityResult
from analysis.social_engine import SocialEngine, SocialResult
from bot.constants import Chain
from scoring.pipeline import ScoringPipeline, ScoringResult
from scoring.risk_opportunity_matrix import RiskOpportunityTier, classify
from state.session_store import SessionStore

ProgressHook = Callable[[], Awaitable[None]]


@dataclass
class ScoredResult:
    """Part VIII Step 7's output shape, extended with `holder` (Step 8),
    `momentum` (Step 9), `risk_opportunity` (Step 10 [custom roadmap:
    Step 12] — NOT a Playbook field; see
    `scoring/risk_opportunity_matrix.py`'s module docstring for why it's
    additive to, never a replacement for, `scoring.score_risk`/
    `score_opportunity`, both of which stay exactly as they were), and
    now `social` (Step 14). `result_id` is the `SessionStore.cache_put`
    key this exact object is stored under (Part II.7 §2's UUID-cache
    tier) — set after construction, once the real key is known (see
    `run_scan`'s docstring for why this is a two-step build: a data-
    construction ordering detail, not an import cycle — this module
    never imports from `rendering.*`, see module docstring)."""

    core: CoreResult
    security: SecurityResult
    holder: HolderResult
    momentum: MomentumResult
    social: SocialResult
    scoring: ScoringResult
    risk_opportunity: RiskOpportunityTier
    result_id: str = ""


async def run_scan(
    raw_address: str,
    core_engine: CoreEngine,
    security_engine: SecurityEngine,
    holder_engine: HolderEngine,
    momentum_engine: MomentumEngine,
    social_engine: SocialEngine,
    scoring_pipeline: ScoringPipeline,
    session_store: SessionStore,
    chain_hint: Chain | None = None,
    on_core_complete: ProgressHook | None = None,
    on_security_complete: ProgressHook | None = None,
    on_holder_complete: ProgressHook | None = None,
    on_social_complete: ProgressHook | None = None,
    on_momentum_complete: ProgressHook | None = None,
) -> ScoredResult:
    """
    Chains Core -> (Security || Holder || Social) -> Momentum -> Scoring
    (v3) -> Risk/Opportunity classification.

    Security, Holder, and Social all consume only `core_result` and never
    each other's output, so all three now run concurrently via
    `asyncio.gather` (Step 8's reasoning for the original pair, extended
    to three per Step 14's own Integration Requirements: "Momentum and
    Social can run in parallel with each other and with Holder"). Momentum
    stays outside the gather, unchanged from the Step 9/10 passes:
    `MomentumEngine.compute`'s signature takes `holder: HolderResult` as
    a required positional argument, so it cannot start until the gather
    above resolves, and it is synchronous (no provider, no I/O), so it
    was never a candidate for the gather regardless.

    `on_core_complete` fires once, right after Core finishes (before the
    concurrent trio starts). `on_security_complete`/`on_holder_complete`/
    `on_social_complete` each fire independently, right when THAT
    engine's own call returns — since the three run concurrently against
    real network calls, any can finish first. `on_momentum_complete`
    fires once, immediately after the (instant, synchronous) momentum
    computation. All hooks are optional zero-argument async callables;
    tests pass `None` (the default) or recording fakes, nothing here
    needs a real Message to run.

    Never raises past this function for engine-level failures —
    `CoreEngine.analyze`, `SecurityEngine.analyze`, `HolderEngine.analyze`,
    and `SocialEngine.analyze` all already degrade rather than raise
    (Steps 4/5/8/13's own contracts); `MomentumEngine.compute` mirrors
    that by setting `degraded=True` whenever either of its two required
    inputs was itself degraded (Step 9's own contract — Social's own
    degraded state doesn't additionally flip Momentum's flag, since
    Momentum's `degraded` field per its own spec only ever reflects
    `core`/`holder`, not the optional `social` argument), never raising
    either. A fully degraded `ScoredResult` is a normal, valid return
    value, not an error path a caller needs to catch separately.
    `scoring_pipeline.score` itself never sees a degraded engine result
    as a reason to raise either — `score_risk_v2`/`score_opportunity_v3`'s
    own formulas already treat a degraded input as contributing a defined
    neutral value (see each formula function's own docstring), not as an
    error state this function needs to special-case.

    `risk_opportunity` is computed from the SAME `scoring_result.score_risk`/
    `score_opportunity` values already on `ScoringResult` — classification
    only, never a recomputation (module docstring's "additive, not a
    replacement" note applies here at the call-site level too).
    """
    core_result = await core_engine.analyze(raw_address, chain_hint=chain_hint)
    if on_core_complete is not None:
        await on_core_complete()

    async def _run_security() -> SecurityResult:
        result = await security_engine.analyze(core_result)
        if on_security_complete is not None:
            await on_security_complete()
        return result

    async def _run_holder() -> HolderResult:
        result = await holder_engine.analyze(core_result)
        if on_holder_complete is not None:
            await on_holder_complete()
        return result

    async def _run_social() -> SocialResult:
        """STEP 14 VERIFICATION FIX: `SecurityEngine.analyze` and
        `HolderEngine.analyze` both short-circuit on an unresolved
        `core_result` (chain/primary_pair is None or core_result.degraded)
        rather than touching their provider — this call site derived a
        handle from `core_result.primary_pair.base_token_symbol` without
        that same guard, so a fully-degraded scan (no address could be
        resolved at all) crashed with an AttributeError on `None
        .base_token_symbol` instead of producing the degraded-but-complete
        `ScoredResult` `run_scan`'s own docstring promises. Caught by
        `test_scan_orchestration.py::test_run_scan_degraded_core_still_produces_complete_result`
        under real execution — see docs/STEP14_STEP15_HANDOFF.md."""
        if core_result.degraded or core_result.primary_pair is None:
            result = SocialResult(
                x_score=0,
                verified_follower_ratio=0.0,
                tweet_frequency_per_day=0.0,
                influencer_mention_count=0,
                sentiment_ratio=0.0,
                follower_growth_pct=0.0,
                degraded=True,
                degraded_reason="No resolved pair to derive a social handle from.",
            )
        else:
            result = await social_engine.analyze(core_result.primary_pair.base_token_symbol)
        if on_social_complete is not None:
            await on_social_complete()
        return result

    security_result, holder_result, social_result = await asyncio.gather(
        _run_security(), _run_holder(), _run_social()
    )

    momentum_result = momentum_engine.compute(core_result, holder_result, social=social_result)
    if on_momentum_complete is not None:
        await on_momentum_complete()

    scoring_result = scoring_pipeline.score(
        core_result, security_result, holder=holder_result, momentum=momentum_result, social=social_result
    )
    risk_opportunity = classify(scoring_result.score_risk, scoring_result.score_opportunity)

    scored = ScoredResult(
        core=core_result,
        security=security_result,
        holder=holder_result,
        momentum=momentum_result,
        social=social_result,
        scoring=scoring_result,
        risk_opportunity=risk_opportunity,
    )
    result_id = session_store.cache_put(scored)
    scored.result_id = result_id
    return scored
