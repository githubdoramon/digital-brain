from __future__ import annotations

from types import SimpleNamespace

import pytest

import telegram_bot


def test_load_config_requires_webhook_secret(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setattr(
        telegram_bot.immich_client,
        "get_immich_config",
        lambda require_device=True: SimpleNamespace(
            base_url="https://immich.example",
            api_key="immich-key",
            device_id="telegram-bot",
        ),
    )

    with pytest.raises(telegram_bot.TelegramConfigError, match="TELEGRAM_WEBHOOK_SECRET"):
        telegram_bot._load_config()


def test_load_config_requires_allowed_chat_ids(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(
        telegram_bot.immich_client,
        "get_immich_config",
        lambda require_device=True: SimpleNamespace(
            base_url="https://immich.example",
            api_key="immich-key",
            device_id="telegram-bot",
        ),
    )

    with pytest.raises(telegram_bot.TelegramConfigError, match="TELEGRAM_ALLOWED_CHAT_IDS"):
        telegram_bot._load_config()
