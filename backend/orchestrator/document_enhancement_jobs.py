from __future__ import annotations

import threading
import time
from typing import Any

import async_jobs
import documents as documents_service
from observability.logger import get_runtime_logger
from scheduled_jobs import DOCUMENT_ENHANCEMENT

logger = get_runtime_logger(__name__)
JOB_TYPE = DOCUMENT_ENHANCEMENT.job_type
RETRY_SECONDS = DOCUMENT_ENHANCEMENT.retry_seconds or 60
POLL_SECONDS = DOCUMENT_ENHANCEMENT.poll_seconds
_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def enqueue_document_enhancement(
    document_id: str,
    *,
    user_email: str | None = None,
    source: str = "document_upload",
    delay_seconds: int = 0,
) -> dict[str, Any] | None:
    cleaned = str(document_id or "").strip()
    if not cleaned:
        return None
    job = async_jobs.enqueue_job(
        job_type=JOB_TYPE,
        user_email="system",
        dedupe_key=cleaned,
        payload={
            "document_id": cleaned,
            "user_email": str(user_email or "").strip() or None,
            "source": str(source or "document_upload").strip() or "document_upload",
        },
        status_message="Queued",
        delay_seconds=delay_seconds,
        replace_existing=True,
    )
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "revision": job.get("revision"),
        "document_id": cleaned,
        "next_run_at": job.get("next_run_at"),
    }


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(target=_worker_loop, name="document-enhancement-jobs", daemon=True)
    _WORKER_THREAD.start()
    logger.info("[document_enhancement_jobs] worker started")


def stop_worker(timeout: float = 5.0) -> None:
    _STOP_EVENT.set()
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        _WORKER_THREAD.join(timeout=timeout)


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
    document_id = str(payload.get("document_id") or "").strip()
    user_email = str(payload.get("user_email") or "").strip() or None
    try:
        if not document_id:
            raise ValueError("document_id is required")
        documents_service._set_enhancement_status(
            document_id,
            documents_service.DocumentEnhancementStatus.PROCESSING,
        )
        documents_service.enhance_document(document_id, user_email=user_email)
        documents_service._set_enhancement_status(
            document_id,
            documents_service.DocumentEnhancementStatus.COMPLETE,
        )
    except Exception as exc:
        logger.exception(
            "[document_enhancement_jobs] processing failed job_id=%s revision=%s document_id=%s; retrying in %ss",
            job_id,
            revision,
            document_id,
            RETRY_SECONDS,
        )
        try:
            documents_service._set_enhancement_status(
                document_id,
                documents_service.DocumentEnhancementStatus.FAILED,
                error=str(exc),
            )
        except Exception:
            logger.exception("[document_enhancement_jobs] could not persist failure state for %s", document_id)
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
        result={"document_id": document_id, "enhancement_status": "complete"},
        status_message="Completed",
        revision=revision,
    )
    return True


def _worker_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            processed = process_due_once()
        except Exception:
            logger.exception("[document_enhancement_jobs] worker tick failed")
            processed = False
        if processed:
            time.sleep(0)
        else:
            _STOP_EVENT.wait(POLL_SECONDS)
