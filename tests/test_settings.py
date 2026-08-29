"""
Step 15 - Settings. Covers `bot.settings_logic` (pure cycle/toggle/reset
functions) and `state.session_store.SessionStore`'s new
`get_settings`/`set_settings` slot. `handlers/settings_handler.py`'s own
aiogram-facing shell is exercised indirectly (it calls straight through
to these same functions with no branching logic of its own worth a
second, redundant test suite here) — see this file's module docstring
convention followed throughout this codebase (WatchHandler over
filter_presets has the same split, same reasoning).
"""

from __future__ import annotations

import pytest

from bot.constants import Chain, TradingBot
from bot.settings_logic import (
    CYCLE_FIELD_KEYS,
    TOGGLE_FIELD_KEYS,
    cycle_field,
    reset_settings,
    toggle_field,
)
from bot.types import UserSettings
from state.session_store import SessionStore


def test_defaults_match_the_dataclass_own_defaults() -> None:
    settings = UserSettings()
    assert settings.language == "en"
    assert settings.default_chain is Chain.SOL
    assert settings.preferred_bot is TradingBot.TROJAN
    assert settings.slippage_pct == 5.0
    assert settings.anti_mev is True
    assert settings.notification_style == "standard"
    assert settings.default_filter_profile == "balanced"
    assert settings.show_technical_errors is False


# -- cycle_field ---------------------------------------------------------


def test_cycle_language_toggles_between_en_and_ha() -> None:
    settings = UserSettings(language="en")
    settings = cycle_field(settings, "language")
    assert settings.language == "ha"
    settings = cycle_field(settings, "language")
    assert settings.language == "en"


def test_cycle_default_chain_wraps_around_all_six() -> None:
    settings = UserSettings()
    seen = []
    for _ in range(6):
        settings = cycle_field(settings, "default_chain")
        seen.append(settings.default_chain)
    assert seen[-1] is Chain.SOL  # back to the start after exactly 6 steps
    assert len(set(seen)) == 6  # all six chains actually visited, none skipped/repeated early


def test_cycle_preferred_bot_wraps_around_all_six() -> None:
    settings = UserSettings()
    seen = []
    for _ in range(6):
        settings = cycle_field(settings, "preferred_bot")
        seen.append(settings.preferred_bot)
    assert seen[-1] is TradingBot.TROJAN
    assert len(set(seen)) == 6


def test_cycle_slippage_climbs_the_ladder_low_to_high() -> None:
    settings = UserSettings(slippage_pct=5.0)
    settings = cycle_field(settings, "slippage_pct")
    assert settings.slippage_pct == 7.5
    settings = cycle_field(settings, "slippage_pct")
    assert settings.slippage_pct == 10.0


def test_cycle_notification_style_toggles() -> None:
    settings = UserSettings(notification_style="standard")
    settings = cycle_field(settings, "notification_style")
    assert settings.notification_style == "minimal"
    settings = cycle_field(settings, "notification_style")
    assert settings.notification_style == "standard"


def test_cycle_default_filter_profile_covers_all_four() -> None:
    settings = UserSettings()
    seen = []
    for _ in range(4):
        settings = cycle_field(settings, "default_filter_profile")
        seen.append(settings.default_filter_profile)
    assert set(seen) == {"conservative", "balanced", "aggressive", "custom"}
    assert seen[-1] == "balanced"  # back to the start after exactly 4 steps


def test_cycle_only_changes_the_targeted_field() -> None:
    settings = UserSettings(anti_mev=False, show_technical_errors=True)
    updated = cycle_field(settings, "language")
    assert updated.anti_mev is False
    assert updated.show_technical_errors is True
    assert updated.language != settings.language


def test_cycle_unknown_field_key_is_a_no_op() -> None:
    settings = UserSettings()
    assert cycle_field(settings, "not_a_real_field") == settings


@pytest.mark.parametrize("field_key", CYCLE_FIELD_KEYS)
def test_every_declared_cycle_field_key_actually_changes_something(field_key: str) -> None:
    settings = UserSettings()
    assert cycle_field(settings, field_key) != settings


# -- toggle_field ----------------------------------------------------------


def test_toggle_anti_mev_flips_both_ways() -> None:
    settings = UserSettings(anti_mev=True)
    settings = toggle_field(settings, "anti_mev")
    assert settings.anti_mev is False
    settings = toggle_field(settings, "anti_mev")
    assert settings.anti_mev is True


def test_toggle_show_technical_errors_flips_both_ways() -> None:
    settings = UserSettings(show_technical_errors=False)
    settings = toggle_field(settings, "show_technical_errors")
    assert settings.show_technical_errors is True
    settings = toggle_field(settings, "show_technical_errors")
    assert settings.show_technical_errors is False


def test_toggle_unknown_field_key_is_a_no_op() -> None:
    settings = UserSettings()
    assert toggle_field(settings, "not_a_real_field") == settings


@pytest.mark.parametrize("field_key", TOGGLE_FIELD_KEYS)
def test_every_declared_toggle_field_key_actually_flips(field_key: str) -> None:
    settings = UserSettings()
    assert toggle_field(settings, field_key) != settings


# -- reset_settings ----------------------------------------------------------


def test_reset_settings_returns_fresh_defaults() -> None:
    assert reset_settings() == UserSettings()


def test_reset_after_heavy_customization_actually_clears_everything() -> None:
    settings = UserSettings(
        language="ha", default_chain=Chain.TON, preferred_bot=TradingBot.GMGN,
        slippage_pct=15.0, anti_mev=False, notification_style="minimal",
        default_filter_profile="custom", show_technical_errors=True,
    )
    assert reset_settings() == UserSettings()
    assert settings != UserSettings()  # sanity: the customized one really was different


# -- SessionStore round trip -------------------------------------------------


def test_get_settings_creates_and_persists_a_default_on_first_access() -> None:
    store = SessionStore()
    first = store.get_settings(111)
    assert first == UserSettings()
    second = store.get_settings(111)
    assert second == first  # same stored instance's values, not a fresh default each call


def test_set_settings_then_get_settings_round_trips() -> None:
    store = SessionStore()
    updated = cycle_field(UserSettings(), "language")
    store.set_settings(222, updated)
    assert store.get_settings(222) == updated


def test_settings_are_independent_per_user() -> None:
    store = SessionStore()
    store.set_settings(1, toggle_field(UserSettings(), "anti_mev"))
    assert store.get_settings(1).anti_mev is False
    assert store.get_settings(2).anti_mev is True  # untouched user still gets true defaults


def test_reset_to_defaults_round_trips_through_the_store() -> None:
    """Step 15 Definition of Done: "reset-to-defaults works" - exercised
    end-to-end through the store, the way SettingsHandler's
    `settings_reset` branch actually uses it."""
    store = SessionStore()
    store.set_settings(5, UserSettings(language="ha", anti_mev=False, slippage_pct=15.0))
    assert store.get_settings(5) != UserSettings()

    store.set_settings(5, reset_settings())
    assert store.get_settings(5) == UserSettings()
