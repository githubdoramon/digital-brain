from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import meeting_transcript_jobs
from auth import get_current_user
from routes.events import create_events_router
from schemas import MeetingTranscriptPayload


def _payload(**overrides) -> MeetingTranscriptPayload:
    data = {
        "upload_id": "upload-1",
        "session_id": "session-1",
        "transcript_hash": "hash-1",
        "meeting": {
            "original_id": "external-1",
            "provider": "hyprnote",
            "title": "Queue Review",
            "started_at": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            "ended_at": datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc),
        },
        "participants": [{"name": "Alex Example", "email": "alex@example.test"}],
        "speaker_identities": [{"id": "speaker-1", "label": "Alex Example"}],
        "transcript": {"segments": [{"speaker_id": "speaker-1", "text": "We should queue this."}]},
    }
    data.update(overrides)
    return MeetingTranscriptPayload.model_validate(data)


def test_enqueue_transcript_uses_generic_replacing_job(monkeypatch):
    calls = []

    def fake_enqueue_job(**kwargs):
        calls.append(kwargs)
        return {
            "job_id": "async_job:test",
            "status": "pending",
            "revision": 3,
            "next_run_at": "2026-01-01T12:00:30+00:00",
        }

    monkeypatch.setattr(meeting_transcript_jobs.async_jobs, "enqueue_job", fake_enqueue_job)

    result = meeting_transcript_jobs.enqueue_transcript(
        _payload(),
        current_user={"email": "User@Example.Test"},
    )

    assert result["job_id"] == "async_job:test"
    assert result["meeting_key"] == "external:hyprnote:external-1"
    assert calls[0]["job_type"] == meeting_transcript_jobs.JOB_TYPE
    assert calls[0]["user_email"] == "user@example.test"
    assert calls[0]["dedupe_key"] == "external:hyprnote:external-1"
    assert calls[0]["delay_seconds"] == meeting_transcript_jobs.DEBOUNCE_SECONDS
    assert calls[0]["replace_existing"] is True
    assert calls[0]["payload"]["transcript"]["upload_id"] == "upload-1"


def test_process_due_once_retries_failed_job(monkeypatch):
    marked = []

    monkeypatch.setattr(
        meeting_transcript_jobs.async_jobs,
        "claim_due_job",
        lambda job_type: {
            "job_id": "async_job:test",
            "revision": 2,
            "payload": {
                "transcript": _payload().model_dump(by_alias=True, mode="json"),
                "current_user": {"email": "user@example.test"},
            },
        },
    )
    monkeypatch.setattr(meeting_transcript_jobs, "_find_existing_transcript_event", lambda payload: None)

    def fail_ingest(*_args, **_kwargs):
        raise RuntimeError("temporary LLM outage")

    monkeypatch.setattr(meeting_transcript_jobs.events_service, "ingest_meeting_transcript", fail_ingest)
    monkeypatch.setattr(
        meeting_transcript_jobs.async_jobs,
        "mark_failed",
        lambda *args, **kwargs: marked.append((args, kwargs)),
    )

    assert meeting_transcript_jobs.process_due_once() is True
    assert marked[0][0] == ("async_job:test",)
    assert marked[0][1]["revision"] == 2
    assert marked[0][1]["retry_delay_seconds"] == meeting_transcript_jobs.RETRY_SECONDS
    assert "temporary LLM outage" in marked[0][1]["error"]


