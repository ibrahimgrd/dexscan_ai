"""
Layer: pure business logic (Playbook Part VIII Step 15).

`UserSettings` field mutation - the Settings-screen analogue of
`analysis.filter_presets.set_bool_field`/`cycle_numeric_field`, split
out of `handlers/settings_handler.py` for the same reason that file's
own logic is split from `analysis/filter_presets.py`: this module has
zero aiogram import, so it's directly unit-testable without the
Telegram framework installed (Part V.2 - composition over a handler
that mixes Telegram I/O with the decision of what a tap actually does).

Every field is either "cycle" (steps through a fixed, ordered option
list, wrapping around) or "toggle" (flips a bool) - no field needs a
free-text/numeric-keypad entry, matching Part I.2's progressive-
disclosure principle (toggles/cycles before free numeric entry) the
same way Advanced Rules already does for slippage-like values.
"""

from __future__ import annotations

from dataclasses import replace

from bot.constants import Chain, TradingBot
from bot.types import UserSettings

# Field key -> ordered option cycle. Order is deliberate where it
# matters (SLIPPAGE_LADDER_PCT climbs low-to-high, same shape as
# filter_presets.py's numeric ladders) so "tap to change" moves
# predictably rather than jumping around.
LANGUAGE_CYCLE: tuple[str, ...] = ("en", "ha")
CHAIN_CYCLE: tuple[Chain, ...] = (Chain.SOL, Chain.ETH, Chain.BSC, Chain.BASE, Chain.ARB, Chain.TON)
BOT_CYCLE: tuple[TradingBot, ...] = (
    TradingBot.TROJAN, TradingBot.BANANA_GUN, TradingBot.BULLX,
    TradingBot.PHOTON, TradingBot.MAESTRO, TradingBot.GMGN,
)
SLIPPAGE_LADDER_PCT: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0, 5.0, 7.5, 10.0, 15.0)
NOTIFICATION_STYLE_CYCLE: tuple[str, ...] = ("standard", "minimal")
FILTER_PROFILE_CYCLE: tuple[str, ...] = ("conservative", "balanced", "aggressive", "custom")

CYCLE_FIELD_KEYS: tuple[str, ...] = (
    "language", "default_chain", "preferred_bot", "slippage_pct",
    "notification_style", "default_filter_profile",
)
TOGGLE_FIELD_KEYS: tuple[str, ...] = ("anti_mev", "show_technical_errors")


def _cycle_next(current: object, options: tuple) -> object:
    """Steps to the option after `current`, wrapping to the first after
    the last. Falls back to `options[0]` if `current` isn't in the list
    at all (defensive - never actually expected, since every field
    always starts from a `UserSettings` default that IS one of its own
    cycle's options)."""
    try:
        index = options.index(current)
    except ValueError:
        return options[0]
    return options[(index + 1) % len(options)]


def cycle_field(settings: UserSettings, field_key: str) -> UserSettings:
    """Returns a NEW `UserSettings` with `field_key` advanced to its next
    cycle value; all other fields unchanged. Unknown `field_key` returns
    `settings` unmodified rather than raising - same "a stale/malformed
    tap simply has no effect" reasoning as
    `filter_presets.cycle_numeric_field`'s own docstring."""
    if field_key == "language":
        return replace(settings, language=_cycle_next(settings.language, LANGUAGE_CYCLE))
    if field_key == "default_chain":
        return replace(settings, default_chain=_cycle_next(settings.default_chain, CHAIN_CYCLE))
    if field_key == "preferred_bot":
        return replace(settings, preferred_bot=_cycle_next(settings.preferred_bot, BOT_CYCLE))
    if field_key == "slippage_pct":
        return replace(settings, slippage_pct=_cycle_next(settings.slippage_pct, SLIPPAGE_LADDER_PCT))
    if field_key == "notification_style":
        return replace(settings, notification_style=_cycle_next(settings.notification_style, NOTIFICATION_STYLE_CYCLE))
    if field_key == "default_filter_profile":
        return replace(settings, default_filter_profile=_cycle_next(settings.default_filter_profile, FILTER_PROFILE_CYCLE))
    return settings


def toggle_field(settings: UserSettings, field_key: str) -> UserSettings:
    """Returns a NEW `UserSettings` with `field_key` flipped; all other
    fields unchanged. Unknown `field_key` returns `settings` unmodified."""
    if field_key == "anti_mev":
        return replace(settings, anti_mev=not settings.anti_mev)
    if field_key == "show_technical_errors":
        return replace(settings, show_technical_errors=not settings.show_technical_errors)
    return settings


def reset_settings() -> UserSettings:
    """Step 15 Definition of Done: "reset-to-defaults works." A fresh
    `UserSettings()` IS the default - no separate constant to drift out
    of sync with the dataclass's own field defaults."""
    return UserSettings()
