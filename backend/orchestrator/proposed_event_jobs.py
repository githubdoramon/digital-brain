from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone
from typing import Any

import async_jobs
import proposed_events
from notifications import PROPOSED_EVENTS_READY_NOTIFICATION_TYPE, send_notification_to_user
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

JOB_TYPE = "proposed_events_daily"
POLL_SECONDS = 60
RETRY_SECONDS = 300

_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()
_LAST_SCHEDULER_LOG_MINUTE: str | None = None


def enqueue_daily_scan(
    *,
    user_email: str,
    target_date: date,
    timezone_name: str,
    replace_existing: bool = False,
) -> dict[str, Any]:
    dedupe_key = f"proposed-events:{target_date.isoformat()}:{timezone_name}"
    existing = async_jobs.get_job(
        job_type=JOB_TYPE,
        user_email=user_email,
        dedupe_key=dedupe_key,
    )
    if existing and not replace_existing:
        logger.info(
            "[proposed_event_jobs] daily_scan_already_exists user=%s date=%s timezone=%s status=%s job_id=%s",
            user_email,
            target_date.isoformat(),
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
    job = async_jobs.enqueue_job(
        job_type=JOB_TYPE,
        user_email=user_email,
        dedupe_key=dedupe_key,
        payload={
            "target_date": target_date.isoformat(),
            "timezone": timezone_name,
        },
        status_message="Queued",
        replace_existing=replace_existing,
    )
    logger.info(
        "[proposed_event_jobs] queued job_id=%s user=%s date=%s timezone=%s created=%s",
        job.get("job_id"),
        user_email,
        target_date.isoformat(),
        timezone_name,
        job.get("created"),
    )
    return job


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        name="proposed-event-jobs",
        daemon=True,
    )
    _WORKER_THREAD.start()
    logger.info("[proposed_event_jobs] worker started")


def stop_worker(timeout: float = 5.0) -> None:
    _STOP_EVENT.set()
    thread = _WORKER_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=timeout)


def process_due_once() -> bool:
    job = async_jobs.claim_due_job(job_type=JOB_TYPE)
    if not job:
        return False

    job_id = str(job["job_id"])
    revision = int(job["revision"])
    payload = dict(job.get("payload") or {})
    try:
        target_date = date.fromisoformat(str(payload.get("target_date") or ""))
        timezone_name = str(payload.get("timezone") or "UTC")
        logger.info(
            "[proposed_event_jobs] processing_start job_id=%s revision=%s user=%s date=%s timezone=%s",
            job_id,
            revision,
            job["user_email"],
            target_date.isoformat(),
            timezone_name,
        )
        result = proposed_events.analyze_user_day(
            user_email=str(job["user_email"]),
            target_date=target_date,
            timezone_name=timezone_name,
        )
        _send_ready_notification(str(job["user_email"]), result)
    except Exception as exc:
        logger.exception(
            "[proposed_event_jobs] processing failed job_id=%s revision=%s; retrying in %ss",
            job_id,
            revision,
            RETRY_SECONDS,
        )
        async_jobs.mark_failed(
            job_id,
            error=str(exc),
            status_message="Retry scheduled",
            revision=revision,
            retry_delay_seconds=RETRY_SECONDS,
        )
        return True

    async_jobs.mark_succeeded(
        job_id,
        result=result,
        status_message="Completed",
        revision=revision,
    )
    logger.info(
        "[proposed_event_jobs] processing complete job_id=%s revision=%s created=%s",
        job_id,
        revision,
        result.get("created"),
    )
    return True


def enqueue_due_daily_scans(*, now_utc: datetime | None = None) -> int:
    global _LAST_SCHEDULER_LOG_MINUTE
    resolved_now = now_utc or datetime.now(timezone.utc)
    enqueued = 0
    users = proposed_events.list_users_for_daily_scan()
    due_count = 0
    not_due_count = 0
    scheduler_minute = resolved_now.strftime("%Y-%m-%dT%H:%M")
    should_log_summary = scheduler_minute != _LAST_SCHEDULER_LOG_MINUTE
    for user in users:
        user_email = str(user.get("user_email") or "").strip()
        if not user_email:
            continue
        schedule = proposed_events.should_run_daily_scan(user_email, now_utc=resolved_now)
        if not schedule:
            not_due_count += 1
            continue
        due_count += 1
        job = enqueue_daily_scan(
            user_email=user_email,
            target_date=schedule["target_date"],
            timezone_name=schedule["timezone"],
        )
        if job.get("created"):
            enqueued += 1
    if should_log_summary:
        logger.info(
            "[proposed_event_jobs] scheduler_tick users=%s due=%s not_due=%s enqueued=%s now_utc=%s",
            len(users),
            due_count,
            not_due_count,
            enqueued,
            resolved_now.isoformat(),
        )
        _LAST_SCHEDULER_LOG_MINUTE = scheduler_minute
    return enqueued


def get_scheduler_status(user_email: str | None = None) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    if user_email:
        users = [{"user_email": user_email}]
    else:
        users = proposed_events.list_users_for_daily_scan()
    eligible: list[dict[str, Any]] = []
    for user in users:
        user_email = str(user.get("user_email") or "").strip()
        if not user_email:
            continue
        eligibility = proposed_events.describe_daily_scan_eligibility(
            user_email,
            now_utc=now_utc,
        )
        job = async_jobs.get_job(
            job_type=JOB_TYPE,
            user_email=user_email,
            dedupe_key=f"proposed-events:{eligibility['target_date']}:{eligibility['timezone']}",
        )
        eligible.append(
            {
                **eligibility,
                "job": {
                    "job_id": job.get("job_id"),
                    "status": job.get("status"),
                    "next_run_at": job.get("next_run_at"),
                    "status_message": job.get("status_message"),
                    "result": job.get("result"),
                    "error": job.get("error"),
                }
                if job
                else None,
            }
        )
    return {
        "worker_alive": bool(_WORKER_THREAD and _WORKER_THREAD.is_alive()),
        "job_type": JOB_TYPE,
        "now_utc": now_utc.isoformat(),
        "user_count": len(users),
        "users": eligible,
    }


def _worker_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            enqueue_due_daily_scans()
            processed = process_due_once()
            proposed_events.expire_pending()
        except Exception:
            logger.exception("[proposed_event_jobs] worker tick failed")
            processed = False
        if processed:
            time.sleep(0)
        else:
            _STOP_EVENT.wait(POLL_SECONDS)


def _send_ready_notification(user_email: str, result: dict[str, Any]) -> None:
    created = int(result.get("created") or 0)
    if created <= 0:
        logger.info(
            "[proposed_event_jobs] notification_skipped_no_new_proposals user=%s date=%s skipped=%s reasons=%s",
            user_email,
            result.get("date"),
            result.get("skipped"),
            result.get("skip_reasons"),
        )
        return
    noun = "event" if created == 1 else "events"
    notification_result = send_notification_to_user(
        notification_type=PROPOSED_EVENTS_READY_NOTIFICATION_TYPE,
        user_email=user_email,
        title="Review your day",
        message=f"{created} possible missed {noun} ready to review.",
        data={"kind": "proposed_events_ready"},
    )
    logger.info(
        "[proposed_event_jobs] notification_sent user=%s created=%s sent=%s errors=%s",
        user_email,
        created,
        notification_result.get("sent"),
        notification_result.get("errors"),
    )
