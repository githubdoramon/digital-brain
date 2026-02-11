"""Enums for command result payloads consumed by UI layers."""

from __future__ import annotations

from enum import Enum


class CommandResultType(str, Enum):
    """Known `type` values emitted by command handlers."""

    COMMAND = "command"
    EVENT_CONFIRMATION = "event_confirmation"
    NEED_USER_INPUT = "need_user_input"

    @classmethod
    def from_value(cls, value: object) -> CommandResultType:
        normalized = str(value or "").strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return cls.COMMAND
