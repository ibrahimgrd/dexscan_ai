"""
Layer: Integrations — deep-link construction (Playbook Part VIII Step 11;
format in Part IV.1).

HARD SECURITY BOUNDARY (Part I.3, restated here since this is the one
module in the whole project whose entire job is producing a URL a user
might actually tap): nothing in this file, or anywhere under
`integrations/`, ever holds a private key, requests a seed phrase,
custodies funds, signs a transaction, or submits one to a chain.
`build_deep_link` below is a pure string-formatting function - no
network call, no wallet, no side effect of any kind - and its output is
never opened or POSTed to by DexScan AI itself. The ONLY place a user
can actually reach one of these links is `handlers.trade_staging_handler
.approve_and_get_link`, and only after they've already seen the Trade
Staging screen's disclaimer and tapped "Approve & Open Bot" themselves -
this module has no opinion on that gate; it only formats a string.

Deliberately NOT where `enter_trade_staging`/`approve_and_get_link`/
`abort_trade_staging` live, despite Part IV.1 naming all three alongside
`build_deep_link` in the same breath. Documented deviation (Part VI: code
already written wins over prose describing it - the same rule Step 9's
README entry invoked for its own Momentum-wiring question): those three
functions need `state.fsm.FSMEngine` and `state.session_store.SessionStore`
to do their job (resolving a cached `ScoredResult`, transitioning FSM
state), which would make this module stateful and Telegram-flow-aware -
exactly what `handlers/auto_watch.py`'s own module docstring says
`handlers/*` is for ("Telegram-free... zero aiogram import" business
logic that still needs FSM/SessionStore) as distinct from what
`integrations/`, `analysis/`, and `scoring/` are for (pure, dependency-
free computation, Part V.2). `build_deep_link` fits the second kind
exactly; the staging lifecycle functions fit the first. They live in
`handlers/trade_staging_handler.py` accordingly - see that module's own
docstring for the same note from its side.
"""

from __future__ import annotations

from urllib.parse import quote

from bot.constants import Chain, TradingBot
from integrations.providers import BOT_PROVIDERS

# Telegram's own `start=` deep-link parameter is capped at 64 bytes -
# this project's own choice only in the sense of which value to fall
# back to when that cap would otherwise be exceeded (see
# `build_deep_link`'s docstring below), not the 64 itself.
_TELEGRAM_START_PARAM_MAX_LEN = 64

# A short, project-level referral tag (Part IV.1's literal
# `{referral_tag}_{contract_address}` format). Claude's documented
# assumption (Part VI "on ambiguity"): no referral program has been
# configured for this project, and each of the six bots' own real
# referral-tag conventions differ in practice (confirmed while
# researching integrations/providers.py: "r-", "ref_", "i_", and
# "access_" all appear across different bots' own promotional links) -
# this project's Playbook specifies one uniform format, so that format
# is what's implemented here, simplification flagged rather than
# silently "corrected" per-bot.
_REFERRAL_TAG = "dexscan"


class UnsupportedChainError(Exception):
    """Raised when asked to build a deep link for a (bot, chain) pair
    `integrations.providers.BOT_PROVIDERS[bot].supported_chains` doesn't
    include - e.g. Trojan (Solana-only) for an ETH result. A correctly
    built caller never triggers this: Trade Staging only ever offers
    bots `integrations.providers.bots_for_chain` returns for the
    result's own chain. Exists as a defensive, typed error rather than a
    silently wrong link, the same role `state.fsm.InvalidTransitionError`
    plays for FSM misuse - a bug signal for the caller, not a recoverable
    user-facing condition."""

    def __init__(self, bot: TradingBot, chain: Chain) -> None:
        self.bot = bot
        self.chain = chain
        super().__init__(f"{bot.value} does not support {chain.value}")


def build_deep_link(
    bot: TradingBot,
    chain: Chain,
    address: str,
    *,
    slippage_pct: float | None = None,
    anti_mev: bool | None = None,
) -> str:
    """
    Builds the URL a "Approve & Open Bot" tap hands to Telegram's own
    `url=` button handling (the exact same client-side-only mechanism
    `rendering/result_renderer.py`'s pre-existing "View on Explorer"
    button already uses) - see module docstring for why this function
    itself never opens, fetches, or otherwise acts on what it returns.

    Raises `UnsupportedChainError` if `bot` doesn't support `chain` (see
    that exception's own docstring). `slippage_pct`/`anti_mev` are only
    ever included when `BotProvider.supports_slippage_param`/
    `supports_anti_mev_param` says the target bot actually accepts them
    (Part IV.1: "Slippage and anti-MEV preferences... passed through
    where the target bot supports them" - silently dropped, never
    raised, for a bot that doesn't; there is no way to express an
    unsupported preference on someone else's URL scheme). Neither
    parameter's exact query-string key is documented by any of the six
    bots (none publish a public deep-link API for this) - `slippage`/
    `anti_mev` are this project's own reasonable, documented choice, not
    a verified real integration; see `integrations/providers.py`'s
    Verification Status note for the same caveat applied to the base
    URLs themselves.
    """
    provider = BOT_PROVIDERS[bot]
    if chain not in provider.supported_chains:
        raise UnsupportedChainError(bot, chain)

    extra_params: list[str] = []
    if provider.supports_slippage_param and slippage_pct is not None:
        extra_params.append(f"slippage={slippage_pct:g}")
    if provider.supports_anti_mev_param and anti_mev is not None:
        extra_params.append(f"anti_mev={'on' if anti_mev else 'off'}")

    if provider.uses_telegram_start_param:
        start_param = f"{_REFERRAL_TAG}_{address}"
        if len(start_param) > _TELEGRAM_START_PARAM_MAX_LEN:
            # Losing referral credit is a cosmetic loss; a truncated
            # address would silently point at the WRONG token, which is
            # a safety issue, not a cosmetic one - drop the prefix
            # entirely rather than truncate. Not reachable with today's
            # three address families (longest is TON at 48 chars, well
            # under the cap even with `_REFERRAL_TAG` attached) - kept
            # as a real, tested branch rather than an unchecked
            # assumption, since a longer future address family (Part
            # II.6's own Future Compatibility note) could change that.
            start_param = address
        url = f"{provider.base_url}?start={quote(start_param, safe='')}"
        if extra_params:
            url += "&" + "&".join(extra_params)
        return url

    # Non-Telegram branch (Photon today - see integrations/providers.py
    # module docstring, finding #2): address goes directly in the path.
    # No referral-start mechanism exists to attach to, and `extra_params`
    # is always empty here in practice (Photon's own BotProvider row
    # sets both `supports_*_param` flags False) - not asserted, so a
    # future non-Telegram provider with real query-param support isn't
    # silently blocked by this function.
    url = f"{provider.base_url}/{quote(address, safe='')}"
    if extra_params:
        url += "?" + "&".join(extra_params)
    return url
