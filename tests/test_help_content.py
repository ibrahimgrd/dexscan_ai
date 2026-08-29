"""
Step 15 - Help/FAQ/Tutorial/Security Basics content
(`rendering.help_content`). Zero aiogram import - the actual screen
rendering built on top of this lives in `rendering/menus.py`'s
`render_help`/`render_faq_answer`/`render_tutorial`/`render_security_basics`
and is covered in tests/test_menus.py alongside every other screen there.
"""

from __future__ import annotations

from rendering.help_content import FAQ_ENTRIES, SECURITY_BASICS_ENTRIES, TUTORIAL_STEPS, get_faq_entry, render_security_basics_lines


def test_every_faq_entry_has_a_unique_id() -> None:
    ids = [entry.entry_id for entry in FAQ_ENTRIES]
    assert len(ids) == len(set(ids))


def test_every_faq_entry_has_a_nonempty_question_and_answer() -> None:
    for entry in FAQ_ENTRIES:
        assert entry.question.strip()
        assert entry.answer.strip()


def test_get_faq_entry_returns_the_matching_entry() -> None:
    known_id = FAQ_ENTRIES[0].entry_id
    entry = get_faq_entry(known_id)
    assert entry is not None
    assert entry.entry_id == known_id


def test_get_faq_entry_unknown_id_returns_none_not_raises() -> None:
    """Same "stale/malformed tap has no effect" contract as
    filter_presets.set_bool_field - a button from a previous app
    version must never crash the handler that calls this."""
    assert get_faq_entry("this-id-does-not-exist") is None


def test_faq_covers_the_core_no_custody_question() -> None:
    """Part IV.3's single most important guarantee ("no custody, ever")
    must actually be in the FAQ, not just the Welcome/About disclaimer -
    a content-completeness check, not just a lookup-mechanics one."""
    assert any("fund" in entry.question.lower() or "custody" in entry.answer.lower() for entry in FAQ_ENTRIES)


def test_tutorial_has_at_least_one_step_and_mentions_trade_staging_confirmation() -> None:
    assert len(TUTORIAL_STEPS) >= 1
    assert any("confirm" in step.lower() or "trade staging" in step.lower() for step in TUTORIAL_STEPS)


def test_security_basics_entries_are_nonempty_pairs() -> None:
    assert len(SECURITY_BASICS_ENTRIES) >= 1
    for term, explanation in SECURITY_BASICS_ENTRIES:
        assert term.strip()
        assert explanation.strip()


def test_security_basics_covers_the_playbook_glossary_terms() -> None:
    """Appendix B's glossary and this screen should speak one
    vocabulary - honeypot, rug pull, and LP lock are the three terms
    Part III.2 and Part IV.3 both lean on most heavily."""
    terms_lower = {term.lower() for term, _ in SECURITY_BASICS_ENTRIES}
    assert any("honeypot" in t for t in terms_lower)
    assert any("rug" in t for t in terms_lower)
    assert any("lp lock" in t or "liquidity" in t for t in terms_lower)


def test_render_security_basics_lines_produces_one_line_per_entry() -> None:
    lines = render_security_basics_lines()
    assert len(lines) == len(SECURITY_BASICS_ENTRIES)
    for line, (term, _) in zip(lines, SECURITY_BASICS_ENTRIES):
        assert f"<b>{term}</b>" in line
