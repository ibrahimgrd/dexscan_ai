"""
Layer: Rendering (Playbook Part VIII Step 3; screen inventory in Part II.9,
site map in Part II.5).

One `render_*` function per screen in Part II.9's table. Every function is
pure: known data in, `RenderedMessage` out. Nothing here calls an external
API, imports an analysis/scoring engine, or calls `FSMEngine` itself - a
handler fetches whatever context it needs and passes it in as a plain
argument (Part V.2 separation of concerns; Part VIII Step 3 constraint:
"a menu builder never imports an engine").

Two deviations from a literal reading of the playbook, flagged per the
"flag it explicitly" rule rather than silently resolved:

1. Home vs Welcome. Part II.9 lists "Welcome" as almost every screen's
   Home target, but Step 2 already established the "nav_main" callback as
   the universal Home action, landing on Main Menu. Re-showing the
   onboarding/disclaimer screen every time someone taps Home from deep in
   the app is worse UX than Part II.9's table literally implies, and
   Welcome is a one-time gateway, not a destination worth repeatedly
   returning to. This module treats "Home" as "top of the Idle cluster"
   (Main Menu) everywhere except Main Menu's own Back button, which does
   go to Welcome specifically, per Part II.9's Main Menu row.
2. Result List / Result Detail moved to `rendering/result_renderer.py` in
   Step 7, once real `CoreResult`/`SecurityResult`/`ScoringResult` data
   existed to render — this file's own versions were Step 3 placeholders
   only, per Part VIII Step 3's "no live data connections yet" constraint,
   and are gone from here now (not duplicated). Trade Staging's own
   Step-3 placeholder (`_SampleResult`) moved out the same way in Step
   11, once a real `ScoredResult` + selected `TradingBot` existed to
   render it against — see `rendering/result_renderer.py`'s
   `render_trade_staging`/`render_trade_link_ready` for the real thing.
   Its Back-button callback_data ("result_back_detail") was reserved
   here since Step 3 and is now claimed by
   `handlers.trade_staging_handler.TradeStagingHandler`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.constants import FSMState
from bot.types import AutoWatchStatus, EngineStatus
from rendering.html_utils import escape_html
from state.fsm import FSMContext

if TYPE_CHECKING:
    from analysis.filter_presets import FilterProfile
    from bot.types import UserSettings

DISCLAIMER = (
    "DexScan AI never holds your keys, never custodies funds, and never "
    "executes trades."
)


@dataclass
class RenderedMessage:
    """One screen, ready to send/edit: HTML body + its inline keyboard."""

    html: str
    keyboard: InlineKeyboardMarkup


# ---------------------------------------------------------------------------
# Back/Home footer construction (Part II.5 / II.9 / IV.4)
# ---------------------------------------------------------------------------

# Every screen's Back-button destination. Values are callback_data strings,
# not screen names, since that's what the button actually needs to carry.
# "welcome" is absent on purpose - Part II.9 gives it no Back target.
_SCREEN_BACK_TARGET: dict[str, str] = {
    "main_menu": "nav_welcome",  # the one screen where Back != Home (see module docstring)
    "scan_menu": "nav_main",
    "filter_config": "nav_scan",
    "advanced_rules": "nav_filters",  # Step 12 revalidation addition - Part
                                       # II.5: Advanced Rules nests under
                                       # Filter & Analysis Config, not Scan Menu
    # "trade_staging" removed in Step 11 - real rendering (and its own
    # Back-row construction) moved to rendering/result_renderer.py, so
    # this table no longer has an entry for it (module docstring).
    "my_filters": "nav_main",
    "recent_results": "nav_main",
    "auto_watch": "nav_main",
    "settings": "nav_main",
    "help": "nav_main",
    "about": "nav_main",
    # Step 15 additions - all three nest under the Help hub (site map:
    # Help -> FAQ / Tutorial / Security Basics), never under Main Menu
    # directly, so Back from any of them returns to the hub they came
    # from, not two levels up at once.
    "help_faq_answer": "nav_help",
    "tutorial": "nav_help",
    "security_basics": "nav_help",
}

HOME_CALLBACK = "nav_main"


def _footer_rows(screen: str) -> list[list[InlineKeyboardButton]]:
    """Builds the Back/Home row(s) for `screen`, per `_SCREEN_BACK_TARGET`
    and the two special cases Part II.9 calls out: Welcome has neither;
    Scanning collapses both into a single Cancel action."""
    if screen == "welcome":
        return []
    if screen == "scanning":
        return [[InlineKeyboardButton(text="\u274c Cancel", callback_data="scan_cancel")]]

    back_target = _SCREEN_BACK_TARGET.get(screen, HOME_CALLBACK)
    row = [InlineKeyboardButton(text="\u25c0\ufe0f Back", callback_data=back_target)]
    if back_target != HOME_CALLBACK:
        row.append(InlineKeyboardButton(text="\U0001f3e0 Home", callback_data=HOME_CALLBACK))
    return [row]


def _kb(*rows: list[InlineKeyboardButton], screen: str) -> InlineKeyboardMarkup:
    """Assembles a screen's own button rows plus its footer into one
    keyboard - the one place every render_* function routes through, so
    Back/Home placement (Part IV.4) can never be forgotten on a new screen."""
    return InlineKeyboardMarkup(inline_keyboard=[*rows, *_footer_rows(screen)])


# ---------------------------------------------------------------------------
# Welcome / Main Menu
# ---------------------------------------------------------------------------


def render_welcome() -> RenderedMessage:
    html = (
        "\U0001f6f0\ufe0f <b>DexScan AI</b>\n\n"
        "A read-only token intelligence layer across six chains: Solana, "
        "Ethereum, BNB Chain, Base, Arbitrum, and TON.\n\n"
        f"<b>{escape_html(DISCLAIMER)}</b> Every analysis ends with "
        "information, never an action taken for you."
    )
    rows = [[InlineKeyboardButton(text="\u25b6\ufe0f Enter DexScan AI", callback_data="nav_main")]]
    return RenderedMessage(html=html, keyboard=_kb(*rows, screen="welcome"))


def render_main_menu(ctx: FSMContext) -> RenderedMessage:
    watching = ctx.state is FSMState.AUTO_WATCH_ACTIVE
    status_line = (
        "\U0001f441 Auto-Watch is running." if watching else "Ready to scan."
    )
    html = (
        "\U0001f3e0 <b>Main Menu</b>\n\n"
        f"{escape_html(status_line)}"
    )
    rows = [
        [InlineKeyboardButton(text="\U0001f50d Scan Now", callback_data="nav_scan")],
        [
            InlineKeyboardButton(text="\U0001f4ca My Filters", callback_data="nav_filters"),
            InlineKeyboardButton(text="\U0001f553 Recent", callback_data="nav_recent"),
        ],
        [
            InlineKeyboardButton(text="\U0001f441 Auto-Watch", callback_data="nav_watch"),
            InlineKeyboardButton(text="\u2699\ufe0f Settings", callback_data="nav_settings"),
        ],
        [
            InlineKeyboardButton(text="\u2753 Help", callback_data="nav_help"),
            InlineKeyboardButton(text="\u2139\ufe0f About", callback_data="nav_about"),
        ],
    ]
    return RenderedMessage(html=html, keyboard=_kb(*rows, screen="main_menu"))


# ---------------------------------------------------------------------------
# Scan Menu / Filter Config / Scanning Progress
# ---------------------------------------------------------------------------


def render_scan_menu() -> RenderedMessage:
    html = (
        "\U0001f50d <b>Scan Menu</b>\n\n"
        "Pick a scan type, or paste a contract address directly."
    )
    rows = [
        [InlineKeyboardButton(text="\U0001f525 Trending Pairs", callback_data="scan_trending")],
        [InlineKeyboardButton(text="\U0001f195 New Listings", callback_data="scan_new")],
        [InlineKeyboardButton(text="\U0001f3af Custom Filter", callback_data="nav_custom_filter")],
        [InlineKeyboardButton(text="\u2328\ufe0f Paste Contract Address", callback_data="scan_paste")],
    ]
    return RenderedMessage(html=html, keyboard=_kb(*rows, screen="scan_menu"))


_FILTER_PRESETS: tuple[tuple[str, str], ...] = (
    ("conservative", "\U0001f6e1\ufe0f Conservative"),
    ("balanced", "\u2696\ufe0f Balanced"),
    ("aggressive", "\U0001f680 Aggressive"),
    ("custom", "\U0001f527 Custom"),
)


def render_filter_config(selected_preset: str | None = None) -> RenderedMessage:
    """`selected_preset`, when given, is a real `FilterProfile.name`
    (Step 12 [custom roadmap: Step 13] made these three presets real —
    `analysis.filter_presets.NAMED_PRESETS`). "Custom" is real as of the
    Step 12 revalidation pass too — see `render_advanced_rules` for the
    actual rule-by-rule editing screen this one links to."""
    from analysis.filter_presets import NAMED_PRESETS

    if selected_preset and selected_preset in NAMED_PRESETS:
        profile = NAMED_PRESETS[selected_preset]
        body = (
            f"Selected: <b>{escape_html(selected_preset.title())}</b>\n"
            f"Min liquidity: ${profile.min_liquidity_usd:,.0f} · "
            f"Min pool age: {profile.min_pool_age_hours:.0f}h · "
            f"Max tax: {profile.max_tax_pct:.0f}%\n\n"
        )
    elif selected_preset == "custom":
        body = "Selected: <b>Custom</b>. Open Advanced Rules to review or change it.\n\n"
    elif selected_preset:
        body = f"Selected: <b>{escape_html(selected_preset.title())}</b>.\n\n"
    else:
        body = ""
    html = (
        "\U0001f3af <b>Filter & Analysis Config</b>\n\n"
        f"{body}"
        "Choose a preset to use with Auto-Watch, or open Advanced Rules "
        "to tune every threshold yourself."
    )
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"rule_preset:{key}")]
        for key, label in _FILTER_PRESETS
    ]
    rows.append([InlineKeyboardButton(text="\u270f\ufe0f Advanced Rules", callback_data="rule_advanced")])
    return RenderedMessage(html=html, keyboard=_kb(*rows, screen="filter_config"))


# field_key -> display label, for Advanced Rules' toggle rows. Kept here
# (not in analysis/filter_presets.py) because every other UI label in this
# module lives here too - Part V.2's "rendering never leaks into analysis,
# analysis never leaks into rendering" cuts both ways.
_TOGGLE_LABELS: dict[str, str] = {
    "freeze": "Reject active freeze authority",
    "concentration": "Reject high holder concentration",
    "social": "Require verified social presence",
}
# field_key -> (display label, unit-formatting callable), for Advanced
# Rules' numeric cycle rows.
_NUMERIC_LABELS: dict[str, tuple[str, Callable[[object], str]]] = {
    "liquidity": ("Min liquidity", lambda v: f"${v:,.0f}"),
    "age": ("Min pool age", lambda v: f"{v:.0f}h"),
    "tax": ("Max combined tax", lambda v: f"{v:.0f}%"),
    "mktcap": ("Market cap band", lambda v: f"${v[0]:,.0f}\u2013${v[1]:,.0f}"),
}


def render_advanced_rules(profile: "FilterProfile") -> RenderedMessage:
    """The real rule-by-rule Custom editing screen `render_filter_config`
    used to describe as not built yet (Step 12 revalidation). `profile`
    is this user's in-progress draft (`handlers.watch_handler`'s
    `custom_filter_draft` payload key) - always a `FilterProfile`, never
    None, since every handler path here seeds one via
    `analysis.filter_presets.default_custom_profile` before this is ever
    called. Toggle rows show the action a tap performs (matching
    `analysis.filter_presets.set_bool_field`'s explicit-target-value
    design); numeric rows show the current value and cycle on tap
    (`cycle_numeric_field` - no explicit target needed, see that
    function's own docstring for why a stale double-tap there is
    harmless in a way a boolean flip wouldn't be)."""
    from analysis.filter_presets import (
        NUMERIC_FIELD_KEYS,
        TOGGLE_FIELD_KEYS,
        get_numeric_value,
        get_toggle_value,
    )

    html = (
        "\u270f\ufe0f <b>Advanced Rules</b> — Custom profile\n\n"
        "Honeypot rejection is always on for every profile, including "
        "this one, and isn't shown below as a toggle - it's a floor, not "
        "a setting. Tap any row to change it; nothing applies to your "
        "Auto-Watch until you save.\n"
    )

    rows: list[list[InlineKeyboardButton]] = []
    for field_key in TOGGLE_FIELD_KEYS:
        current = get_toggle_value(profile, field_key)
        icon = "\u2705" if current else "\u2b1c"
        target = "off" if current else "on"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {_TOGGLE_LABELS[field_key]}",
                    callback_data=f"rule_tgl:{field_key}:{target}",
                )
            ]
        )

    for field_key in NUMERIC_FIELD_KEYS:
        label, fmt = _NUMERIC_LABELS[field_key]
        current_value = get_numeric_value(profile, field_key)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{label}: {fmt(current_value)} (tap to change)",
                    callback_data=f"rule_num:{field_key}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text="\U0001f4be Save & Use for Auto-Watch", callback_data="rule_save")])
    return RenderedMessage(html=html, keyboard=_kb(*rows, screen="advanced_rules"))


