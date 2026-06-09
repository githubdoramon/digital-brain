from __future__ import annotations

import threading
import time
from typing import Any, Callable

import async_jobs
import contacts as contacts_service
import events as events_service
import todos as todos_service
from observability.logger import get_runtime_logger
from schemas import MeetingTranscriptPayload, TodoIn
from search_normalization import normalize_search_text

logger = get_runtime_logger(__name__)

JOB_TYPE = "meeting_transcript"
DEBOUNCE_SECONDS = 30
RETRY_SECONDS = 60
POLL_SECONDS = 2

_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def enqueue_transcript(
    payload: MeetingTranscriptPayload,
    *,
    current_user: dict[str, Any],
    debounce_seconds: int = DEBOUNCE_SECONDS,
) -> dict[str, Any]:
    user_email = contacts_service.normalize_email(current_user.get("email") or "")
    if not user_email:
        raise ValueError("current_user with email is required")

    meeting_key = build_meeting_key(payload)
    job = async_jobs.enqueue_job(
        job_type=JOB_TYPE,
        user_email=user_email,
        dedupe_key=meeting_key,
        payload={
            "transcript": payload.model_dump(by_alias=True, mode="json"),
            "current_user": current_user,
        },
        status_message="Queued",
        delay_seconds=debounce_seconds,
        replace_existing=True,
    )
    logger.info(
        "[meeting_transcript_jobs] queued job_id=%s revision=%s user=%s meeting_key=%s "
        "upload_id=%s session_id=%s transcript_hash=%s",
        job.get("job_id"),
        job.get("revision"),
        user_email,
        meeting_key,
        payload.upload_id,
        payload.session_id,
        payload.transcript_hash,
    )
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "revision": job.get("revision"),
        "meeting_key": meeting_key,
        "next_run_at": job.get("next_run_at"),
    }


def build_meeting_key(payload: MeetingTranscriptPayload) -> str:
    provider = normalize_search_text(payload.meeting.provider or "")
    original_id = normalize_search_text(payload.meeting.original_id or "")
    if provider and original_id:
        return f"external:{provider}:{original_id}"

    session_id = normalize_search_text(payload.session_id or "")
    if session_id:
        return f"session:{session_id}"

    title = normalize_search_text(payload.meeting.title or "untitled meeting")
    return f"meeting:{payload.meeting.started_at.isoformat()}:{title}"


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        name="meeting-transcript-jobs",
        daemon=True,
    )
    _WORKER_THREAD.start()
    logger.info("[meeting_transcript_jobs] worker started")


def stop_worker(timeout: float = 5.0) -> None:
    _STOP_EVENT.set()
    thread = _WORKER_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=timeout)


def process_due_once(
    *,
    todo_writer: Callable[[TodoIn], None] | None = None,
) -> bool:
    job = async_jobs.claim_due_job(job_type=JOB_TYPE)
    if not job:
        return False

    job_id = str(job["job_id"])
    revision = int(job["revision"])
    payload = dict(job.get("payload") or {})
    try:
        transcript = MeetingTranscriptPayload.model_validate(payload.get("transcript") or {})
        skipped_result = _unchanged_transcript_result(transcript)
        if skipped_result is not None:
            async_jobs.mark_succeeded(
                job_id,
                result=skipped_result,
                status_message="Skipped unchanged transcript",
                revision=revision,
            )
            logger.info(
                "[meeting_transcript_jobs] skipped unchanged transcript job_id=%s revision=%s "
                "event_id=%s transcript_hash=%s",
                job_id,
                revision,
                skipped_result.get("event_id"),
                transcript.transcript_hash,
            )
            return True

        result = events_service.ingest_meeting_transcript(
            transcript,
            current_user=dict(payload.get("current_user") or {}),
            todo_writer=todo_writer or todos_service.ingest_todo,
        )
    except Exception as exc:
        logger.exception(
            "[meeting_transcript_jobs] processing failed job_id=%s revision=%s; retrying in %ss",
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
        "[meeting_transcript_jobs] processing complete job_id=%s revision=%s event_id=%s",
        job_id,
        revision,
        result.get("event_id"),
    )
    return True


def _worker_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            processed = process_due_once()
        except Exception:
            logger.exception("[meeting_transcript_jobs] worker tick failed")
            processed = False
        if processed:
            time.sleep(0)
        else:
            _STOP_EVENT.wait(POLL_SECONDS)


def _unchanged_transcript_result(payload: MeetingTranscriptPayload) -> dict[str, Any] | None:
    incoming_hash = str(payload.transcript_hash or "").strip()
    if not incoming_hash:
        return None

    event = _find_existing_transcript_event(payload)
    if not event:
        return None

    raw = event.get("raw") if isinstance(event, dict) else None
    if not isinstance(raw, dict):
        return None

    stored_hash = str(raw.get("transcript_hash") or "").strip()
    if not stored_hash or stored_hash != incoming_hash:
        return None

    return {
        "event_id": event.get("id"),
        "skipped": True,
        "reason": "unchanged_transcript_hash",
        "transcript_hash": incoming_hash,
    }


def _find_existing_transcript_event(payload: MeetingTranscriptPayload) -> dict[str, Any] | None:
    title = (payload.meeting.title or "").strip() or "Untitled meeting"
    external_identifier = events_service._get_transcript_external_identifier(payload)
    event_id = events_service._resolve_meeting_transcript_event_id(
        title=title,
        start_date=payload.meeting.started_at,
        external_identifier=external_identifier,
        session_id=payload.session_id,
    )
    return events_service._get_event_by_id(event_id)