def test_process_due_once_marks_success(monkeypatch):
    marked = []

    monkeypatch.setattr(
        meeting_transcript_jobs.async_jobs,
        "claim_due_job",
        lambda job_type: {
            "job_id": "async_job:test",
            "revision": 4,
            "payload": {
                "transcript": _payload().model_dump(by_alias=True, mode="json"),
                "current_user": {"email": "user@example.test"},
            },
        },
    )
    monkeypatch.setattr(meeting_transcript_jobs, "_find_existing_transcript_event", lambda payload: None)
    monkeypatch.setattr(
        meeting_transcript_jobs.events_service,
        "ingest_meeting_transcript",
        lambda *_args, **_kwargs: {"event_id": "event:test"},
    )
    monkeypatch.setattr(
        meeting_transcript_jobs.async_jobs,
        "mark_succeeded",
        lambda *args, **kwargs: marked.append((args, kwargs)),
    )

    assert meeting_transcript_jobs.process_due_once() is True
    assert marked[0][0] == ("async_job:test",)
    assert marked[0][1]["revision"] == 4
    assert marked[0][1]["result"] == {"event_id": "event:test"}


def test_process_due_once_skips_unchanged_transcript_hash(monkeypatch):
    marked = []
    ingest_calls = []

    monkeypatch.setattr(
        meeting_transcript_jobs.async_jobs,
        "claim_due_job",
        lambda job_type: {
            "job_id": "async_job:test",
            "revision": 5,
            "payload": {
                "transcript": _payload().model_dump(by_alias=True, mode="json"),
                "current_user": {"email": "user@example.test"},
            },
        },
    )
    monkeypatch.setattr(
        meeting_transcript_jobs,
        "_find_existing_transcript_event",
        lambda payload: {"id": "event:test", "raw": {"transcript_hash": "hash-1"}},
    )
    monkeypatch.setattr(
        meeting_transcript_jobs.events_service,
        "ingest_meeting_transcript",
        lambda *_args, **_kwargs: ingest_calls.append(True),
    )
    monkeypatch.setattr(
        meeting_transcript_jobs.async_jobs,
        "mark_succeeded",
        lambda *args, **kwargs: marked.append((args, kwargs)),
    )

    assert meeting_transcript_jobs.process_due_once() is True
    assert ingest_calls == []
    assert marked[0][0] == ("async_job:test",)
    assert marked[0][1]["revision"] == 5
    assert marked[0][1]["status_message"] == "Skipped unchanged transcript"
    assert marked[0][1]["result"] == {
        "event_id": "event:test",
        "skipped": True,
        "reason": "unchanged_transcript_hash",
        "transcript_hash": "hash-1",
    }


def test_process_due_once_regenerates_when_transcript_hash_differs(monkeypatch):
    marked = []

    monkeypatch.setattr(
        meeting_transcript_jobs.async_jobs,
        "claim_due_job",
        lambda job_type: {
            "job_id": "async_job:test",
            "revision": 6,
            "payload": {
                "transcript": _payload().model_dump(by_alias=True, mode="json"),
                "current_user": {"email": "user@example.test"},
            },
        },
    )
    monkeypatch.setattr(
        meeting_transcript_jobs,
        "_find_existing_transcript_event",
        lambda payload: {"id": "event:test", "raw": {"transcript_hash": "old-hash"}},
    )
    monkeypatch.setattr(
        meeting_transcript_jobs.events_service,
        "ingest_meeting_transcript",
        lambda *_args, **_kwargs: {"event_id": "event:test", "summary": "Updated"},
    )
    monkeypatch.setattr(
        meeting_transcript_jobs.async_jobs,
        "mark_succeeded",
        lambda *args, **kwargs: marked.append((args, kwargs)),
    )

    assert meeting_transcript_jobs.process_due_once() is True
    assert marked[0][0] == ("async_job:test",)
    assert marked[0][1]["revision"] == 6
    assert marked[0][1]["status_message"] == "Completed"
    assert marked[0][1]["result"] == {"event_id": "event:test", "summary": "Updated"}


