"""
Shared provider-test infrastructure — real, hand-written fakes for
aiohttp.ClientSession's PUBLIC surface, used by test_dexscreener_provider.py,
test_rugcheck_provider.py, and test_solana_rpc_provider.py.

STEP 14 VERIFICATION GATE — why this file exists: all three of the
files above previously imported `aiohttp._ScriptedResponse` and
constructed `aiohttp.ClientSession(url_behaviors={...})`. Neither of
those is a real aiohttp API (confirmed against the actual aiohttp
public interface, not just assumed) and no repository-level
conftest.py defined them either — so every test in all three files
raised ImportError/TypeError at collection, before a single assertion
ever ran. This module replaces that with `FakeSession`/`FakeResponse`,
built ONLY on aiohttp's real, documented public shape: a session's
`.get()`/`.post()` return something usable as
`async with session.get(url, ...) as response:`, and a response
exposes `.status`, `.json()`, `.text()`. Nothing here reaches into
aiohttp internals — these are ordinary Python test doubles satisfying
that same public protocol, per this repo's own request ("real public
testing mechanisms such as unittest.mock or another stable public
interface").

Usage:
    session = FakeSession({
        url: FakeResponse(status=200, json_body={...}),        # fixed
        other_url: aiohttp.ClientError("down"),                 # raises
        third_url: [aiohttp.ClientError("blip"), FakeResponse()],  # scripted in order
    })
    provider = SomeProvider(session)
    ...
    assert session.requested_urls == [...]
    assert session.requested_timeouts == [...]   # aiohttp.ClientTimeout objects passed in
    assert session.requested_json_bodies == [...]  # POST bodies, in order
"""
from __future__ import annotations

import json as _json
from typing import Any


class FakeResponse:
    """Usable as `async with session.get(...) as response:` — the only
    pattern every provider in this codebase actually uses."""

    def __init__(
        self,
        status: int = 200,
        json_body: Any = None,
        text_body: str | None = None,
    ) -> None:
        self.status = status
        self._json_body = json_body
        self._text_body = text_body if text_body is not None else ("" if json_body is None else _json.dumps(json_body))

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def json(self, content_type: str | None = None) -> Any:
        return self._json_body

    async def text(self) -> str:
        return self._text_body


Behavior = "FakeResponse | BaseException"


class FakeSession:
    """Records every `.get()`/`.post()` call (URL, timeout, and — for
    POST — the JSON body) and returns/raises whatever was scripted for
    that URL. A URL scripted with a list is consumed one entry at a
    time (front to back) across successive calls to the SAME url —
    used to script "fails once, then succeeds" without needing the
    caller to know which physical retry attempt it's on. A URL
    scripted with a single value (not a list) returns/raises that same
    value every time it's requested."""

    def __init__(self, behaviors: dict[str, Any] | None = None) -> None:
        self._behaviors: dict[str, list[Any]] = {
            url: (list(value) if isinstance(value, list) else [value]) for url, value in (behaviors or {}).items()
        }
        self.requested_urls: list[str] = []
        self.requested_timeouts: list[Any] = []
        self.requested_json_bodies: list[Any] = []

    def _resolve(self, url: str) -> FakeResponse:
        script = self._behaviors[url]
        item = script[0] if len(script) == 1 else script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def get(self, url: str, headers: dict | None = None, timeout: Any = None, **_: Any) -> FakeResponse:
        self.requested_urls.append(url)
        self.requested_timeouts.append(timeout)
        return self._resolve(url)

    def post(self, url: str, json: Any = None, timeout: Any = None, **_: Any) -> FakeResponse:
        self.requested_urls.append(url)
        self.requested_timeouts.append(timeout)
        self.requested_json_bodies.append(json)
        return self._resolve(url)

    async def close(self) -> None:
        return None
