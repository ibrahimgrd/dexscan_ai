"""
Layer: Handlers — Auto-Watch, Filter Presets, and on-demand discovery
scans (Playbook Part VIII Step 12; landed as custom-roadmap Step 13).

The Telegram-I/O shell around `handlers.auto_watch.AutoWatchManager` and
`analysis.filter_presets` — same split as `scan_handler.py`/
`scan_orchestration.py` (Part V.2): the manager and the preset logic
stay aiogram-free and independently testable; this file is what actually
sends/edits messages, reads callback params, and transitions FSM state.

Owns six callback commands `handlers/navigation.py`'s own docstring
listed as "not built yet" before this pass: `watch_start`, `watch_stop`
(Auto-Watch's Start/Stop), `rule_preset:*` (Filter Config's presets),
`scan_trending`, `scan_new` (Scan Menu's two discovery buttons — Paste
was already `ScanHandler`'s). Also takes over `nav_watch`/`nav_filters`
from `handlers/navigation.py`'s `_NAV_TARGETS`, since both now need real
data (an `AutoWatchStatus`, a real preset) that lookup table has no way
to supply.
"""

from __future__ import annotations

import logging

from aiogram.types import CallbackQuery

from analysis.core_engine import CoreEngine
from analysis.filter_presets import (
    NAMED_PRESETS,
    cycle_numeric_field,
    default_custom_profile,
    set_bool_field,
)
from analysis.holder_engine import HolderEngine
from analysis.momentum_engine import MomentumEngine
from analysis.security_engine import SecurityEngine
from analysis.social_engine import SocialEngine
from bot.constants import FSMState
from handlers.auto_watch import AutoWatchManager
from handlers.base import TelegramEvent, send_rendered
from handlers.callback_parser import parse_callback
from handlers.scan_orchestration import run_scan
from rendering.error_renderer import render_error
from rendering.menus import (
    render_advanced_rules,
    render_auto_watch,
    render_filter_config,
    render_my_filters,
)
from rendering.result_renderer import render_result_list, render_watch_alert
from scoring.pipeline import ScoringPipeline
from state.fsm import FSMContext, FSMEngine
from state.session_store import SessionStore

logger = logging.getLogger(__name__)

_OWNED_COMMANDS = frozenset(
    {
        "nav_watch", "nav_filters", "watch_start", "watch_stop",
        "rule_preset", "scan_trending", "scan_new",
        # Step 12 revalidation additions: Advanced Rules / Custom Filter,
        # previously unhandled placeholders (rule_advanced had no owner
        # at all; rule_tgl/rule_num were anticipated in the callback
        # parser's own docstring/tests but never wired to a real screen).
        "rule_advanced", "rule_tgl", "rule_num", "rule_save",
    }
)

# Auto-Watch's own reasonable default when a user taps Start without ever
# visiting Filter Config first (Part VI "on ambiguity": most reasonable
# default, documented, not a silent guess) — Balanced, not the loosest or
# strictest option, so an un-configured watch doesn't surprise anyone in
# either direction.
_DEFAULT_INTERVAL_MIN = 10
_MAX_DISCOVERY_SCAN_CANDIDATES = 10  # bounds an on-demand Trending/New tap's own latency + provider cost

_DISCOVERY_FETCH_FAILED_ERROR = "Couldn't reach the discovery feed just now."


