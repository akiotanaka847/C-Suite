"""Settings parsing for comma-separated list env vars.

Regression test for pydantic-settings json-decoding comma-separated values
into list fields and raising SettingsError before validators run.

The email/telegram/discord allowlist fields were removed when channel
access moved to the People roster; the only remaining comma-list field
is DISCORD_GUILD_IDS, which is exercised below to keep the parsing
pattern under test.
"""
from __future__ import annotations

import pytest

from openexecutive.config import Settings


@pytest.fixture(autouse=True)
def _required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("EXEC_EMAIL_ADDRESS", "exec@example.com")


def test_discord_guild_ids_comma_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_GUILD_IDS", "123,456")
    assert Settings().discord_guild_ids == [123, 456]


def test_discord_guild_ids_single_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_GUILD_IDS", "789")
    assert Settings().discord_guild_ids == [789]


def test_discord_guild_ids_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_GUILD_IDS", "")
    assert Settings().discord_guild_ids == []
