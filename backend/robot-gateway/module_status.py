"""Derived module presence/status helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from config import ROBOT_MODULE_OFFLINE_AFTER_SECONDS


def derive_module_status(
    *,
    last_seen_at: datetime | None,
    reported_status: str | None,
    status_updated_at: datetime | None,
    now: datetime | None = None,
) -> str:
    """Return the module status shown to clients.

    Presence is derived from recent activity. A module is offline when it has
    not reported telemetry or status within the configured window.
    """

    if last_seen_at is None or _is_stale(last_seen_at, now=now):
        return "offline"

    if reported_status == "error" and status_updated_at is not None:
        return "error"

    if (
        reported_status == "offline"
        and status_updated_at is not None
        and _same_or_later(status_updated_at, last_seen_at)
    ):
        return "offline"

    return "online"


def _is_stale(last_seen_at: datetime, *, now: datetime | None = None) -> bool:
    current = _coerce_now(now, reference=last_seen_at)
    return current - last_seen_at > timedelta(seconds=ROBOT_MODULE_OFFLINE_AFTER_SECONDS)


def _same_or_later(left: datetime, right: datetime) -> bool:
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    if left.tzinfo is not None and right.tzinfo is None:
        right = right.replace(tzinfo=left.tzinfo)
    return left >= right


def _coerce_now(now: datetime | None, *, reference: datetime) -> datetime:
    if now is not None:
        if now.tzinfo is None and reference.tzinfo is not None:
            return now.replace(tzinfo=reference.tzinfo)
        if now.tzinfo is not None and reference.tzinfo is None:
            return now.replace(tzinfo=None)
        return now

    return datetime.now(reference.tzinfo or UTC)
