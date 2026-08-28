"""Un .env con variables opcionales en blanco debe arrancar.

`cp .env.example .env` deja varias variables presentes pero vacías. Antes de
este test, DISCORD_NOTIFY_CHANNEL_ID="" abortaba el arranque de la API con un
error de parseo de entero — el primer paso del Quick Start rompía el arranque.
"""

from csuite.config import Settings


def _settings(**overrides):
    base = {"ANTHROPIC_API_KEY": "sk-ant-test"}
    base.update(overrides)
    return Settings(**base)


def test_blank_notify_channel_id_is_treated_as_unset():
    assert _settings(DISCORD_NOTIFY_CHANNEL_ID="").discord_notify_channel_id is None


def test_whitespace_notify_channel_id_is_treated_as_unset():
    assert _settings(DISCORD_NOTIFY_CHANNEL_ID="   ").discord_notify_channel_id is None


def test_real_notify_channel_id_still_parses():
    assert _settings(DISCORD_NOTIFY_CHANNEL_ID="12345").discord_notify_channel_id == 12345


def test_blank_guild_ids_is_empty_list():
    assert _settings(DISCORD_GUILD_IDS="").discord_guild_ids == []
