"""
Playbook reference: Unified Developer Playbook, Part VIII Step 1 - Unit
Testing Requirements.

`Settings` raises a clear validation error when `telegram_bot_token` is
missing, and loads correctly (with the documented defaults) when present.

Requires `pydantic` + `pydantic-settings` to be installed - see
requirements.txt. If this file cannot import, run
`pip install -r requirements.txt` first.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import Settings


def test_settings_loads_with_required_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-test-token")
    monkeypatch.delenv("DEFAULT_CHAIN", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    settings = Settings(_env_file=None)  # ignore any .env in the test cwd

    assert settings.telegram_bot_token == "123456:fake-test-token"
    assert settings.default_chain == "sol"
    assert settings.log_level == "INFO"


def test_settings_raises_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_respects_explicit_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-test-token")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.log_level == "DEBUG"
