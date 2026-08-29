"""
Step 15 - unified error rendering (`rendering.error_copy`, wrapped by
`rendering.error_renderer.render_error`). Tests target `error_copy`
directly (zero aiogram import, so genuinely executable in a sandbox
without aiogram installed) - `error_renderer.render_error` adds only
keyboard construction around the exact same `ErrorRenderPlan`, covered
separately below wherever aiogram is actually available.
"""

from __future__ import annotations

import pytest

from rendering.error_copy import build_error_render_plan

_HOME = ("\U0001f3e0 Home", "nav_main")


# -- the show_technical x technical_detail 2x2 matrix, Step 15's own
#    explicit Unit Testing Requirement ----------------------------------


def test_show_technical_true_detail_present_includes_the_detail() -> None:
    plan = build_error_render_plan("Something went wrong.", "ConnectionError: timed out", show_technical=True, recovery_action=_HOME)
    assert "Something went wrong." in plan.message_html
    assert "ConnectionError: timed out" in plan.message_html


def test_show_technical_true_detail_absent_shows_no_placeholder() -> None:
    plan = build_error_render_plan("Something went wrong.", None, show_technical=True, recovery_action=_HOME)
    assert "Something went wrong." in plan.message_html
    assert "<code>" not in plan.message_html  # nothing true to show, so nothing shown - not a "(no detail)" filler


def test_show_technical_false_detail_present_hides_the_detail() -> None:
    plan = build_error_render_plan("Something went wrong.", "ConnectionError: timed out", show_technical=False, recovery_action=_HOME)
    assert "Something went wrong." in plan.message_html
    assert "ConnectionError" not in plan.message_html
    assert "timed out" not in plan.message_html


def test_show_technical_false_detail_absent_shows_only_the_plain_message() -> None:
    plan = build_error_render_plan("Something went wrong.", None, show_technical=False, recovery_action=_HOME)
    assert plan.message_html == "\u26a0\ufe0f Something went wrong."


# -- plain-language-first, never buried ------------------------------------


def test_plain_message_always_appears_before_any_technical_detail() -> None:
    plan = build_error_render_plan("Plain sentence.", "raw traceback line", show_technical=True, recovery_action=_HOME)
    assert plan.message_html.index("Plain sentence.") < plan.message_html.index("raw traceback line")


def test_dynamic_error_text_is_html_escaped() -> None:
    """Part II.8's threat model - an error message could in principle
    echo back attacker-influenced content (e.g. a malformed pasted
    string) - must never break message HTML."""
    plan = build_error_render_plan("Bad input: <script>", None, show_technical=False, recovery_action=_HOME)
    assert "<script>" not in plan.message_html
    assert "&lt;script&gt;" in plan.message_html


# -- exactly one recovery action -------------------------------------------


def test_recovery_action_tuple_is_carried_through_unmodified() -> None:
    plan = build_error_render_plan("Oops.", None, show_technical=False, recovery_action=("\U0001f504 Try Again", "nav_scan"))
    assert plan.recovery_label == "\U0001f504 Try Again"
    assert plan.recovery_callback == "nav_scan"


@pytest.mark.parametrize(
    "recovery_action",
    [("\U0001f3e0 Home", "nav_main"), ("\U0001f504 Try Again", "nav_scan"), ("\u25c0\ufe0f Back", "nav_help")],
)
def test_plan_always_carries_exactly_one_recovery_action_never_a_list(recovery_action: tuple[str, str]) -> None:
    plan = build_error_render_plan("Oops.", None, False, recovery_action)
    assert isinstance(plan.recovery_label, str)
    assert isinstance(plan.recovery_callback, str)


# -- render_error (aiogram-facing wrapper) - real keyboard, exactly one
#    button, no more ---------------------------------------------------


@pytest.mark.asyncio
async def test_render_error_keyboard_has_exactly_one_button() -> None:
    from rendering.error_renderer import render_error

    rendered = render_error("Oops.", "detail", show_technical=True, recovery_action=_HOME)
    rows = rendered.keyboard.inline_keyboard
    total_buttons = sum(len(row) for row in rows)
    assert total_buttons == 1
    assert rows[0][0].text == _HOME[0]
    assert rows[0][0].callback_data == _HOME[1]


@pytest.mark.asyncio
async def test_render_error_html_matches_the_pure_plan_exactly() -> None:
    from rendering.error_copy import build_error_render_plan
    from rendering.error_renderer import render_error

    plan = build_error_render_plan("Consistency check.", "tech", True, _HOME)
    rendered = render_error("Consistency check.", "tech", True, _HOME)
    assert rendered.html == plan.message_html
