from __future__ import annotations

import contact_tag_jobs


def test_enqueue_contact_tag_enrichment_uses_replacing_job(monkeypatch):
    calls: list[dict] = []

    def fake_enqueue_job(**kwargs):
        calls.append(kwargs)
        return {
            "job_id": "job-1",
            "status": "pending",
            "revision": 2,
            "next_run_at": "soon",
        }

    monkeypatch.setattr(contact_tag_jobs.async_jobs, "enqueue_job", fake_enqueue_job)

    result = contact_tag_jobs.enqueue_contact_tag_enrichment(" contact:123 ", source="contact_update")

    assert calls == [
        {
            "job_type": contact_tag_jobs.JOB_TYPE,
            "user_email": "system",
            "dedupe_key": "contact:123",
            "payload": {"contact_id": "contact:123", "source": "contact_update"},
            "status_message": "Queued",
            "delay_seconds": 0,
            "replace_existing": True,
        }
    ]
    assert result == {
        "job_id": "job-1",
        "status": "pending",
        "revision": 2,
        "contact_id": "contact:123",
        "next_run_at": "soon",
    }


def test_process_due_once_persists_generated_tags(monkeypatch):
    marked: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        contact_tag_jobs.async_jobs,
        "claim_due_job",
        lambda job_type: {
            "job_id": "job-1",
            "revision": 4,
            "payload": {"contact_id": "contact:123"},
        },
    )
    monkeypatch.setattr(
        contact_tag_jobs.contacts_service,
        "generate_and_persist_contact_tags",
        lambda contact_id: {
            "contact_id": contact_id,
            "updated": True,
            "tags": ["Work", "Client"],
        },
    )
    monkeypatch.setattr(
        contact_tag_jobs.async_jobs,
        "mark_succeeded",
        lambda *args, **kwargs: marked.append(("succeeded", {"args": args, **kwargs})),
    )

    assert contact_tag_jobs.process_due_once() is True
    assert marked == [
        (
            "succeeded",
            {
                "args": ("job-1",),
                "result": {
                    "contact_id": "contact:123",
                    "updated": True,
                    "tags": ["Work", "Client"],
                },
                "status_message": "Completed",
                "revision": 4,
            },
        )
    ]
