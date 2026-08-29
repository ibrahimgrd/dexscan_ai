"""
Playbook reference: Unified Developer Playbook, Part VIII Step 7 - Unit
Testing Requirements: "render_result_detail snapshot test against a known
ScoredResult fixture, asserting exact section presence/order."

Requires aiogram (RenderedMessage.keyboard is a real InlineKeyboardMarkup)
- confirmed installable and this file confirmed passing for real in the
Step 11 session's sandbox; prior sessions' sandboxes had no network
access to do this and left it syntax-checked only against
result_renderer.py's actual source - see README.md's Step 11 entry.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from analysis.core_engine import CoreResult
from analysis.holder_engine import HolderResult
from analysis.momentum_engine import MomentumResult
from analysis.security_engine import SecurityResult
from analysis.social_engine import SocialResult
from bot.constants import Chain, TradingBot
from handlers.scan_orchestration import ScoredResult
from integrations.providers import BOT_PROVIDERS
from rendering.result_renderer import (
    render_result_detail,
    render_result_list,
    render_trade_link_ready,
    render_trade_staging,
)
from scoring.pipeline import ScoringResult
from scoring.risk_opportunity_matrix import classify


def _core_result(**overrides) -> CoreResult:
    from analysis.api_abstraction import PairData

    pair = PairData(
        chain=Chain.SOL, dex_id="raydium", pair_address="pair1",
        base_token_address="Tok1111111111111111111111111111111111111111",
        base_token_symbol="TEST", base_token_name="Test Token", quote_token_symbol="SOL",
        price_usd=0.01, liquidity_usd=250_000.0, fdv=2_000_000.0, market_cap=1_500_000.0,
        volume_5m=100.0, volume_1h=1000.0, volume_6h=5000.0, volume_24h=180_000.0,
        price_change_5m=0.5, price_change_1h=2.0, price_change_6h=4.0, price_change_24h=12.0,
        buys_24h=200, sells_24h=150, pair_created_at_ms=1_700_000_000_000,
    )
    base = dict(
        address="Tok1111111111111111111111111111111111111111", chain=Chain.SOL, primary_pair=pair,
        liquidity_usd=250_000.0, market_cap=1_500_000.0, fdv=2_000_000.0, dilution_ratio=0.75,
        volume_24h=180_000.0, pool_age_days=30.0, price_change={"5m": 0.5, "1h": 2.0, "6h": 4.0, "24h": 12.0},
        buy_pressure_pct=57.1,
    )
    base.update(overrides)
    return CoreResult(**base)


def _security_result(**overrides) -> SecurityResult:
    base = dict(
        trust_score=85.0, risk_level="Low Risk", mint_authority_active=False,
        freeze_authority_active=False, buy_tax_pct=0.0, sell_tax_pct=0.0,
        lp_lock_ratio_pct=100.0, lp_lock_duration_days=180.0, ownership_renounced=True,
        scam_flags=[], provider_notes=[],
    )
    base.update(overrides)
    return SecurityResult(**base)


def _holder_result(**overrides) -> HolderResult:
    base = dict(
        holder_count=18, holder_growth_24h_pct=20.0, hci_pct=22.4, whale_count=1,
        classified_wallets={}, insider_bundle_detected=False, insider_bundle_wallet_count=0,
        holder_count_is_estimate=True,
    )
    base.update(overrides)
    return HolderResult(**base)


def _momentum_result(**overrides) -> MomentumResult:
    base = dict(
        volume_growth_pct=0.0, liquidity_growth_pct=0.0, price_momentum=16.0,
        buy_momentum=60.0, whale_momentum=0.0, social_momentum=0.0, trending_score=12.8,
    )
    base.update(overrides)
    return MomentumResult(**base)


def _social_result(**overrides) -> SocialResult:
    base = dict(
        x_score=60, verified_follower_ratio=0.5, tweet_frequency_per_day=2.0,
        influencer_mention_count=1, sentiment_ratio=0.35, follower_growth_pct=0.0,
    )
    base.update(overrides)
    return SocialResult(**base)


def _scoring_result(**overrides) -> ScoringResult:
    base = dict(
        score_opportunity=65.0, score_risk=10.0, score_confidence=45.0, score_ai=72.5,
        tier_label="Solid, Monitor",
        explanation=["No mint/freeze authority, tax, or ownership red flags detected.", "Pool age: 30.0 days."],
        pipeline_version="v3",
    )
    base.update(overrides)
    return ScoringResult(**base)


def _scored_result(**overrides) -> ScoredResult:
    scoring = overrides.get("scoring", _scoring_result())
    base = dict(
        core=_core_result(), security=_security_result(), holder=_holder_result(),
        momentum=_momentum_result(), social=_social_result(), scoring=scoring,
        risk_opportunity=classify(scoring.score_risk, scoring.score_opportunity),
        result_id="abc123",
    )
    base.update(overrides)
    return ScoredResult(**base)


def _all_callback_data(rendered) -> set[str]:
    return {
        b.callback_data
        for row in rendered.keyboard.inline_keyboard
        for b in row
        if b.callback_data is not None
    }


def _all_urls(rendered) -> set[str]:
    return {b.url for row in rendered.keyboard.inline_keyboard for b in row if b.url is not None}


# ---------------------------------------------------------------------------
# render_result_detail — snapshot-style section presence/order
# ---------------------------------------------------------------------------


def test_result_detail_contains_every_populatable_section_in_order() -> None:
    """Part VIII Step 7 Definition of Done: every field Steps 4-6 can
    populate is rendered, plus explicit not-yet-available placeholders
    for Holder/Momentum/Social, in a stable order."""
    rendered = render_result_detail(_scored_result())
    html = rendered.html

    header_idx = html.find("AI Intel Report")
    score_idx = html.find("AI Score")
    core_idx = html.find("Core Metrics")
    security_idx = html.find("Security")
    holder_idx = html.find("Holder Analysis")
    momentum_idx = html.find("Momentum")
    social_idx = html.find("Social Signals")

    assert -1 not in (header_idx, score_idx, core_idx, security_idx, holder_idx, momentum_idx, social_idx)
    assert header_idx < score_idx < core_idx < security_idx < holder_idx < momentum_idx < social_idx


def test_result_detail_risk_level_and_ai_score_are_separate_lines() -> None:
    """Part III.6's rule, and this step's own Acceptance Criteria: never
    combined into one figure."""
    rendered = render_result_detail(_scored_result())
    lines = rendered.html.split("\n")
    ai_score_lines = [l for l in lines if "AI Score" in l]
    risk_level_lines = [l for l in lines if "Low Risk" in l or "Trust Score" in l]
    assert ai_score_lines and risk_level_lines
    assert ai_score_lines[0] != risk_level_lines[0]


def test_risk_opportunity_matrix_section_shows_live_label_and_both_raw_scores() -> None:
    """Custom roadmap Step 10 [Step 12]: the matrix section renders the
    real classification and both underlying axis scores - not a static
    placeholder, and not a number with no traceable source."""
    scoring = _scoring_result(score_risk=20.0, score_opportunity=80.0)
    rendered = render_result_detail(_scored_result(scoring=scoring))
    html = rendered.html

    assert "Risk / Opportunity Read" in html
    assert "Strong Foundation, High Interest" in html  # Low risk / High opportunity per the real classify() call
    assert "Risk: 20/100" in html
    assert "Opportunity: 80/100" in html


def test_risk_opportunity_matrix_section_does_not_replace_existing_separate_signals() -> None:
    """The new section is additive - Risk Level (inside Security) and AI
    Score must still both be present and still be separate from each
    other AND from the new matrix line, per
    risk_opportunity_matrix.py's own 'additive, not a replacement'
    design note."""
    rendered = render_result_detail(_scored_result())
    html = rendered.html

    assert "AI Score" in html
    assert "Trust Score" in html  # Risk Level's own section, still present
    assert "Risk / Opportunity Read" in html
    # all three appear as genuinely separate lines, not merged into one
    lines_with_a_score_word = [
        line for line in html.split("\n")
        if "AI Score" in line or "Trust Score" in line or "Risk / Opportunity Read" in line
    ]
    assert len(lines_with_a_score_word) == 3


def test_risk_opportunity_matrix_section_appears_between_ai_score_and_core_metrics() -> None:
    rendered = render_result_detail(_scored_result())
    html = rendered.html
    score_idx = html.find("AI Score")
    matrix_idx = html.find("Risk / Opportunity Read")
    core_idx = html.find("Core Metrics")
    assert score_idx < matrix_idx < core_idx


def test_result_detail_has_zero_not_yet_available_placeholders() -> None:
    """Holder went live in the Step 8 integration pass, Momentum in the
    Step 9 pass, Social in this Step 14 pass — this was the last of
    Step 7's four "not yet available" slots (Trading's own button was
    never this kind of text placeholder; Step 11 gave it a real,
    always-rendered button from the start). Matches Step 14's own
    Definition of Done verbatim: "Step 7's rendering has zero remaining
    'not yet available' placeholders anywhere in Result Detail." This
    test used to assert the opposite (exactly one such placeholder,
    Social's own) — inverted here rather than deleted, since a zero-
    placeholder assertion is exactly the kind of thing worth guarding
    against silently regressing back to a placeholder later."""
    rendered = render_result_detail(_scored_result())
    assert "Not yet available" not in rendered.html


def test_social_section_shows_live_data_not_placeholder() -> None:
    social = _social_result(x_score=77, sentiment_ratio=0.6, influencer_mention_count=3)
    rendered = render_result_detail(_scored_result(social=social))
    assert "77" in rendered.html
    assert "Positive" in rendered.html
    assert "+0.60" in rendered.html


def test_social_section_shows_unavailable_reason_when_degraded() -> None:
    degraded = _social_result(degraded=True, degraded_reason="account not found")
    rendered = render_result_detail(_scored_result(social=degraded))
    assert "account not found" in rendered.html
    assert "Not yet available" not in rendered.html  # degraded, not unbuilt - different wording on purpose


def test_holder_section_shows_live_data_not_placeholder() -> None:
    """Ask #5: HCI/Top-10 concentration, holder count, whale count, and
    insider-cluster status must all come from the real HolderResult, not
    a static string."""
    holder = _holder_result(
        hci_pct=63.7, whale_count=5, holder_count=20,
        insider_bundle_detected=True, insider_bundle_wallet_count=4,
    )
    rendered = render_result_detail(_scored_result(holder=holder))
    html = rendered.html

    assert "arrives in a later build step" not in html.split("Holder Analysis")[1].split("Momentum")[0]
    assert "63.7%" in html
    assert "Whales (>1.5% each): 5" in html
    assert "4 wallets funded together at launch" in html


def test_holder_section_degraded_shows_reason_not_fabricated_numbers() -> None:
    """Same Part IV.3 partial-failure contract _core_section/
    _security_section already have: a degraded Holder result shows its
    reason, not a zero-value metric dressed up as real data."""
    degraded_holder = HolderResult(
        holder_count=0, holder_growth_24h_pct=0.0, hci_pct=0.0, whale_count=0,
        degraded=True, degraded_reason="Holder analysis is currently available for Solana only, not ETH.",
    )
    rendered = render_result_detail(_scored_result(holder=degraded_holder))
    assert "Holder analysis is currently available for Solana only, not ETH." in rendered.html


def test_holder_section_dev_balance_is_honest_gap_not_fabricated() -> None:
    """HolderResult has no dev/deployer-wallet field (analysis/
    holder_engine.py's own module docstring: no off-chain labeled-wallet
    source in this build) - the renderer must say so plainly rather than
    inventing a number, the same "no fabricated metrics" rule
    test_result_detail_degraded_core_shows_reason_not_fabricated_numbers
    already enforces for Core."""
    rendered = render_result_detail(_scored_result(holder=_holder_result()))
    holder_section = rendered.html.split("Holder Analysis")[1].split("Momentum")[0]
    assert "Dev Balance" in holder_section
    assert "not available" in holder_section
    dev_balance_line = [line for line in holder_section.split("\n") if "Dev Balance" in line][0]
    assert not any(char.isdigit() for char in dev_balance_line)


def _momentum_section_text(rendered) -> str:
    return rendered.html.split("Momentum</b>")[1].split("Social Signals")[0]


def test_momentum_section_shows_live_data_not_placeholder() -> None:
    """Step 9 ask: Trending Score, price/buy momentum must come from the
    real MomentumResult, not a static string."""
    momentum = _momentum_result(trending_score=42.5, price_momentum=-8.3, buy_momentum=25.0)
    rendered = render_result_detail(_scored_result(momentum=momentum))
    section = _momentum_section_text(rendered)

    assert "arrives in a later build step" not in section
    assert "+42.5" in section
    assert "-8.3" in section
    assert "+25.0" in section


def test_momentum_section_degraded_shows_generic_reason_not_fabricated_numbers() -> None:
    """MomentumResult carries no degraded_reason field of its own (Step
    9's interface - `degraded` only ever mirrors Core's or Holder's own
    degraded state, and those sections already show their specific
    reason elsewhere on the same screen) - the section must still say
    *something* generic rather than rendering blank or a fabricated
    number."""
    degraded_momentum = MomentumResult(
        volume_growth_pct=0.0, liquidity_growth_pct=0.0, price_momentum=0.0,
        buy_momentum=0.0, whale_momentum=0.0, social_momentum=0.0, trending_score=0.0,
        degraded=True,
    )
    rendered = render_result_detail(_scored_result(momentum=degraded_momentum))
    section = _momentum_section_text(rendered)
    assert "Unavailable" in section
    assert not any(char.isdigit() for char in section)


def test_momentum_section_documented_gap_fields_are_honest_not_fabricated() -> None:
    """volume_growth_pct/liquidity_growth_pct/whale_momentum are a
    documented, permanent 0.0 in this build (analysis/momentum_engine.py's
    own docstring - no stored history to diff against, a structural gap,
    not "coming later"). The renderer must not print these as if "0.0"
    were a real reading - same "no fabricated metrics" principle as
    Holder's Dev Balance line (Step 8)."""
    rendered = render_result_detail(_scored_result(momentum=_momentum_result()))
    section = _momentum_section_text(rendered)
    assert "not measurable without stored history" in section
    # None of the three documented-gap fields get their own numeric line.
    assert "Volume Growth: 0.0" not in section
    assert "Liquidity Growth: 0.0" not in section
    assert "Whale Momentum: 0.0" not in section


def test_momentum_section_social_momentum_reflects_real_value_once_nonzero() -> None:
    """Step 14: the Social Engine now always runs, so social_momentum is a
    real computed reading on every scan, not a "coming later" placeholder
    sentinel - see rendering/result_renderer.py's own Step 14 comment. A
    exactly-neutral 0.0 is therefore a legitimate real value and must render
    as a real number like any other, the same as a nonzero reading."""
    zero_social = render_result_detail(_scored_result(momentum=_momentum_result(social_momentum=0.0)))
    section = _momentum_section_text(zero_social)
    assert "not available yet" not in section
    assert "Social Momentum: 0.0" in section

    real_social = render_result_detail(_scored_result(momentum=_momentum_result(social_momentum=33.0)))
    section = _momentum_section_text(real_social)
    assert "not available yet" not in section
    assert "+33.0" in section


def test_result_detail_degraded_core_shows_reason_not_fabricated_numbers() -> None:
    degraded_core = _core_result(
        primary_pair=None, chain=None, liquidity_usd=0.0, market_cap=0.0, fdv=0.0,
        dilution_ratio=None, volume_24h=0.0, pool_age_days=None, price_change={},
        degraded=True, degraded_reason="No trading pairs found for this address.",
    )
    rendered = render_result_detail(_scored_result(core=degraded_core))
    assert "No trading pairs found for this address." in rendered.html
    assert "$0.00" not in rendered.html  # must not print fabricated zero-value metrics


def test_result_detail_has_explorer_link_matching_chain() -> None:
    rendered = render_result_detail(_scored_result())
    urls = _all_urls(rendered)
    assert any("solscan.io" in u for u in urls)


def test_result_detail_no_explorer_link_when_chain_unresolved() -> None:
    degraded_core = _core_result(chain=None, primary_pair=None, degraded=True, degraded_reason="test")
    rendered = render_result_detail(_scored_result(core=degraded_core))
    assert _all_urls(rendered) == set()


def test_result_detail_buy_button_carries_result_id() -> None:
    """Real as of Step 11 (was Step 7's deliberately inert placeholder,
    a bare 'exec_stage' matching nothing - see git history / this
    file's own prior version). Carries the same result_id every other
    Result Detail button already does, so TradeStagingHandler resolves
    the exact ScoredResult this screen is showing, not a stale one."""
    rendered = render_result_detail(_scored_result(result_id="xyz789"))
    assert "exec_stage:xyz789" in _all_callback_data(rendered)


def test_result_detail_rescan_and_back_carry_the_result_id() -> None:
    rendered = render_result_detail(_scored_result(result_id="xyz789"))
    callbacks = _all_callback_data(rendered)
    assert "scan_rescan:xyz789" in callbacks
    assert "result_back_list:xyz789" in callbacks


def test_result_detail_escapes_adversarial_token_name() -> None:
    hostile_pair_core = _core_result()
    hostile_pair_core.primary_pair.base_token_name = "<b>Evil</b> & Co"
    hostile_pair_core.primary_pair.base_token_symbol = "<script>"
    rendered = render_result_detail(_scored_result(core=hostile_pair_core))
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


def test_result_detail_includes_scoring_explanation_lines() -> None:
    rendered = render_result_detail(_scored_result())
    assert "No mint/freeze authority, tax, or ownership red flags detected." in rendered.html


# ---------------------------------------------------------------------------
# render_result_list
# ---------------------------------------------------------------------------


def test_result_list_empty_shows_no_matches() -> None:
    rendered = render_result_list([])
    assert "No matches" in rendered.html


def test_result_list_single_result_no_pagination_controls() -> None:
    rendered = render_result_list([_scored_result()])
    callbacks = _all_callback_data(rendered)
    assert not any(c.startswith("result_page:") for c in callbacks)


def test_result_list_button_carries_result_id_and_score() -> None:
    rendered = render_result_list([_scored_result(result_id="rid1")])
    callbacks = _all_callback_data(rendered)
    assert "result_view:rid1" in callbacks


def test_result_list_pagination_appears_beyond_five_results() -> None:
    results = [_scored_result(result_id=f"r{i}") for i in range(7)]
    rendered = render_result_list(results, page=0)
    callbacks = _all_callback_data(rendered)
    assert "result_page:1" in callbacks
    # page 0 shows exactly 5 result buttons + nav row + back/home row
    result_buttons = [c for c in callbacks if c.startswith("result_view:")]
    assert len(result_buttons) == 5


def test_result_list_page_out_of_range_clamps_not_crashes() -> None:
    results = [_scored_result(result_id=f"r{i}") for i in range(7)]
    rendered = render_result_list(results, page=99)
    assert isinstance(rendered.keyboard, InlineKeyboardMarkup)  # didn't raise


def test_result_list_back_target_is_scan_menu() -> None:
    rendered = render_result_list([_scored_result()])
    assert "nav_scan" in _all_callback_data(rendered)


# ---------------------------------------------------------------------------
# render_trade_staging / render_trade_link_ready (Step 11 - moved here from
# rendering/menus.py's Step-3 _SampleResult placeholder; see that module's
# own docstring and this file's module docstring)
# ---------------------------------------------------------------------------


def test_trade_staging_shows_chosen_bot_name_and_chain() -> None:
    rendered = render_trade_staging(_scored_result(), TradingBot.PHOTON)
    assert "Photon" in rendered.html
    assert "SOL" in rendered.html


def test_trade_staging_has_approve_change_and_abort_buttons() -> None:
    rendered = render_trade_staging(_scored_result(), TradingBot.TROJAN)
    callbacks = _all_callback_data(rendered)
    assert {"exec_approve", "exec_change_bot", "exec_abort"} <= callbacks


def test_trade_staging_back_target_is_result_detail() -> None:
    """Part II.9's screen table: Trade Staging's own Back target,
    reserved since Step 3 (rendering/menus.py's module docstring,
    deviation #2) and real as of this step."""
    rendered = render_trade_staging(_scored_result(), TradingBot.TROJAN)
    assert "result_back_detail" in _all_callback_data(rendered)


def test_trade_staging_warns_when_bot_not_operational() -> None:
    """BullX specifically, per integrations/providers.py's own
    Verification Status note (suspended 2026-06-01) - the warning text
    must actually reach the screen, not just exist as a data field
    nobody renders."""
    assert not BOT_PROVIDERS[TradingBot.BULLX].is_operational  # guards against the fixture itself drifting
    rendered = render_trade_staging(_scored_result(), TradingBot.BULLX)
    assert "\u26a0" in rendered.html  # warning icon
    assert "suspended" in rendered.html.lower() or "2026-06" in rendered.html


def test_trade_staging_no_warning_for_an_operational_bot() -> None:
    rendered = render_trade_staging(_scored_result(), TradingBot.TROJAN)
    assert "\u26a0" not in rendered.html


def test_trade_staging_escapes_adversarial_token_symbol() -> None:
    hostile_core = _core_result()
    hostile_core.primary_pair.base_token_symbol = "<script>"
    rendered = render_trade_staging(_scored_result(core=hostile_core), TradingBot.TROJAN)
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


def test_trade_link_ready_has_a_url_button_to_the_supplied_link() -> None:
    deep_link = "https://t.me/solana_trojanbot?start=dexscan_Tok1"
    rendered = render_trade_link_ready(_scored_result(), TradingBot.TROJAN, deep_link)
    assert deep_link in _all_urls(rendered)


def test_trade_link_ready_has_no_callback_that_could_re_trigger_approval() -> None:
    """Part IV.1's Acceptance Criteria in spirit: once a link exists, this
    screen's only actions are 'open the link' (a url= button, never a
    callback DexScan AI's own dispatcher would route) and Home - nothing
    here can call approve_and_get_link a second time."""
    rendered = render_trade_link_ready(_scored_result(), TradingBot.TROJAN, "https://t.me/x?start=y")
    assert "exec_approve" not in _all_callback_data(rendered)


def test_trade_link_ready_shows_bot_and_token_name() -> None:
    rendered = render_trade_link_ready(_scored_result(), TradingBot.GMGN, "https://t.me/gmgnaibot?start=dexscan_x")
    assert "GMGN" in rendered.html
    assert "TEST" in rendered.html  # base_token_symbol from _core_result's fixture pair
