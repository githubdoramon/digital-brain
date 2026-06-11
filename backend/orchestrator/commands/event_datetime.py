from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_EVENT_TIMEZONE = timezone.utc


def resolve_event_timezone(timezone_name: Any) -> tzinfo:
    name = str(timezone_name or "").strip()
    if not name:
        return DEFAULT_EVENT_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return DEFAULT_EVENT_TIMEZONE


def event_timezone_from_context(context: dict[str, Any] | None) -> tzinfo:
    if not isinstance(context, dict):
        return DEFAULT_EVENT_TIMEZONE
    client_context = context.get("client_context")
    if not isinstance(client_context, dict):
        return DEFAULT_EVENT_TIMEZONE
    return resolve_event_timezone(client_context.get("timezone"))


def normalize_event_datetime(
    value: datetime | None,
    *,
    default_tz: tzinfo = DEFAULT_EVENT_TIMEZONE,
) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=default_tz)


def parse_event_datetime(
    raw: Any,
    *,
    default_tz: tzinfo = DEFAULT_EVENT_TIMEZONE,
) -> datetime | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return normalize_event_datetime(raw, default_tz=default_tz)

    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return normalize_event_datetime(parsed, default_tz=default_tz)