_ENGINE_DISPLAY_ORDER: tuple[str, ...] = ("core", "security", "holder", "momentum", "social")
_ENGINE_STATUS_ICON: dict[EngineStatus, str] = {
    EngineStatus.PENDING: "\u26aa",
    EngineStatus.RUNNING: "\U0001f7e1",
    EngineStatus.DONE: "\U0001f7e2",
    EngineStatus.FAILED: "\U0001f534",
}


def render_scanning_progress(engine_status: dict[str, EngineStatus] | None = None) -> RenderedMessage:
    """Core, Security, Holder, Momentum, and (as of Step 14) Social are
    all real — `handlers.scan_handler` drives real per-engine status
    through this function during a scan. Security, Holder, and Social
    update independently since `scan_orchestration.run_scan` gathers all
    three concurrently; Momentum jumps PENDING -> DONE in one hop (no
    real "in progress" moment to show for a synchronous, instant
    computation - see that module's docstring).
    `engine_status.get(name, PENDING)` means this function never needed
    to change when Social went from placeholder to real; only its
    caller's dict did."""
    status = engine_status or {name: EngineStatus.PENDING for name in _ENGINE_DISPLAY_ORDER}
    lines = [
        f"{_ENGINE_STATUS_ICON[status.get(name, EngineStatus.PENDING)]} {name.title()}"
        for name in _ENGINE_DISPLAY_ORDER
    ]
    html = "\u23f3 <b>Scanning...</b>\n\n" + "\n".join(lines)
    return RenderedMessage(html=html, keyboard=_kb(screen="scanning"))


