from __future__ import annotations

import threading
import time
from typing import Any

import async_jobs
import events as events_service
from observability.logger import get_runtime_logger
from scheduled_jobs import EVENT_TAG_ENRICHMENT

logger = get_runtime_logger(__name__)

JOB_TYPE = EVENT_TAG_ENRICHMENT.job_type
RETRY_SECONDS = EVENT_TAG_ENRICHMENT.retry_seconds or 60
POLL_SECONDS = EVENT_TAG_ENRICHMENT.poll_seconds

_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def enqueue_event_tag_enrichment(
    event_id: str,
    *,
    source: str = "event_persistence",
    delay_seconds: int = 0,
) -> dict[str, Any] | None:
    cleaned_event_id = str(event_id or "").strip()
    if not cleaned_event_id:
        return None

    job = async_jobs.enqueue_job(
        job_type=JOB_TYPE,
        user_email="system",
        dedupe_key=cleaned_event_id,
        payload={
            "event_id": cleaned_event_id,
            "source": str(source or "event_persistence").strip() or "event_persistence",
        },
        status_message="Queued",
        delay_seconds=delay_seconds,
        replace_existing=True,
    )
    logger.info(
        "[event_tag_jobs] queued job_id=%s revision=%s event_id=%s source=%s",
        job.get("job_id"),
        job.get("revision"),
        cleaned_event_id,
        source,
    )
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "revision": job.get("revision"),
        "event_id": cleaned_event_id,
        "next_run_at": job.get("next_run_at"),
    }


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        name="event-tag-jobs",
        daemon=True,
    )
    _WORKER_THREAD.start()
    logger.info("[event_tag_jobs] worker started")


def stop_worker(timeout: float = 5.0) -> None:
    _STOP_EVENT.set()
    thread = _WORKER_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=timeout)


def get_worker_status() -> dict[str, Any]:
    return {
        "job_type": JOB_TYPE,
        "worker_alive": bool(_WORKER_THREAD and _WORKER_THREAD.is_alive()),
        "poll_seconds": POLL_SECONDS,
        "retry_seconds": RETRY_SECONDS,
    }


def process_due_once() -> bool:
    job = async_jobs.claim_due_job(job_type=JOB_TYPE)
    if not job:
        return False

    job_id = str(job["job_id"])
    revision = int(job["revision"])
    payload = dict(job.get("payload") or {})
    event_id = str(payload.get("event_id") or "").strip()
    try:
        if not event_id:
            raise ValueError("event_id is required")
        result = events_service.generate_and_persist_event_tags(event_id)
    except Exception as exc:
        logger.exception(
            "[event_tag_jobs] processing failed job_id=%s revision=%s event_id=%s; retrying in %ss",
            job_id,
            revision,
            event_id,
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
        status_message="Completed" if result.get("updated") else "No changes",
        revision=revision,
    )
    logger.info(
        "[event_tag_jobs] processing complete job_id=%s revision=%s event_id=%s updated=%s tags=%s",
        job_id,
        revision,
        event_id,
        result.get("updated"),
        result.get("tags"),
    )
    return True


def _worker_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            processed = process_due_once()
        except Exception:
            logger.exception("[event_tag_jobs] worker tick failed")
            processed = False
        if processed:
            time.sleep(0)
        else:
            _STOP_EVENT.wait(POLL_SECONDS)