def test_process_due_once_force_regenerate_bypasses_same_hash_skip(monkeypatch):
    marked = []
    ingested = []

    monkeypatch.setattr(
        meeting_transcript_jobs.async_jobs,
        "claim_due_job",
        lambda job_type: {
            "job_id": "async_job:test",
            "revision": 7,
            "payload": {
                "transcript": _payload().model_dump(by_alias=True, mode="json"),
                "current_user": {"email": "user@example.test"},
                "force_regenerate": True,
            },
        },
    )
    monkeypatch.setattr(
        meeting_transcript_jobs,
        "_find_existing_transcript_event",
        lambda payload: {"id": "event:test", "raw": {"transcript_hash": "hash-1"}},
    )
    monkeypatch.setattr(
        meeting_transcript_jobs.events_service,
        "ingest_meeting_transcript",
        lambda *args, **kwargs: ingested.append((args, kwargs)) or {"event_id": "event:test"},
    )
    monkeypatch.setattr(
        meeting_transcript_jobs.async_jobs,
        "mark_succeeded",
        lambda *args, **kwargs: marked.append((args, kwargs)),
    )

    assert meeting_transcript_jobs.process_due_once() is True
    assert len(ingested) == 1
    assert marked[0][1]["result"] == {"event_id": "event:test"}


def test_transcript_route_acknowledges_queue(monkeypatch):
    app = FastAPI()
    app.include_router(create_events_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": "user@example.test"}

    monkeypatch.setattr(
        "routes.events.meeting_transcript_jobs.enqueue_transcript",
        lambda payload, current_user: {
            "job_id": "async_job:test",
            "status": "pending",
            "revision": 1,
            "meeting_key": "external:hyprnote:external-1",
            "next_run_at": "2026-01-01T12:00:30+00:00",
        },
    )

    response = TestClient(app).post(
        "/ingest/meetings/transcript",
        json=_payload().model_dump(by_alias=True, mode="json"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "queued": True,
        "job_id": "async_job:test",
        "status": "pending",
        "revision": 1,
        "meeting_key": "external:hyprnote:external-1",
        "next_run_at": "2026-01-01T12:00:30+00:00",
    }


def test_rerun_meeting_summary_requeues_stored_transcript(monkeypatch):
    app = FastAPI()
    app.include_router(create_events_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": "user@example.test"}

    stored = _payload().model_dump(by_alias=True, mode="json")
    monkeypatch.setattr(
        "routes.events.events_service.get_event_by_id",
        lambda event_id: {"id": event_id, "raw": {"source": "meeting_transcript_ingest", **stored}},
    )
    calls = []

    def fake_enqueue(payload, current_user, force_regenerate=False):
        calls.append((payload, current_user, force_regenerate))
        return {
            "job_id": "async_job:rerun",
            "status": "pending",
            "revision": 2,
            "meeting_key": "external:hyprnote:external-1",
            "next_run_at": "2026-01-01T12:00:30+00:00",
        }

    monkeypatch.setattr("routes.events.meeting_transcript_jobs.enqueue_transcript", fake_enqueue)

    response = TestClient(app).post("/meetings/event:test/summary/rerun")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "queued": True,
        "rerun": True,
        "event_id": "event:test",
        "job_id": "async_job:rerun",
        "status": "pending",
        "revision": 2,
        "meeting_key": "external:hyprnote:external-1",
        "next_run_at": "2026-01-01T12:00:30+00:00",
    }
    assert calls[0][0].transcript_hash == "hash-1"
    assert calls[0][1] == {"email": "user@example.test"}
    assert calls[0][2] is True


def test_meeting_detail_includes_stored_transcript_for_web_review(monkeypatch):
    app = FastAPI()
    app.include_router(create_events_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": "user@example.test"}

    monkeypatch.setattr(
        "routes.events.events_service.get_meeting",
        lambda _event_id: {
            "id": "event:test",
            "title": "Queue Review",
            "raw": {"source": "meeting_transcript_ingest", "transcript_text": "Alex: We should queue this."},
        },
    )

    response = TestClient(app).get("/meetings/event:test")

    assert response.status_code == 200
    assert response.json()["raw"]["transcript_text"] == "Alex: We should queue this."
