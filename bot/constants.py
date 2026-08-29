"""Shared enums and naming constants for DexScan AI.

Playbook reference: Unified Developer Playbook, Part VIII Step 1.
This module is the single source of truth for FSM states, supported chains,
and the callback-prefix taxonomy (Part V.6). Every later step extends these
tables additively; nothing here should ever be renamed once a screen or
handler depends on it.
"""

from __future__ import annotations

from enum import Enum


class FSMState(str, Enum):
    """Conversational states, per Playbook Part II.4. Exit rules for each
    state are enforced by Step 2's FSMEngine, not here — this module only
    names the states."""

    IDLE = "idle"
    AWAITING_ADDRESS = "awaiting_address"
    CONFIGURING_FILTER = "configuring_filter"
    SCANNING = "scanning"
    RESULT_READY = "result_ready"
    RESULT_DETAIL = "result_detail"
    TRADE_STAGING = "trade_staging"
    AUTO_WATCH_ACTIVE = "auto_watch_active"
    ERROR = "error"


class Chain(str, Enum):
    """Supported blockchains, per Playbook Part II.6. A seventh chain is one
    new member here plus one new regex entry and API adapter (Step 4's
    Future Compatibility note) — no other module changes."""

    SOL = "sol"
    ETH = "eth"
    BSC = "bsc"
    BASE = "base"
    ARB = "arb"
    TON = "ton"


# Address shape patterns, per Playbook Part II.6 - centralized here (rather
# than duplicated in Step 3's shape-only check and Step 4's chain-specific
# detection) since both need the exact same regexes. EVM_CHAINS groups the
# four chains that share the `0x...` shape, since a `0x...` string is only
# ever candidate-matched against these four (Step 4's job), never Solana/TON.
SOLANA_ADDRESS_PATTERN = r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
EVM_ADDRESS_PATTERN = r"^0x[a-fA-F0-9]{40}$"
TON_ADDRESS_PATTERN = r"^[a-zA-Z0-9_-]{48}$"

EVM_CHAINS: frozenset[Chain] = frozenset({Chain.ETH, Chain.BSC, Chain.BASE, Chain.ARB})

# Part III.3 (Holder Engine, Step 8): "Whale threshold = any wallet
# holding > 1.5% of supply." Named here per that step's own instruction
# (a deliberate exception to the usual convention of keeping an engine's
# formula constants local to its own module - c.f. security_engine.py's
# _HIGH_TAX_COMBINED_THRESHOLD_PCT) since a "whale" is a concept other
# future consumers (rendering, a later engine) may reasonably want to
# reference without importing holder_engine.py itself.
WHALE_HOLDER_THRESHOLD_PCT = 1.5

# Part III.4 (Social Engine, Step 13): "high-tier-account activity" - the
# concrete follower count above which a mentioning account counts toward
# `SocialResult.influencer_mention_count`. Not given a specific number by
# the Playbook (Part VI's "on ambiguity" rule applies: most reasonable
# value consistent with Part I-IV, documented as an assumption). Named
# here rather than kept local to social_engine.py for the same reason as
# WHALE_HOLDER_THRESHOLD_PCT above - a future renderer will want to label
# this threshold too.
INFLUENCER_FOLLOWER_THRESHOLD = 10_000


class TradingBot(str, Enum):
    """Supported external execution bots, per Playbook Part IV.1. Declared
    here (rather than inside Step 11's integrations module) so it's a
    stable, dependency-free enum other early constants/types can reference
    without importing from a module that doesn't exist until Step 11 -
    the deep-link logic itself is still entirely Step 11's scope."""

    TROJAN = "trojan"
    BANANA_GUN = "banana_gun"
    BULLX = "bullx"
    PHOTON = "photon"
    MAESTRO = "maestro"
    GMGN = "gmgn"


# Callback prefix taxonomy (Part V.6). A new screen extends this tuple;
# it never introduces a parallel naming scheme.
# "scan_" added in Step 3: Scan Menu's Trending/New/Paste/Cancel actions
# don't fit "nav_" (pure navigation, no state change) or "exec_" (reserved
# for Step 11's Trade Staging approve/abort) - they're their own category:
# actions that kick off analysis.
CALLBACK_PREFIXES: tuple[str, ...] = (
    "nav_",
    "set_",
    "rule_",
    "result_",
    "exec_",
    "watch_",
    "settings_",
    "help_",
    "about_",
    "scan_",
)
