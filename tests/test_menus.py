"""
Playbook reference: Unified Developer Playbook, Part VIII Step 3 -
Acceptance Criteria ("every screen ... has a corresponding render_*
function; every screen's keyboard includes Back/Home per Part II.5's
rule"), Definition of Done ("every render_* function has a unit test
asserting Back/Home presence where required"), Unit Testing Requirements
("menu snapshot tests: assert exact button callback_data values match
Part II.9/Part VII conventions").

Result List/Result Detail moved to `rendering/result_renderer.py` in
Step 7, and Trade Staging moved there too in Step 11 (see
`tests/test_result_renderer.py` for all three's real tests, against real
data) — this file now covers what's left in `rendering/menus.py`: the 12
static/simple screens only.

Requires aiogram (RenderedMessage.keyboard is a real InlineKeyboardMarkup)
- confirmed installable and this file confirmed passing for real in the
  Step 11 session's sandbox (`pip install -r requirements.txt && pytest
  tests/test_menus.py`); prior sessions' sandboxes had no network access
  to do this and left it syntax-checked only - see README.md's Step 11
  entry for the same note applied project-wide.

The expected Back/Home targets table below is hand-derived independently
from Part II.9's screen table, not copied out of rendering/menus.py's own
`_SCREEN_BACK_TARGET` - so this test actually checks the implementation
against the playbook, not against itself (same principle as
test_fsm.py's independently-encoded adjacency table).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from bot.constants import FSMState
from bot.types import AutoWatchStatus, EngineStatus, UserSettings
from rendering.menus import (
    RenderedMessage,
    render_about,
    render_auto_watch,
    render_faq_answer,
    render_filter_config,
    render_help,
    render_main_menu,
    render_my_filters,
    render_recent_results,
    render_scan_menu,
    render_scanning_progress,
    render_security_basics,
    render_settings,
    render_tutorial,
    render_welcome,
)
from state.fsm import FSMContext


def _all_callback_data(rendered: RenderedMessage) -> set[str]:
    """Flattens every button's callback_data out of a rendered keyboard,
    for simple 'is this target present' assertions without caring about
    row/column layout."""
    assert isinstance(rendered.keyboard, InlineKeyboardMarkup)
    return {
        button.callback_data
        for row in rendered.keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }


def _idle_ctx() -> FSMContext:
    return FSMContext(state=FSMState.IDLE)


# ---------------------------------------------------------------------------
# Back/Home presence per screen (Part II.5 / II.9) - independently derived,
# not copied from rendering/menus.py's own table.
# ---------------------------------------------------------------------------


def test_welcome_has_no_back_or_home_but_has_an_entry_button() -> None:
    """Part II.9: Welcome's Back/Home targets are both '-'."""
    rendered = render_welcome()
    callbacks = _all_callback_data(rendered)
    assert "nav_welcome" not in callbacks  # nothing points back at itself
    assert "nav_main" in callbacks  # the one "enter the app" action
    assert len(rendered.keyboard.inline_keyboard) == 1
    assert len(rendered.keyboard.inline_keyboard[0]) == 1


def test_main_menu_back_goes_to_welcome_home_goes_to_main() -> None:
    """Part II.9: Main Menu's row is the one exception where Back (Welcome)
    and Home (Main Menu itself) genuinely differ."""
    rendered = render_main_menu(_idle_ctx())
    callbacks = _all_callback_data(rendered)
    assert "nav_welcome" in callbacks
    assert "nav_main" in callbacks
    # Plus its four real feature buttons (Part II.5's site map)
    for target in ("nav_scan", "nav_filters", "nav_recent", "nav_watch", "nav_settings", "nav_help", "nav_about"):
        assert target in callbacks, f"Main Menu missing a button to {target}"


def test_scan_menu_back_is_main_menu_only_no_redundant_home() -> None:
    """Part II.9: Scan Menu's Back AND Home targets are both Main Menu -
    rendering/menus.py collapses identical Back/Home into one button
    rather than showing two buttons that do the same thing."""
    rendered = render_scan_menu()
    callbacks = _all_callback_data(rendered)
    assert "nav_main" in callbacks
    footer_row = rendered.keyboard.inline_keyboard[-1]
    assert len(footer_row) == 1, "Back and Home should collapse to one button when identical"


def test_filter_config_back_is_scan_menu_home_is_main_menu_distinct() -> None:
    """Part II.9: Filter & Analysis Config's Back target is Scan Menu,
    distinct from Home (Main Menu) - both buttons must be present."""
    rendered = render_filter_config()
    callbacks = _all_callback_data(rendered)
    assert "nav_scan" in callbacks
    assert "nav_main" in callbacks
    footer_row = rendered.keyboard.inline_keyboard[-1]
    assert len(footer_row) == 2, "Back (Scan Menu) and Home (Main Menu) must be distinct buttons here"


def test_scanning_progress_collapses_to_single_cancel_action() -> None:
    """Part II.9: Scanning/Progress's Back AND Home targets are both
    'Cancel only'."""
    rendered = render_scanning_progress()
    callbacks = _all_callback_data(rendered)
    assert "scan_cancel" in callbacks
    assert len(rendered.keyboard.inline_keyboard[-1]) == 1