# ---------------------------------------------------------------------------
# My Filters / Recent Results / Auto-Watch
# ---------------------------------------------------------------------------


def render_my_filters() -> RenderedMessage:
    """Named presets (Conservative/Balanced/Aggressive) are real as of
    Step 12 [custom roadmap: Step 13] — see `render_filter_config`. This
    screen is specifically about SAVED CUSTOM presets, which is a
    different, still-unbuilt capability (Step 12's own Scope never
    included a rule-by-rule editing/saving UI — only the underlying
    `FilterProfile` dataclass supports a "custom" profile fully; nothing
    lets a person build and save one yet)."""
    html = (
        "\U0001f4ca <b>My Filters</b>\n\n"
        "The three built-in presets (Conservative/Balanced/Aggressive) are "
        "ready to use with Auto-Watch — pick one from Filter &amp; Analysis "
        "Config. Saving your own custom preset here isn't built yet."
    )
    return RenderedMessage(html=html, keyboard=_kb(screen="my_filters"))


def render_recent_results() -> RenderedMessage:
    html = (
        "\U0001f553 <b>Recent Results & Stats</b>\n\n"
        "No scans yet this session. Results you view will show up here."
    )
    return RenderedMessage(html=html, keyboard=_kb(screen="recent_results"))


def render_auto_watch(ctx: FSMContext, status: AutoWatchStatus | None = None) -> RenderedMessage:
    """`status`, when given, is the real `AutoWatchManager.status()`
    result (Step 12 [custom roadmap: Step 13]), used only to enrich the
    detail text (which preset, interval, matches found). The Start/Stop
    button itself is driven by `ctx.state` alone, exactly as it was
    before this pass — the FSM is the authoritative signal for "is a
    watch active," not this function's optional second argument, so a
    caller that hasn't wired `status` through yet still gets a correct
    button, just without the richer detail line."""
    active = ctx.state is FSMState.AUTO_WATCH_ACTIVE
    if active:
        if status is not None:
            detail = (
                f"Status: <b>running</b> ({escape_html(status.profile_name.title())} filter, "
                f"checking every {status.interval_min} min)\n"
                f"Matches found this session: {status.matches_found}\n\n"
            )
        else:
            detail = "Status: <b>running</b>.\n\n"
        html = (
            "\U0001f441 <b>Auto-Watch</b>\n\n"
            f"{detail}"
            "Background monitoring only — every match still needs your tap "
            "on Result Detail before anything else happens."
        )
        rows = [[InlineKeyboardButton(text="\u23f9 Stop", callback_data="watch_stop")]]
    else:
        html = (
            "\U0001f441 <b>Auto-Watch</b>\n\n"
            "Status: <b>stopped</b>. Starts a background scan loop against "
            "New Listings, alerting you on matches to your active filter — "
            "never trades automatically."
        )
        rows = [[InlineKeyboardButton(text="\u25b6\ufe0f Start", callback_data="watch_start")]]
    return RenderedMessage(html=html, keyboard=_kb(*rows, screen="auto_watch"))


