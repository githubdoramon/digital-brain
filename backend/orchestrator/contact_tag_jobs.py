from __future__ import annotations

import threading
import time
from typing import Any

import async_jobs
import contacts as contacts_service
from observability.logger import get_runtime_logger
from scheduled_jobs import CONTACT_TAG_ENRICHMENT

logger = get_runtime_logger(__name__)

JOB_TYPE = CONTACT_TAG_ENRICHMENT.job_type
RETRY_SECONDS = CONTACT_TAG_ENRICHMENT.retry_seconds or 60
POLL_SECONDS = CONTACT_TAG_ENRICHMENT.poll_seconds

_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def enqueue_contact_tag_enrichment(
    contact_id: str,
    *,
    source: str = "contact_persistence",
    delay_seconds: int = 0,
) -> dict[str, Any] | None:
    cleaned_contact_id = str(contact_id or "").strip()
    if not cleaned_contact_id:
        return None

    job = async_jobs.enqueue_job(
        job_type=JOB_TYPE,
        user_email="system",
        dedupe_key=cleaned_contact_id,
        payload={
            "contact_id": cleaned_contact_id,
            "source": str(source or "contact_persistence").strip() or "contact_persistence",
        },
        status_message="Queued",
        delay_seconds=delay_seconds,
        replace_existing=True,
    )
    logger.info(
        "[contact_tag_jobs] queued job_id=%s revision=%s contact_id=%s source=%s",
        job.get("job_id"),
        job.get("revision"),
        cleaned_contact_id,
        source,
    )
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "revision": job.get("revision"),
        "contact_id": cleaned_contact_id,
        "next_run_at": job.get("next_run_at"),
    }


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        name="contact-tag-jobs",
        daemon=True,
    )
    _WORKER_THREAD.start()
    logger.info("[contact_tag_jobs] worker started")


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
    contact_id = str(payload.get("contact_id") or "").strip()
    try:
        if not contact_id:
            raise ValueError("contact_id is required")
        result = contacts_service.generate_and_persist_contact_tags(contact_id)
    except Exception as exc:
        logger.exception(
            "[contact_tag_jobs] processing failed job_id=%s revision=%s contact_id=%s; retrying in %ss",
            job_id,
            revision,
            contact_id,
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
        "[contact_tag_jobs] processing complete job_id=%s revision=%s contact_id=%s updated=%s tags=%s",
        job_id,
        revision,
        contact_id,
        result.get("updated"),
        result.get("tags"),
    )
    return True


def _worker_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            processed = process_due_once()
        except Exception:
            logger.exception("[contact_tag_jobs] worker tick failed")
            processed = False
        if processed:
            time.sleep(0)
        else:
            _STOP_EVENT.wait(POLL_SECONDS)
