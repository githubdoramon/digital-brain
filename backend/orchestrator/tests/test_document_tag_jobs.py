from __future__ import annotations

import document_tag_jobs


def test_enqueue_document_tag_enrichment_uses_replacing_job(monkeypatch):
    calls: list[dict] = []

    def fake_enqueue_job(**kwargs):
        calls.append(kwargs)
        return {
            "job_id": "job-1",
            "status": "pending",
            "revision": 2,
            "next_run_at": "soon",
        }

    monkeypatch.setattr(document_tag_jobs.async_jobs, "enqueue_job", fake_enqueue_job)

    result = document_tag_jobs.enqueue_document_tag_enrichment(" doc:123 ", source="document_update")

    assert calls == [
        {
            "job_type": document_tag_jobs.JOB_TYPE,
            "user_email": "system",
            "dedupe_key": "doc:123",
            "payload": {"document_id": "doc:123", "source": "document_update"},
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


def test_process_due_once_persists_generated_tags(monkeypatch):
    marked: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        document_tag_jobs.async_jobs,
        "claim_due_job",
        lambda job_type: {
            "job_id": "job-1",
            "revision": 4,
            "payload": {"document_id": "doc:123"},
        },
    )
    monkeypatch.setattr(
        document_tag_jobs.documents_service,
        "generate_and_persist_document_tags",
        lambda document_id: {
            "document_id": document_id,
            "updated": True,
            "tags": ["Legal", "Contract"],
        },
    )
    monkeypatch.setattr(
        document_tag_jobs.async_jobs,
        "mark_succeeded",
        lambda *args, **kwargs: marked.append(("succeeded", {"args": args, **kwargs})),
    )

    assert document_tag_jobs.process_due_once() is True
    assert marked == [
        (
            "succeeded",
            {
                "args": ("job-1",),
                "result": {
                    "document_id": "doc:123",
                    "updated": True,
                    "tags": ["Legal", "Contract"],
                },
                "status_message": "Completed",
                "revision": 4,
            },
        )
    ]
