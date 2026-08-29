"""
Step 11 (custom roadmap) - real execution proof for `RugCheckProvider`'s
retry/backoff logic, using this repo's own FakeSession/FakeResponse test
doubles (tests/_aiohttp_test_doubles.py - see that module's docstring
for why they replaced this file's previous aiohttp._ScriptedResponse /
url_behaviors= usage, which referenced aiohttp internals that don't
exist). `asyncio.sleep` is patched to a no-op so these run instantly
rather than actually waiting out the real 1s/2s backoff delays - the
assertions are about attempt count and eventual outcome, not real
wall-clock timing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from analysis.providers.rugcheck import RugCheckProvider
from bot.constants import Chain
from tests._aiohttp_test_doubles import FakeResponse, FakeSession

_SAFE_REPORT_RESPONSE = {
    "score": 100, "score_normalised": 5, "risks": [],
    "mintAuthority": None, "freezeAuthority": None,
}


@pytest.mark.asyncio
async def test_success_on_first_attempt_never_retries() -> None:
    url = "https://api.rugcheck.xyz/v1/tokens/scan/solana/SomeMint"
    session = FakeSession({url: FakeResponse(status=200, json_body=_SAFE_REPORT_RESPONSE)})
    provider = RugCheckProvider(session)

    with patch("analysis.providers.rugcheck.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        report = await provider.scan("SomeMint", Chain.SOL)

    assert report.chain_supported is True
    assert session.requested_urls == [url]
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_transient_failure_then_success_retries_and_recovers() -> None:
    """The FakeSession's per-URL script is a LIST here - "fails once,
    then succeeds" against the exact same URL, consumed front-to-back
    across the two attempts, rather than needing a bespoke stateful
    subclass for this one case."""
    url = "https://api.rugcheck.xyz/v1/tokens/scan/solana/SomeMint"
    session = FakeSession({
        url: [aiohttp.ClientError("transient connection drop"), FakeResponse(status=200, json_body=_SAFE_REPORT_RESPONSE)]
    })
    provider = RugCheckProvider(session)

    with patch("analysis.providers.rugcheck.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        report = await provider.scan("SomeMint", Chain.SOL)

    assert report.chain_supported is True
    assert session.requested_urls == [url, url]  # failed once, retried once, succeeded
    mock_sleep.assert_called_once()  # exactly one backoff wait between the two attempts


@pytest.mark.asyncio
async def test_all_attempts_failing_raises_after_exhausting_retries() -> None:
    url = "https://api.rugcheck.xyz/v1/tokens/scan/solana/SomeMint"
    session = FakeSession({url: aiohttp.ClientError("down")})
    provider = RugCheckProvider(session)

    with patch("analysis.providers.rugcheck.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="RugCheck request failed after 3 attempts"):
            await provider.scan("SomeMint", Chain.SOL)

    assert len(session.requested_urls) == 3  # exactly _MAX_RETRY_ATTEMPTS, no more, no fewer


@pytest.mark.asyncio
async def test_404_is_a_real_answer_not_retried() -> None:
    """A 404 means 'RugCheck has nothing for this token' - a legitimate,
    immediate answer, not a connectivity problem retrying could fix."""
    url = "https://api.rugcheck.xyz/v1/tokens/scan/solana/UnknownMint"
    session = FakeSession({url: FakeResponse(status=404)})
    provider = RugCheckProvider(session)

    with patch("analysis.providers.rugcheck.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        report = await provider.scan("UnknownMint", Chain.SOL)

    assert session.requested_urls == [url]  # exactly one attempt - no retry loop entered at all
    mock_sleep.assert_not_called()
    assert report.chain_supported is True  # Solana IS a confirmed chain - a 404 there is real, not "unsupported"


@pytest.mark.asyncio
async def test_backoff_delays_double_each_retry() -> None:
    """1s, then 2s - the documented exponential schedule, not just 'some
    delay happened.'"""
    url = "https://api.rugcheck.xyz/v1/tokens/scan/solana/SomeMint"
    session = FakeSession({url: aiohttp.ClientError("down")})
    provider = RugCheckProvider(session)

    with patch("analysis.providers.rugcheck.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RuntimeError):
            await provider.scan("SomeMint", Chain.SOL)

    waited = [call.args[0] for call in mock_sleep.call_args_list]
    assert waited == [1.0, 2.0]
