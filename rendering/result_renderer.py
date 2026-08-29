"""
Layer: Rendering — result screens (Playbook Part VIII Step 7; screen
inventory in Part II.9. Holder section switched from placeholder to live
`HolderResult` data in the Step 8 integration pass; Momentum section
switched to live `MomentumResult` data in the Step 9 integration pass;
Trade Staging — `render_trade_staging`/`render_trade_link_ready` — moved
here from `rendering/menus.py`'s Step-3 `_SampleResult` placeholder in
this Step 11 integration pass, the same move Result List/Detail
themselves got in Step 7; see `rendering/menus.py`'s own module
docstring for the same note from its side).

Replaces `rendering/menus.py`'s Step-3 `_SampleResult`-based placeholders
for Result List, Result Detail, and (as of Step 11) Trade Staging with
the real thing, built from `handlers.scan_orchestration.ScoredResult`.

Dependency direction (checked explicitly during the Step 8 integration
pass, still true here): this module imports `ScoredResult` FROM
`handlers.scan_orchestration`. That module imports nothing back from
here — a one-way dependency, not a cycle. See that module's docstring
for the same note from its side.

Same purity contract as every other render_* function (Part V.2): known
data in, `RenderedMessage` out, no engine calls, no FSM access. Reuses
`rendering.menus`'s `HOME_CALLBACK`/`escape_html`/`RenderedMessage`
rather than duplicating them — this file is an extension of the
rendering layer, not a parallel one.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from analysis.core_engine import CoreResult
from analysis.holder_engine import HolderResult
from analysis.momentum_engine import MomentumResult
from analysis.security_engine import SecurityResult
from analysis.social_engine import SocialResult
from bot.constants import Chain, TradingBot, WHALE_HOLDER_THRESHOLD_PCT
from handlers.scan_orchestration import ScoredResult
from integrations.providers import BOT_PROVIDERS
from rendering.html_utils import escape_html
from rendering.menus import DISCLAIMER, HOME_CALLBACK, RenderedMessage
from scoring.pipeline import ScoringResult
from scoring.risk_opportunity_matrix import RiskOpportunityTier

_RESULTS_PER_PAGE = 5

# Public block explorers, one per supported chain (Part II.6) — stable,
# long-established URL conventions, not the kind of versioned API detail
# Steps 4-5 needed to verify live; used here only as an outbound `url=`
# button (Telegram opens it client-side, no callback/handler involved).
_EXPLORER_URL_TEMPLATE: dict[Chain, str] = {
    Chain.SOL: "https://solscan.io/token/{address}",
    Chain.ETH: "https://etherscan.io/token/{address}",
    Chain.BSC: "https://bscscan.com/token/{address}",
    Chain.BASE: "https://basescan.org/token/{address}",
    Chain.ARB: "https://arbiscan.io/token/{address}",
    Chain.TON: "https://tonscan.org/address/{address}",
}


def _back_home_row(back_callback: str) -> list[InlineKeyboardButton]:
    """Same visual convention as `rendering.menus._footer_rows` (a single
    combined button when Back and Home would be identical, two when they
    differ) — reimplemented at this small scale rather than importing a
    private helper across files, since here `back_callback` is dynamic
    per-result (carries a result_id), unlike menus.py's static per-screen
    lookup table."""
    if back_callback == HOME_CALLBACK:
        return [InlineKeyboardButton(text="\U0001f3e0 Home", callback_data=HOME_CALLBACK)]
    return [
        InlineKeyboardButton(text="\u25c0\ufe0f Back", callback_data=back_callback),
        InlineKeyboardButton(text="\U0001f3e0 Home", callback_data=HOME_CALLBACK),
    ]


def _format_usd(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.2f}"


def _format_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def _format_signed(value: float) -> str:
    """Like `_format_pct`, minus the trailing '%' — for Momentum's
    unbounded, non-percentage figures (trending_score, price_momentum,
    buy_momentum, social_momentum) so they don't read as percentages of
    something they aren't."""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}"


