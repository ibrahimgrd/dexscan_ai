"""
Step 11 (custom roadmap) - real execution proof for
`SolanaRpcHolderProvider`'s multi-endpoint fallback logic, using this
repo's own FakeSession/FakeResponse test doubles
(tests/_aiohttp_test_doubles.py - see that module's docstring for why
they replaced this file's previous aiohttp._ScriptedResponse /
aiohttp._RequestContext / url_behaviors= usage, which referenced
aiohttp internals that don't exist). The double controls exactly which
URL succeeds/fails and records call order/timeouts, so these assertions
are about this provider's real control flow, not just its parser-level
output (which test_holder_engine.py already covers with fakes that
never exercise this file's own network code at all).
"""

from __future__ import annotations

import aiohttp
import pytest

from analysis.providers.solana_rpc import SolanaRpcHolderProvider
from bot.constants import Chain
from tests._aiohttp_test_doubles import FakeResponse, FakeSession

_TOKEN_SUPPLY_RESPONSE = {
    "jsonrpc": "2.0", "id": 0,
    "result": {"context": {"slot": 1}, "value": {"amount": "1000000", "decimals": 0, "uiAmount": 1000000.0, "uiAmountString": "1000000"}},
}


def _ok(body: dict) -> FakeResponse:
    return FakeResponse(status=200, json_body=body)


@pytest.mark.asyncio
async def test_first_endpoint_success_never_tries_second() -> None:
    urls = ["https://primary.example/rpc", "https://backup.example/rpc"]
    session = FakeSession({urls[0]: _ok(_TOKEN_SUPPLY_RESPONSE)})
    provider = SolanaRpcHolderProvider(session, rpc_urls=urls)

    supply = await provider._get_token_supply("SomeMintAddress")

    assert supply == 1000000.0
    assert session.requested_urls == [urls[0]]  # second URL never touched


@pytest.mark.asyncio
async def test_first_endpoint_transport_failure_falls_through_to_second() -> None:
    urls = ["https://primary.example/rpc", "https://backup.example/rpc"]
    session = FakeSession({
        urls[0]: aiohttp.ClientError("connection refused"),
        urls[1]: _ok(_TOKEN_SUPPLY_RESPONSE),
    })
    provider = SolanaRpcHolderProvider(session, rpc_urls=urls)

    supply = await provider._get_token_supply("SomeMintAddress")

    assert supply == 1000000.0
    assert session.requested_urls == urls  # tried first, THEN fell through to second


@pytest.mark.asyncio
async def test_non_200_status_also_triggers_fallback_not_just_connection_errors() -> None:
    urls = ["https://primary.example/rpc", "https://backup.example/rpc"]
    session = FakeSession({
        urls[0]: FakeResponse(status=503, text_body="Service Unavailable"),
        urls[1]: _ok(_TOKEN_SUPPLY_RESPONSE),
    })
    provider = SolanaRpcHolderProvider(session, rpc_urls=urls)

    supply = await provider._get_token_supply("SomeMintAddress")
    assert supply == 1000000.0
    assert session.requested_urls == urls


@pytest.mark.asyncio
async def test_all_endpoints_failing_raises_after_trying_every_one() -> None:
    urls = ["https://primary.example/rpc", "https://backup.example/rpc", "https://public.example/rpc"]
    session = FakeSession({
        urls[0]: aiohttp.ClientError("down"),
        urls[1]: aiohttp.ClientError("also down"),
        urls[2]: aiohttp.ClientError("still down"),
    })
    provider = SolanaRpcHolderProvider(session, rpc_urls=urls)

    with pytest.raises(RuntimeError, match="All 3 configured Solana RPC endpoint"):
        await provider._get_token_supply("SomeMintAddress")

    assert session.requested_urls == urls  # every single one was actually tried, in order


@pytest.mark.asyncio
async def test_json_rpc_level_error_does_not_trigger_fallback() -> None:
    """A 200 response containing a per-call JSON-RPC `error` object is a
    real, valid answer (e.g. "account not found") - not a transport
    failure, so it must NOT burn through the fallback list. Only the
    first URL should ever be touched."""
    urls = ["https://primary.example/rpc", "https://backup.example/rpc"]
    error_response = {"jsonrpc": "2.0", "id": 0, "error": {"code": -32602, "message": "Invalid param"}}
    session = FakeSession({urls[0]: FakeResponse(status=200, json_body=error_response)})
    provider = SolanaRpcHolderProvider(session, rpc_urls=urls)

    supply = await provider._get_token_supply("BadMintAddress")

    assert supply == 0.0  # None result -> parse_token_supply's own documented zero-default
    assert session.requested_urls == [urls[0]]  # second endpoint never touched - this wasn't a transport failure


