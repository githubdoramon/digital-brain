from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import async_jobs
import llm_admission
from db import get_conn
from job_leases import lease_heartbeat
from notifications import DAILY_BRIEFING_NOTIFICATION_TYPE, send_notification_to_user
from observability.logger import get_runtime_logger
from scheduled_jobs import DAILY_BRIEFING

logger = get_runtime_logger(__name__)

JOB_TYPE = DAILY_BRIEFING.job_type
POLL_SECONDS = DAILY_BRIEFING.poll_seconds
RETRY_SECONDS = DAILY_BRIEFING.retry_seconds or 300
DAILY_BRIEFING_UTC_HOUR = DAILY_BRIEFING.time_utc.hour if DAILY_BRIEFING.time_utc else 0
DAILY_BRIEFING_UTC_MINUTE = DAILY_BRIEFING.time_utc.minute if DAILY_BRIEFING.time_utc else 0

_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def enqueue_daily_briefing_job(
    *,
    user_email: str,
    briefing_date: date,
    timezone_name: str,
    replace_existing: bool = False,
    log_existing: bool = True,
) -> dict[str, Any]:
    async_jobs.recover_stale_jobs(job_type=JOB_TYPE, user_email=user_email)
    cancel_superseded_briefings(user_email=user_email)
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
        if log_existing:
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
    if queued["job_id"] in cancel_superseded_briefings(user_email=user_email):
        queued = {**queued, "status": "cancelled", "should_schedule": False}
    return queued


def cancel_superseded_briefings(*, user_email=None, now_utc=None):
    now = _as_utc(now_utc or datetime.now(timezone.utc))
    cancelled = set()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT job_id,revision,user_email,payload FROM async_jobs
            WHERE job_type=%s AND (%s::text IS NULL OR user_email=%s)
                AND status IN ('pending','failed','running')
            ORDER BY user_email,job_id FOR UPDATE""",
            (JOB_TYPE, user_email, user_email),
        )
        dated = []
        for row in cur.fetchall():
            payload = row["payload"] or {}
            try:
                day = date.fromisoformat(str(payload.get("date") or ""))
                today = now.astimezone(ZoneInfo(str(payload.get("timezone") or "UTC"))).date()
            except (ValueError, ZoneInfoNotFoundError):
                continue
            dated.append((row, day, today))
        latest = {}
        for row, day, _ in dated:
            latest[row["user_email"]] = max(latest.get(row["user_email"], day), day)
        for row, day, today in dated:
            if day >= today or day >= latest[row["user_email"]]:
                continue
            cur.execute(
                """UPDATE async_jobs SET status='cancelled',revision=revision+1,
                status_message='Replaced by a newer daily briefing',error=NULL,
                finished_at=NOW(),updated_at=NOW() WHERE job_id=%s AND revision=%s""",
                (row["job_id"], row["revision"]),
            )
            if cur.rowcount:
                cancelled.add(row["job_id"])
        conn.commit()
    if cancelled:
        logger.info("[briefing.job] superseded_days_cancelled count=%d", len(cancelled))
    return cancelled


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


@async_jobs.background_worker("daily_briefing_jobs", poll_seconds=POLL_SECONDS, check_claim=True)
def process_due_once() -> bool:
    cancel_superseded_briefings()
    async_jobs.recover_stale_jobs(job_type=JOB_TYPE)
    job = async_jobs.claim_due_job(job_type=JOB_TYPE, newest_first=True)
    if not job:
        return False
    payload = dict(job.get("payload") or {})
    with lease_heartbeat(lambda: async_jobs.heartbeat_job(job["job_id"], job["revision"])):
        _execute_daily_briefing_job(
            job_id=str(job["job_id"]),
            date_value=str(payload.get("date") or ""),
            timezone_name=str(payload.get("timezone") or "UTC"),
            user_email=str(payload.get("user_email") or job["user_email"]),
            revision=int(job["revision"]),
        )
    return True


def enqueue_due_daily_briefings(*, now_utc: datetime | None = None) -> int:
    resolved_now = _as_utc(now_utc or datetime.now(timezone.utc))
    if (resolved_now.hour, resolved_now.minute) < (
        DAILY_BRIEFING_UTC_HOUR,
        DAILY_BRIEFING_UTC_MINUTE,
    ):
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
            log_existing=False,
        )
        if job.get("created"):
            enqueued += 1
    if enqueued:
        logger.info(
            "[briefing.job] scheduler_enqueued users=%s enqueued=%s now_utc=%s",
            len(users),
            enqueued,
            resolved_now.isoformat(),
        )
    cancel_superseded_briefings(now_utc=resolved_now)
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


def get_worker_status() -> dict[str, Any]:
    return {
        "job_type": JOB_TYPE,
        "worker_alive": bool(_WORKER_THREAD and _WORKER_THREAD.is_alive()),
        "poll_seconds": POLL_SECONDS,
        "retry_seconds": RETRY_SECONDS,
    }


def _worker_loop() -> None:
    last_health = 0
    while not _STOP_EVENT.is_set():
        try:
            enqueue_due_daily_briefings()
            if time.monotonic() - last_health >= 60:
                log_queue_health()
                last_health = time.monotonic()
            processed = process_due_once()
        except Exception:
            logger.exception("[briefing.job] worker tick failed")
            processed = False
        if processed:
            time.sleep(0)
        else:
            _STOP_EVENT.wait(POLL_SECONDS)


def log_queue_health():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT status,COUNT(*) AS count,
            MAX(EXTRACT(EPOCH FROM NOW()-requested_at))::int AS oldest_age_s
            FROM async_jobs WHERE job_type=%s AND status NOT IN ('succeeded','cancelled')
            GROUP BY status ORDER BY status""",
            (JOB_TYPE,),
        )
        rows = [dict(row) for row in cur.fetchall()]
    logger.info("[briefing.job] queue_health states=%s", rows)


