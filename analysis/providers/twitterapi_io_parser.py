"""
Layer: Provider adapter — twitterapi.io schema mapping (Playbook Part
II.3; Part VIII Step 13).

All schema knowledge for twitterapi.io lives here, deliberately split
from `twitterapi_io.py`'s network I/O — same rationale, and the exact
same file split, as Steps 4-5's `dexscreener`/`dexscreener_parser` and
`rugcheck`/`rugcheck_parser` pairs: this module has zero external
dependencies (stdlib only), so it's testable with plain fixture dicts,
no aiohttp, no real API key, no network.

Every field name and response shape below (`GET /twitter/user/info`,
`GET /twitter/user/followers`, `GET /twitter/tweet/advanced_search`) is
verified against twitterapi.io's own published API reference
(docs.twitterapi.io/api-reference) while implementing this step, not
guessed at — see `twitterapi_io.py`'s docstring for the specific pages.
"""

from __future__ import annotations

from datetime import datetime

from analysis.api_abstraction import Tweet, XUserProfile

# twitterapi.io's fixed datetime format, e.g. "Thu Dec 13 08:41:26 +0000
# 2007" - the same format the legacy Twitter API used, and the one
# format every timestamp field in this provider's responses uses
# (createdAt on a user, createdAt on a tweet).
_TWITTER_DATETIME_FORMAT = "%a %b %d %H:%M:%S %z %Y"


def _parse_twitter_datetime(raw: str | None) -> datetime | None:
    """`None` for a missing/empty field OR one that doesn't match the
    expected format — a malformed timestamp degrades that one field
    rather than failing the whole parse (Part V.5's spirit applied at
    field granularity, same as `dexscreener_parser.py`'s handling of an
    individual missing numeric field)."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, _TWITTER_DATETIME_FORMAT)
    except ValueError:
        return None


def _user_info_from_dict(raw: dict, fallback_username: str = "") -> XUserProfile:
    """Maps one twitterapi.io `UserInfo`-shaped dict to `XUserProfile`.
    Reused across all three endpoints below — a followers-list entry and
    a tweet's `author` field are both this exact same shape, per
    twitterapi.io's own schema (confirmed while implementing this step:
    all three endpoints share one `UserInfo` component in twitterapi.io's
    published OpenAPI spec)."""
    is_unavailable = bool(raw.get("unavailable", False))
    return XUserProfile(
        user_id=str(raw.get("id", "")),
        username=raw.get("userName") or fallback_username,
        display_name=raw.get("name", ""),
        is_verified=bool(raw.get("isBlueVerified", False)),
        follower_count=int(raw.get("followers", 0) or 0),
        following_count=int(raw.get("following", 0) or 0),
        tweet_count=int(raw.get("statusesCount", 0) or 0),
        created_at=_parse_twitter_datetime(raw.get("createdAt")),
        description=raw.get("description", ""),
        account_exists=not is_unavailable,
    )


def parse_user_lookup(payload: dict, requested_handle: str) -> XUserProfile:
    """
    `GET /twitter/user/info` wraps the profile in `{data, status, msg}`
    (confirmed against twitterapi.io's published OpenAPI spec for this
    endpoint). `status == "error"` or a missing/null `data` both mean
    "this handle didn't resolve to a real account" — not a transport
    failure (the HTTP call itself succeeded with a 200), so this returns
    an `account_exists=False` profile rather than raising;
    `twitterapi_io.py` is the layer that raises for actual transport
    failures. `requested_handle` fills `username` in that not-found case,
    since the response body has no username to report.
    """
    if payload.get("status") == "error" or not payload.get("data"):
        return XUserProfile(
            user_id="", username=requested_handle, display_name="", is_verified=False,
            follower_count=0, following_count=0, tweet_count=0, created_at=None,
            description="", account_exists=False,
        )
    return _user_info_from_dict(payload["data"], fallback_username=requested_handle)


def parse_followers(payload: dict) -> list[XUserProfile]:
    """
    `GET /twitter/user/followers` wraps the page in `{followers: [...],
    has_next_page, next_cursor}` (confirmed against twitterapi.io's
    published endpoint reference). This provider only ever requests one
    page (see `SocialDataProvider.list_followers`'s docstring for why) —
    `has_next_page`/`next_cursor` are present in the real response but
    unused here, since this method deliberately isn't a full paginating
    fetch.
    """
    return [_user_info_from_dict(entry) for entry in payload.get("followers", [])]


def parse_mentions(payload: dict) -> list[Tweet]:
    """
    `GET /twitter/tweet/advanced_search` wraps results in `{tweets: [...],
    has_next_page, next_cursor}`, each tweet carrying a full `author`
    `UserInfo` object (confirmed against twitterapi.io's published
    OpenAPI spec for this endpoint). A tweet with a missing/malformed
    `author` block (shouldn't happen per the spec, but Part V.5 says
    don't trust an external payload blindly) is skipped rather than
    crashing the whole parse — one bad entry doesn't need to cost every
    other real mention in the same response.
    """
    tweets: list[Tweet] = []
    for entry in payload.get("tweets", []):
        author = entry.get("author")
        if not isinstance(author, dict):
            continue
        tweets.append(
            Tweet(
                tweet_id=str(entry.get("id", "")),
                text=entry.get("text", ""),
                author_username=author.get("userName", ""),
                author_follower_count=int(author.get("followers", 0) or 0),
                author_is_verified=bool(author.get("isBlueVerified", False)),
                created_at=_parse_twitter_datetime(entry.get("createdAt")),
            )
        )
    return tweets
