"""Shared data types used across layers.

Playbook reference: Unified Developer Playbook, Part VIII Step 1.

Design note (read before extending in Step 2): `SessionContext` is
deliberately minimal here — a bare per-user container with a free-form
payload dict. Step 2's FSMEngine stores its `FSMContext` inside
`SessionContext.payload["fsm"]` rather than replacing this class, so
`SessionStore` (state/session_store.py) never needs to know the shape of
whatever a later step decides to keep per-user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from bot.constants import Chain, TradingBot


@dataclass
class SessionContext:
    """Minimal per-user, in-memory-only session container (Part I.3 —
    statelessness). Never serialized to disk."""

    user_id: int
    payload: dict[str, Any] = field(default_factory=dict)


class EngineStatus(str, Enum):
    """One analytical engine's status on the Scanning/Progress screen
    (Part II.9). Added in Step 3 with only PENDING ever actually produced
    (no engine exists yet to run/finish/fail) - Steps 4-14 report real
    RUNNING/DONE/FAILED values as each engine executes."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class AutoWatchStatus:
    """Part VIII Step 12's Public Interface — `AutoWatchManager.status`'s
    return type (handlers/auto_watch.py). A plain read-only snapshot, not
    the live `asyncio.Task` itself — nothing outside that module should
    ever touch a raw Task (Part V.2: composition over exposing internals)."""

    user_id: int
    profile_name: str
    interval_min: int
    matches_found: int
    started_at: datetime


@dataclass
class UserSettings:
    """Part VIII Step 15's Public Interface. Session-scoped only — never
    persisted beyond the process, same as everything else in
    `state.session_store.SessionStore` (Part I.3). Deliberately a plain
    dataclass, not pydantic: constructed entirely from already-validated
    internal values (a cycled enum member, a toggled bool), never
    directly from raw external input, so it doesn't cross the I/O
    boundary Part V.1 reserves pydantic for.

    `default_filter_profile` is this file's one explicit ambiguity
    resolution (Part VI "on ambiguity"): Step 15's prose lists "analysis
    rule defaults" among Settings' fields, but its own code sample
    doesn't name a field for it. The most reasonable reading consistent
    with Part I-IV and with Step 12's own `FilterProfile.name` values is
    "which preset a fresh scan/Filter Config screen starts pre-selected
    on" - so that's what this field drives
    (`rendering.menus.render_filter_config`'s own default arg).
    """

    language: Literal["en", "ha"] = "en"
    default_chain: Chain = Chain.SOL
    preferred_bot: TradingBot = TradingBot.TROJAN
    slippage_pct: float = 5.0
    anti_mev: bool = True
    notification_style: Literal["standard", "minimal"] = "standard"
    default_filter_profile: Literal["conservative", "balanced", "aggressive", "custom"] = "balanced"
    show_technical_errors: bool = False
