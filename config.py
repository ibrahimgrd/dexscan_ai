"""Typed application configuration.

Playbook reference: Unified Developer Playbook, Part VIII Step 1;
standard described in Part V.3.

`Settings` is the single object that reads environment variables. No other
module in this codebase should call `os.environ` directly — later steps
that need a new provider key (Step 4's DexScreener base URL, Step 5's
RugCheck key, Step 13's twitterapi.io key, etc.) add a field here.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loads from a `.env` file (see `.env.example`) or real environment
    variables, environment variables taking precedence."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str
    default_chain: str = "sol"
    log_level: str = "INFO"
    dexscreener_base_url: str = "https://api.dexscreener.com"
    rugcheck_api_key: str | None = None

    # Step 8 (Holder Engine) - both optional. `helius_api_key` unset means
    # `providers.solana_rpc.resolve_rpc_url` falls back to the free public
    # Solana RPC endpoint below with no key at all; setting it switches to
    # Helius's free tier ($0/mo, no card required, confirmed while
    # implementing this step) for higher rate limits, same wire protocol.
    # `solana_public_rpc_url` is itself override-able so a Shyft/QuickNode/
    # Chainstack free-tier URL (or a test double) can be dropped in without
    # touching provider code - see that module's docstring.
    helius_api_key: str | None = None
    solana_public_rpc_url: str = "https://api.mainnet-beta.solana.com"

    # Step 11 (custom roadmap) - Solana RPC fallback chain, tried in this
    # order (Helius, then these two, then the public URL above as the
    # guaranteed last resort) by `solana_rpc_parser.resolve_rpc_urls`.
    # QuickNode has no generic template - unlike Helius/Shyft, each
    # account's endpoint URL is unique, so this holds the whole URL,
    # pasted from the QuickNode dashboard, not a short key.
    quicknode_rpc_url: str | None = None
    shyft_api_key: str | None = None

    # Step 13 (Social Engine) - matches the TWITTERAPI_IO_KEY placeholder
    # already anticipated in .env.example since Step 1. No free tier to
    # fall back to without a key (unlike Step 8's Holder Engine) -
    # twitterapi.io is pay-per-call from the first request, so this one
    # is required for SocialEngine to run against real data rather than
    # optional-with-a-fallback.
    twitterapi_io_key: str | None = None
