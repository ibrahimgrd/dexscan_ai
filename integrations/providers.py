"""
Layer: Integrations — the six-bot provider table (Playbook Part VIII
Step 11; provider list in Part IV.1).

Pure, dependency-free data (Part V.2's purity contract, same as
`analysis/filter_presets.py` and `scoring/risk_opportunity_matrix.py`):
no network I/O, no engine calls, no FSM/SessionStore access. Nothing
here ever touches a wallet, a key, or a transaction — every value in
this table only ever ends up formatted into an outbound `url=` button
(Telegram opens it client-side, exactly like `rendering/result_renderer.py`'s
pre-existing block-explorer buttons); `integrations/trading.py`'s
`build_deep_link` is the one function that reads this table.

VERIFICATION STATUS — READ BEFORE SHIPPING TO REAL USERS. Bot usernames
and URLs below were checked via web search during this build session
(2026-08-16), not against each bot's own formal API docs, because none
of the six publishes one for "how to construct a pre-filled deep link" —
every source is a marketing page, a blog, or a referral post, and this
category is a well-documented phishing target (CoinGecko's own trading-
bot guide explicitly warns against searching Telegram manually for these
bots by name for exactly this reason). Treat every `link_template`/
`base_url` below as Claude's best-effort, timestamped snapshot per
Part VI's "on ambiguity" rule, not a verified-forever fact — re-confirm
against each bot's own official site before this ever reaches a real
user, and prefer making this table configurable (Settings, once Step 15
exists) over hand-editing it blind.

TWO REAL, TIME-SENSITIVE FINDINGS from that same search, both load-
bearing for this table's shape:

1. BullX suspended ALL trading functionality on 2026-06-01 ("pause for
   future upgrades," no return date given at the time of this session,
   multiple independent sources from as recently as ~2026-08 describe it
   as a de facto permanent shutdown). Kept in this table — the Playbook
   names six providers by name, and removing one outright is a bigger,
   less reversible deviation than flagging it — but `is_operational` is
   deliberately `False` with a `status_note` a renderer can surface, and
   `providers.py`'s own helpers (`bots_for_chain`/`default_bot_for_chain`
   below) never offer it as a default. Flip `is_operational` back to
   `True` once (if) it actually resumes; nothing else in this module
   needs to change to do that.
2. Photon (`photon-sol.tinyastro.io`) is NOT a Telegram bot — it is a
   web-only trading terminal with no documented `t.me` presence at all
   (confirmed against a source that specifically fact-checks other
   Photon marketing claims, not just repeats them). Modeled here with
   `uses_telegram_start_param=False` and a plain web `base_url` rather
   than forced into the other five bots' `t.me/...?start=...` shape —
   see `integrations/trading.py`'s `build_deep_link` for the branch this
   drives. Solana-only, same as Trojan.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.constants import Chain, TradingBot


@dataclass(frozen=True)
class BotProvider:
    """One row of Part IV.1's provider table.

    `supported_chains` is Claude's best-effort synthesis of each bot's
    own marketing/help pages as of this session — see module docstring's
    Verification Status note; it is deliberately conservative (a chain
    is only listed here if a source explicitly named it), so this table
    may under-list a bot's true coverage but should never over-promise
    it. `uses_telegram_start_param=True` means `address` (plus a short
    referral prefix) is passed via Telegram's own `?start=` deep-link
    parameter (Part IV.1's literal format); `False` means the bot has no
    such mechanism and the address is placed directly in the URL path
    instead (Photon, currently the only such case).
    """

    bot: TradingBot
    display_name: str
    supported_chains: frozenset[Chain]
    base_url: str
    uses_telegram_start_param: bool
    supports_slippage_param: bool
    supports_anti_mev_param: bool
    is_operational: bool = True
    status_note: str | None = None


# Six rows, TradingBot enum declaration order (bot/constants.py) — that
# order is also what `bots_for_chain` iterates in, so "Change Target
# Bot" cycles deterministically rather than depending on dict insertion
# order coincidentally matching it.
BOT_PROVIDERS: dict[TradingBot, BotProvider] = {
    TradingBot.TROJAN: BotProvider(
        bot=TradingBot.TROJAN,
        display_name="Trojan",
        supported_chains=frozenset({Chain.SOL}),
        base_url="https://t.me/solana_trojanbot",
        uses_telegram_start_param=True,
        supports_slippage_param=True,
        supports_anti_mev_param=True,
    ),
    TradingBot.BANANA_GUN: BotProvider(
        bot=TradingBot.BANANA_GUN,
        display_name="Banana Gun",
        # bananagun.io's own site names ETH/SOL/BSC/BASE plus several
        # chains outside this project's six (MegaETH, Robinhood, Stable,
        # Arc) — only the overlap with `bot.constants.Chain` is listed.
        supported_chains=frozenset({Chain.SOL, Chain.ETH, Chain.BSC, Chain.BASE}),
        base_url="https://t.me/BananaGun_Bot",
        uses_telegram_start_param=True,
        supports_slippage_param=True,
        supports_anti_mev_param=True,
    ),
    TradingBot.BULLX: BotProvider(
        bot=TradingBot.BULLX,
        display_name="BullX",
        supported_chains=frozenset({Chain.SOL, Chain.BSC, Chain.BASE, Chain.ARB}),
        base_url="https://t.me/BullxBetaBot",
        uses_telegram_start_param=True,
        supports_slippage_param=True,
        supports_anti_mev_param=False,
        is_operational=False,
        status_note="BullX paused all trading on 2026-06-01 and has not confirmed a return — see module docstring.",
    ),
    TradingBot.PHOTON: BotProvider(
        bot=TradingBot.PHOTON,
        display_name="Photon",
        supported_chains=frozenset({Chain.SOL}),
        # Documented assumption (module docstring, finding #2): Photon is
        # a web terminal, not a Telegram bot. This exact path shape
        # (.../lp/{address}) is the commonly-cited pattern for a Photon
        # token page but was NOT independently confirmed against
        # Photon's own docs during this session - verify before shipping.
        base_url="https://photon-sol.tinyastro.io/en/lp",
        uses_telegram_start_param=False,
        supports_slippage_param=False,
        supports_anti_mev_param=False,
    ),
    TradingBot.MAESTRO: BotProvider(
        bot=TradingBot.MAESTRO,
        display_name="Maestro",
        supported_chains=frozenset({Chain.SOL, Chain.ETH, Chain.BSC, Chain.BASE, Chain.ARB, Chain.TON}),
        base_url="https://t.me/maestro",
        uses_telegram_start_param=True,
        supports_slippage_param=True,
        supports_anti_mev_param=True,
    ),
    TradingBot.GMGN: BotProvider(
        bot=TradingBot.GMGN,
        display_name="GMGN",
        supported_chains=frozenset({Chain.SOL, Chain.BSC, Chain.BASE}),
        base_url="https://t.me/gmgnaibot",
        uses_telegram_start_param=True,
        supports_slippage_param=True,
        supports_anti_mev_param=False,
    ),
}


def bots_for_chain(chain: Chain, *, operational_only: bool = True) -> list[TradingBot]:
    """Every bot that supports `chain`, in `TradingBot`'s own declaration
    order (deterministic - callers that cycle through this, like
    `handlers.trade_staging_handler.change_target_bot`, always cycle the
    same direction). `operational_only=True` (the default) excludes
    BullX for as long as `BOT_PROVIDERS[TradingBot.BULLX].is_operational`
    stays `False` - the one place that flag actually changes behavior,
    not just display text."""
    return [
        bot
        for bot in TradingBot
        if chain in BOT_PROVIDERS[bot].supported_chains and (BOT_PROVIDERS[bot].is_operational or not operational_only)
    ]


def default_bot_for_chain(chain: Chain) -> TradingBot | None:
    """The first operational bot (declaration order) supporting `chain` -
    `None` only if every bot supporting this chain happens to be
    non-operational (not reachable today: all six chains this project
    supports have at least one operational bot backing them as of this
    table; kept as a real `None` case, not asserted-away, since a future
    edit to `is_operational` could change that and this function must
    stay honest if it does)."""
    candidates = bots_for_chain(chain, operational_only=True)
    return candidates[0] if candidates else None
