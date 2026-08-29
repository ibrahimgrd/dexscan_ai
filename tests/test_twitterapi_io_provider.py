"""
Step 13 revalidation — real execution proof for `TwitterApiIoProvider`'s
new retry/backoff logic (previously absent; this closes that gap).

Deliberately does NOT use the `aiohttp.ClientSession(url_behaviors=...)` /
`_ScriptedResponse` pattern the sibling provider test files (rugcheck,
dexscreener, solana_rpc) reference: neither exists anywhere in this repo
or in real aiohttp — `from aiohttp import _ScriptedResponse` raises
ImportError against the real package. Those three files' own retry/
success tests would fail at collection, before a single assertion runs,
in any environment with real aiohttp installed. Flagged in this step's
handoff notes as a pre-existing gap, not introduced here. This file uses
plain `unittest.mock` against `ClientSession.get` instead, so it's
runnable regardless of whether that shared fixture gets built later.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from analysis.providers.twitterapi_io import TwitterApiIoProvider


def _fake_response(status: int, json_body: dict | None = None, text_body: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_body or {})
    resp.text = AsyncMock(return_value=text_body)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _session_returning(*responses: MagicMock) -> MagicMock:
    """A ClientSession whose .get() yields each of `responses` in order
    across successive calls — models a real session's behavior across
    retry attempts without needing a real network or a shared fixture
    module."""
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(side_effect=list(responses))
    return session


@pytest.mark.asyncio
async def test_success_on_first_attempt_never_retries() -> None:
    session = _session_returning(_fake_response(200, {"status": "success", "data": {}}))
    provider = TwitterApiIoProvider(session=session, api_key="k")

    with patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
        result = await provider._get("/twitter/user/info", {"userName": "x"})

    assert result == {"status": "success", "data": {}}
    assert session.get.call_count == 1
    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_recovers_after_two_transient_failures() -> None:
    session = _session_returning(
        _fake_response(503, text_body="upstream hiccup"),
        _fake_response(429, text_body="rate limited"),
        _fake_response(200, {"status": "success", "data": {"ok": True}}),
    )
    provider = TwitterApiIoProvider(session=session, api_key="k")

    with patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
        result = await provider._get("/twitter/user/info", {"userName": "x"})

    assert result == {"status": "success", "data": {"ok": True}}
    assert session.get.call_count == 3
    # exponential: 1s then 2s, per _RETRY_BACKOFF_BASE_SECONDS * (2**attempt)
    assert [call.args[0] for call in sleep_mock.call_args_list] == [1.0, 2.0]


@pytest.mark.asyncio
async def test_raises_after_exhausting_all_attempts() -> None:
    session = _session_returning(
        _fake_response(500, text_body="a"),
        _fake_response(500, text_body="b"),
        _fake_response(500, text_body="c"),
    )
    provider = TwitterApiIoProvider(session=session, api_key="k")

    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            await provider._get("/twitter/user/info", {"userName": "x"})

    assert session.get.call_count == 3
