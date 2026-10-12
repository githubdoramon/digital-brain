from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import events as events_service
import google_calendar
from observability.logger import get_runtime_logger
from scheduled_jobs import GOOGLE_CALENDAR_SYNC

logger = get_runtime_logger(__name__)

JOB_TYPE = "google_calendar_sync"
POLL_SECONDS = GOOGLE_CALENDAR_SYNC.poll_seconds
SYNC_INTERVAL_SECONDS = GOOGLE_CALENDAR_SYNC.run_interval_seconds or 300
TIMEZONE_NAME = GOOGLE_CALENDAR_SYNC.timezone_name or "Europe/Lisbon"
RECONCILIATION_HOUR = GOOGLE_CALENDAR_SYNC.time_local.hour if GOOGLE_CALENDAR_SYNC.time_local else 3

_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()
_STATUS_LOCK = threading.Lock()
_LAST_SYNC_AT: str | None = None
_LAST_RECONCILIATION_AT: str | None = None
_LAST_ERROR: str | None = None
_SYNCED_EVENT_COUNT = 0
_RECONCILED_EVENT_COUNT = 0
_LAST_RECONCILIATION_WEEK: tuple[int, int] | None = None


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        name="google-calendar-sync",
        daemon=True,
    )
    _WORKER_THREAD.start()
    logger.info("[google_calendar.sync] worker started")


def stop_worker(timeout: float = 5.0) -> None:
    _STOP_EVENT.set()
    thread = _WORKER_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=timeout)


def sync_once(*, now_utc: datetime | None = None) -> dict[str, int]:
    global _LAST_SYNC_AT, _LAST_RECONCILIATION_AT, _LAST_ERROR
    global _SYNCED_EVENT_COUNT, _RECONCILED_EVENT_COUNT
    global _LAST_RECONCILIATION_WEEK

    now = _as_utc(now_utc or datetime.now(timezone.utc))
    local_now = now.astimezone(ZoneInfo(TIMEZONE_NAME))
    reconcile = _is_reconciliation_due(local_now)
    window_start = now - timedelta(days=14)
    window_end = now + timedelta(days=60)
    reconciliation_start = now - timedelta(days=14)
    reconciliation_end = now + timedelta(days=14)
    synced_count = 0
    reconciled_count = 0
    errors: list[str] = []
    connections = google_calendar.list_connections()

    for connection in connections:
        email = str(connection.get("user_email") or "").strip()
        selected_ids = connection.get("selected_calendar_ids") or []
        account_error: str | None = None
        account_reconciled = False
        try:
            if not selected_ids:
                google_calendar.record_sync_result(email)
                continue
            for calendar_id in selected_ids:
                calendar_id = str(calendar_id).strip()
                if not calendar_id:
                    continue
                provider_events = google_calendar.list_events(
                    email,
                    calendar_id,
                    time_min=window_start,
                    time_max=window_end,
                )
                seen_ids: set[str] = set()
                for provider_event in provider_events:
                    provider_id = str(provider_event.get("id") or "").strip()
                    if not provider_id:
                        continue
                    seen_ids.add(provider_id)
                    if provider_event.get("status") == "cancelled":
                        events_service.ingest_google_calendar_event(calendar_id, provider_event)
                        events_service.mark_google_calendar_event_cancelled(calendar_id, provider_id)
                    else:
                        event_id = events_service.ingest_google_calendar_event(calendar_id, provider_event)
                        if event_id:
                            synced_count += 1

                if reconcile:
                    local_ids = events_service.list_google_calendar_event_ids(
                        calendar_id,
                        start_date=reconciliation_start,
                        end_date=reconciliation_end,
                    )
                    for local_id in local_ids:
                        if local_id in seen_ids:
                            continue
                        current = google_calendar.get_event(email, calendar_id, local_id)
                        if not current or current.get("status") == "cancelled":
                            events_service.mark_google_calendar_event_cancelled(calendar_id, local_id)
                        else:
                            events_service.ingest_google_calendar_event(calendar_id, current)
                        reconciled_count += 1
            account_reconciled = reconcile
            google_calendar.record_sync_result(email, reconciled=account_reconciled)
        except Exception as exc:
            account_error = _safe_error(exc)
            errors.append(f"{email}: {account_error}")
            google_calendar.record_sync_result(email, error=account_error)
            logger.exception("[google_calendar.sync] account sync failed user=%s", email)

    completed_at = now.isoformat()
    with _STATUS_LOCK:
        _LAST_SYNC_AT = completed_at
        _SYNCED_EVENT_COUNT = synced_count
        if reconcile and not errors:
            _LAST_RECONCILIATION_AT = completed_at
            _RECONCILED_EVENT_COUNT = reconciled_count
            iso_week = local_now.isocalendar()
            _LAST_RECONCILIATION_WEEK = (iso_week.year, iso_week.week)
        _LAST_ERROR = "; ".join(errors)[:1000] if errors else None

    logger.info(
        "[google_calendar.sync] completed accounts=%d events=%d reconciled=%d weekly=%s errors=%d",
        len(connections),
        synced_count,
        reconciled_count,
        reconcile,
        len(errors),
    )
    return {"events_synced": synced_count, "events_reconciled": reconciled_count}


def get_worker_status() -> dict[str, Any]:
    with _STATUS_LOCK:
        return {
            "job_type": JOB_TYPE,
            "worker_alive": bool(_WORKER_THREAD and _WORKER_THREAD.is_alive()),
            "poll_seconds": POLL_SECONDS,
            "sync_interval_seconds": SYNC_INTERVAL_SECONDS,
            "timezone_name": TIMEZONE_NAME,
            "reconciliation_time_local": f"{RECONCILIATION_HOUR:02d}:00 Sunday",
            "last_sync_at": _LAST_SYNC_AT,
            "last_reconciliation_at": _LAST_RECONCILIATION_AT,
            "last_events_synced": _SYNCED_EVENT_COUNT,
            "last_events_reconciled": _RECONCILED_EVENT_COUNT,
            "last_error": _LAST_ERROR,
        }


def _worker_loop() -> None:
    next_sync_at = 0.0
    while not _STOP_EVENT.is_set():
        now_monotonic = time.monotonic()
        if now_monotonic >= next_sync_at:
            try:
                sync_once()
            except Exception:
                logger.exception("[google_calendar.sync] worker tick failed")
            next_sync_at = time.monotonic() + SYNC_INTERVAL_SECONDS
        _STOP_EVENT.wait(POLL_SECONDS)


def _is_reconciliation_due(local_now: datetime) -> bool:
    week_key = (local_now.isocalendar().year, local_now.isocalendar().week)
    return (
        local_now.weekday() == 6
        and local_now.hour >= RECONCILIATION_HOUR
        and week_key != _LAST_RECONCILIATION_WEEK
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _safe_error(error: Exception) -> str:
    if isinstance(error, google_calendar.GoogleCalendarError):
        return str(error)
    return f"{type(error).__name__}: Google Calendar sync failed"