@pytest.mark.asyncio
async def test_only_the_last_endpoint_gets_the_full_timeout_budget() -> None:
    """Every endpoint but the last uses the shorter fallback-attempt
    timeout; the last one gets the full original budget (module
    docstring's documented latency tradeoff) - verified directly against
    the timeout objects the provider actually constructed and passed to
    the session, not just inferred from behavior."""
    urls = ["https://primary.example/rpc", "https://backup.example/rpc", "https://public.example/rpc"]
    session = FakeSession({
        urls[0]: aiohttp.ClientError("down"),
        urls[1]: aiohttp.ClientError("also down"),
        urls[2]: _ok(_TOKEN_SUPPLY_RESPONSE),
    })
    provider = SolanaRpcHolderProvider(
        session, rpc_urls=urls, request_timeout_seconds=15, fallback_attempt_timeout_seconds=6
    )

    await provider._get_token_supply("SomeMintAddress")

    assert [t.total for t in session.requested_timeouts] == [6, 6, 15]


class _RoutedSession(FakeSession):
    """A more realistic scripted session for the end-to-end test below:
    `get_holders()` makes several DIFFERENT batch calls (getTokenSupply,
    then getTokenLargestAccounts, then getAccountInfo) against whichever
    URL it's currently trying - this session inspects the outgoing
    JSON-RPC method name to return the right scripted body, rather than
    assuming a fixed call count/order the way the simpler single-call
    tests above can. Built on the real FakeSession base (for its
    requested_urls/requested_timeouts bookkeeping) rather than on any
    aiohttp internals."""

    def __init__(self, primary_url: str, backup_url: str, responses_by_method: dict[str, dict]) -> None:
        super().__init__()
        self._primary_url = primary_url
        self._backup_url = backup_url
        self._responses_by_method = responses_by_method

    def post(self, url: str, json: dict | None = None, timeout=None, **_) -> FakeResponse:
        self.requested_urls.append(url)
        self.requested_timeouts.append(timeout)
        self.requested_json_bodies.append(json)
        if url == self._primary_url:
            raise aiohttp.ClientError("primary down")
        method = json[0]["method"]
        return FakeResponse(status=200, json_body=self._responses_by_method[method])


@pytest.mark.asyncio
async def test_get_holders_end_to_end_falls_back_through_the_full_real_method() -> None:
    """Not just the low-level _batch_call/_get_token_supply helpers -
    proves the fallback works through the actual public `get_holders`
    method a real HolderEngine calls, across its real multi-call sequence
    (supply, then largest-accounts, then owner-resolution), with the
    primary endpoint down for the entire scan."""
    primary_url, backup_url = "https://primary.example/rpc", "https://backup.example/rpc"

    largest_accounts_response = {
        "jsonrpc": "2.0", "id": 0,
        "result": {"context": {"slot": 1}, "value": [
            {"address": "TokenAcct1", "amount": "500000", "decimals": 0, "uiAmount": 500000.0, "uiAmountString": "500000"},
        ]},
    }
    account_info_response = {
        "jsonrpc": "2.0", "id": 0,
        "result": {"context": {"slot": 1}, "value": {
            "data": {"parsed": {"info": {"owner": "WalletOwner1", "tokenAmount": {"amount": "500000"}}}, "program": "spl-token"},
            "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        }},
    }
    session = _RoutedSession(
        primary_url, backup_url,
        responses_by_method={
            "getTokenSupply": _TOKEN_SUPPLY_RESPONSE,
            "getTokenLargestAccounts": largest_accounts_response,
            "getAccountInfo": account_info_response,
        },
    )
    provider = SolanaRpcHolderProvider(session, rpc_urls=[primary_url, backup_url])

    holders = await provider.get_holders("SomeMintAddress", Chain.SOL)

    assert len(holders) == 1
    assert holders[0].wallet_address == "WalletOwner1"
    # The primary was tried (and failed) for every one of the scan's
    # logical calls, and the backup answered every one - the whole scan
    # still succeeded end-to-end despite the "preferred" endpoint being
    # completely down throughout.
    assert primary_url in session.requested_urls
    assert backup_url in session.requested_urls
    assert set(session.requested_urls) == {primary_url, backup_url}
