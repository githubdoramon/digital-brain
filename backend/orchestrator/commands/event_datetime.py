from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any

DEFAULT_EVENT_TIMEZONE = timezone.utc


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
