"""
Layer: Provider adapter — twitterapi.io network I/O (Playbook Part II.3;
Part VIII Step 13).

Just the HTTP calls: constructs each request, checks the status, hands
the JSON body to the matching `twitterapi_io_parser` function. All schema
knowledge lives in that sibling module (see its own docstring for why
they're split); this file's only job is talking to the network and
turning transport failures into a clean raised exception for
`SocialEngine` to catch — same contract as `dexscreener.py`/`rugcheck.py`.

Endpoints verified against twitterapi.io's own published API reference
(docs.twitterapi.io/api-reference) while implementing this step:
- GET /twitter/user/info            (user lookup)
- GET /twitter/user/followers       (one page of followers, newest first)
- GET /twitter/tweet/advanced_search (mention search)

Auth: `X-API-Key` header (twitterapi.io's own scheme — not OAuth, unlike
the official X API this project deliberately avoids per Part III.4's
provider choice).
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from analysis.api_abstraction import Tweet, XUserProfile
from analysis.providers.twitterapi_io_parser import (
    parse_followers,
    parse_mentions,
    parse_user_lookup,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.twitterapi.io"  # verified
_REQUEST_TIMEOUT_SECONDS = 10
_MENTIONS_LOOKBACK_SECONDS = 24 * 60 * 60  # Part III.4: "recent ticker-mentioning tweets"

# Step 13 revalidation addition: this provider previously raised on the
# very first failure, with no protection against a transient blip or a
# 429, unlike rugcheck.py's own provider (Step 11, custom roadmap). Same
# numbers, same reasoning (see rugcheck.py's own comment for the ~23s
# worst-case bound this produces) - the two providers' resilience should
# stay in step even though they were hardened in different steps.
_MAX_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SECONDS = 1.0


class TwitterApiIoProvider:
    """Satisfies `analysis.api_abstraction.SocialDataProvider`. Holds its
    own `aiohttp.ClientSession` rather than requiring the caller to
    manage one — same convention as `DexScreenerProvider`/
    `RugCheckProvider`; `main.py`'s composition root constructs one
    instance for the life of the process, whenever Social Engine is
    wired into it (deliberately deferred — see this step's own
    integration notes)."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str, base_url: str = _BASE_URL) -> None:
        self._session = session
        self._api_key = api_key
        self._base_url = base_url

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key}

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        """Shared GET-and-decode for all three endpoints below — same
        error contract as `DexScreenerProvider.get_pairs`: raises
        `RuntimeError` once every retry is exhausted, never swallows a
        real transport failure into an empty/default result. A non-200
        here always means "the request itself failed" (auth, rate limit,
        malformed query) — twitterapi.io reports "handle not found" as a
        200 with `status: "error"` in the body (see
        `twitterapi_io_parser.parse_user_lookup`'s docstring), which is a
        normal parsed outcome, not a transport failure, so it's never
        retried.

        Retried up to `_MAX_RETRY_ATTEMPTS` times with exponential
        backoff on a timeout, connection error, or non-200 status —
        `SocialEngine` catches and degrades only after every retry is
        exhausted. Retrying a non-200 is deliberately not narrower than
        that (e.g. skipping retry on a 401): a sustained auth failure
        still exhausts all three attempts and raises exactly as before,
        just ~3s slower, while a transient 429 or 5xx — which retrying
        can genuinely fix — gets the chance to. Same tradeoff
        rugcheck.py's own provider already made for the same reason."""
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRY_ATTEMPTS):
            try:
                async with self._session.get(
                    url, headers=self._headers(), params=params,
                    timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS),
                ) as response:
                    if response.status != 200:
                        body_preview = (await response.text())[:200]
                        raise RuntimeError(f"twitterapi.io returned HTTP {response.status}: {body_preview}")
                    return await response.json()  # type: ignore[no-any-return]
            except (aiohttp.ClientError, RuntimeError, asyncio.TimeoutError) as exc:
                last_exc = exc
                is_last_attempt = attempt == _MAX_RETRY_ATTEMPTS - 1
                if is_last_attempt:
                    break
                backoff_seconds = _RETRY_BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "twitterapi.io request failed, retrying",
                    extra={
                        "attempt": attempt + 1,
                        "max_attempts": _MAX_RETRY_ATTEMPTS,
                        "path": path,
                        "error": str(exc),
                    },
                )
                await asyncio.sleep(backoff_seconds)

        raise RuntimeError(
            f"twitterapi.io request failed after {_MAX_RETRY_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc

    async def lookup_user(self, handle: str) -> XUserProfile:
        """GET /twitter/user/info?userName={handle} (verified)."""
        payload = await self._get("/twitter/user/info", {"userName": handle})
        return parse_user_lookup(payload, requested_handle=handle)

    async def list_followers(self, handle: str, limit: int) -> list[XUserProfile]:
        """GET /twitter/user/followers?userName={handle} (verified) —
        one page, up to twitterapi.io's own per-page cap (200, confirmed
        against the endpoint's published reference); `limit` truncates
        the parsed result client-side if the page returns more than
        asked for, rather than assuming the API will always exactly
        match `limit`."""
        payload = await self._get("/twitter/user/followers", {"userName": handle})
        return parse_followers(payload)[:limit]

    async def search_mentions(self, ticker: str) -> list[Tweet]:
        """
        GET /twitter/tweet/advanced_search (verified) with a
        `$TICKER OR #TICKER` query, `since_time:` bounding the last 24h.
        Uses `since_time:UNIX` specifically, NOT the older `since:DATE`
        operator — twitterapi.io's own API reference states plainly that
        `since:`/`until:` are "not supported now," a platform-level
        change confirmed while implementing this step, not this
        playbook's original assumption. `queryType=Latest` for
        chronological order (the alternative, "Top", ranks by engagement,
        which would bias `sentiment_ratio` toward whatever already went
        viral rather than a representative recent sample).
        """
        since_unix = int(time.time()) - _MENTIONS_LOOKBACK_SECONDS
        query = f"(${ticker} OR #{ticker}) since_time:{since_unix}"
        payload = await self._get(
            "/twitter/tweet/advanced_search",
            {"query": query, "queryType": "Latest", "cursor": ""},
        )
        return parse_mentions(payload)