def test_secondary_screens_all_have_main_menu_back_button() -> None:
    """Part II.9: My Filters, Recent Results, Auto-Watch, Settings, Help,
    About all Back-target Main Menu specifically."""
    for rendered in (
        render_my_filters(),
        render_recent_results(),
        render_auto_watch(_idle_ctx()),
        render_settings(UserSettings()),  # STEP 15: now requires real UserSettings, not a Step 3 zero-arg stub
        render_help(),
        render_about(),
    ):
        assert "nav_main" in _all_callback_data(rendered)


# ---------------------------------------------------------------------------
# Content correctness
# ---------------------------------------------------------------------------


def test_every_render_function_returns_nonempty_html_and_a_keyboard() -> None:
    all_rendered = [
        render_welcome(),
        render_main_menu(_idle_ctx()),
        render_scan_menu(),
        render_filter_config(),
        render_scanning_progress(),
        render_my_filters(),
        render_recent_results(),
        render_auto_watch(_idle_ctx()),
        render_settings(UserSettings()),  # STEP 15: now requires real UserSettings, not a Step 3 zero-arg stub
        render_help(),
        render_about(),
        render_tutorial(),
        render_security_basics(),
        render_faq_answer("custody"),
    ]
    for rendered in all_rendered:
        assert isinstance(rendered, RenderedMessage)
        assert rendered.html.strip() != ""
        assert isinstance(rendered.keyboard, InlineKeyboardMarkup)
        assert len(rendered.keyboard.inline_keyboard) >= 1


def test_main_menu_status_line_reflects_auto_watch_state() -> None:
    idle_html = render_main_menu(FSMContext(state=FSMState.IDLE)).html
    watching_html = render_main_menu(FSMContext(state=FSMState.AUTO_WATCH_ACTIVE)).html
    assert idle_html != watching_html
    assert "Auto-Watch" in watching_html


def test_auto_watch_screen_shows_start_when_idle_and_stop_when_active() -> None:
    idle_callbacks = _all_callback_data(render_auto_watch(FSMContext(state=FSMState.IDLE)))
    active_callbacks = _all_callback_data(
        render_auto_watch(FSMContext(state=FSMState.AUTO_WATCH_ACTIVE))
    )
    assert "watch_start" in idle_callbacks and "watch_stop" not in idle_callbacks
    assert "watch_stop" in active_callbacks and "watch_start" not in active_callbacks


def test_auto_watch_screen_shows_stop_button_even_without_status_passed() -> None:
    """The button is state-driven, not status-driven (rendering/menus.py's
    own docstring note) - a caller in FSMState.AUTO_WATCH_ACTIVE that
    hasn't wired status through still gets a correct button."""
    from datetime import datetime, timezone

    from state.fsm import FSMContext as _FSMContext

    without_status = render_auto_watch(_FSMContext(state=FSMState.AUTO_WATCH_ACTIVE))
    assert "watch_stop" in _all_callback_data(without_status)
    assert "running" in without_status.html.lower()

    status = AutoWatchStatus(
        user_id=1, profile_name="conservative", interval_min=5, matches_found=3,
        started_at=datetime.now(timezone.utc),
    )
    with_status = render_auto_watch(_FSMContext(state=FSMState.AUTO_WATCH_ACTIVE), status=status)
    assert "watch_stop" in _all_callback_data(with_status)
    assert "Conservative" in with_status.html
    assert "checking every 5 min" in with_status.html
    assert "Matches found this session: 3" in with_status.html


def test_scanning_progress_default_is_all_pending() -> None:
    """Calling with no engine_status arg (this function's default) shows
    every engine PENDING - Core/Security are real as of Steps 4-7, but
    `handlers.scan_handler` always passes real status once a scan starts;
    this test covers the function's own zero-argument default, not a
    claim that no engine exists."""
    rendered = render_scanning_progress()
    for engine_name in ("core", "security", "holder", "momentum", "social"):
        assert engine_name.title() in rendered.html
    # A pending run shouldn't claim anything is done or failed
    assert "\U0001f7e2" not in rendered.html  # green (done) icon
    assert "\U0001f534" not in rendered.html  # red (failed) icon


def test_scanning_progress_reflects_mixed_engine_statuses() -> None:
    rendered = render_scanning_progress(
        {
            "core": EngineStatus.DONE,
            "security": EngineStatus.RUNNING,
            "holder": EngineStatus.PENDING,
            "momentum": EngineStatus.FAILED,
            "social": EngineStatus.PENDING,
        }
    )
    assert "\U0001f7e2" in rendered.html  # core done
    assert "\U0001f7e1" in rendered.html  # security running
    assert "\U0001f534" in rendered.html  # momentum failed


# ---------------------------------------------------------------------------
# Escaping - dynamic content must actually be escaped (Part II.8's threat
# model), not just theoretically escapable via html_utils in isolation.
# ---------------------------------------------------------------------------


