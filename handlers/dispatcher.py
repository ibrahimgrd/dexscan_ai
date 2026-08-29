"""
Layer: Handlers — central dispatcher (Playbook Part VIII Step 2; routing
architecture in Part II.1/II.2). Step 3 adds the callback parser as the
first thing `dispatch()` does for any CallbackQuery.

Chain-of-responsibility: walks registered handlers in order, calling
`can_handle` on each until one returns True, then calls its `handle` and
stops. Registration order matters — more specific handlers go first, with
a catch-all (`handlers.navigation.UnknownInputHandler`) registered last,
so the chain always has *some* match.

Zero references to any analysis, scoring, or trading module (Step 2
Definition of Done) — Step 3's addition is `handlers.callback_parser`, a
parsing utility, not a business-logic module, so this constraint still
holds.

Step 3 note on "register the parser as the first chain link" (Part VIII
Step 3's Scope): `parse_callback` runs here, unconditionally, before the
handler loop even starts — genuinely first, not just first *among
handlers*. What it produces is used for logging visibility in this
module; the actual routing decision is still each handler's own job
(most concretely `handlers.navigation.MenuNavigationHandler`, which parses
again to read `.command` — cheap, pure, and cheaper than threading a
parsed value through the `Handler` protocol's signature, which Step 2
already fixed as `(event, ctx)`. Changing that signature now to carry a
third value would be exactly the kind of interface break Part VI's
execution protocol says to avoid unless something is actually broken.
"""

from __future__ import annotations

import logging

from aiogram.types import CallbackQuery

from handlers.base import Handler, TelegramEvent, reply
from handlers.callback_parser import parse_callback
from state.fsm import FSMEngine

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, fsm: FSMEngine) -> None:
        self._fsm = fsm
        self._handlers: list[Handler] = []

    def register(self, handler: Handler) -> None:
        self._handlers.append(handler)

    async def dispatch(self, event: TelegramEvent) -> None:
        """
        Immediate callback acknowledgment (Part II.8) happens before
        anything else — the client's loading spinner clears regardless
        of how long routing/handling actually takes. Parsing (Step 3)
        happens right after, still before any handler sees the event.
        """
        if isinstance(event, CallbackQuery):
            await event.answer()
            parsed = parse_callback(event.data or "")
            logger.debug(
                "Callback received", extra={"command": parsed.command, "params": parsed.params}
            )

        user_id = event.from_user.id if event.from_user else None
        if user_id is None:
            logger.warning("Event carried no identifiable user; dropping")
            return

        ctx = self._fsm.get_state(user_id)

        for handler in self._handlers:
            try:
                if await handler.can_handle(event, ctx):
                    await handler.handle(event, ctx)
                    return
            except Exception:
                # Minimal safety net (Part IV.4: zero dead ends). Step 15
                # replaces this with the full plain-language, opt-in-
                # technical-detail error screen (Part IV.3) — this is a
                # deliberately broad `except Exception` at the outermost
                # dispatch boundary specifically so one bad handler can
                # never leave a user with no response at all; it is not a
                # substitute for the specific, typed error handling every
                # engine does internally (Part V.5).
                logger.exception("Handler raised while processing event")
                await reply(event, "Something went wrong on that last action. Send /start for a fresh Main Menu.")
                return

        # Reachable only if the caller built a Dispatcher without
        # registering handlers.navigation.UnknownInputHandler last — a
        # wiring bug in main.py, not a user-facing condition.
        logger.error("No handler matched and no catch-all was registered")
