"""
Layer: pure business logic (Playbook Part VIII Step 15; Part IV.3's
"plain language first, technical detail opt-in, exactly one recovery
action" made real).

Split out of `rendering/error_renderer.py` for the same reason
`bot/settings_logic.py` is split from `handlers/settings_handler.py`:
zero aiogram import, so directly unit-testable without the Telegram
framework installed. `error_renderer.py` wraps this module's
`ErrorRenderPlan` into an actual `RenderedMessage` with a real
`InlineKeyboardMarkup` - the ONE place that conversion happens, so
every error screen in this codebase is built the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

from rendering.html_utils import escape_html


@dataclass
class ErrorRenderPlan:
    """Everything an error screen needs to say, still framework-free:
    the finished HTML body and the single recovery button's label +
    callback_data. `rendering.error_renderer.render_error` is the only
    thing that turns `recovery_label`/`recovery_callback` into a real
    keyboard."""

    message_html: str
    recovery_label: str
    recovery_callback: str


def build_error_render_plan(
    user_error: str,
    technical_detail: str | None,
    show_technical: bool,
    recovery_action: tuple[str, str],
) -> ErrorRenderPlan:
    """
    Part VIII Step 15's Public Interface, `render_error`'s exact
    signature, minus the keyboard construction:

    - `user_error`: plain-language sentence, ALWAYS shown, always first
      (Part IV.3 — never buried, never conditional).
    - `technical_detail`: appended, in a monospace block, ONLY when
      `show_technical` is True — never shown by default (Part V.3: raw
      internals are never surfaced to a user who hasn't opted in). A
      `None` detail with `show_technical=True` shows nothing extra
      rather than a placeholder like "(no detail)" — there's nothing
      true to say, so nothing is said.
    - `recovery_action`: `(label, callback_data)` for the ONE button
      this screen gets (Part IV.4: "exactly one recovery action for
      error screens" — never zero, never a menu of options).
    """
    recovery_label, recovery_callback = recovery_action
    html = f"\u26a0\ufe0f {escape_html(user_error)}"
    if show_technical and technical_detail:
        html += f"\n\n<code>{escape_html(technical_detail)}</code>"
    return ErrorRenderPlan(message_html=html, recovery_label=recovery_label, recovery_callback=recovery_callback)
