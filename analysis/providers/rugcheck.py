"""
Layer: Provider adapter — RugCheck network I/O (Playbook Part II.3;
Part VIII Step 5; retry/backoff added in the Step 11 [custom roadmap]
reliability pass).

Just the HTTP call: constructs the request, checks the status, hands the
JSON body to `rugcheck_parser.parse_report`. All schema knowledge lives
in that sibling module (deliberately split out — see its own docstring
for why, and for the per-field confidence levels this adapter is built
on); this file's only job is talking to the network.

Base URL and endpoint path verified against RugCheck's own Swagger
reference (api.rugcheck.xyz/swagger) and a real CLI output sample from a
third-party wrapper, while implementing this step.

Chain coverage: RugCheck's `/tokens/scan/{chain}/{contractAddress}`
endpoint does accept a chain parameter, but every source consulted while
researching this step — RugCheck's own site positioning, "mint address"
terminology throughout its data model, and every third-party description
found — describes it as Solana's security scanner specifically, with no
confirmed evidence of mature non-Solana coverage. This adapter attempts
the live call for any chain (it may genuinely work for more than Solana
today, or more chains may gain coverage over time) but treats a request
for a non-Solana chain that comes back empty or 404 as
`chain_supported=False` rather than a transport failure — see
`_is_chain_unsupported_response`. Solana is the one case this provider's
behavior should be trusted with confidence; anything else is best-effort
until verified against a live response.

Retry/backoff, not multi-provider fallback (Step 11, custom roadmap):
unlike `solana_rpc.py`'s `SolanaRpcHolderProvider`, which can genuinely
fail over to a different provider because every Solana RPC vendor speaks
the identical standard JSON-RPC protocol, RugCheck isn't an RPC node at
all — it's a specialized analysis service computing things (LP lock
ratio, ownership renouncement, tax simulation) no generic Solana RPC
endpoint exposes on its own. A QuickNode/Shyft-style swap can't stand in
for a RugCheck outage; there is no second verified provider with the
same feature set to fail over to (Step 5's own Future Compatibility note
already anticipates one existing someday — `SecurityEngine.__init__`
was deliberately designed to accept a list of providers when one does —
but naming a specific real one is out of scope here without a person
identifying which). What retrying CAN genuinely fix — a transient
timeout or dropped connection, not a sustained outage — it does: up to
`_MAX_RETRY_ATTEMPTS` attempts with exponential backoff before this
method finally raises for `SecurityEngine` to catch and degrade.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from analysis.api_abstraction import SecurityReport
from analysis.providers.rugcheck_parser import parse_report
from bot.constants import Chain

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.rugcheck.xyz/v1"
_REQUEST_TIMEOUT_SECONDS = 10

# 3 attempts, 1s/2s exponential backoff between them (so: attempt, wait
# 1s, attempt, wait 2s, attempt) - long enough to ride out a brief
# network blip or a momentary 5xx, short enough that a genuinely down
# RugCheck still fails within a bounded ~23s worst case
# (10+1+10+2+10) rather than hanging indefinitely.
_MAX_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SECONDS = 1.0

# See module docstring - Solana is confirmed; everything else is
# attempted but not trusted the same way.
_CONFIRMED_SUPPORTED_CHAINS = frozenset({Chain.SOL})

_RUGCHECK_CHAIN_ID: dict[Chain, str] = {
    Chain.SOL: "solana",
    Chain.ETH: "ethereum",
    Chain.BSC: "bsc",
    Chain.BASE: "base",
    Chain.ARB: "arbitrum",
    # TON deliberately absent - no evidence RugCheck covers it at all;
    # scan() returns chain_supported=False immediately rather than
    # guessing at a chain-id string with zero supporting evidence.
}


class RugCheckProvider:
    """Satisfies `analysis.api_abstraction.SecurityDataProvider`. Holds
    its own `aiohttp.ClientSession` — `main.py`'s composition root
    constructs one instance for the life of the process, same pattern as
    `DexScreenerProvider` (Part II.3).

    `api_key`, if provided, is sent as `X-API-KEY` — sources disagreed on
    whether RugCheck's read/scan endpoints require one at all (some
    describe it as a free, keyless API; one source described key-gated
    access), so this supports the key without requiring it, working
    either way. `config.Settings` would carry this as an optional
    `rugcheck_api_key` field, already anticipated in `.env.example`
    since Step 1."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str | None = None,
        base_url: str = _BASE_URL,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._base_url = base_url

    async def scan(self, address: str, chain: Chain) -> SecurityReport:
        """
        GET /tokens/scan/{chain}/{contractAddress}, retried up to
        `_MAX_RETRY_ATTEMPTS` times with exponential backoff on a
        transport-level failure (timeout, connection error, unexpected
        5xx) before finally raising — `SecurityEngine` catches and
        degrades only after every retry is exhausted. A 404 or empty
        body is NOT a transport failure and is never retried: it's
        parsed as `chain_supported=False` when the chain isn't a
        confirmed one (see module docstring), or as an empty-but-valid
        report otherwise — both are real, immediate answers, not a
        connectivity problem retrying could fix.
        """
        chain_id = _RUGCHECK_CHAIN_ID.get(chain)
        if chain_id is None:
            return parse_report({}, chain_supported=False)

        url = f"{self._base_url}/tokens/scan/{chain_id}/{address}"
        headers = {"X-API-KEY": self._api_key} if self._api_key else {}

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRY_ATTEMPTS):
            try:
                async with self._session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
                ) as response:
                    if response.status == 404:
                        return parse_report({}, chain_supported=chain in _CONFIRMED_SUPPORTED_CHAINS)
                    if response.status != 200:
                        body_preview = (await response.text())[:200]
                        raise RuntimeError(f"RugCheck returned HTTP {response.status}: {body_preview}")
                    payload = await response.json()
                return parse_report(payload, chain_supported=True)
            except (aiohttp.ClientError, RuntimeError, asyncio.TimeoutError) as exc:
                last_exc = exc
                is_last_attempt = attempt == _MAX_RETRY_ATTEMPTS - 1
                if is_last_attempt:
                    break
                backoff_seconds = _RETRY_BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "RugCheck request failed, retrying",
                    extra={"attempt": attempt + 1, "max_attempts": _MAX_RETRY_ATTEMPTS, "error": str(exc)},
                )
                await asyncio.sleep(backoff_seconds)

        raise RuntimeError(f"RugCheck request failed after {_MAX_RETRY_ATTEMPTS} attempts: {last_exc}") from last_exc
