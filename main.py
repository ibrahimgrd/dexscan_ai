"""
Entry point.

Playbook reference: Unified Developer Playbook, Part VIII Steps 3-7 -
HTML menu rendering + Callback Parser + network recovery loop (Step 3),
wired to the real Core/Security/Scoring stack (Steps 4-7), the Holder
Engine (Step 8), Momentum + Scoring v2 + the Risk/Opportunity Matrix
(custom-roadmap Steps 9/12), Auto-Watch + Filter Presets (Playbook Step
12, landed as custom-roadmap Step 13), the Trading Integration Layer +
Trade Staging (Playbook Step 11, landed as custom-roadmap Step 14), and
now the Social Intelligence Engine + Scoring v3 (Playbook Step 13/14,
this pass - see README.md's "Continuing the build" table for why custom-
roadmap numbers and Playbook numbers diverge; this docstring names the
Playbook number for every milestone specifically so that history stops
compounding here). This pass completes the Platform-phase milestone
(Part VII.2) - the full original Blueprint's documented five-engine
scope is now wired end to end.
"""
from __future__ import annotations
import asyncio
import logging
import aiohttp
from aiogram import Bot
from aiogram import Dispatcher as AiogramDispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import CallbackQuery, Message
from aiohttp import ClientError
import logging_setup
from analysis.core_engine import CoreEngine
from analysis.holder_engine import HolderEngine
from analysis.momentum_engine import MomentumEngine
from analysis.providers.dexscreener import DexScreenerProvider
from analysis.providers.rugcheck import RugCheckProvider
from analysis.providers.solana_rpc import SolanaRpcHolderProvider
from analysis.providers.solana_rpc_parser import resolve_rpc_urls
from analysis.providers.twitterapi_io import TwitterApiIoProvider
from analysis.security_engine import SecurityEngine
from analysis.social_engine import SocialEngine
from config import Settings
from handlers.auto_watch import AutoWatchManager
from handlers.dispatcher import Dispatcher as ChainDispatcher
from handlers.help_handler import HelpHandler
from handlers.navigation import build_navigation_handlers
from handlers.scan_handler import ScanHandler
from handlers.scan_orchestration import ScoredResult
from handlers.settings_handler import SettingsHandler
from handlers.trade_staging_handler import TradeStagingHandler
from handlers.watch_handler import WatchHandler
from rendering.result_renderer import render_watch_alert
from scoring.pipeline import ScoringPipeline
from state.fsm import FSMEngine
from state.session_store import SessionStore
logger = logging.getLogger(__name__)
async def main() -> None:
    settings = Settings()
    logging_setup.configure(settings.log_level)
    logger.info(
        "DexScan AI starting",
        extra={
            "default_chain": settings.default_chain,
            "playbook_step": 14,
            # No custom_roadmap_step logged for this pass deliberately -
            # STEP11_HANDOFF's own plan redirects away from continuing
            # the custom roadmap's own Step 15 (Database Persistence,
            # README's flagged open architecture question) and finishes
            # Playbook Steps 12-14 directly instead. Inventing a fake
            # custom-roadmap number for that would misrepresent it.
        },
    )
    if not settings.twitterapi_io_key:
        logger.warning(
            "twitterapi_io_key is not set - Social Engine will degrade on every "
            "call (Part IV.3's partial-failure tolerance: only the Social section "
            "of a scan is affected, not the other four engines)."
        )
    # Process-wide singletons (Part II.3)
    session_store = SessionStore()
    fsm = FSMEngine(session_store)

    # One aiohttp session for the life of the process, shared by every
    # provider - closed alongside the bot's own session in `finally`.
    http_session = aiohttp.ClientSession()
    market_provider = DexScreenerProvider(http_session, base_url=settings.dexscreener_base_url)
    security_provider = RugCheckProvider(http_session, api_key=settings.rugcheck_api_key)
    holder_provider = SolanaRpcHolderProvider(
        http_session,
        rpc_urls=resolve_rpc_urls(
            settings.helius_api_key, settings.quicknode_rpc_url, settings.shyft_api_key, settings.solana_public_rpc_url
        ),
    )
    # Empty string, not None, when unset - deliberately NOT special-cased
    # any further than that. An empty/invalid key fails twitterapi.io's
    # own auth check same as any other bad key would, which
    # TwitterApiIoProvider's retry/backoff already exhausts and raises
    # past, which SocialEngine.analyze already catches into a normal
    # `degraded=True` SocialResult (Step 13's own contract) - the exact
    # same "unmeasured, not a confirmed finding" path a real transient
    # outage takes. No separate no-key code path needed anywhere.
    social_provider = TwitterApiIoProvider(http_session, api_key=settings.twitterapi_io_key or "")
    core_engine = CoreEngine(market_provider)
    security_engine = SecurityEngine(security_provider)
    holder_engine = HolderEngine(holder_provider)
    # No provider: Step 9's own Scope note - "the one engine in this
    # playbook that's purely computational" - it only combines
    # CoreResult/HolderResult/SocialResult, never calls an external API
    # itself.
    momentum_engine = MomentumEngine()
    social_engine = SocialEngine(social_provider)
    scoring_pipeline = ScoringPipeline()

    # Step 3 Requirement: HTML Parse Mode for rich inline keyboard menus.
    # Constructed here, ahead of AutoWatchManager below, since its
    # on_match hook needs a real `bot` to actually deliver an alert
    # (Part II.8's documented "new message, not an edit" exception).
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    async def _deliver_watch_alert(user_id: int, scored: ScoredResult) -> None:
        alert = render_watch_alert(scored)
        try:
            await bot.send_message(user_id, alert.html, parse_mode="HTML", reply_markup=alert.keyboard)
        except Exception:
            logger.exception("Failed to deliver Auto-Watch alert", extra={"user_id": user_id})

    # market_provider also satisfies TokenDiscoveryProvider (Part V.2 -
    # one concrete adapter, two Protocols; see DexScreenerProvider's own
    # docstring) - no second provider instance needed for discovery.
    auto_watch_manager = AutoWatchManager(
        session_store=session_store,
        discovery_provider=market_provider,
        core_engine=core_engine,
        security_engine=security_engine,
        holder_engine=holder_engine,
        momentum_engine=momentum_engine,
        social_engine=social_engine,
        scoring_pipeline=scoring_pipeline,
        on_match=_deliver_watch_alert,
    )

    chain_dispatcher = ChainDispatcher(fsm)
    scan_handler = ScanHandler(
        fsm, core_engine, security_engine, holder_engine, momentum_engine, social_engine,
        scoring_pipeline, session_store,
    )
    watch_handler = WatchHandler(
        fsm, auto_watch_manager, market_provider,
        core_engine, security_engine, holder_engine, momentum_engine, social_engine,
        scoring_pipeline, session_store,
    )
    trade_staging_handler = TradeStagingHandler(fsm, session_store)
    settings_handler = SettingsHandler(fsm, session_store)
    help_handler = HelpHandler(fsm)
    for handler in build_navigation_handlers(
        fsm,
        session_store,
        extra_handlers=[scan_handler, watch_handler, trade_staging_handler, settings_handler, help_handler],
    ):
        chain_dispatcher.register(handler)

    aiogram_dispatcher = AiogramDispatcher()
    @aiogram_dispatcher.message()
    async def _route_message(message: Message) -> None:
        await chain_dispatcher.dispatch(message)
    @aiogram_dispatcher.callback_query()
    async def _route_callback(callback_query: CallbackQuery) -> None:
        await chain_dispatcher.dispatch(callback_query)
    retry_delay = 3  # Initial backoff delay in seconds
    max_retry_delay = 60  # Maximum backoff delay
    try:
        while True:
            try:
                logger.info("Starting polling...")
                await aiogram_dispatcher.start_polling(bot)
                break  # Exit loop if polling stops cleanly without error
            except (TelegramNetworkError, ClientError, TimeoutError, OSError) as exc:
                logger.warning(
                    "Network interruption detected (%s). Retrying in %d seconds...",
                    exc,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                # Exponential backoff
                retry_delay = min(retry_delay * 2, max_retry_delay)
            except (KeyboardInterrupt, asyncio.CancelledError):
                logger.info("Polling stopped manually by user.")
                break
            except Exception as exc:
                logger.exception("Unexpected error during polling: %s", exc)
                await asyncio.sleep(retry_delay)
    finally:
        # Step 12's own Constraint: "no orphaned tasks on /stop or
        # process shutdown" - every active Auto-Watch loop is cancelled
        # and awaited before either session closes underneath it.
        logger.info("Stopping all Auto-Watch tasks...")
        await auto_watch_manager.emergency_stop_all()
        logger.info("Closing bot session...")
        await bot.session.close()
        await http_session.close()
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application exited.")
