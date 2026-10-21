from __future__ import annotations

import threading
import time
from typing import Any

import async_jobs
from observability.logger import get_runtime_logger
from scheduled_jobs import CONTACT_ENRICHMENT

logger = get_runtime_logger(__name__)

JOB_TYPE = CONTACT_ENRICHMENT.job_type
POLL_SECONDS = CONTACT_ENRICHMENT.poll_seconds
RETRY_SECONDS = CONTACT_ENRICHMENT.retry_seconds or 300

_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def enqueue_contact_enrichment(
    *,
    user_email: str,
    source_key: str,
    source_text: str,
    contact_ids: list[str],
    source_event_id: str | None = None,
) -> dict[str, Any] | None:
    cleaned_email = str(user_email or "").strip()
    cleaned_key = str(source_key or "").strip()
    cleaned_text = str(source_text or "").strip()
    cleaned_contact_ids = list(
        dict.fromkeys(str(value).strip() for value in contact_ids if str(value).strip())
    )
    if not cleaned_email or not cleaned_key or not cleaned_text or not cleaned_contact_ids:
        return None

    job = async_jobs.enqueue_job(
        job_type=JOB_TYPE,
        user_email=cleaned_email,
        dedupe_key=cleaned_key,
        payload={
            "source_text": cleaned_text,
            "contact_ids": cleaned_contact_ids,
            "source_event_id": str(source_event_id or "").strip() or None,
        },
        status_message="Queued",
        delay_seconds=0,
    )
    logger.info(
        "[contact_enrichment_jobs] queued job_id=%s revision=%s contacts=%d",
        job.get("job_id"),
        job.get("revision"),
        len(cleaned_contact_ids),
    )
    return job


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        name="contact-enrichment-jobs",
        daemon=True,
    )
    _WORKER_THREAD.start()
    logger.info("[contact_enrichment_jobs] worker started")


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


@async_jobs.background_worker("contact_enrichment_jobs")
def process_due_once() -> bool:
    job = async_jobs.claim_due_job(job_type=JOB_TYPE)
    if not job:
        return False

    job_id = str(job["job_id"])
    revision = int(job["revision"])
    payload = dict(job.get("payload") or {})
    try:
        result = analyze_contact_enrichment(
            user_email=str(job["user_email"]),
            source_text=str(payload.get("source_text") or ""),
            contact_ids=payload.get("contact_ids") or [],
            source_event_id=payload.get("source_event_id"),
        )
    except Exception as exc:
        logger.exception(
            "[contact_enrichment_jobs] processing failed job_id=%s revision=%s; retrying in %ss",
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
    try:
        async_jobs.clear_succeeded_payload(job_id, revision=revision)
    except Exception:
        logger.warning(
            "[contact_enrichment_jobs] Could not clear completed job payload job_id=%s",
            job_id,
            exc_info=True,
        )
    logger.info(
        "[contact_enrichment_jobs] processing complete job_id=%s revision=%s relationships=%d professions=%d",
        job_id,
        revision,
        result["relationship_proposals"],
        result["profession_proposals"],
    )
    return True


def analyze_contact_enrichment(
    *,
    user_email: str,
    source_text: str,
    contact_ids: list[str],
    source_event_id: str | None = None,
) -> dict[str, int]:
    text = str(source_text or "").strip()
    target_ids = {str(value).strip() for value in contact_ids if str(value).strip()}
    if not text or not target_ids:
        return {"relationship_proposals": 0, "profession_proposals": 0}

    import knowledge_jobs

    # One extractor owns automatic/proposed decisions for both facts and graph changes.
    # Retain the original event or user command as evidence, never generated suggestions.
    if source_event_id:
        knowledge_jobs.enqueue_event(user_email, source_event_id)
    else:
        knowledge_jobs.enqueue_contact_context(user_email, text, sorted(target_ids))
    return {
        "relationship_proposals": 0,
        "profession_proposals": 0,
    }


def _worker_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            processed = process_due_once()
        except Exception:
            logger.exception("[contact_enrichment_jobs] worker tick failed")
            processed = False
        if processed:
            time.sleep(0)
        else:
            _STOP_EVENT.wait(POLL_SECONDS)