class WatchHandler:
    def __init__(
        self,
        fsm: FSMEngine,
        auto_watch_manager: AutoWatchManager,
        discovery_provider,  # TokenDiscoveryProvider (Protocol - no runtime import needed)
        core_engine: CoreEngine,
        security_engine: SecurityEngine,
        holder_engine: HolderEngine,
        momentum_engine: MomentumEngine,
        social_engine: SocialEngine,
        scoring_pipeline: ScoringPipeline,
        session_store: SessionStore,
    ) -> None:
        self._fsm = fsm
        self._auto_watch_manager = auto_watch_manager
        self._discovery_provider = discovery_provider
        self._core_engine = core_engine
        self._security_engine = security_engine
        self._holder_engine = holder_engine
        self._momentum_engine = momentum_engine
        self._social_engine = social_engine
        self._scoring_pipeline = scoring_pipeline
        self._session_store = session_store

    async def can_handle(self, event: TelegramEvent, ctx: FSMContext) -> bool:
        if not isinstance(event, CallbackQuery):
            return False
        parsed = parse_callback(event.data or "")
        return parsed.command in _OWNED_COMMANDS

    async def handle(self, event: TelegramEvent, ctx: FSMContext) -> None:
        assert isinstance(event, CallbackQuery) and event.from_user is not None
        user_id = event.from_user.id
        parsed = parse_callback(event.data or "")

        if parsed.command == "nav_watch":
            status = self._auto_watch_manager.status(user_id)
            # Auto-Watch's FSM state must reflect whether a watch is
            # ACTUALLY running, not just "the user navigated here" - a
            # real bug caught by tests.test_watch_flow_integration while
            # building this handler: transitioning to IDLE unconditionally
            # (matching every other nav_* jump's pattern) made the screen
            # show "stopped" even while a watch was genuinely active,
            # since render_auto_watch's button is driven by FSM state,
            # not by `status` alone (rendering/menus.py's own docstring).
            target_state = FSMState.AUTO_WATCH_ACTIVE if status is not None else FSMState.IDLE
            new_ctx = self._fsm.transition(user_id, target_state)
            await send_rendered(event, render_auto_watch(new_ctx, status=status))
            return

        if parsed.command == "nav_filters":
            self._fsm.transition(user_id, FSMState.IDLE)
            await send_rendered(event, render_my_filters())
            return

        if parsed.command == "rule_preset":
            await self._handle_preset_selected(event, user_id, parsed.params)
            return

        if parsed.command == "rule_advanced":
            await self._handle_advanced_rules_entry(event, user_id, ctx)
            return

        if parsed.command == "rule_tgl":
            await self._handle_toggle_field(event, user_id, ctx, parsed.params)
            return

        if parsed.command == "rule_num":
            await self._handle_cycle_field(event, user_id, ctx, parsed.params)
            return

        if parsed.command == "rule_save":
            await self._handle_save_custom(event, user_id, ctx)
            return

        if parsed.command == "watch_start":
            await self._handle_watch_start(event, user_id, ctx)
            return

        if parsed.command == "watch_stop":
            await self._handle_watch_stop(event, user_id)
            return

        if parsed.command in ("scan_trending", "scan_new"):
            await self._handle_discovery_scan(event, user_id, source=parsed.command)
            return

    async def _handle_preset_selected(self, event: CallbackQuery, user_id: int, params: list[str]) -> None:
        """Stores the choice in the FSM's own payload
        (`state.fsm.FSMEngine.transition`'s `**payload` merge) rather
        than a new dedicated store — this is exactly the kind of small,
        per-user, session-scoped value that payload already exists for
        (Part I.3: no persistence beyond a session either way).
        `_handle_watch_start` reads it back via `ctx.payload.get(...)`."""
        preset_key = params[0] if params else "balanced"
        if preset_key not in NAMED_PRESETS and preset_key != "custom":
            preset_key = "balanced"
        new_ctx = self._fsm.transition(user_id, FSMState.IDLE, selected_filter_preset=preset_key)
        await send_rendered(event, render_filter_config(selected_preset=new_ctx.payload.get("selected_filter_preset")))

    async def _handle_advanced_rules_entry(self, event: CallbackQuery, user_id: int, ctx: FSMContext) -> None:
        """rule_advanced: Filter Config -> Advanced Rules (Part II.5 site
        map). Resumes this user's in-progress draft if one already exists
        in payload, or seeds a fresh one from Balanced otherwise - same
        payload-as-session-scoped-store pattern _handle_preset_selected
        already uses for `selected_filter_preset`."""
        draft = ctx.payload.get("custom_filter_draft") or default_custom_profile()
        new_ctx = self._fsm.transition(user_id, FSMState.CONFIGURING_FILTER, custom_filter_draft=draft)
        await send_rendered(event, render_advanced_rules(new_ctx.payload["custom_filter_draft"]))

    async def _handle_toggle_field(
        self, event: CallbackQuery, user_id: int, ctx: FSMContext, params: list[str]
    ) -> None:
        """rule_tgl:<field_key>:<on|off>. Sets the field to the explicit
        value the tapped button encoded (see filter_presets.set_bool_field
        for why that's safer than a blind flip against a stale button).
        A malformed callback (missing params) re-renders the current
        draft unchanged rather than raising - the tap simply has no
        effect, which is the correct behavior for input this app itself
        generated and a client redraw will immediately correct anyway."""
        draft = ctx.payload.get("custom_filter_draft") or default_custom_profile()
        if len(params) >= 2:
            draft = set_bool_field(draft, params[0], value=(params[1] == "on"))
        new_ctx = self._fsm.transition(user_id, FSMState.CONFIGURING_FILTER, custom_filter_draft=draft)
        await send_rendered(event, render_advanced_rules(new_ctx.payload["custom_filter_draft"]))

    async def _handle_cycle_field(
        self, event: CallbackQuery, user_id: int, ctx: FSMContext, params: list[str]
    ) -> None:
        """rule_num:<field_key>. Mirrors _handle_toggle_field one layer
        down (cycle_numeric_field instead of set_bool_field)."""
        draft = ctx.payload.get("custom_filter_draft") or default_custom_profile()
        if params:
            draft = cycle_numeric_field(draft, params[0])
        new_ctx = self._fsm.transition(user_id, FSMState.CONFIGURING_FILTER, custom_filter_draft=draft)
        await send_rendered(event, render_advanced_rules(new_ctx.payload["custom_filter_draft"]))

    async def _handle_save_custom(self, event: CallbackQuery, user_id: int, ctx: FSMContext) -> None:
        """rule_save: commits the in-progress draft as this user's
        selected preset. Sets *both* `selected_filter_preset="custom"`
        and keeps `custom_filter_draft` in payload - `_handle_watch_start`
        reads the draft object directly for "custom" rather than doing a
        NAMED_PRESETS lookup that has no "custom" key, which was this
        pass's whole bug. Falls back to a fresh Balanced-seeded draft if
        somehow reached with none in payload (defensive; every real path
        here goes through rule_advanced first, which always sets one)."""
        draft = ctx.payload.get("custom_filter_draft") or default_custom_profile()
        self._fsm.transition(
            user_id, FSMState.IDLE, selected_filter_preset="custom", custom_filter_draft=draft
        )
        await send_rendered(event, render_filter_config(selected_preset="custom"))

    async def _handle_watch_start(self, event: CallbackQuery, user_id: int, ctx: FSMContext) -> None:
        """Reads whichever preset `rule_preset:*` last stored for this
        user (Filter Config and Auto-Watch are separate screens per Part
        II.5's site map — this is what connects the two without a new
        FSM state), defaulting to Balanced (this module's own documented
        assumption) if the user starts a watch without ever visiting
        Filter Config first.

        `AutoWatchManager.on_match` is bound once, at construction, by
        `main.py`'s composition root — not re-supplied per call here.
        This handler only ever calls `start`/`stop`/`status` on an
        already-fully-wired manager, exactly the same relationship
        `ScanHandler` has with the already-constructed engines it calls
        `run_scan` against.

        Step 12 revalidation fix: `preset_key == "custom"` used to fall
        through to `NAMED_PRESETS.get("custom", NAMED_PRESETS["balanced"])`
        — "custom" is never a NAMED_PRESETS key, so every custom-configured
        watch silently ran as Balanced instead. Now reads the actual draft
        `rule_advanced`/`rule_tgl`/`rule_num`/`rule_save` built, with the
        same Balanced fallback only for the edge case of a "custom"
        selection with no draft ever built (e.g. picked from Filter
        Config's preset row without ever opening Advanced Rules)."""
        preset_key = ctx.payload.get("selected_filter_preset", "balanced")
        if preset_key == "custom":
            profile = ctx.payload.get("custom_filter_draft") or NAMED_PRESETS["balanced"]
        else:
            profile = NAMED_PRESETS.get(preset_key, NAMED_PRESETS["balanced"])

        new_ctx = self._fsm.transition(user_id, FSMState.AUTO_WATCH_ACTIVE)
        await self._auto_watch_manager.start(user_id, profile, interval_min=_DEFAULT_INTERVAL_MIN)
        status = self._auto_watch_manager.status(user_id)
        await send_rendered(event, render_auto_watch(new_ctx, status=status))

    async def _handle_watch_stop(self, event: CallbackQuery, user_id: int) -> None:
        await self._auto_watch_manager.stop(user_id)
        new_ctx = self._fsm.transition(user_id, FSMState.IDLE)
        await send_rendered(event, render_auto_watch(new_ctx, status=None))

    async def _handle_discovery_scan(self, event: CallbackQuery, user_id: int, source: str) -> None:
        """On-demand version of what Auto-Watch does on a timer: fetch
        candidates from the discovery feed named by this specific button
        (Trending -> boosted, New -> new_listings — see
        `TokenDiscoveryProvider`'s own docstring for what each actually
        means), scan every one through the real pipeline, show them all
        as a Result List. No filter applied here (unlike Auto-Watch,
        this is "show me what's out there right now," not "alert me on
        a match"), and bounded to `_MAX_DISCOVERY_SCAN_CANDIDATES` so one
        tap can't trigger an unbounded number of scans."""
        self._fsm.transition(user_id, FSMState.SCANNING)

        try:
            if source == "scan_trending":
                candidates = await self._discovery_provider.get_trending(limit=_MAX_DISCOVERY_SCAN_CANDIDATES)
            else:
                candidates = await self._discovery_provider.get_new_listings(limit=_MAX_DISCOVERY_SCAN_CANDIDATES)
        except Exception as exc:
            logger.exception("Discovery feed fetch failed", extra={"user_id": user_id, "source": source})
            self._fsm.transition(user_id, FSMState.IDLE)
            show_technical = self._session_store.get_settings(user_id).show_technical_errors
            await send_rendered(
                event,
                render_error(
                    _DISCOVERY_FETCH_FAILED_ERROR,
                    technical_detail=f"{type(exc).__name__}: {exc}",
                    show_technical=show_technical,
                    recovery_action=("\U0001f504 Try Again", "nav_scan"),
                ),
            )
            return

        results = []
        for candidate in candidates:
            try:
                scored = await run_scan(
                    candidate.token_address,
                    self._core_engine, self._security_engine, self._holder_engine,
                    self._momentum_engine, self._social_engine, self._scoring_pipeline, self._session_store,
                    chain_hint=candidate.chain,
                )
                results.append(scored)
            except Exception:
                logger.warning(
                    "Discovery scan failed for one candidate, skipping it",
                    extra={"user_id": user_id, "address": candidate.token_address},
                )
                continue

        self._fsm.transition(user_id, FSMState.RESULT_READY)
        await send_rendered(event, render_result_list(results))
