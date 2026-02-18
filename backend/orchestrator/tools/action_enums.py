"""Shared enums for tool action parameters."""

from __future__ import annotations

from enum import Enum


class HomeAssistantAction(str, Enum):
    """Allowed action values for the `home_assistant` tool."""

    LIST_TOOLS = "list_tools"
    CALL_TOOL = "call_tool"

    @classmethod
    def from_value(cls, value: object) -> HomeAssistantAction | None:
        normalized = str(value or "").strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return None


class LookupContactAction(str, Enum):
    """Allowed action values for the `lookup_contact` tool."""

    SEARCH = "search"
    GET_RELATIONSHIPS = "get_relationships"
    FIND_RELATED = "find_related"

    @classmethod
    def from_value(
        cls,
        value: object,
        *,
        default: LookupContactAction | None = None,
    ) -> LookupContactAction | None:
        normalized = str(value or "").strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return default


class SelectContactsAction(str, Enum):
    """Allowed action values for the `select_contacts` tool."""

    SELECT = "select"
    LIST_GROUPS = "list_groups"
    GET_GROUP = "get_group"
    CREATE_GROUP = "create_group"
    ARCHIVE_GROUP = "archive_group"

    @classmethod
    def from_value(
        cls,
        value: object,
        *,
        default: SelectContactsAction | None = None,
    ) -> SelectContactsAction | None:
        normalized = str(value or "").strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return default


class GetEventsAction(str, Enum):
    """Allowed action values for the `get_events` tool."""

    BY_IDS = "by_ids"
    BY_TIME_SPAN = "by_time_span"

    @classmethod
    def from_value(
        cls,
        value: object,
        *,
        default: GetEventsAction | None = None,
    ) -> GetEventsAction | None:
        normalized = str(value or "").strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return default