def _execute_daily_briefing_job(
    *,
    job_id: str,
    date_value: str,
    timezone_name: str,
    user_email: str,
    revision: int | None,
) -> None:
    from observability.mirador import traced_daily_briefing_run

    with traced_daily_briefing_run() as trace:
        _execute_daily_briefing_job_impl(
            job_id=job_id,
            date_value=date_value,
            timezone_name=timezone_name,
            user_email=user_email,
            revision=revision,
            trace=trace,
        )


def _execute_daily_briefing_job_impl(
    *,
    job_id: str,
    date_value: str,
    timezone_name: str,
    user_email: str,
    revision: int | None,
    trace: Any | None,
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
            if trace is not None:
                trace.set_outcome("error", error_type="BriefingGenerationError")
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

        if trace is not None:
            trace.set_result_counts(
                events=int(result.get("event_count") or 0),
                todos=int(result.get("todo_count") or 0),
                news_items=len(result.get("news_items") or []),
            )

        published = async_jobs.mark_succeeded(
            job_id,
            result={"briefing_id": result.get("briefing_id")},
            status_message="Daily briefing ready",
            revision=revision,
        )
        if not published:
            logger.info("[briefing.job] Superseded completion ignored job=%s", job_id)
            return
        _notify_daily_briefing_ready(user_email=user_email, result=result)
        logger.info(
            "[briefing.job] Job completed job=%s briefing_id=%s",
            job_id,
            result.get("briefing_id"),
        )
    except async_jobs.JobSuperseded:
        logger.info("[briefing.job] Superseded publication ignored job=%s", job_id)
    except llm_admission.BackgroundDeferred:
        raise
    except Exception as exc:
        if trace is not None:
            trace.set_outcome("error", error_type=type(exc).__name__)
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
            data={
                "kind": "daily_briefing_ready",
                "briefingId": result.get("briefing_id") or "",
                "date": result.get("date") or "",
                "timezone": result.get("timezone") or "",
            },
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