def _core_section(core: CoreResult) -> str:
    if core.degraded:
        return (
            "\U0001f4ca <b>Core Metrics</b>\n"
            f"Unavailable — {escape_html(core.degraded_reason or 'unknown reason')}"
        )

    lines = [
        "\U0001f4ca <b>Core Metrics</b>",
        f"Liquidity: {_format_usd(core.liquidity_usd)}",
        f"Market Cap: {_format_usd(core.market_cap)}",
        f"FDV: {_format_usd(core.fdv)}"
        + (f" (Dilution: {core.dilution_ratio:.0%})" if core.dilution_ratio is not None else ""),
        f"24h Volume: {_format_usd(core.volume_24h)}",
        f"Pool Age: {core.pool_age_days:.1f} days" if core.pool_age_days is not None else "Pool Age: unknown",
        f"Buy Pressure: {core.buy_pressure_pct:.0f}%",
    ]
    if core.price_change:
        change_parts = [f"{tf}: {_format_pct(v)}" for tf, v in core.price_change.items()]
        lines.append("Price Change: " + " / ".join(change_parts))
    return "\n".join(lines)


def _security_section(security: SecurityResult) -> str:
    if security.degraded:
        return (
            "\U0001f512 <b>Security</b>\n"
            f"Unavailable — {escape_html(security.degraded_reason or 'unknown reason')}"
        )

    lines = [
        "\U0001f512 <b>Security</b>",
        f"Trust Score: {security.trust_score:.0f}/100 ({escape_html(security.risk_level)})",
        f"Mint Authority: {'Active' if security.mint_authority_active else 'Renounced'}",
        f"Freeze Authority: {'Active' if security.freeze_authority_active else 'Renounced'}",
        f"Tax: Buy {security.buy_tax_pct:.1f}% / Sell {security.sell_tax_pct:.1f}%",
        (
            f"LP Locked: {security.lp_lock_ratio_pct:.0f}%"
            if security.lp_lock_ratio_pct is not None
            else "LP Locked: unknown"
        ),
        f"Ownership: {'Renounced' if security.ownership_renounced else 'Not renounced'}",
    ]
    return "\n".join(lines)


def _holder_section(holder: HolderResult) -> str:
    """Part VIII Step 8 -> this integration pass: live `HolderResult`
    data, replacing the Step-7 placeholder. Mirrors `_core_section` /
    `_security_section`'s degrade-first shape exactly, including reusing
    "Unavailable — {reason}" for a runtime-degraded result (chain not
    supported, provider outage) — kept textually distinct from
    `_social_section`'s own "Unavailable (...)" wording only in that this
    one's reason always describes a real failed lookup, never a feature
    that doesn't exist yet (there's no such feature left in this
    renderer as of Step 14 — Holder was the first of the four Step-7
    placeholders to go, in the Step 8 pass; Social was the last, in
    this one).

    "Top 10 Concentration" and "HCI" are the same underlying number
    (`holder_engine.py`'s own docstring: "HCI = sum of top-10 holders' %
    of supply") — shown as one line, not two, so the two labels don't
    silently duplicate one figure with no indication they're identical.

    Dev/deployer wallet balance is NOT rendered as a number: it isn't a
    field this engine's free-tier data source produces at all (see
    `analysis/holder_engine.py`'s module docstring — off-chain
    wallet-labeling data no RPC endpoint exposes on its own). Shown as an
    explicit, honest gap instead of a fabricated figure — the same
    "explicit placeholder, never silent omission" rule Step 7 applied to
    this entire section before live data existed, and the same principle
    `test_result_detail_degraded_core_shows_reason_not_fabricated_numbers`
    already enforces elsewhere in this renderer.
    """
    if holder.degraded:
        return (
            "\U0001f465 <b>Holder Analysis</b>\n"
            f"Unavailable — {escape_html(holder.degraded_reason or 'unknown reason')}"
        )

    count_label = f"~{holder.holder_count}" if holder.holder_count_is_estimate else f"{holder.holder_count}"
    lines = [
        "\U0001f465 <b>Holder Analysis</b>",
        f"Top 10 Concentration (HCI): {holder.hci_pct:.1f}%",
        f"Holders Tracked: {count_label}" + (" (top accounts only)" if holder.holder_count_is_estimate else ""),
        f"Whales (>{WHALE_HOLDER_THRESHOLD_PCT:g}% each): {holder.whale_count}",
        f"New Holders (24h, top-holder proxy): {holder.holder_growth_24h_pct:.0f}%",
    ]

    if holder.insider_bundle_detected:
        lines.append(
            f"\u26a0\ufe0f Insider Cluster: {holder.insider_bundle_wallet_count} wallets funded together at launch"
        )
    else:
        lines.append("Insider Cluster: None detected")

    burn_count = sum(1 for label in holder.classified_wallets.values() if label == "burn")
    if burn_count:
        lines.append(f"Burned/Incinerator Wallets: {burn_count}")

    lines.append("Dev Balance: not available (no labeled deployer wallet source in this build)")

    return "\n".join(lines)


