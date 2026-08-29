"""
Layer: pure content (Playbook Part VIII Step 15 - FAQ accordion,
Tutorial, Security Basics).

Plain data + lookup functions, zero aiogram import - `rendering/menus.py`
turns these into real screens; this module only owns what gets said,
not how it's framed as Telegram HTML/keyboard. Kept separate from
`rendering/menus.py` itself so the content (which will grow - Step 15's
own "other Playbook-defined content" for About, more FAQ entries later)
doesn't turn that file's already-long screen inventory into an
unreadable wall of copy, and so an accordion's "does this id exist"
lookup is unit-testable without aiogram.

Security Basics content deliberately reuses the Playbook's own Appendix
B glossary terms (honeypot, rug pull, LP lock, mint/freeze authority,
HCI) rather than inventing parallel definitions - one vocabulary, used
consistently from the Playbook through to what a user actually reads.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaqEntry:
    entry_id: str
    question: str
    answer: str


FAQ_ENTRIES: tuple[FaqEntry, ...] = (
    FaqEntry(
        "custody",
        "Does DexScan AI ever hold my funds?",
        "No. It never custodies funds, holds your keys, or executes a trade for you — every action "
        "toward an external bot happens only after your own explicit tap on Trade Staging.",
    ),
    FaqEntry(
        "ai_score",
        "What does the AI Score mean?",
        "A categorical, explainable read on a token's profile (e.g. \u201cSolid, Monitor\u201d) — never "
        "trading advice, and never a \u201cBuy\u201d or \u201cBullish\u201d call. It always ships with the "
        "specific factors that produced it, in Result Detail.",
    ),
    FaqEntry(
        "risk_vs_score",
        "Why are Risk Level and AI Score shown separately?",
        "\u201cIs this contract safe to interact with\u201d and \u201cis this an interesting opportunity\u201d "
        "are different questions. A token can be safe but unremarkable, or risky but hyped — blending "
        "them into one number would hide that.",
    ),
    FaqEntry(
        "chains",
        "Which chains are supported?",
        "Solana, Ethereum, BNB Chain, Base, Arbitrum, and TON. Paste an address and the chain is "
        "detected automatically.",
    ),
    FaqEntry(
        "auto_watch",
        "Does Auto-Watch trade for me?",
        "No — it only scans in the background and alerts you on a match. Every alert still routes "
        "through the same human-approval Trade Staging screen as a manual scan.",
    ),
    FaqEntry(
        "accuracy",
        "Can I fully trust a \u201cStrong Profile\u201d verdict?",
        "Treat every verdict as a starting point, not a guarantee — this is heuristic analysis of "
        "public on-chain and social data, not a promise. Under-promising on purpose is a design "
        "principle here, not a hedge.",
    ),
)

_FAQ_BY_ID: dict[str, FaqEntry] = {entry.entry_id: entry for entry in FAQ_ENTRIES}


def get_faq_entry(entry_id: str) -> FaqEntry | None:
    """Returns the entry, or None for an unknown/stale id (a button
    from a previous app version, or a malformed callback) - same
    "no effect rather than raise" contract as filter_presets'
    set_bool_field/cycle_numeric_field for input this app itself
    generated."""
    return _FAQ_BY_ID.get(entry_id)


TUTORIAL_STEPS: tuple[str, ...] = (
    "1\ufe0f\u20e3 Tap <b>Scan Now</b>, then paste a contract address (or pick Trending / New Listings).",
    "2\ufe0f\u20e3 DexScan AI runs five independent checks — Core, Security, Holder, Momentum, and "
    "Social — and shows you what each one found.",
    "3\ufe0f\u20e3 Open <b>Result Detail</b> for the full AI Intel Report: a categorical verdict, plus "
    "the specific factors behind it.",
    "4\ufe0f\u20e3 Ready to act? Tap <b>Buy via Preferred Bot</b>. You'll always confirm on Trade Staging "
    "before anything opens in an external bot — DexScan AI itself never touches the trade.",
    "5\ufe0f\u20e3 Want it running unattended? <b>Auto-Watch</b> scans the background and alerts you on a "
    "match, using the same human-approval step.",
)

SECURITY_BASICS_ENTRIES: tuple[tuple[str, str], ...] = (
    ("Honeypot", "A contract that lets you buy but blocks or heavily taxes selling. The Security engine flags a sell tax of 99% or higher as a critical honeypot alert."),
    ("Rug pull", "A project abandoning or draining liquidity after attracting buyers. Locked or burned LP tokens are the main defense to check for."),
    ("Mint authority", "If still active, the deployer can create new tokens and dilute your share at will. DexScan AI flags this independently of freeze authority."),
    ("Freeze authority", "If still active, the deployer can block transfers outright — a stronger red flag than mint authority alone."),
    ("LP lock", "Liquidity provider tokens made inaccessible (locked or burned) so they can't be pulled out from under holders."),
    ("Holder concentration (HCI)", "The combined share of supply held by the top 10 wallets. DexScan AI flags this above 30%."),
)


def render_security_basics_lines() -> tuple[str, ...]:
    """One "<b>Term</b>: explanation" line per entry - a plain data
    transform kept here (not in rendering/menus.py) so it's testable
    alongside the content it formats."""
    return tuple(f"<b>{term}</b>: {explanation}" for term, explanation in SECURITY_BASICS_ENTRIES)
