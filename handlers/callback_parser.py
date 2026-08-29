"""
Layer: Handlers (Playbook Part VIII Step 3; format in Part II.7, address
patterns in Part II.6).

Two independent parsing jobs live here:

1. `parse_callback` - decodes a button's `callback_data` string
   ("nav_main", "set_chain:sol", "rule_tgl:honeypot:on") into a structured
   command + params, per Part II.7's positional-shorthand tier. The
   in-memory UUID cache (Part II.7 tier 2, e.g. "result_view:{uuid}") is
   NOT resolved here - this function only splits the string; resolving a
   uuid against `SessionStore.cache_get` is the calling handler's job,
   once real cached payloads exist (Step 6 onward).
2. `validate_address_shape` - shape-only validation of raw pasted text
   against Part II.6's three regex families. Deliberately NOT chain-aware:
   it answers "does this look like any supported address at all", not
   "which chain is this" - that resolution (including the EVM
   candidate-query fallback) is Step 4's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.constants import EVM_ADDRESS_PATTERN, SOLANA_ADDRESS_PATTERN, TON_ADDRESS_PATTERN

_ADDRESS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (SOLANA_ADDRESS_PATTERN, EVM_ADDRESS_PATTERN, TON_ADDRESS_PATTERN)
)


@dataclass
class ParsedCallback:
    """A decoded `callback_data` string. `command` is everything before
    the first `:` (or the whole string, if there's no `:`); `params` is
    everything after, split on further `:` characters. Empty params list
    for a bare command like "nav_main"."""

    command: str
    params: list[str] = field(default_factory=list)


@dataclass
class ParsedAddress:
    """The result of shape-checking one piece of raw pasted text against
    Part II.6's three address families."""

    raw: str
    is_valid_shape: bool


def parse_callback(data: str) -> ParsedCallback:
    """Splits `data` on ':' per Part II.7's positional-shorthand tier.
    Never raises - a malformed/empty string just produces a
    ParsedCallback with an empty command, which no registered handler's
    command table will match, so it naturally falls through to
    UnknownInputHandler rather than needing a special error path here."""
    if not data:
        return ParsedCallback(command="", params=[])

    parts = data.split(":")
    return ParsedCallback(command=parts[0], params=parts[1:])


def validate_address_shape(text: str) -> bool:
    """True if `text` (after stripping surrounding whitespace - users
    often paste with a trailing newline or space) matches any of Part
    II.6's three address-family patterns. Purely a shape check: a
    shape-valid address can still turn out to not exist on any chain
    (Step 4's job to discover that)."""
    candidate = text.strip()
    if not candidate:
        return False
    return any(pattern.match(candidate) for pattern in _ADDRESS_PATTERNS)


def parse_address(text: str) -> ParsedAddress:
    """Convenience wrapper bundling the raw input with its shape-validity
    verdict - used by the AwaitingAddress text handler (handlers/
    navigation.py) so it has both pieces without calling this module
    twice."""
    candidate = text.strip()
    return ParsedAddress(raw=candidate, is_valid_shape=validate_address_shape(candidate))
