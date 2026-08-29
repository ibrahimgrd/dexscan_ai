"""
Playbook reference: Unified Developer Playbook, Part VIII Step 3 - Unit
Testing Requirements: table-driven test over sample callback_data strings
(Part II.7's own examples), plus validate_address_shape against Part
II.6's three address families.

Pure stdlib + this project's own modules only (handlers.callback_parser
depends only on bot.constants, which is pure stdlib) - no pydantic/aiogram
dependency, so this file can run even before those packages are installed.
"""

from __future__ import annotations

import pytest

from handlers.callback_parser import (
    ParsedAddress,
    ParsedCallback,
    parse_address,
    parse_callback,
    validate_address_shape,
)

# Part II.7's own examples, plus edge cases this module's docstrings call
# out explicitly (empty string, bare command with no colon).
_CALLBACK_CASES: list[tuple[str, str, list[str]]] = [
    ("nav_main", "nav_main", []),
    ("set_chain:sol", "set_chain", ["sol"]),
    ("result_view:8f3c1a2b", "result_view", ["8f3c1a2b"]),
    ("rule_tgl:honeypot:on", "rule_tgl", ["honeypot", "on"]),
    ("", "", []),
    ("scan_trending", "scan_trending", []),
    ("rule_preset:conservative", "rule_preset", ["conservative"]),
]


@pytest.mark.parametrize("data,expected_command,expected_params", _CALLBACK_CASES)
def test_parse_callback_table(
    data: str, expected_command: str, expected_params: list[str]
) -> None:
    result = parse_callback(data)
    assert result == ParsedCallback(command=expected_command, params=expected_params)


def test_parse_callback_never_raises_on_garbage() -> None:
    for garbage in ["::::", ":", "a:b:c:d:e:f", "   ", "\n"]:
        result = parse_callback(garbage)
        assert isinstance(result, ParsedCallback)


# Part II.6's three address families - a real/realistic-shaped example and
# a clearly-wrong example per family, plus cross-family negatives. Each
# invalid case isolates ONE thing that's wrong (length XOR character set)
# rather than mixing both, so a failure points at the actual bug.
_ADDRESS_SHAPE_CASES: list[tuple[str, bool]] = [
    # Solana - base58, 32-44 chars (real wrapped-SOL mint address)
    ("So11111111111111111111111111111111111111112", True),
    ("1" * 34, True),  # '1' IS in the base58 alphabet (only 0/O/I/l excluded)
    ("1" * 31, False),  # one char under the 32 minimum
    ("1" * 45, False),  # one char over the 44 maximum
    ("0" * 34, False),  # '0' is NOT in the base58 alphabet
    # EVM - 0x + exactly 40 hex chars (shared shape across ETH/BSC/BASE/ARB)
    ("0x" + "a1B2" * 10, True),
    ("0x" + "a" * 39, False),  # one char short
    ("0x" + "a" * 41, False),  # one char long
    ("0x" + "G" * 40, False),  # right length, non-hex characters
    ("0X" + "a" * 40, False),  # capital X - the pattern requires lowercase 0x
    # TON - exactly 48 chars from [a-zA-Z0-9_-]
    ("EQ" + "A" * 46, True),
    ("EQ" + "A" * 45, False),  # one char short
    ("EQ" + "A" * 47, False),  # one char long
    # Cross-family negatives
    ("", False),
    ("not-an-address", False),
    ("   ", False),
]


@pytest.mark.parametrize("text,expected", _ADDRESS_SHAPE_CASES)
def test_validate_address_shape_table(text: str, expected: bool) -> None:
    assert validate_address_shape(text) is expected


def test_validate_address_shape_strips_whitespace() -> None:
    """Users often paste with a trailing newline or leading/trailing
    spaces - shape validation should not reject on that basis alone."""
    address = "0x" + "a" * 40
    assert validate_address_shape(f"  {address}  \n") is True


def test_parse_address_bundles_raw_and_validity() -> None:
    address = "0x" + "b" * 40
    result = parse_address(f" {address} ")
    assert result == ParsedAddress(raw=address, is_valid_shape=True)

    invalid = parse_address("definitely not an address")
    assert invalid == ParsedAddress(raw="definitely not an address", is_valid_shape=False)
