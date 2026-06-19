"""Durable command-state recovery helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

import conversations

RESTORED_FROM_METADATA_KEY = "_restored_from_metadata"

CommandResultBuilder = Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any] | None]


def is_restored_from_metadata(command_data: dict[str, Any] | None) -> bool:
    return bool(isinstance(command_data, dict) and command_data.get(RESTORED_FROM_METADATA_KEY))


def get_recoverable_command_data(
    preview_id: str,
    user_email: str,
    *,
    get_cached_data: Callable[[str], dict[str, Any] | None],
    build_from_command_result: CommandResultBuilder,
) -> dict[str, Any] | None:
    """Return command state from cache, falling back to persisted message metadata."""
    command_data = get_cached_data(preview_id)
    if command_data:
        return command_data

    exchange = conversations.get_command_exchange_from_metadata(preview_id, user_email)
    if not exchange:
        return None

    metadata = exchange.get("assistant_metadata")
    if not isinstance(metadata, dict):
        return None
    command_result = metadata.get("command_result")
    if not isinstance(command_result, dict):
        return None

    command_state = command_result.get("command_state")
    if isinstance(command_state, dict):
        restored = deepcopy(command_state)
        restored.setdefault("thread_id", exchange.get("thread_id"))
        restored.setdefault("user_email", user_email)
        restored[RESTORED_FROM_METADATA_KEY] = True
        return restored

    restored = build_from_command_result(command_result, exchange, user_email)
    if not restored:
        return None
    restored[RESTORED_FROM_METADATA_KEY] = True
    return restored


def ensure_restored_thread_exists(
    command_data: dict[str, Any],
    user_email: str,
) -> str | None:
    """Return a restored thread id only when it still exists and belongs to the user."""
    thread_id = command_data.get("thread_id")
    if not thread_id or not is_restored_from_metadata(command_data):
        return str(thread_id) if thread_id else None

    conversations.ensure_thread(str(thread_id), user_email)
    return str(thread_id)
