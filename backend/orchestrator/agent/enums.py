"""Shared enums for agent control-flow and tool outcomes."""

from __future__ import annotations

from enum import Enum


class LimitAction(str, Enum):
    """Controller action after evaluating limits and recovery options."""

    OK = "ok"
    VIOLATION = "violation"
    ESCALATED = "escalated"


class FollowUpSource(str, Enum):
    """Origins for user-facing follow-up prompts."""

    CONTACT_CLARIFICATION = "contact_clarification"
    UI_FOLLOW_UP = "ui_follow_up"


class ConfidenceTier(str, Enum):
    """Routing confidence buckets used for tool-visibility policy."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ToolVisibilityMode(str, Enum):
    """Controller visibility modes for available tools."""

    FULL = "full"
    RESTRICTED = "restricted"
    RESTRICTED_WITH_RESOLUTION = "restricted_with_resolution"
    NONE = "none"


class ToolStatus(str, Enum):
    """Canonical status values used by tool results."""

    SUCCESS = "success"
    NEED_USER_INPUT = "need_user_input"
    NO_PEOPLE = "no_people"
    NO_PROGRESS = "no_progress"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(
        cls,
        value: object,
        *,
        default: ToolStatus | None = None,
    ) -> ToolStatus:
        """Parse arbitrary status input into a known enum value."""
        normalized = str(value or "").strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return default or cls.UNKNOWN
