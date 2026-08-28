from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import document_enhancement_jobs
import documents


def test_enqueue_document_enhancement_uses_replacing_job(monkeypatch):
    calls: list[dict] = []

    def fake_enqueue_job(**kwargs):
        calls.append(kwargs)
        return {"job_id": "job-1", "status": "pending", "revision": 2, "next_run_at": "soon"}

    monkeypatch.setattr(document_enhancement_jobs.async_jobs, "enqueue_job", fake_enqueue_job)

    result = document_enhancement_jobs.enqueue_document_enhancement(
        " doc:123 ", user_email="user@example.test", source="web_retry"
    )

    assert calls == [
        {
            "job_type": "document_enhancement",
            "user_email": "system",
            "dedupe_key": "doc:123",
            "payload": {
                "document_id": "doc:123",
                "user_email": "user@example.test",
                "source": "web_retry",
            },
            "status_message": "Queued",
            "delay_seconds": 0,
            "replace_existing": True,
        }
    ]
    assert result == {
        "job_id": "job-1",
        "status": "pending",
        "revision": 2,
        "document_id": "doc:123",
        "next_run_at": "soon",
    }


def test_upload_creates_placeholder_and_queues_before_enhancement(monkeypatch, tmp_path: Path):
    stored = documents.StoredFileInfo(
        document_id="doc:123",
        path=tmp_path / "doc:123.txt",
        file_name="notes.txt",
        mime_type="text/plain",
        size=5,
    )
    events: list[str] = []
    stored.path.write_text("hello")

    monkeypatch.setattr(documents, "_store_upload", lambda _upload, _document_id: stored)
    monkeypatch.setattr(
        documents,
        "_create_document_placeholder",
        lambda **_kwargs: events.append("placeholder") or {"document_id": "doc:123", "title": "Notes"},
    )
    monkeypatch.setattr(
        documents,
        "_enqueue_document_enhancement",
        lambda *_args, **_kwargs: events.append("queued") or {"job_id": "job-1"},
    )
    monkeypatch.setattr(
        documents,
        "get_document",
        lambda _document_id: events.append("read")
        or {"document_id": "doc:123", "title": "Notes", "enhancement_status": "pending"},
    )

    result = documents.ingest_document(
        title=None,
        tags=None,
        contact_ids=None,
        description=None,
        upload=SimpleNamespace(filename="notes.txt", content_type="text/plain"),
        user_email="user@example.test",
    )

    assert events == ["placeholder", "queued", "read"]
    assert result["enhancement_status"] == "pending"


def test_process_due_once_marks_document_complete(monkeypatch):
    marked: list[tuple[str, dict]] = []
    states: list[tuple[str, str, dict]] = []

    monkeypatch.setattr(
        document_enhancement_jobs.async_jobs,
        "claim_due_job",
        lambda job_type: {
            "job_id": "job-1",
            "revision": 4,
            "payload": {"document_id": "doc:123", "user_email": "user@example.test"},
        },
    )
    monkeypatch.setattr(
        document_enhancement_jobs.documents_service,
        "_set_enhancement_status",
        lambda document_id, status, **kwargs: states.append((document_id, status.value, kwargs)),
    )
    monkeypatch.setattr(
        document_enhancement_jobs.documents_service,
        "enhance_document",
        lambda document_id, user_email: {"document_id": document_id},
    )
    monkeypatch.setattr(
        document_enhancement_jobs.async_jobs,
        "mark_succeeded",
        lambda *args, **kwargs: marked.append(("succeeded", {"args": args, **kwargs})),
    )

    assert document_enhancement_jobs.process_due_once() is True
    assert states == [
        ("doc:123", "processing", {}),
        ("doc:123", "complete", {}),
    ]
    assert marked[0][1]["args"] == ("job-1",)


def test_process_due_once_persists_failure_and_retries(monkeypatch):
    failed: list[tuple[str, dict]] = []
    states: list[tuple[str, str, dict]] = []

    monkeypatch.setattr(
        document_enhancement_jobs.async_jobs,
        "claim_due_job",
        lambda job_type: {"job_id": "job-1", "revision": 4, "payload": {"document_id": "doc:123"}},
    )
    monkeypatch.setattr(
        document_enhancement_jobs.documents_service,
        "_set_enhancement_status",
        lambda document_id, status, **kwargs: states.append((document_id, status.value, kwargs)),
    )
    monkeypatch.setattr(
        document_enhancement_jobs.documents_service,
        "enhance_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("embedding unavailable")),
    )
    monkeypatch.setattr(
        document_enhancement_jobs.async_jobs,
        "mark_failed",
        lambda *args, **kwargs: failed.append(("failed", {"args": args, **kwargs})),
    )

    assert document_enhancement_jobs.process_due_once() is True
    assert states == [
        ("doc:123", "processing", {}),
        ("doc:123", "failed", {"error": "embedding unavailable"}),
    ]
    assert failed[0][1]["retry_delay_seconds"] == 60
