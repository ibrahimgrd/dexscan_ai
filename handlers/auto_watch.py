"""
Layer: Handlers — Auto-Watch background monitoring (Playbook Part VIII
Step 12; landed as custom-roadmap Step 13).

Background monitoring, never execution (Part I.3's non-negotiable
boundary, restated here since this is the one module in the whole
project that runs unattended, on its own timer, without a human tapping
anything): this file re-runs the SAME `scan_orchestration.run_scan`
pipeline every other handler uses, filters results through
`filter_presets.matches`, and calls an injected `on_match` hook — it
never constructs a trade, never calls anything in `integrations/*`
(which doesn't exist in this codebase yet regardless), and every match
still requires a human tap through Result Detail to go any further
(Part IV.2's Mode 2, exactly as the playbook names it).

Deliberately Telegram-free, same reasoning as `scan_orchestration.py`
(Part V.2): this file has zero aiogram import. `on_match` is an injected
async callback, not a direct `bot.send_message` call — whatever
Telegram-facing handler constructs this class supplies that callback,
the same "optional hook" pattern `run_scan`'s own progress hooks already
established. This is what makes `AutoWatchManager` fully testable with
a fake `TokenDiscoveryProvider` and a recording `on_match` fake, no
aiogram shim required.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from analysis.core_engine import CoreEngine
from analysis.filter_presets import FilterProfile, matches
from analysis.holder_engine import HolderEngine
from analysis.momentum_engine import MomentumEngine
from analysis.security_engine import SecurityEngine
from analysis.social_engine import SocialEngine
from bot.types import AutoWatchStatus
from handlers.scan_orchestration import ScoredResult, run_scan
from scoring.pipeline import ScoringPipeline
from state.session_store import SessionStore

logger = logging.getLogger(__name__)

OnMatchHook = Callable[[int, ScoredResult], Awaitable[None]]


class AutoWatchManager:
    """Part VIII Step 12's Public Interface exactly:
    `start`/`stop`/`emergency_stop_all`/`status`. One `asyncio.Task` per
    active user, held in `SessionStore`'s registry (this class never
    keeps its own copy of a Task — `SessionStore` is the single source
    of truth for "is user X currently being watched," so `status()`,
    `stop()`, and a process-wide `emergency_stop_all()` can never
    disagree about what's actually running).

    `discovery_provider` is `TokenDiscoveryProvider`, never the concrete
    `DexScreenerProvider` (Part V.2, Dependency Inversion — same
    contract every other engine in this codebase already holds itself
    to)."""

    def __init__(
        self,
        session_store: SessionStore,
        discovery_provider,  # TokenDiscoveryProvider (Protocol - no runtime import needed)
        core_engine: CoreEngine,
        security_engine: SecurityEngine,
        holder_engine: HolderEngine,
        momentum_engine: MomentumEngine,
        social_engine: SocialEngine,
        scoring_pipeline: ScoringPipeline,
        on_match: OnMatchHook,
        poll_interval_seconds_override: float | None = None,
    ) -> None:
        self._session_store = session_store
        self._discovery_provider = discovery_provider
        self._core_engine = core_engine
        self._security_engine = security_engine
        self._holder_engine = holder_engine
        self._momentum_engine = momentum_engine
        self._social_engine = social_engine
        self._scoring_pipeline = scoring_pipeline
        self._on_match = on_match
        # Tests need a real watch cycle to run in milliseconds, not
        # `interval_min` minutes (Step 12's own Definition of Done: "no
        # real asyncio.sleep durations... use a fake clock or a very
        # short test interval") - this override replaces the
        # interval-minutes-to-seconds conversion entirely when set,
        # rather than requiring every test to pass interval_min=0 and
        # rely on that happening to round to something fast.
        self._poll_interval_seconds_override = poll_interval_seconds_override
        self._statuses: dict[int, AutoWatchStatus] = {}

    async def start(self, user_id: int, profile: FilterProfile, interval_min: int) -> None:
        """Starting again while already running replaces the old watch
        cleanly (stops it first) rather than leaving two loops running
        for the same user — `stop()` already handles "no existing task"
        as a no-op, so this is always safe to call unconditionally."""
        await self.stop(user_id)

        self._statuses[user_id] = AutoWatchStatus(
            user_id=user_id,
            profile_name=profile.name,
            interval_min=interval_min,
            matches_found=0,
            started_at=datetime.now(timezone.utc),
        )
        task = asyncio.create_task(self._watch_loop(user_id, profile, interval_min))
        self._session_store.set_watch_task(user_id, task)

    async def stop(self, user_id: int) -> None:
        """Cancels and fully awaits the task before returning — Step
        12's own Constraint: "cancellable cleanly (no orphaned tasks)."
        Awaiting after cancel is what actually makes this true; a bare
        `.cancel()` without awaiting only *requests* cancellation; the
        task could still be mid-cleanup (or, worse, mid-iteration) when
        this method returns otherwise."""
        task = self._session_store.pop_watch_task(user_id)
        self._statuses.pop(user_id, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def emergency_stop_all(self) -> None:
        """Step 12's own Acceptance Criteria: cancels every running task
        system-wide, not just one user's — `SessionStore.clear_all_watch_tasks`
        atomically empties the registry so no stale entry survives this
        call for any user."""
        tasks = self._session_store.clear_all_watch_tasks()
        self._statuses.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def status(self, user_id: int) -> AutoWatchStatus | None:
        """`None` if this user has no active watch — never raises for an
        unknown/never-started user, same "absence is a normal outcome"
        contract `SessionStore.cache_get`/`get` already hold themselves
        to."""
        return self._statuses.get(user_id)

    async def _watch_loop(self, user_id: int, profile: FilterProfile, interval_min: int) -> None:
        """
        Runs until cancelled. Each cycle: fetch candidates from the
        discovery feed (Part VIII Step 12's own Internal Architecture:
        "the same scan pipeline Step 7/10 already built... never a
        parallel implementation" — `run_scan` here is the literal same
        function `scan_handler.py` calls, with every progress hook left
        at its default `None` since there's no live message to edit
        during a background cycle), filter through `matches`, call
        `on_match` once per newly-seen match, then sleep.

        `_alerted_addresses` is scoped to THIS task's own lifetime —
        reset on every `start()` call, never persisted (Part I.3) — so
        the same match is never alerted on twice in one watch session,
        but a stopped-and-restarted watch starts fresh rather than
        remembering what it already flagged before.

        STEP 14 VERIFICATION FIX (Auto-Watch Cooldown Review): this set
        records addresses that ALREADY MATCHED AND ALERTED, not every
        address ever scanned. The Playbook names no distinct time-based
        cooldown mechanism anywhere (Part VIII Step 12; confirmed absent
        from the full text search of the Playbook while resolving this
        question) — duplicate-ALERT prevention is the one, sufficient,
        documented mechanism (see this docstring's own wording above,
        unchanged). An earlier revision of this loop added every
        successfully-scanned address here regardless of whether it
        matched, which had the unintended side effect of also
        permanently skipping any RE-evaluation of a non-matching
        candidate for the rest of the watch session — including cases
        where a real, later match should have fired (`min_pool_age_hours`
        is a filter gate that a candidate mechanically crosses just by
        staying in the discovery feed as time passes; a recovered
        `degraded` engine is another). That over-broad suppression was
        never the documented intent ("the SAME MATCH... never alerted on
        twice") and is fixed here — see
        `tests/test_auto_watch.py::test_non_matching_candidate_is_re_evaluated_once_it_ages_past_the_threshold`
        for the regression test (red before this fix, green after).

        A single candidate's scan failing (a raised exception somewhere
        in `run_scan` — though Steps 4/5/8's own contracts mean this
        should be rare) is logged and skipped, not fatal to the whole
        cycle — Part IV.3's partial-failure principle applied at the
        per-candidate level: one bad address in a batch of thirty
        shouldn't take down the other twenty-nine.

        `asyncio.CancelledError` is deliberately re-raised, never
        caught-and-continued — that exception means `stop()` or
        `emergency_stop_all()` asked this loop to end, which is not a
        failure this method should try to recover from.
        """
        alerted_addresses: set[str] = set()
        interval_seconds = (
            self._poll_interval_seconds_override
            if self._poll_interval_seconds_override is not None
            else interval_min * 60.0
        )

        while True:
            try:
                candidates = await self._discovery_provider.get_new_listings()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Auto-Watch discovery fetch failed this cycle", extra={"user_id": user_id, "error": str(exc)})
                candidates = []

            for candidate in candidates:
                if candidate.token_address in alerted_addresses:
                    continue
                try:
                    scored = await run_scan(
                        candidate.token_address,
                        self._core_engine,
                        self._security_engine,
                        self._holder_engine,
                        self._momentum_engine,
                        self._social_engine,
                        self._scoring_pipeline,
                        self._session_store,
                        chain_hint=candidate.chain,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Auto-Watch scan failed for one candidate, skipping it",
                        extra={"user_id": user_id, "address": candidate.token_address, "error": str(exc)},
                    )
                    continue

                if matches(profile, scored):
                    alerted_addresses.add(candidate.token_address)
                    status = self._statuses.get(user_id)
                    if status is not None:
                        status.matches_found += 1
                    try:
                        await self._on_match(user_id, scored)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Auto-Watch on_match hook raised", extra={"user_id": user_id})

            await asyncio.sleep(interval_seconds)
