"""
Layer: Rendering (Playbook Part VIII Step 3; standard in Part II.8).

The one shared HTML-escaping function every renderer uses before
interpolating any value that didn't originate as a literal string in this
codebase - most importantly, on-chain token names and symbols, which are
attacker-controlled: a malicious deployer can name a token anything,
including text crafted to break Telegram's HTML parser or inject a fake
link (Part II.8's stated threat model).

Telegram's Bot API HTML mode supports a small tag subset (<b>, <i>, <a
href="">, <code>, etc.) - anything NOT meant to be one of those tags must
have its `&`, `<`, `>` escaped, and `"` too if it might ever land inside a
quoted attribute (an `<a href="...">`, most concretely). This module
escapes all four unconditionally rather than trying to track which call
sites need which subset - the cost of over-escaping plain text is zero;
the cost of under-escaping attacker-controlled text is a broken or
maliciously-modified message.
"""

from __future__ import annotations

# Order matters: '&' MUST be replaced first. Escaping '<'/'>' before '&'
# would introduce fresh '&' characters (from "&lt;"/"&gt;") that a
# subsequent '&' replacement would then double-escape into "&amp;lt;".
_ESCAPE_ORDER: tuple[tuple[str, str], ...] = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
)


def escape_html(text: str) -> str:
    """Escapes `text` for safe interpolation into a Telegram HTML-mode
    message. Idempotent-safe input handling: run this exactly once, on the
    raw value, immediately before interpolating it - never on text that's
    already been escaped (that would double-escape it), and never skip it
    for "probably safe" values (Part II.8: assume every external string,
    e.g. a token name, is attacker-controlled)."""
    escaped = text
    for raw, replacement in _ESCAPE_ORDER:
        escaped = escaped.replace(raw, replacement)
    return escaped
