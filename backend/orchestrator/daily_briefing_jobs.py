from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone
from typing import Any

import async_jobs
from db import get_conn
from notifications import DAILY_BRIEFING_NOTIFICATION_TYPE, send_notification_to_user
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

JOB_TYPE = "daily_briefing"
POLL_SECONDS = 60
RETRY_SECONDS = 300
DAILY_BRIEFING_UTC_HOUR = 5
DAILY_BRIEFING_UTC_MINUTE = 0

_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()
_LAST_SCHEDULER_LOG_MINUTE: str | None = None


def enqueue_daily_briefing_job(
    *,
    user_email: str,
    briefing_date: date,
    timezone_name: str,
    replace_existing: bool = False,
) -> dict[str, Any]:
    dedupe_key = build_daily_briefing_dedupe_key(
        briefing_date=briefing_date,
        timezone_name=timezone_name,
    )
    existing = async_jobs.get_job(
        job_type=JOB_TYPE,
        user_email=user_email,
        dedupe_key=dedupe_key,
    )
    if existing and not replace_existing:
        logger.info(
            "[briefing.job] daily_job_already_exists user=%s date=%s timezone=%s status=%s job_id=%s",
            user_email,
            briefing_date.isoformat(),
            timezone_name,
            existing.get("status"),
            existing.get("job_id"),
        )
        return {
            "job_id": existing.get("job_id"),
            "status": existing.get("status"),
            "revision": existing.get("revision"),
            "created": False,
            "should_schedule": False,
        }
    queued = async_jobs.enqueue_job(
        job_type=JOB_TYPE,
        user_email=user_email,
        dedupe_key=dedupe_key,
        payload={
            "date": briefing_date.isoformat(),
            "timezone": timezone_name,
            "user_email": user_email,
        },
        status_message="Queued for generation",
        replace_existing=replace_existing,
    )
    logger.info(
        "[briefing.job] queued job_id=%s user=%s date=%s timezone=%s created=%s",
        queued.get("job_id"),
        user_email,
        briefing_date.isoformat(),
        timezone_name,
        queued.get("created"),
    )
    return queued


def run_daily_briefing_job(
    *,
    job_id: str,
    date_value: str,
    timezone_name: str,
    user_email: str,
) -> None:
    if not async_jobs.mark_running(job_id):
        logger.info("[briefing.job] Skipping run; job already active/completed: %s", job_id)
        return
    _execute_daily_briefing_job(
        job_id=job_id,
        date_value=date_value,
        timezone_name=timezone_name,
        user_email=user_email,
        revision=None,
    )


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        name="daily-briefing-jobs",
        daemon=True,
    )
    _WORKER_THREAD.start()
    logger.info("[briefing.job] worker started")


def stop_worker(timeout: float = 5.0) -> None:
    _STOP_EVENT.set()
    thread = _WORKER_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=timeout)


def process_due_once() -> bool:
    job = async_jobs.claim_due_job(job_type=JOB_TYPE)
    if not job:
        return False
    payload = dict(job.get("payload") or {})
    _execute_daily_briefing_job(
        job_id=str(job["job_id"]),
        date_value=str(payload.get("date") or ""),
        timezone_name=str(payload.get("timezone") or "UTC"),
        user_email=str(payload.get("user_email") or job["user_email"]),
        revision=int(job["revision"]),
    )
    return True


def enqueue_due_daily_briefings(*, now_utc: datetime | None = None) -> int:
    global _LAST_SCHEDULER_LOG_MINUTE
    resolved_now = _as_utc(now_utc or datetime.now(timezone.utc))
    scheduler_minute = resolved_now.strftime("%Y-%m-%dT%H:%M")
    should_log_summary = scheduler_minute != _LAST_SCHEDULER_LOG_MINUTE
    if (resolved_now.hour, resolved_now.minute) < (
        DAILY_BRIEFING_UTC_HOUR,
        DAILY_BRIEFING_UTC_MINUTE,
    ):
        if should_log_summary:
            logger.info(
                "[briefing.job] scheduler_tick users=0 due=0 enqueued=0 now_utc=%s reason=before_05_00_utc",
                resolved_now.isoformat(),
            )
            _LAST_SCHEDULER_LOG_MINUTE = scheduler_minute
        return 0

    users = list_users_for_daily_briefing()
    enqueued = 0
    for user in users:
        user_email = str(user.get("user_email") or "").strip()
        if not user_email:
            continue
        timezone_name = str(user.get("timezone") or "").strip() or "UTC"
        job = enqueue_daily_briefing_job(
            user_email=user_email,
            briefing_date=resolved_now.date(),
            timezone_name=timezone_name,
        )
        if job.get("created"):
            enqueued += 1
    if should_log_summary:
        logger.info(
            "[briefing.job] scheduler_tick users=%s due=%s enqueued=%s now_utc=%s",
            len(users),
            len(users),
            enqueued,
            resolved_now.isoformat(),
        )
        _LAST_SCHEDULER_LOG_MINUTE = scheduler_minute
    return enqueued