def _momentum_section(momentum: MomentumResult) -> str:
    """Part VIII Step 9 -> this integration pass: live `MomentumResult`
    data, replacing the Step-7 placeholder.

    Unlike `_core_section`/`_security_section`/`_holder_section`,
    `MomentumResult` carries no `degraded_reason` field — Step 9's own
    interface deliberately doesn't duplicate one, since `degraded` here
    only ever mirrors Core's or Holder's own degraded state (see
    `analysis/momentum_engine.py`'s `MomentumResult` docstring); those
    two sections already show their own specific reason elsewhere on the
    same screen, so this one stays generic rather than re-deriving a
    reason it was never given.

    Three of `MomentumResult`'s six fields (`volume_growth_pct`,
    `liquidity_growth_pct`, `whale_momentum`) are a documented, permanent
    0.0 in this build — not "coming later" the way Social is, but a
    structural gap this stateless phase can't close (no stored history to
    diff against; see the engine's own module docstring). Shown as one
    honest line, not three fabricated-looking zero values sprinkled
    through the section — the same "explicit gap, never a fabricated
    number" rule Step 8's Dev Balance line already established for this
    renderer.

    `trending_score`/`price_momentum`/`buy_momentum` are signed, unbounded
    figures (Part III.5's own formula never clamps them the way Part
    III.6's higher-level scores do) — formatted with `_format_signed`,
    never a trailing "%", since they aren't percentages.
    """
    if momentum.degraded:
        return (
            "\U0001f4c8 <b>Momentum</b>\n"
            "Unavailable — underlying scan data (Core or Holder) was incomplete for this token."
        )

    lines = [
        "\U0001f4c8 <b>Momentum</b>",
        f"Trending Score: {_format_signed(momentum.trending_score)}",
        f"Price Acceleration (1h vs 6h trend): {_format_signed(momentum.price_momentum)}",
        f"Buy Pressure Momentum: {_format_signed(momentum.buy_momentum)}",
        # Step 14: Social Engine now always runs, so social_momentum is
        # always a real reading, 0.0 included (neutral sentiment is a
        # genuine outcome, not an absence of data) — the old
        # `!= 0.0 else "not available yet"` branch here would now
        # misreport a real neutral reading as missing data, so it's gone.
        f"Social Momentum: {_format_signed(momentum.social_momentum)}",
        "Volume / Liquidity / Whale trend: not measurable without stored history in this build",
    ]

    return "\n".join(lines)


def _score_section(scoring: ScoringResult) -> str:
    explanation = "\n".join(f"\u2022 {escape_html(line)}" for line in scoring.explanation)
    return (
        f"AI Score: <b>{scoring.score_ai:.0f}/100</b> \u2014 {escape_html(scoring.tier_label)}\n\n"
        f"<b>Why this score:</b>\n{explanation}"
    )


def _risk_opportunity_matrix_section(tier: RiskOpportunityTier, score_risk: float, score_opportunity: float) -> str:
    """Custom roadmap Step 10 [Step 12] — NOT a Playbook Part III.6
    section. Renders as an ADDITIONAL, clearly separate block, never
    merged into `_score_section` above or `_security_section`'s own Risk
    Level line — both of those stay completely unchanged by this
    function's existence (`scoring/risk_opportunity_matrix.py`'s module
    docstring explains why this distinction matters). Both raw axis
    scores are always shown alongside the label specifically so the
    combined read never hides the two numbers that produced it."""
    return (
        f"\U0001f3af <b>Risk / Opportunity Read</b>\n"
        f"{escape_html(tier.label)} ({tier.risk_band} Risk / {tier.opportunity_band} Opportunity)\n"
        f"{escape_html(tier.description)}\n"
        f"Risk: {score_risk:.0f}/100 \u00b7 Opportunity: {score_opportunity:.0f}/100"
    )


