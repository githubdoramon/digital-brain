"""Shared historical-import window; this is not an archive retention policy."""

from __future__ import annotations

from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta

from messaging_normalization import Origin

HISTORY_MONTHS = 6


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def history_cutoff(now: datetime | None = None) -> datetime:
    current = now if now is not None else _utc_now()
    if current.tzinfo is None:
        raise ValueError("missing_timezone")
    return current.astimezone(timezone.utc) - relativedelta(months=HISTORY_MONTHS)


def within_history_window(record: dict, cutoff: datetime | None = None) -> bool:
    if Origin(record["origin"]) != Origin.HISTORY:
        return True
    sent_at = datetime.fromisoformat(record["sent_at"])
    if sent_at.tzinfo is None:
        raise ValueError("missing_timezone")
    return sent_at >= (cutoff if cutoff is not None else history_cutoff())
