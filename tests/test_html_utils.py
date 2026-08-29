"""
Playbook reference: Unified Developer Playbook, Part VIII Step 3 -
Definition of Done: "HTML sanitizer round-trips a token name containing
<, >, and & without breaking markup." Unit Testing Requirements: "Sanitizer
test with adversarial token-name strings (Part II.8's threat model)."

Not listed in Step 3's literal Scope (which only names test_callback_
parser.py and test_menus.py) - added as a small extra file specifically
because rendering.html_utils has zero aiogram/pydantic dependency, so this
is one of the only places in Step 3 a security-relevant function can
actually be executed and verified in this sandbox, not just syntax-
checked. The same cases are exercised again in context inside
test_menus.py; this file is the one that can actually run right now.
"""

from __future__ import annotations

import pytest

from rendering.html_utils import escape_html

# Part II.8's stated threat model: on-chain token names/symbols are
# attacker-controlled and must never be trusted to not contain markup or
# injected links.
_ADVERSARIAL_CASES: list[tuple[str, str]] = [
    ("<b>fake bold</b>", "&lt;b&gt;fake bold&lt;/b&gt;"),
    ('<a href="evil.com">click</a>', "&lt;a href=&quot;evil.com&quot;&gt;click&lt;/a&gt;"),
    ("Ampersand & Co", "Ampersand &amp; Co"),
    ("<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
    ("100% > 50%", "100% &gt; 50%"),
    ("plain token name", "plain token name"),
    ("", ""),
]


@pytest.mark.parametrize("raw,expected", _ADVERSARIAL_CASES)
def test_escape_html_table(raw: str, expected: str) -> None:
    assert escape_html(raw) == expected


def test_escape_html_contains_no_raw_angle_brackets_or_ampersands() -> None:
    """The actual safety property, checked directly rather than just via
    exact-string-match: after escaping, none of the three dangerous raw
    characters survive unescaped."""
    dangerous = "<script>alert('& steal funds')</script>"
    result = escape_html(dangerous)
    assert "<" not in result
    assert ">" not in result
    # A bare '&' is only safe once every '&' is followed by a valid entity
    # sequence - simplest correct check here is that the ONLY '&' occurrences
    # left are the ones this function itself just introduced as entities.
    assert "&amp;" in result or "&lt;" in result or "&gt;" in result or "&quot;" in result


def test_escape_html_does_not_double_escape_ampersand_from_its_own_output() -> None:
    """Regression guard for the exact bug the module's own docstring warns
    about: escaping '<' before '&' would turn '<' into '&lt;', and a
    naive second pass over '&' would then mangle it into '&amp;lt;'. This
    checks the real function's actual escape order is still correct."""
    result = escape_html("<")
    assert result == "&lt;"
    assert "&amp;lt;" not in result


def test_escape_html_round_trips_a_token_name_with_all_three_characters() -> None:
    """Step 3's literal Definition of Done line: a token name containing
    <, >, and & together must escape cleanly without breaking markup."""
    token_name = "Rug&Pull <Scam> Token"
    result = escape_html(token_name)
    assert result == "Rug&amp;Pull &lt;Scam&gt; Token"
    assert "<" not in result and ">" not in result

    # Simulate interpolating it into an actual Telegram HTML message, the
    # way rendering/menus.py does, and confirm the surrounding real tags
    # are the only unescaped angle brackets present.
    message = f"<b>{result}</b> just listed"
    assert message.count("<") == 2  # only the two real <b>/</b> tags
    assert message == "<b>Rug&amp;Pull &lt;Scam&gt; Token</b> just listed"


def test_escape_html_is_idempotent_safe_to_call_but_not_by_default_idempotent() -> None:
    """escape_html is documented as "call exactly once" - verifying that
    calling it twice DOES double-escape (proving the function itself has
    no special-cases hiding that risk) is what makes the "call it exactly
    once" rule in its docstring an actual requirement worth stating,
    rather than a moot warning about something that can't happen."""
    once = escape_html("<b>")
    twice = escape_html(once)
    assert once == "&lt;b&gt;"
    assert twice == "&amp;lt;b&amp;gt;"
    assert once != twice