# Placeholder retired as of this Step 14 pass — see `_social_section`
# below. Holder's own placeholder went first (Step 8), Momentum's next
# (Step 9); this is the last of the four Step-7 "not yet available"
# slots (Trading's own was never this kind of placeholder — Step 11
# gave it a real, always-rendered button from the start).
def _social_section(social: SocialResult) -> str:
    """Part VIII Step 13 -> this integration pass: live `SocialResult`
    data, replacing the Step-7 placeholder — same shape as
    `_holder_section`/`_momentum_section` before it: a degraded check
    first, then real lines, then one honest line for the field this
    build structurally can't populate (see `SocialResult`'s own
    docstring for why `follower_growth_pct` is a permanent, not a
    coming-later, gap here).

    `degraded=True` covers both "account lookup failed" and "account
    resolved to nothing" (Step 13's own `SocialEngine.analyze` contract)
    — this section doesn't need to distinguish the two itself, same as
    `_holder_section`/`_momentum_section` don't distinguish their own
    engines' different degrade causes beyond the one `degraded_reason`
    string.

    `verified_follower_ratio` is shown as a percentage (Part III.4 gives
    it 0.0-1.0; this renderer's own naming convention note applies the
    same *100 display-only conversion `_holder_section` already uses for
    HCI)."""
    if social.degraded:
        return (
            "\U0001f426 <b>Social Signals</b>\n"
            f"Unavailable ({escape_html(social.degraded_reason or 'account could not be resolved')})."
        )

    sentiment_word = (
        "Positive" if social.sentiment_ratio > 0 else "Negative" if social.sentiment_ratio < 0 else "Neutral"
    )
    lines = [
        "\U0001f426 <b>Social Signals</b>",
        f"X Score: {social.x_score}/100",
        f"Verified Follower Ratio: {social.verified_follower_ratio * 100:.1f}%",
        f"Tweet Frequency: {social.tweet_frequency_per_day:.1f}/day",
        f"Influencer Mentions: {social.influencer_mention_count}",
        f"Sentiment: {sentiment_word} ({social.sentiment_ratio:+.2f})",
        "Follower Growth: not measurable — twitterapi.io exposes no per-follower join date to diff against",
    ]
    return "\n".join(lines)


def render_result_list(results: list[ScoredResult], page: int = 0) -> RenderedMessage:
    """
    Paginated, 5 per page (Part II.5's site map). In this build, a scan
    only ever produces one `ScoredResult` (Step 7 wires single-address
    paste only — Trending/New Listings discovery, which could produce
    more, isn't wired to any engine yet); pagination is still implemented
    for real rather than assumed-away, since the signature commits to it
    and a one-item list is just the page_count==1 case of the same logic.
    """
    total_pages = max(1, (len(results) + _RESULTS_PER_PAGE - 1) // _RESULTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_items = results[page * _RESULTS_PER_PAGE : (page + 1) * _RESULTS_PER_PAGE]

    if not results:
        body = "No matches for this scan."
        rows: list[list[InlineKeyboardButton]] = []
    else:
        body = f"{len(results)} result(s):"
        rows = [
            [
                InlineKeyboardButton(
                    text=(
                        f"{escape_html(r.core.primary_pair.base_token_symbol) if r.core.primary_pair else '?'} "
                        f"\u2014 {r.scoring.score_ai:.0f} ({escape_html(r.scoring.tier_label)})"
                    ),
                    callback_data=f"result_view:{r.result_id}",
                )
            ]
            for r in page_items
        ]
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="\u25c0\ufe0f Prev", callback_data=f"result_page:{page - 1}"))
            nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="Next \u25b6\ufe0f", callback_data=f"result_page:{page + 1}"))
            rows.append(nav_row)

    html = f"\U0001f4cb <b>Results</b>\n\n{escape_html(body)}"
    rows.extend([_back_home_row("nav_scan")])
    return RenderedMessage(html=html, keyboard=InlineKeyboardMarkup(inline_keyboard=rows))


