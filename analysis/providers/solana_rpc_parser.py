"""
Layer: Provider adapter — Solana JSON-RPC response parsing (Playbook
Part II.3; Part VIII Step 8).

Pure data transformation, zero network dependency — same split rationale
as `dexscreener_parser.py` / `rugcheck_parser.py`: the part with the most
genuine interpretive judgment (which raw JSON-RPC field means what) is
executable and fixture-testable on its own, in any environment, without
aiohttp installed.

Unlike DexScreener/RugCheck — one HTTP response carries everything needed
for one `PairData` / `SecurityReport` — a single Solana JSON-RPC method
never carries enough on its own to build a full `HolderRecord` or
`FundingRecord`. Each function below parses exactly ONE raw response
shape into one small piece of data; `solana_rpc.py`'s network layer is
where several of these get called and combined (that module's own
docstring explains why that combination step belongs there and not here).

Confidence level per method/field, from Solana's own RPC reference
(solana.com/docs/rpc) and Helius's docs (which mirror the identical
shapes for standard methods — verified side-by-side while implementing
this step, since Helius serves plain Solana JSON-RPC alongside its own
proprietary ones):

- HIGH confidence: `getTokenSupply` and `getTokenLargestAccounts`'s
  response shapes (`value.{amount,decimals,uiAmount,uiAmountString}` and
  `value[].{address,amount,decimals,uiAmount,uiAmountString}` respectively)
  — both are core, long-stable JSON-RPC methods with one canonical shape,
  not a third party's own evolving schema.
- HIGH confidence, but easy to get backwards: `getAccountInfo`'s
  jsonParsed SPL-token-account shape. `value.owner` (top level) is the
  *Token Program's own address* — every SPL token account's runtime
  owner is the Token Program, always the same value, useless for holder
  identification. The actual holder wallet is nested at
  `value.data.parsed.info.owner`. Mixing these up would silently
  attribute every holder to the same wrong address; `parse_account_owner`
  reads the nested path deliberately and guards against the shallow one.
- MEDIUM confidence: `getSignaturesForAddress`'s shape is stable and
  standard, but this module only reads the two fields it needs
  (`signature`, `slot`, `blockTime`), not the full envelope.
- MEDIUM confidence / documented heuristic: `parse_funding_from_transaction`'s
  "fee payer = funder" inference for a wallet's earliest transaction — see
  its own docstring for exactly what pattern this does and doesn't catch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RawTokenBalance:
    """One entry from `getTokenLargestAccounts`. The address here is a
    SPL *token account*, not a wallet — module docstring. `solana_rpc.py`
    resolves the owning wallet via a separate `getAccountInfo` call
    before this is turned into a `HolderRecord`."""

    token_account_address: str
    ui_amount: float


@dataclass
class SignatureInfo:
    """One entry from `getSignaturesForAddress`."""

    signature: str
    slot: int
    block_time: int | None


def parse_token_supply(raw_result: dict[str, Any]) -> float:
    """`getTokenSupply`'s `result.value` -> decimal-adjusted total
    supply. Prefers `uiAmountString` (exact decimal text) over the
    deprecated float `uiAmount`, same precision reasoning Solana's own
    reference gives for preferring the string form. Returns `0.0` (not
    `None`) on a missing/unparseable value — `solana_rpc.py` treats a
    zero supply as "nothing to compute a percentage against" and returns
    no holders, which is the correct behavior for this malformed-response
    case too."""
    value = raw_result.get("value")
    if not isinstance(value, dict):
        return 0.0

    ui_amount_string = value.get("uiAmountString")
    if isinstance(ui_amount_string, str):
        try:
            return float(ui_amount_string)
        except ValueError:
            logger.debug("getTokenSupply uiAmountString not parseable: %r", ui_amount_string)

    ui_amount = value.get("uiAmount")
    if isinstance(ui_amount, (int, float)):
        return float(ui_amount)
    return 0.0


def parse_largest_accounts(raw_result: dict[str, Any]) -> list[RawTokenBalance]:
    """`getTokenLargestAccounts`'s `result.value` -> balances, re-sorted
    descending defensively. Solana's own reference documents this as
    already sorted by balance descending, but this module doesn't trust
    an external response's ordering by convention alone — the same
    caution `core_engine.py` applies by computing `PrimaryPair` via
    `argmax` itself rather than trusting a provider's own "sorted"
    ordering."""
    entries = raw_result.get("value")
    if not isinstance(entries, list):
        return []

    balances: list[RawTokenBalance] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address")
        if not isinstance(address, str) or not address:
            continue
        ui_amount = _extract_ui_amount(entry)
        balances.append(RawTokenBalance(token_account_address=address, ui_amount=ui_amount))

    return sorted(balances, key=lambda b: b.ui_amount, reverse=True)


def _extract_ui_amount(entry: dict[str, Any]) -> float:
    ui_amount_string = entry.get("uiAmountString")
    if isinstance(ui_amount_string, str):
        try:
            return float(ui_amount_string)
        except ValueError:
            pass
    ui_amount = entry.get("uiAmount")
    if isinstance(ui_amount, (int, float)):
        return float(ui_amount)
    return 0.0


def parse_account_owner(raw_result: dict[str, Any]) -> str | None:
    """`getAccountInfo` (jsonParsed) -> the wallet that owns this SPL
    token account's balance, or `None` on any shape that isn't a parsed
    SPL token account (closed account, non-token account, an RPC node
    that silently ignored the jsonParsed encoding request). The caller
    treats a `None` owner as "skip this one holder," never a crash.

    Reads `value.data.parsed.info.owner` deliberately — NOT the
    top-level `value.owner` — see module docstring's confidence note.
    The `program` check is a guard against accepting a differently-shaped
    `parsed` payload that happens to also expose an `info.owner` key.
    """
    value = raw_result.get("value")
    if not isinstance(value, dict):
        return None

    data = value.get("data")
    if not isinstance(data, dict) or data.get("program") not in ("spl-token", "spl-token-2022"):
        return None

    parsed = data.get("parsed")
    if not isinstance(parsed, dict):
        return None

    info = parsed.get("info")
    if not isinstance(info, dict):
        return None

    owner = info.get("owner")
    return owner if isinstance(owner, str) and owner else None


def parse_signatures(raw_result: list[Any]) -> list[SignatureInfo]:
    """`getSignaturesForAddress` -> signatures in the order returned
    (newest-first, per Solana's reference). `solana_rpc.py` takes the
    *last* entry (oldest-in-page) as this wallet's earliest-known
    activity within the fetched page — see that module's docstring for
    the page-size cap and exactly what that approximation does and
    doesn't guarantee."""
    out: list[SignatureInfo] = []
    for entry in raw_result:
        if not isinstance(entry, dict):
            continue
        signature = entry.get("signature")
        slot = entry.get("slot")
        if not isinstance(signature, str) or not isinstance(slot, int):
            continue
        block_time = entry.get("blockTime")
        out.append(
            SignatureInfo(
                signature=signature,
                slot=slot,
                block_time=block_time if isinstance(block_time, int) else None,
            )
        )
    return out


def parse_funding_from_transaction(
    raw_result: dict[str, Any] | None, funded_wallet: str
) -> tuple[str | None, int | None, int | None]:
    """
    `getTransaction` (jsonParsed) -> `(funding_source_address, slot,
    block_time)` for `funded_wallet`, or `(None, None, None)` together
    when no clear funding pattern is found — never a partial tuple, so a
    caller can't end up with a slot but no funder or vice versa.

    Documented heuristic, not a guaranteed-correct parse of every
    possible transaction shape: a wallet's earliest transaction being a
    plain SOL-balance increase almost always means "someone sent this
    brand-new wallet its starting SOL," and in exactly that pattern the
    transaction's fee payer (`accountKeys[0]` — the one account required
    to sign and pay for a simple transfer) is, in the overwhelming
    majority of real transfers, the sender itself. This does NOT attempt
    to handle multi-hop funding, program-mediated transfers (e.g. routed
    through a DEX or an exchange's hot-wallet dispatcher), or a wallet
    whose first activity wasn't a SOL-balance increase at all — those
    cases correctly fall through to `(None, None, None)` ("couldn't
    confirm a funder") rather than guessing at one.
    """
    if raw_result is None:
        return None, None, None

    slot = raw_result.get("slot")
    block_time = raw_result.get("blockTime")
    meta = raw_result.get("meta")
    transaction = raw_result.get("transaction")

    if not isinstance(meta, dict) or not isinstance(transaction, dict):
        return None, None, None

    message = transaction.get("message")
    if not isinstance(message, dict):
        return None, None, None

    pubkeys = _extract_account_keys(message.get("accountKeys"))
    if funded_wallet not in pubkeys:
        return None, None, None

    idx = pubkeys.index(funded_wallet)
    pre_balances = meta.get("preBalances")
    post_balances = meta.get("postBalances")
    if not isinstance(pre_balances, list) or not isinstance(post_balances, list):
        return None, None, None
    if idx >= len(pre_balances) or idx >= len(post_balances):
        return None, None, None

    gained_sol = post_balances[idx] > pre_balances[idx]
    if not gained_sol:
        return None, None, None

    if not pubkeys or pubkeys[0] == funded_wallet:
        # No distinct fee payer to point to, or the wallet paid its own
        # fee (e.g. a rent-reclaim quirk) - not a funding signal.
        return None, None, None

    funder = pubkeys[0]
    resolved_slot = slot if isinstance(slot, int) else None
    resolved_block_time = block_time if isinstance(block_time, int) else None
    return funder, resolved_slot, resolved_block_time


def _extract_account_keys(raw_keys: Any) -> list[str]:
    if not isinstance(raw_keys, list):
        return []
    pubkeys: list[str] = []
    for key in raw_keys:
        if isinstance(key, dict):
            pk = key.get("pubkey")
            if isinstance(pk, str):
                pubkeys.append(pk)
        elif isinstance(key, str):
            pubkeys.append(key)
    return pubkeys


# --- RPC endpoint selection (moved here from solana_rpc.py during the
# Step 11 [custom roadmap] fallback-RPC pass) -------------------------
#
# Pure string/list logic, zero network dependency - belongs here for the
# same reason every other function in this file does (this module's own
# docstring): independently testable without aiohttp installed, in any
# environment. `solana_rpc.py`'s `SolanaRpcHolderProvider` is the only
# consumer; it never builds a URL itself.

_PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"  # verified: solana.com/docs/references/clusters
_HELIUS_RPC_URL_TEMPLATE = "https://mainnet.helius-rpc.com/?api-key={key}"  # verified: helius.dev/docs
_SHYFT_RPC_URL_TEMPLATE = "https://rpc.shyft.to?api_key={key}"  # verified: docs.shyft.to/solana/rpc-calls


def resolve_rpc_urls(
    helius_api_key: str | None,
    quicknode_rpc_url: str | None,
    shyft_api_key: str | None,
    fallback_url: str = _PUBLIC_RPC_URL,
) -> list[str]:
    """
    `config.Settings`' four Solana RPC fields -> the priority-ordered
    endpoint list `SolanaRpcHolderProvider` tries in turn, falling
    through to the next one only on a transport-level failure (that
    class's own docstring covers what counts as one).

    QuickNode has no generic "template + short key" the other two do —
    confirmed against QuickNode's own docs while implementing this step:
    each account gets a unique, complete endpoint URL
    (`https://<random-name>.solana-mainnet.quiknode.pro/<token>/`) from
    its dashboard, not a key to combine with a fixed template. So
    `quicknode_rpc_url` is the whole URL, pasted directly into `.env`,
    unlike `helius_api_key`/`shyft_api_key`.

    `fallback_url` (the free public cluster) is ALWAYS included, last,
    even if every paid provider below it is configured — never the one
    entry a misconfigured or expired key could silently remove from the
    list, since it's the only one that needs no credential to work at
    all. Priority order (Helius, then QuickNode, then Shyft, then
    public) reflects the order these were added to this project, not a
    measured ranking of RPC quality — nothing about this function
    prevents reordering `.env`'s keys against a different provider
    setup other than editing this one place.
    """
    urls: list[str] = []
    if helius_api_key:
        urls.append(_HELIUS_RPC_URL_TEMPLATE.format(key=helius_api_key))
    if quicknode_rpc_url:
        urls.append(quicknode_rpc_url)
    if shyft_api_key:
        urls.append(_SHYFT_RPC_URL_TEMPLATE.format(key=shyft_api_key))
    if fallback_url not in urls:
        urls.append(fallback_url)
    return urls