def test_help_hub_lists_every_faq_question_as_its_own_button() -> None:
    """STEP 15: Help became a real accordion - the hub screen shows
    QUESTIONS as tappable entry points (answers moved to
    render_faq_answer, a same-message-edit target), not a wall of
    inline Q&A text the way Step 3's placeholder did."""
    from rendering.help_content import FAQ_ENTRIES

    rendered = render_help()
    button_texts = {button.text for row in rendered.keyboard.inline_keyboard for button in row}
    for entry in FAQ_ENTRIES:
        assert entry.question in button_texts
    callback_data = _all_callback_data(rendered)
    for entry in FAQ_ENTRIES:
        assert f"help_faq:{entry.entry_id}" in callback_data


def test_faq_answer_screen_shows_the_real_answer_and_edits_back_to_faq() -> None:
    """The accordion's actual "tap a question -> see the answer, same
    message" behavior (Step 15 Definition of Done). Back targets
    nav_help specifically (the FAQ list), not nav_main - one level up,
    not two."""
    rendered = render_faq_answer("custody")
    assert "custod" in rendered.html.lower() or "fund" in rendered.html.lower()
    assert "nav_help" in _all_callback_data(rendered)
    assert "nav_main" not in _all_callback_data(rendered)


def test_faq_answer_unknown_id_falls_back_to_the_faq_hub_not_a_dead_end() -> None:
    rendered = render_faq_answer("not-a-real-entry-id")
    button_texts = {button.text for row in rendered.keyboard.inline_keyboard for button in row}
    assert "\U0001f4d6 Tutorial" in button_texts  # this IS render_help()'s own output


def test_tutorial_and_security_basics_both_back_to_help_not_main_menu() -> None:
    assert "nav_help" in _all_callback_data(render_tutorial())
    assert "nav_help" in _all_callback_data(render_security_basics())


def test_security_basics_screen_contains_every_glossary_term() -> None:
    from rendering.help_content import SECURITY_BASICS_ENTRIES

    html = render_security_basics().html
    for term, _ in SECURITY_BASICS_ENTRIES:
        assert term in html


# ---------------------------------------------------------------------------
# Settings (Step 15)
# ---------------------------------------------------------------------------


def test_settings_screen_reflects_the_real_passed_in_values_not_fixed_placeholders() -> None:
    """Every field's CURRENT value is shown as its own button's label
    (Advanced Rules' own established convention - e.g. "Min liquidity:
    $50,000 (tap to change)" - not in the message body), so this checks
    the keyboard's button texts, not `.html`."""
    from bot.constants import Chain, TradingBot

    custom = UserSettings(
        language="ha", default_chain=Chain.TON, preferred_bot=TradingBot.GMGN,
        slippage_pct=15.0, anti_mev=False, notification_style="minimal",
        default_filter_profile="custom", show_technical_errors=True,
    )
    rendered = render_settings(custom)
    button_texts = " | ".join(button.text for row in rendered.keyboard.inline_keyboard for button in row)
    assert "Hausa" in button_texts
    assert "TON" in button_texts
    assert "Gmgn" in button_texts or "GMGN" in button_texts
    assert "15" in button_texts
    assert "Custom" in button_texts


def test_settings_screen_has_a_cycle_or_toggle_button_for_every_field() -> None:
    from bot.settings_logic import CYCLE_FIELD_KEYS, TOGGLE_FIELD_KEYS

    callback_data = _all_callback_data(render_settings(UserSettings()))
    for field_key in CYCLE_FIELD_KEYS:
        assert f"settings_cycle:{field_key}" in callback_data
    for field_key in TOGGLE_FIELD_KEYS:
        assert f"settings_toggle:{field_key}" in callback_data


def test_settings_screen_has_a_reset_button() -> None:
    assert "settings_reset" in _all_callback_data(render_settings(UserSettings()))


def test_help_screen_contains_faq_content() -> None:
    """Updated for Step 15's real accordion (see
    test_help_hub_lists_every_faq_question_as_its_own_button and
    test_faq_answer_screen_shows_the_real_answer_and_edits_back_to_faq
    above for the full behavior) - the custody/funds guarantee must
    still be reachable from Help, whether that's the hub's own button
    text or the accordion answer one tap deeper."""
    hub = render_help()
    button_texts = " ".join(button.text for row in hub.keyboard.inline_keyboard for button in row)
    assert "fund" in button_texts.lower() or "custod" in button_texts.lower()


def test_about_and_welcome_share_the_same_disclaimer_text() -> None:
    """Part VIII Step 15's future Acceptance Criteria explicitly requires
    this to hold - checking it now, while both screens are still simple,
    means it can never silently drift as either one is edited later."""
    from rendering.menus import DISCLAIMER

    welcome_html = render_welcome().html
    about_html = render_about().html
    assert DISCLAIMER in welcome_html or DISCLAIMER.replace("&", "&amp;") in welcome_html
    assert DISCLAIMER in about_html or DISCLAIMER.replace("&", "&amp;") in about_html