def render_result_detail(result: ScoredResult) -> RenderedMessage:
    """
    Every field Steps 4-6 (Core/Security/Scoring), Step 8 (Holder), and
    Step 9 (Momentum) can actually populate (Part VIII Step 7's
    Definition of Done, extended twice), plus the custom-roadmap Risk/
    Opportunity Matrix (Step 10 [Step 12] — not a Playbook section; see
    `_risk_opportunity_matrix_section`'s own docstring). Risk Level
    (inside the Security section) and AI Score are STILL separate lines,
    never combined into one figure (Part III.6's rule, unchanged by the
    new matrix section, which adds a labeled cross-reference alongside
    both rather than replacing either).
    """
    core, security, holder, momentum, social, scoring = (
        result.core, result.security, result.holder, result.momentum, result.social, result.scoring
    )

    if core.primary_pair is not None:
        name = escape_html(core.primary_pair.base_token_name)
        symbol = escape_html(core.primary_pair.base_token_symbol)
    else:
        name, symbol = "Unknown Token", "?"

    chain_label = escape_html(core.chain.value.upper()) if core.chain is not None else "Unknown Chain"
    address_line = f"<code>{escape_html(core.address)}</code>"

    html = "\n\n".join(
        [
            f"\U0001f9e0 <b>AI Intel Report</b>\n\n<b>{name}</b> ({symbol}) \u2014 {chain_label}\n{address_line}",
            _score_section(scoring),
            _risk_opportunity_matrix_section(result.risk_opportunity, scoring.score_risk, scoring.score_opportunity),
            _core_section(core),
            _security_section(security),
            _holder_section(holder),
            _momentum_section(momentum),
            _social_section(social),
        ]
    )

    rows: list[list[InlineKeyboardButton]] = []

    explorer_template = _EXPLORER_URL_TEMPLATE.get(core.chain) if core.chain is not None else None
    if explorer_template is not None:
        rows.append(
            [InlineKeyboardButton(text="\U0001f50d View on Explorer", url=explorer_template.format(address=core.address))]
        )

    # Real as of the Step 11 integration pass — was a deliberately inert
    # placeholder through Step 7-10 (this comment used to say "Deferred
    # to Step 11"; see git history / the Step 7 README entry for that
    # wording). Carries `result.result_id` so `TradeStagingHandler` can
    # resolve the same cached `ScoredResult` this screen is already
    # showing, the identical `{command}:{uuid}` shape `result_view`
    # already established in Step 3 — not a parallel convention.
    rows.append(
        [InlineKeyboardButton(text="\u26a1 Buy via Preferred Bot", callback_data=f"exec_stage:{result.result_id}")]
    )

    rows.append([InlineKeyboardButton(text="\U0001f504 Rescan", callback_data=f"scan_rescan:{result.result_id}")])
    rows.append(_back_home_row(f"result_back_list:{result.result_id}"))

    return RenderedMessage(html=html, keyboard=InlineKeyboardMarkup(inline_keyboard=rows))


def render_watch_alert(result: ScoredResult) -> RenderedMessage:
    """Custom-roadmap Step 12 [Step 13, Auto-Watch]. Part II.8's one
    documented exception to "single evolving message": Auto-Watch alerts
    are sent as a NEW message each time, never an edit — there's no
    single in-progress operation to update in place, and editing a
    previous alert would erase the trail of everything already found
    this session (`handlers.auto_watch`'s own module docstring notes the
    same exception from the sending side). Deliberately short — a full
    scan result already exists at Result Detail, one tap away via the
    same `result_view:{uuid}` callback shape Step 3 established (not a
    parallel format — Step 12's own Unit Testing Requirement) — this
    message's only job is to say a match was found and get the person
    there fast."""
    core, scoring = result.core, result.scoring
    if core.primary_pair is not None:
        name = escape_html(core.primary_pair.base_token_name)
        symbol = escape_html(core.primary_pair.base_token_symbol)
    else:
        name, symbol = "Unknown Token", "?"

    html = (
        "\U0001f6a8 <b>Auto-Watch Match</b>\n\n"
        f"<b>{name}</b> ({symbol})\n"
        f"AI Score: {scoring.score_ai:.0f}/100 \u2014 {escape_html(scoring.tier_label)}\n"
        f"{escape_html(result.risk_opportunity.label)}"
    )
    rows = [[InlineKeyboardButton(text="\U0001f4c4 View Full Report", callback_data=f"result_view:{result.result_id}")]]
    return RenderedMessage(html=html, keyboard=InlineKeyboardMarkup(inline_keyboard=rows))