def list_users_for_daily_briefing() -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH candidates AS (
                SELECT DISTINCT user_email
                FROM notification_subscriptions
                WHERE notification_type = 'daily-briefing'
                UNION
                SELECT DISTINCT user_email
                FROM user_devices
                WHERE expo_push_token IS NOT NULL
                UNION
                SELECT DISTINCT user_email
                FROM user_location_history
                WHERE captured_at >= NOW() - INTERVAL '14 days'
            ),
            latest_timezones AS (
                SELECT DISTINCT ON (user_email)
                    user_email,
                    timezone
                FROM user_location_history
                WHERE timezone IS NOT NULL AND timezone <> ''
                ORDER BY user_email, captured_at DESC, id DESC
            )
            SELECT
                candidates.user_email,
                COALESCE(latest_timezones.timezone, 'UTC') AS timezone
            FROM candidates
            LEFT JOIN latest_timezones ON latest_timezones.user_email = candidates.user_email
            WHERE candidates.user_email IS NOT NULL AND candidates.user_email <> ''
            ORDER BY candidates.user_email
            """
        )
        return [dict(row) for row in cur.fetchall()]


def build_daily_briefing_dedupe_key(*, briefing_date: date, timezone_name: str) -> str:
    return f"{briefing_date.isoformat()}::{timezone_name}"


def _worker_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            enqueue_due_daily_briefings()
            processed = process_due_once()
        except Exception:
            logger.exception("[briefing.job] worker tick failed")
            processed = False
        if processed:
            time.sleep(0)
        else:
            _STOP_EVENT.wait(POLL_SECONDS)


def _execute_daily_briefing_job(
    *,
    job_id: str,
    date_value: str,
    timezone_name: str,
    user_email: str,
    revision: int | None,
) -> None:
    from agents.daily_briefing.executor import handle_daily_briefing_request

    logger.info(
        "[briefing.job] Starting job=%s user=%s date=%s tz=%s",
        job_id,
        user_email,
        date_value,
        timezone_name,
    )
    try:
        result = handle_daily_briefing_request(
            {
                "date": date_value,
                "timezone": timezone_name,
                "user_email": user_email,
            }
        )
        if result.get("status") == "error":
            error_message = str(result.get("message") or "Daily briefing generation failed")
            async_jobs.mark_failed(
                job_id,
                error=error_message,
                status_message="Daily briefing failed",
                revision=revision,
                retry_delay_seconds=RETRY_SECONDS,
            )
            logger.warning("[briefing.job] Job failed job=%s error=%s", job_id, error_message)
            return

        async_jobs.mark_succeeded(
            job_id,
            result={"briefing_id": result.get("briefing_id")},
            status_message="Daily briefing ready",
            revision=revision,
        )
        _notify_daily_briefing_ready(user_email=user_email, result=result)
        logger.info(
            "[briefing.job] Job completed job=%s briefing_id=%s",
            job_id,
            result.get("briefing_id"),
        )
    except Exception as exc:
        error_message = str(exc)
        async_jobs.mark_failed(
            job_id,
            error=error_message,
            status_message="Daily briefing failed",
            revision=revision,
            retry_delay_seconds=RETRY_SECONDS,
        )
        logger.exception("[briefing.job] Unexpected failure job=%s", job_id)


def _notify_daily_briefing_ready(*, user_email: str, result: dict[str, Any]) -> None:
    summary = str(result.get("summary") or "").strip()
    message = summary or "Your daily briefing is ready."
    if len(message) > 220:
        message = f"{message[:217].rstrip()}..."
    try:
        notification_result = send_notification_to_user(
            notification_type=DAILY_BRIEFING_NOTIFICATION_TYPE,
            user_email=user_email,
            title="Daily briefing ready",
            message=message,
        )
        logger.info(
            "[briefing.job] Notification result user=%s sent=%s errors=%s",
            user_email,
            notification_result.get("sent"),
            notification_result.get("errors"),
        )
    except Exception:
        logger.warning("[briefing.job] Failed to send push notification user=%s", user_email)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
