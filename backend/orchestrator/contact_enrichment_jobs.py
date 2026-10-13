from __future__ import annotations

import re
import threading
import time
from typing import Any

import async_jobs
import contacts as contacts_service
from agents.contacts.resolver import resolve_contacts_from_text
from contact_enrichment_proposals import (
    create_profession_proposal,
    create_relationship_proposal,
)
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
    cleaned_contact_ids = list(dict.fromkeys(str(value).strip() for value in contact_ids if str(value).strip()))
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

    result = resolve_contacts_from_text(
        text,
        user_email,
        include_enrichment=True,
    )
    relationship_count = 0
    for suggestion in result.get("suggested_relationships") or []:
        if not isinstance(suggestion, dict):
            continue
        from_id = str(suggestion.get("from_contact_id") or "").strip()
        to_id = str(suggestion.get("to_contact_id") or "").strip()
        if from_id not in target_ids or to_id not in target_ids:
            continue
        if create_relationship_proposal(
            user_email=user_email,
            contact_id=from_id,
            related_contact_id=to_id,
            relationship_type=str(suggestion.get("type") or ""),
            reciprocal_type=str(suggestion.get("other_type") or "") or None,
            source_event_id=source_event_id,
            confidence="medium",
            evidence=str(suggestion.get("relationship_hint") or "") or None,
        ):
            relationship_count += 1

    profession_count = 0
    try:
        self_contact = contacts_service.find_self_contact(user_email)
    except Exception:
        logger.warning("[contact_enrichment_jobs] self-contact lookup unavailable")
        self_contact = None
    self_contact_id = str((self_contact or {}).get("contact_id") or "").strip()
    for contact in result.get("resolved_contacts") or []:
        if not isinstance(contact, dict):
            continue
        contact_id = str(contact.get("contact_id") or "").strip()
        profession = str(contact.get("inferred_profession") or "").strip()
        if contact_id not in target_ids or contact_id == self_contact_id or not profession:
            continue
        current = contacts_service.get_contact(contact_id)
        if not current or re.search(r"(?im)^profession:\s*", str(current.get("comments") or "")):
            continue
        if create_profession_proposal(
            user_email=user_email,
            contact_id=contact_id,
            profession=profession,
            source_event_id=source_event_id,
        ):
            profession_count += 1

    return {
        "relationship_proposals": relationship_count,
        "profession_proposals": profession_count,
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