# ---------------------------------------------------------------------------
# Trade Staging (Step 11 — real data, replacing rendering/menus.py's
# Step-3 `_SampleResult` placeholder; see that module's own docstring)
# ---------------------------------------------------------------------------


def render_trade_staging(result: ScoredResult, bot: TradingBot) -> RenderedMessage:
    """
    `bot` is whichever `TradingBot` is currently staged in the caller's
    FSM payload (`handlers.trade_staging_handler`'s own module docstring
    — `enter_trade_staging` seeds it, `change_target_bot` cycles it);
    this function has no opinion on which one that is and never picks a
    default itself, same purity contract every other `render_*` function
    here holds itself to (Part V.2: "known data in, RenderedMessage out").

    Shows an explicit non-operational warning (`BotProvider.status_note`)
    rather than silently offering a bot that can't actually execute right
    now — currently only reachable for BullX; see
    `integrations/providers.py`'s Verification Status note for why it's
    still in the six-bot rotation at all despite that.
    """
    provider = BOT_PROVIDERS[bot]
    core, scoring = result.core, result.scoring

    symbol = escape_html(core.primary_pair.base_token_symbol) if core.primary_pair is not None else "?"
    chain_label = escape_html(core.chain.value.upper()) if core.chain is not None else "Unknown Chain"

    warning_line = ""
    if not provider.is_operational:
        reason = provider.status_note or "This bot may be temporarily unavailable."
        warning_line = f"\n\u26a0\ufe0f {escape_html(reason)}\n"

    html = (
        "\U0001f6a6 <b>Trade Staging</b>\n\n"
        f"Target: <b>{symbol}</b> on {chain_label}\n"
        f"Bot: <b>{escape_html(provider.display_name)}</b>\n"
        f"AI Score: {scoring.score_ai:.0f}/100 \u2014 {escape_html(scoring.tier_label)}\n"
        f"{warning_line}\n"
        f"{escape_html(DISCLAIMER)} Approving opens your chosen bot with this token "
        "pre-filled \u2014 nothing executes here."
    )
    rows = [
        [InlineKeyboardButton(text="\u2705 Approve & Open Bot", callback_data="exec_approve")],
        [InlineKeyboardButton(text="\U0001f504 Change Target Bot", callback_data="exec_change_bot")],
        [InlineKeyboardButton(text="\u274c Abort", callback_data="exec_abort")],
    ]
    rows.append(_back_home_row("result_back_detail"))
    return RenderedMessage(html=html, keyboard=InlineKeyboardMarkup(inline_keyboard=rows))


def render_trade_link_ready(result: ScoredResult, bot: TradingBot, deep_link: str) -> RenderedMessage:
    """
    The screen shown immediately after a real `approve_and_get_link`
    call — `deep_link` is that call's own return value, passed straight
    through into a Telegram `url=` button (the same client-side-only
    mechanism this file's own "View on Explorer" button already uses;
    see `integrations/trading.py`'s module docstring for why DexScan AI
    itself never opens or fetches this URL). This is the last screen the
    Trade Staging flow ever shows — the FSM is already back at Idle by
    the time this renders (`handlers.trade_staging_handler
    .approve_and_get_link`'s own docstring).
    """
    provider = BOT_PROVIDERS[bot]
    core = result.core
    symbol = escape_html(core.primary_pair.base_token_symbol) if core.primary_pair is not None else "?"

    html = (
        "\u2705 <b>Ready</b>\n\n"
        f"Tap below to open <b>{escape_html(provider.display_name)}</b> with <b>{symbol}</b> pre-filled.\n\n"
        f"{escape_html(DISCLAIMER)} DexScan AI's part ends at this link \u2014 the trade itself "
        "happens entirely inside the bot you're about to open."
    )
    rows = [
        [InlineKeyboardButton(text=f"\u26a1 Open {escape_html(provider.display_name)}", url=deep_link)],
        [InlineKeyboardButton(text="\U0001f3e0 Home", callback_data=HOME_CALLBACK)],
    ]
    return RenderedMessage(html=html, keyboard=InlineKeyboardMarkup(inline_keyboard=rows))