# ---------------------------------------------------------------------------
# Settings / Help / About
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Settings / Help / About (Step 15 - real UserSettings-backed screens,
# replacing this section's Step 3 static placeholders)
# ---------------------------------------------------------------------------

_LANGUAGE_LABELS: dict[str, str] = {"en": "English", "ha": "Hausa"}
_NOTIFICATION_STYLE_LABELS: dict[str, str] = {"standard": "Standard", "minimal": "Minimal"}


def render_settings(settings: "UserSettings") -> RenderedMessage:
    """`settings` is this user's real, current `bot.types.UserSettings`
    (`state.session_store.SessionStore.get_settings`) - every value
    below reflects what they'd actually get if they scanned right now,
    not a fixed Step 3 placeholder. Every field is a single tap-to-
    cycle-or-toggle row (Part I.2 progressive disclosure: no numeric
    keypad entry anywhere here), so the screen always shows the CURRENT
    value as the button label, same convention Advanced Rules already
    established for its own numeric rows."""
    html = (
        "\u2699\ufe0f <b>Settings</b>\n\n"
        "Tap any row to change it. Settings apply for this session only "
        "(Part I.3 — nothing here is saved beyond it)."
    )
    rows = [
        [InlineKeyboardButton(
            text=f"\U0001f310 Language: {_LANGUAGE_LABELS[settings.language]}",
            callback_data="settings_cycle:language",
        )],
        [InlineKeyboardButton(
            text=f"\u26d3\ufe0f Default chain: {settings.default_chain.value.upper()}",
            callback_data="settings_cycle:default_chain",
        )],
        [InlineKeyboardButton(
            text=f"\U0001f916 Preferred bot: {settings.preferred_bot.value.replace('_', ' ').title()}",
            callback_data="settings_cycle:preferred_bot",
        )],
        [InlineKeyboardButton(
            text=f"\U0001f4c9 Slippage: {settings.slippage_pct:g}%",
            callback_data="settings_cycle:slippage_pct",
        )],
        [InlineKeyboardButton(
            text=f"{'\u2705' if settings.anti_mev else '\u2b1c'} Anti-MEV routing",
            callback_data="settings_toggle:anti_mev",
        )],
        [InlineKeyboardButton(
            text=f"\U0001f514 Notifications: {_NOTIFICATION_STYLE_LABELS[settings.notification_style]}",
            callback_data="settings_cycle:notification_style",
        )],
        [InlineKeyboardButton(
            text=f"\U0001f3af Default filter: {settings.default_filter_profile.title()}",
            callback_data="settings_cycle:default_filter_profile",
        )],
        [InlineKeyboardButton(
            text=f"{'\u2705' if settings.show_technical_errors else '\u2b1c'} Show technical error detail",
            callback_data="settings_toggle:show_technical_errors",
        )],
        [InlineKeyboardButton(text="\u21a9\ufe0f Reset to Defaults", callback_data="settings_reset")],
    ]
    return RenderedMessage(html=html, keyboard=_kb(*rows, screen="settings"))


def render_help() -> RenderedMessage:
    """The Help hub — real FAQ accordion entry points (one button per
    question; `render_faq_answer` shows the answer via a same-message
    edit, never a new message), plus Tutorial and Security Basics."""
    from rendering.help_content import FAQ_ENTRIES

    html = (
        "\u2753 <b>Help</b>\n\n"
        "Tap a question for its answer, or open the Tutorial / Security "
        "Basics below."
    )
    rows = [
        [InlineKeyboardButton(text=entry.question, callback_data=f"help_faq:{entry.entry_id}")]
        for entry in FAQ_ENTRIES
    ]
    rows.append([InlineKeyboardButton(text="\U0001f4d6 Tutorial", callback_data="help_tutorial")])
    rows.append([InlineKeyboardButton(text="\U0001f6e1\ufe0f Security Basics", callback_data="help_security")])
    return RenderedMessage(html=html, keyboard=_kb(*rows, screen="help"))


def render_faq_answer(entry_id: str) -> RenderedMessage:
    """One FAQ question's answer, same-message-edit target from
    `render_help`'s own buttons (Step 15 Definition of Done: "FAQ edits
    the same message"). An unknown/stale `entry_id` (a button from a
    previous app version) falls back to the Help hub itself rather than
    a broken or blank screen — still zero dead ends."""
    from rendering.help_content import get_faq_entry

    entry = get_faq_entry(entry_id)
    if entry is None:
        return render_help()
    html = f"\u2753 <b>{escape_html(entry.question)}</b>\n\n{escape_html(entry.answer)}"
    rows = [[InlineKeyboardButton(text="\u25c0\ufe0f Back to FAQ", callback_data="nav_help")]]
    return RenderedMessage(html=html, keyboard=InlineKeyboardMarkup(inline_keyboard=rows))


def render_tutorial() -> RenderedMessage:
    from rendering.help_content import TUTORIAL_STEPS

    html = "\U0001f4d6 <b>Tutorial</b>\n\n" + "\n\n".join(TUTORIAL_STEPS)
    return RenderedMessage(html=html, keyboard=_kb(screen="tutorial"))


def render_security_basics() -> RenderedMessage:
    from rendering.help_content import render_security_basics_lines

    html = "\U0001f6e1\ufe0f <b>Security Basics</b>\n\n" + "\n\n".join(render_security_basics_lines())
    return RenderedMessage(html=html, keyboard=_kb(screen="security_basics"))


def render_about() -> RenderedMessage:
    html = (
        "\u2139\ufe0f <b>About DexScan AI</b>\n\n"
        "Version: 0.15.0 (Step 15 of 16 — Settings, Help, Errors & Polish; see the project README)\n"
        "Data sources: DexScreener, RugCheck, standard Solana JSON-RPC endpoints, and twitterapi.io — "
        "each credited here as the engine consuming it shipped.\n\n"
        f"{escape_html(DISCLAIMER)} Nothing in this bot is financial advice."
    )
    return RenderedMessage(html=html, keyboard=_kb(screen="about"))


# ---------------------------------------------------------------------------
# Generic single-message screen (Part VIII Step 3 addition - not one of
# Part II.9's 14 named screens, but needed by both the stale-session
# recovery message and the AwaitingAddress paste-prompt, neither of which
# warrants a full dedicated screen of its own)
# ---------------------------------------------------------------------------


def render_plain(message_html: str, back_callback: str = HOME_CALLBACK) -> RenderedMessage:
    """A single message plus one Back/Home button. `message_html` is
    treated as already-safe, caller-formatted HTML (same convention as
    every other render_* function: escape the dynamic pieces before
    calling this, don't escape the whole string here)."""
    label = "\U0001f3e0 Home" if back_callback == HOME_CALLBACK else "\u25c0\ufe0f Back"
    row = [InlineKeyboardButton(text=label, callback_data=back_callback)]
    return RenderedMessage(html=message_html, keyboard=InlineKeyboardMarkup(inline_keyboard=[row]))
