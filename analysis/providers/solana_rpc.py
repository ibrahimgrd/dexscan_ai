"""
Layer: Provider adapter — Solana JSON-RPC network I/O (Playbook Part
II.3; Part VIII Step 8; multi-endpoint fallback added in the Step 11
[custom roadmap] reliability pass).

FREE-TIER PROVIDER CHOICE — read before touching this file. Step 8's own
"Assumption stated explicitly" text named Solscan as the example holder
data source; Solscan's holder-data endpoints sit behind its paid Pro plan
(reported around $199/mo). This build uses the *standard* Solana
JSON-RPC surface instead — every method this file calls
(`getTokenSupply`, `getTokenLargestAccounts`, `getAccountInfo`,
`getSignaturesForAddress`, `getTransaction`) is part of the base
JSON-RPC 2.0 protocol every Solana RPC node speaks, verified against
Solana's own reference (solana.com/docs/rpc,
solana.com/docs/references/clusters) and Helius's docs (which mirror the
identical shapes for these) while implementing this step. Concretely,
that means `SolanaRpcHolderProvider` works, completely unmodified,
against any of the URLs `solana_rpc_parser.resolve_rpc_urls` can build:
the free public cluster endpoint, Helius, QuickNode, or Shyft — none of
what this file calls is specific to any one of them.

Multi-endpoint fallback (Step 11, custom roadmap): a scan that only ever
tries one RPC endpoint fails outright the moment that one endpoint has a
bad moment — a real, reported problem for the free public endpoint
specifically, whose own documented limits ("not intended for production
applications," solana.com/docs/references/clusters) make exactly this
kind of transient failure expected, not exceptional. `_batch_call` now
tries every endpoint `solana_rpc_parser.resolve_rpc_urls` returns, in
order, falling through to the next one on a transport-level failure
only — see that method's own docstring for the exact timeout/latency
tradeoff this makes.

Combination logic lives here, not in `solana_rpc_parser.py`: unlike
DexScreener/RugCheck (one HTTP call carries everything needed for one
result object), building a single `HolderRecord` needs up to three
separate RPC calls (supply, largest-accounts, and one owner-resolution
lookup per account). Orchestrating and combining those is still a
provider-adapter concern — assembling this specific provider's data into
the shape `HolderDataProvider` promises, the same job `dexscreener.py` /
`rugcheck.py` do with a single call each. `solana_rpc_parser.py`'s
functions do the actual field-level interpretation (and, as of this
pass, endpoint-list resolution — pure logic, moved there for the same
"testable without aiohttp" reason as everything else in that file) and
stay individually fixture-testable without a network.

Every multi-item lookup below (owner resolution, signature lookups,
transaction lookups) goes through `_batch_call`, which sends a JSON-RPC
2.0 *batch* request — one HTTP round trip carrying a JSON array — rather
than one request per item. This is standard JSON-RPC 2.0 behavior every
Solana RPC node supports, used here specifically to keep HTTP round trips
low against the free/public tier's tight rate limits (module docstring
above): up to 20 owner-resolution calls, or up to 10 signature/
transaction lookups, each become ONE round trip instead of many.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from analysis.api_abstraction import FundingRecord, HolderRecord
from analysis.providers.solana_rpc_parser import (
    RawTokenBalance,
    parse_account_owner,
    parse_funding_from_transaction,
    parse_largest_accounts,
    parse_signatures,
    parse_token_supply,
    resolve_rpc_urls,
)
from bot.constants import Chain

logger = logging.getLogger(__name__)

# RPC calls here are heavier than DexScreener/RugCheck's single-call
# adapters (owner resolution and transaction lookups add real latency
# under free-tier load) - a longer budget than core_engine.py's/
# security_engine.py's 10s, deliberately. Only ever applied to the LAST
# endpoint in the fallback list - see _batch_call's docstring for why
# every earlier endpoint gets a shorter budget instead.
_REQUEST_TIMEOUT_SECONDS = 15

# Every endpoint before the last one in the fallback list gets this
# shorter budget, not the full 15s above - a genuinely dead endpoint
# would otherwise cost a full 15s timeout PER call, PER endpoint, before
# ever reaching one that works; worst case with 3 fallback layers
# configured is still bounded (3 x 6s + one full 15s = ~33s) rather than
# unbounded. The final endpoint keeps the full budget deliberately -
# there's nothing left to fall back to if it also fails, so it deserves
# the original patience, not a shortened one.
_FALLBACK_ATTEMPT_TIMEOUT_SECONDS = 6

# `getTokenLargestAccounts` itself hard-caps at 20 - not this file's
# choice, Solana's own RPC method does (see holder_engine.py's docstring
# on why `HolderResult.holder_count_is_estimate` exists as a result).
_MAX_LARGEST_ACCOUNTS = 20

# Free-tier / public-endpoint economy (module docstring's rate-limit
# figures). Capped well under the public endpoint's documented 40-req/10s
# per-method ceiling even before any Helius key exists; each wallet here
# costs 2 more calls (signatures + transaction), both batched.
_MAX_FUNDING_LOOKUP_WALLETS = 10

# How many signatures to pull per wallet when hunting for its earliest
# transaction. A wallet with fewer than this many total transactions ever
# (the common case for a fresh, insider-funded buyer wallet - exactly the
# profile Part III.3's bundle detection cares about) gets its TRUE
# earliest transaction from this one page; a wallet with more is an old/
# active wallet for which "earliest within this page" is a documented
# best-effort approximation, not a confirmed true-earliest (see
# get_launch_block_funding's docstring below).
_SIGNATURES_PAGE_SIZE = 50


class SolanaRpcHolderProvider:
    """Satisfies `analysis.api_abstraction.HolderDataProvider`. Holds its
    own `aiohttp.ClientSession`, same pattern as `DexScreenerProvider` /
    `RugCheckProvider` (Part II.3: one process-wide instance, constructed
    by a composition root — wired into `main.py` as of the Step 9/10
    integration passes).

    Named `solana_rpc.py`, not the Playbook's own suggested
    `providers/chain_rpc.py` — this class only ever speaks to Solana (see
    `HolderEngine`'s chain gate, which never calls this provider for any
    other chain); a name implying multi-chain generality would overpromise
    what's actually built here. An EVM-focused provider, when one exists,
    is a new sibling module, not a rename of this one (this step's own
    Future Compatibility note).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        rpc_urls: list[str] | None = None,
        request_timeout_seconds: int = _REQUEST_TIMEOUT_SECONDS,
        fallback_attempt_timeout_seconds: int = _FALLBACK_ATTEMPT_TIMEOUT_SECONDS,
    ) -> None:
        """`rpc_urls` is the priority-ordered fallback list from
        `solana_rpc_parser.resolve_rpc_urls` — `None` defaults to that
        same function's own zero-config behavior (just the free public
        endpoint), so `SolanaRpcHolderProvider(session)` alone still
        works, same as before this pass. Never constructed with an empty
        list — `resolve_rpc_urls` always includes the public endpoint as
        a guaranteed last entry, and this constructor mirrors that
        guarantee for any other caller."""
        self._session = session
        self._rpc_urls = rpc_urls if rpc_urls else resolve_rpc_urls(None, None, None)
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_seconds)
        self._fallback_timeout = aiohttp.ClientTimeout(total=fallback_attempt_timeout_seconds)

    async def get_holders(self, address: str, chain: Chain) -> list[HolderRecord]:
        """
        Up to the top 20 holders (Solana RPC's own cap on
        `getTokenLargestAccounts` — module-level constant), each resolved
        from a token-account address to its owning wallet and expressed
        as a percent of total supply.

        Raises on any transport-level failure — `HolderEngine` catches
        and degrades, same contract `MarketDataProvider.get_pairs` /
        `SecurityDataProvider.scan` document. `chain` is accepted to
        satisfy the `HolderDataProvider` Protocol signature, but this
        provider only ever meaningfully supports `Chain.SOL` —
        `HolderEngine` gates on that before calling this method at all
        (its own docstring explains why), so reaching here with a
        different chain would itself indicate a caller bug, not a normal
        degrade path; it's asserted rather than silently handled.
        """
        assert chain is Chain.SOL, "SolanaRpcHolderProvider only supports Chain.SOL"

        total_supply = await self._get_token_supply(address)
        if total_supply <= 0:
            return []

        largest = await self._get_largest_accounts(address)
        if not largest:
            return []

        owners = await self._resolve_owners([b.token_account_address for b in largest])

        records: list[HolderRecord] = []
        for balance in largest:
            owner = owners.get(balance.token_account_address)
            if owner is None:
                # Couldn't resolve this one holder's wallet (closed
                # account, an RPC hiccup on just this sub-call within the
                # batch) - skip it rather than fabricate a wallet
                # address. HCI/whale counts over a slightly smaller
                # confirmed set are more honest than counts padded with a
                # guess.
                continue
            records.append(
                HolderRecord(
                    wallet_address=owner,
                    token_account_address=balance.token_account_address,
                    balance=balance.ui_amount,
                    pct_of_supply=(balance.ui_amount / total_supply) * 100,
                )
            )
        return sorted(records, key=lambda r: r.pct_of_supply, reverse=True)

    async def get_launch_block_funding(self, address: str, chain: Chain) -> list[FundingRecord]:
        """
        Earliest-known funding for up to `_MAX_FUNDING_LOOKUP_WALLETS` of
        the top holders (module-level constant — free-tier RPC economy,
        module docstring). Scoped to top holders deliberately, not an
        attempt at every holder: Part III.3 frames insider/bundle
        detection around "coordinated pre-public buying," which is
        precisely a question about the *large* holders, not the long
        tail — a narrower, cheaper scope that still answers the question
        Part III.3 actually asks, not a shortcut around it.

        Never raises past this method for a single wallet's lookup
        failing — that wallet is silently omitted from the result (an
        empty list is a valid, if uninformative, answer). Only a failure
        of the underlying holder lookup itself (the same call
        `get_holders` makes) propagates, for `HolderEngine` to catch and
        degrade the funding section specifically while leaving HCI/whale
        data (from a separate `get_holders` call) untouched.
        """
        assert chain is Chain.SOL, "SolanaRpcHolderProvider only supports Chain.SOL"

        largest = await self._get_largest_accounts(address)
        if not largest:
            return []

        candidates = largest[:_MAX_FUNDING_LOOKUP_WALLETS]
        owners = await self._resolve_owners([b.token_account_address for b in candidates])
        wallets = [
            owners[b.token_account_address] for b in candidates if b.token_account_address in owners
        ]
        if not wallets:
            return []

        sig_pages = await self._batch_call(
            [("getSignaturesForAddress", [w, {"limit": _SIGNATURES_PAGE_SIZE}]) for w in wallets]
        )

        earliest_signature_by_wallet: dict[str, str] = {}
        for wallet, raw_page in zip(wallets, sig_pages):
            if raw_page is None or not isinstance(raw_page, list):
                continue
            signatures = parse_signatures(raw_page)
            if signatures:
                earliest_signature_by_wallet[wallet] = signatures[-1].signature  # oldest-in-page

        if not earliest_signature_by_wallet:
            return []

        wallets_with_signature = list(earliest_signature_by_wallet.keys())
        tx_results = await self._batch_call(
            [
                (
                    "getTransaction",
                    [
                        earliest_signature_by_wallet[w],
                        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                    ],
                )
                for w in wallets_with_signature
            ]
        )

        records: list[FundingRecord] = []
        for wallet, raw_tx in zip(wallets_with_signature, tx_results):
            tx_dict = raw_tx if isinstance(raw_tx, dict) else None
            funder, slot, block_time = parse_funding_from_transaction(tx_dict, wallet)
            records.append(
                FundingRecord(
                    wallet_address=wallet,
                    funding_source_address=funder,
                    funded_at_slot=slot,
                    funded_at_block_time=block_time,
                )
            )
        return records

    async def _get_token_supply(self, mint_address: str) -> float:
        result = await self._rpc_call("getTokenSupply", [mint_address])
        return parse_token_supply(result) if result is not None else 0.0

    async def _get_largest_accounts(self, mint_address: str) -> list[RawTokenBalance]:
        result = await self._rpc_call("getTokenLargestAccounts", [mint_address])
        if result is None:
            return []
        return parse_largest_accounts(result)[:_MAX_LARGEST_ACCOUNTS]

    async def _resolve_owners(self, token_account_addresses: list[str]) -> dict[str, str]:
        if not token_account_addresses:
            return {}
        calls: list[tuple[str, list[Any]]] = [
            ("getAccountInfo", [addr, {"encoding": "jsonParsed"}]) for addr in token_account_addresses
        ]
        results = await self._batch_call(calls)
        owners: dict[str, str] = {}
        for addr, raw in zip(token_account_addresses, results):
            if not isinstance(raw, dict):
                continue
            owner = parse_account_owner(raw)
            if owner is not None:
                owners[addr] = owner
        return owners

    async def _rpc_call(self, method: str, params: list[Any]) -> dict[str, Any] | None:
        """One logical JSON-RPC 2.0 request, sent as a single-item batch
        (`_batch_call` handles both shapes identically)."""
        results = await self._batch_call([(method, params)])
        return results[0]

    async def _batch_call(self, calls: list[tuple[str, list[Any]]]) -> list[dict[str, Any] | None]:
        """
        Sends `calls` as one JSON-RPC 2.0 batch request — a single HTTP
        POST carrying a JSON array — standard JSON-RPC 2.0 behavior every
        Solana RPC node supports (module docstring: this is the main
        lever keeping HTTP round trips low on a rate-limited free tier).

        Tries every URL in `self._rpc_urls`, in order, falling through to
        the next one ONLY on a transport-level failure (timeout,
        connection error, non-200 HTTP status) — Step 11's [custom
        roadmap] reliability pass. Every endpoint but the last uses the
        shorter `_FALLBACK_ATTEMPT_TIMEOUT_SECONDS` budget (module
        docstring explains the latency-vs-resilience tradeoff); the last
        one gets the original, longer `_REQUEST_TIMEOUT_SECONDS`, since
        there's nothing left to fall through to if it also fails. Raises
        only once every configured endpoint has failed — same exception
        type (`RuntimeError`) callers already caught before this pass, so
        `HolderEngine`'s own degrade-and-log behavior needs no changes.

        A JSON-RPC `error` object for one call *within* an otherwise-
        successful batch is a different thing entirely, and does NOT
        trigger a fallback attempt — see the per-item handling below for
        why (same "one bad item doesn't sink the batch" reasoning as
        before this pass, now just also applied per-endpoint).
        """
        if not calls:
            return []

        payload = [
            {"jsonrpc": "2.0", "id": i, "method": method, "params": params}
            for i, (method, params) in enumerate(calls)
        ]

        last_exc: Exception | None = None
        for attempt_index, url in enumerate(self._rpc_urls):
            is_last_endpoint = attempt_index == len(self._rpc_urls) - 1
            timeout = self._timeout if is_last_endpoint else self._fallback_timeout

            try:
                async with self._session.post(url, json=payload, timeout=timeout) as response:
                    if response.status != 200:
                        body_preview = (await response.text())[:200]
                        raise RuntimeError(f"Solana RPC returned HTTP {response.status}: {body_preview}")
                    raw = await response.json(content_type=None)
            except (aiohttp.ClientError, RuntimeError, asyncio.TimeoutError) as exc:
                last_exc = exc
                logger.warning(
                    "Solana RPC endpoint failed%s",
                    " (last configured endpoint)" if is_last_endpoint else ", falling back to next endpoint",
                    extra={
                        "endpoint_index": attempt_index,
                        "endpoint_count": len(self._rpc_urls),
                        "error": str(exc),
                    },
                )
                continue

            if attempt_index > 0:
                logger.warning(
                    "Solana RPC fallback succeeded on a non-primary endpoint",
                    extra={"endpoint_index": attempt_index, "endpoint_count": len(self._rpc_urls)},
                )

            # A single-item batch is occasionally echoed back as a bare
            # object instead of a one-item array by some RPC nodes -
            # normalize both shapes rather than assuming strict spec
            # compliance.
            items = raw if isinstance(raw, list) else [raw]

            by_id: dict[int, dict[str, Any]] = {}
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), int):
                    by_id[item["id"]] = item

            out: list[dict[str, Any] | None] = []
            for i in range(len(calls)):
                item = by_id.get(i)
                if item is None or "error" in item:
                    if item is not None:
                        logger.debug("Solana RPC call returned an error: %r", item.get("error"))
                    out.append(None)
                else:
                    result = item.get("result")
                    out.append(result if isinstance(result, dict) else None)
            return out

        raise RuntimeError(
            f"All {len(self._rpc_urls)} configured Solana RPC endpoint(s) failed; last error: {last_exc}"
        ) from last_exc
