"""In-memory session store and UUID payload cache.

Playbook reference: Unified Developer Playbook, Part VIII Step 1;
callback/UUID-cache design described in Part II.7 (tier 2); background-
task registry added in Step 12 (landed as custom-roadmap Step 13).

This store is the *only* place per-user state lives. It is never backed by
a database, file, or external cache (Part I.3) — a process restart clears
it completely, by design. A single module-level composition-root instance
is created in main.py and injected into every handler; this class must
never be re-instantiated per-request.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from bot.types import SessionContext, UserSettings


class SessionStore:
    """Process-wide, in-memory-only session + UUID cache + background-task
    registry."""

    def __init__(self) -> None:
        self._sessions: dict[int, SessionContext] = {}
        self._uuid_cache: dict[str, Any] = {}
        self._watch_tasks: dict[int, asyncio.Task] = {}
        self._settings: dict[int, UserSettings] = {}

    def get(self, user_id: int) -> SessionContext | None:
        """Return the user's session context, or None if they have none yet
        (e.g. first-ever interaction, or state was cleared by a restart)."""
        return self._sessions.get(user_id)

    def set(self, user_id: int, ctx: SessionContext) -> None:
        """Store (or replace) a user's session context."""
        self._sessions[user_id] = ctx

    def cache_put(self, payload: Any) -> str:
        """Store a large payload (a scan result, a custom filter object)
        that would not fit in a 64-byte callback_data string, and return the
        short key a button can carry instead (Part II.7, tier 2)."""
        key = uuid.uuid4().hex
        self._uuid_cache[key] = payload
        return key

    def cache_get(self, key: str) -> Any | None:
        """Look up a previously cached payload by its key. Returns None for
        an unknown or already-expired key rather than raising, since a stale
        button tap is an expected, recoverable event (Part II.5)."""
        return self._uuid_cache.get(key)

    # -- Auto-Watch background-task registry (Step 12) ---------------------
    #
    # Deliberately thin: this class only holds and hands back raw
    # `asyncio.Task` handles, keyed by user id. It has no idea what the
    # tasks DO (that's `handlers.auto_watch.AutoWatchManager`'s job
    # entirely — Part V.2's separation of concerns applied here exactly
    # like everywhere else: this store is generic per-user storage, never
    # a place business logic lives). One task per user, same "single
    # process-wide instance, never re-created per request" contract as
    # the two dicts above.

    def set_watch_task(self, user_id: int, task: asyncio.Task) -> None:
        """Registers `task` as the user's active watch loop. Overwrites
        any previous entry for the same user without cancelling it —
        `AutoWatchManager.start` is the one place responsible for
        cancelling an old task before starting a new one; this method is
        pure storage, not a safety check."""
        self._watch_tasks[user_id] = task

    def get_watch_task(self, user_id: int) -> asyncio.Task | None:
        """Returns the user's active watch task, or `None` if they have
        none (never started, already stopped, or a stale/unknown user)."""
        return self._watch_tasks.get(user_id)

    def pop_watch_task(self, user_id: int) -> asyncio.Task | None:
        """Removes and returns the user's watch task in one step — the
        exact operation `AutoWatchManager.stop` needs (take the task out
        of the registry, then cancel the object it got back), so there's
        no window where a concurrent `start()` could see a task that's
        about to be cancelled."""
        return self._watch_tasks.pop(user_id, None)

    def all_watch_tasks(self) -> list[asyncio.Task]:
        """Every currently-registered watch task, across every user — a
        read-only snapshot (e.g. "how many watches are active system-
        wide"). Returns a fresh list, not a live view of the internal
        dict."""
        return list(self._watch_tasks.values())

    def clear_all_watch_tasks(self) -> list[asyncio.Task]:
        """Pops and returns every registered task in one atomic step —
        exactly what `AutoWatchManager.emergency_stop_all` needs (Step
        12's own Acceptance Criteria: "cancels every running task, not
        just the calling user's"): cancel every task AND leave the
        registry empty afterward, so a subsequent `status()` call for any
        user correctly reports no active watch, not a stale reference to
        a task that's already been cancelled."""
        tasks = list(self._watch_tasks.values())
        self._watch_tasks.clear()
        return tasks

    # -- Settings (Step 15) -------------------------------------------------
    #
    # Same shape as everything else in this class: one dict, keyed by
    # user id, in-memory only, wiped on restart (Part I.3 - "Settings
    # remain in-memory and session-only," Step 15's own Scope line).
    # `get_settings` never returns None - a user who has never touched
    # Settings still HAS settings, just the dataclass's own defaults, so
    # every caller (SettingsHandler, and any error-rendering call site
    # checking `show_technical_errors`) can call this unconditionally
    # without a None-check of its own.

    def get_settings(self, user_id: int) -> UserSettings:
        """Returns the user's current settings, creating and storing a
        fresh default `UserSettings()` on first access for that user
        (not just returning a throwaway default) - so a later
        `set_settings` call for the same user is always updating the
        same object future `get_settings` calls will see, rather than
        risking two independently-created defaults diverging."""
        if user_id not in self._settings:
            self._settings[user_id] = UserSettings()
        return self._settings[user_id]

    def set_settings(self, user_id: int, settings: UserSettings) -> None:
        """Stores (or replaces) a user's settings outright - callers
        build the new `UserSettings` (typically via
        `bot.settings_logic.cycle_field`/`toggle_field`/`reset_settings`,
        which each return a full new instance) and hand it here, mirroring
        `set()`'s own "pure storage, caller owns the mutation logic"
        contract above."""
        self._settings[user_id] = settings
