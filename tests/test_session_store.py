"""
Playbook reference: Unified Developer Playbook, Part VIII Step 1 - Unit
Testing Requirements.

get/set round-trip, cache_put/cache_get round-trip, and cache_get on an
unknown key returns None rather than raising. Pure stdlib + this project's
own modules only - no pydantic/aiogram dependency, so this file can run
even before those packages are installed.
"""

from __future__ import annotations

from bot.types import SessionContext
from state.session_store import SessionStore


def test_get_returns_none_for_unknown_user() -> None:
    store = SessionStore()
    assert store.get(user_id=12345) is None


def test_set_then_get_round_trips() -> None:
    store = SessionStore()
    ctx = SessionContext(user_id=1, payload={"fsm_state": "awaiting_address", "chain": "sol"})

    store.set(1, ctx)
    fetched = store.get(1)

    assert fetched is not None
    assert fetched.user_id == 1
    assert fetched.payload == {"fsm_state": "awaiting_address", "chain": "sol"}


def test_set_replaces_existing_context_for_same_user() -> None:
    store = SessionStore()
    store.set(1, SessionContext(user_id=1, payload={"chain": "sol"}))
    store.set(1, SessionContext(user_id=1, payload={"chain": "eth"}))

    fetched = store.get(1)
    assert fetched is not None
    assert fetched.payload == {"chain": "eth"}


def test_cache_put_get_round_trips() -> None:
    store = SessionStore()
    payload = {"address": "So11111111111111111111111111111111111111112", "chain": "sol"}

    key = store.cache_put(payload)

    assert isinstance(key, str) and len(key) > 0
    assert store.cache_get(key) == payload


def test_cache_put_returns_unique_keys_across_calls() -> None:
    store = SessionStore()
    key_a = store.cache_put({"n": 1})
    key_b = store.cache_put({"n": 2})

    assert key_a != key_b
    assert store.cache_get(key_a) == {"n": 1}
    assert store.cache_get(key_b) == {"n": 2}


def test_cache_get_unknown_key_returns_none_not_exception() -> None:
    store = SessionStore()
    assert store.cache_get("does-not-exist") is None


def test_stores_are_independent_between_instances() -> None:
    """Guards against an accidental module-level mutable default - each
    SessionStore() must start empty, since Step 2's dispatcher tests will
    construct fresh stores per test."""
    store_a = SessionStore()
    store_a.set(1, SessionContext(user_id=1, payload={"chain": "sol"}))

    store_b = SessionStore()
    assert store_b.get(1) is None
