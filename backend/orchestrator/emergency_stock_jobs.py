from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import async_jobs
from agents.emergency_stock.executor import handle_emergency_stock_request
from observability.logger import get_runtime_logger
from scheduled_jobs import EMERGENCY_STOCK

logger = get_runtime_logger(__name__)

JOB_TYPE = EMERGENCY_STOCK.job_type
POLL_SECONDS = EMERGENCY_STOCK.poll_seconds
RETRY_SECONDS = EMERGENCY_STOCK.retry_seconds or 300
RUN_HOUR = EMERGENCY_STOCK.time_local.hour if EMERGENCY_STOCK.time_local else 5
RUN_MINUTE = EMERGENCY_STOCK.time_local.minute if EMERGENCY_STOCK.time_local else 0
TIMEZONE_NAME = EMERGENCY_STOCK.timezone_name or "Europe/Lisbon"
SYSTEM_USER_EMAIL = "system"

_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        name="emergency-stock-jobs",
        daemon=True,
    )
    _WORKER_THREAD.start()
    logger.info("[emergency_stock.job] worker started timezone=%s", TIMEZONE_NAME)


def stop_worker(timeout: float = 5.0) -> None:
    _STOP_EVENT.set()
    thread = _WORKER_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=timeout)


def enqueue_due_job(*, now_utc: datetime | None = None) -> bool:
    resolved_now = _as_utc(now_utc or datetime.now(timezone.utc))
    local_now = resolved_now.astimezone(ZoneInfo(TIMEZONE_NAME))
    if (local_now.hour, local_now.minute) < (RUN_HOUR, RUN_MINUTE):
        return False

    run_date = local_now.date().isoformat()
    dedupe_key = f"{run_date}::{TIMEZONE_NAME}"
    existing = async_jobs.get_job(
        job_type=JOB_TYPE,
        user_email=SYSTEM_USER_EMAIL,
        dedupe_key=dedupe_key,
    )
    if existing:
        return False

    queued = async_jobs.enqueue_job(
        job_type=JOB_TYPE,
        user_email=SYSTEM_USER_EMAIL,
        dedupe_key=dedupe_key,
        payload={"date": run_date, "timezone": TIMEZONE_NAME},
        status_message="Queued for emergency stock check",
    )
    if queued.get("created"):
        logger.info(
            "[emergency_stock.job] queued job_id=%s date=%s timezone=%s",
            queued.get("job_id"),
            run_date,
            TIMEZONE_NAME,
        )
        return True
    return False


def process_due_once() -> bool:
    job = async_jobs.claim_due_job(job_type=JOB_TYPE)
    if not job:
        return False

    job_id = str(job["job_id"])
    revision = int(job["revision"])
    try:
        result = handle_emergency_stock_request()
        if result.get("status") != "success":
            error_message = str(result.get("message") or "Emergency stock check failed")
            async_jobs.mark_failed(
                job_id,
                error=error_message,
                status_message="Emergency stock check failed",
                revision=revision,
                retry_delay_seconds=RETRY_SECONDS,
            )
            logger.warning(
                "[emergency_stock.job] failed job_id=%s error=%s",
                job_id,
                error_message,
            )
            return True

        summary = {
            key: result.get(key)
            for key in ("items_checked", "actions_found", "sheet_updates", "updated_cells")
        }
        async_jobs.mark_succeeded(
            job_id,
            result=summary,
            status_message="Emergency stock check completed",
            revision=revision,
        )
        logger.info("[emergency_stock.job] completed job_id=%s result=%s", job_id, summary)
    except Exception as exc:
        async_jobs.mark_failed(
            job_id,
            error=str(exc),
            status_message="Emergency stock check failed",
            revision=revision,
            retry_delay_seconds=RETRY_SECONDS,
        )
        logger.exception("[emergency_stock.job] unexpected failure job_id=%s", job_id)
    return True


def get_worker_status() -> dict[str, Any]:
    return {
        "job_type": JOB_TYPE,
        "worker_alive": bool(_WORKER_THREAD and _WORKER_THREAD.is_alive()),
        "poll_seconds": POLL_SECONDS,
        "retry_seconds": RETRY_SECONDS,
        "time_local": f"{RUN_HOUR:02d}:{RUN_MINUTE:02d}",
        "timezone_name": TIMEZONE_NAME,
    }


def _worker_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            enqueue_due_job()
            processed = process_due_once()
        except Exception:
            logger.exception("[emergency_stock.job] worker tick failed")
            processed = False
        if processed:
            time.sleep(0)
        else:
            _STOP_EVENT.wait(POLL_SECONDS)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
