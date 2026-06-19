from __future__ import annotations

import event_tag_jobs


def test_enqueue_event_tag_enrichment_uses_replacing_job(monkeypatch):
    calls: list[dict] = []

    def fake_enqueue_job(**kwargs):
        calls.append(kwargs)
        return {
            "job_id": "job-1",
            "status": "pending",
            "revision": 2,
            "next_run_at": "soon",
        }

    monkeypatch.setattr(event_tag_jobs.async_jobs, "enqueue_job", fake_enqueue_job)

    result = event_tag_jobs.enqueue_event_tag_enrichment(" event:123 ", source="event_command")

    assert calls == [
        {
            "job_type": event_tag_jobs.JOB_TYPE,
            "user_email": "system",
            "dedupe_key": "event:123",
            "payload": {"event_id": "event:123", "source": "event_command"},
            "status_message": "Queued",
            "delay_seconds": 0,
            "replace_existing": True,
        }
    ]
    assert result == {
        "job_id": "job-1",
        "status": "pending",
        "revision": 2,
        "event_id": "event:123",
        "next_run_at": "soon",
    }


def test_process_due_once_persists_generated_tags(monkeypatch):
    marked: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        event_tag_jobs.async_jobs,
        "claim_due_job",
        lambda job_type: {
            "job_id": "job-1",
            "revision": 4,
            "payload": {"event_id": "event:123"},
        },
    )
    monkeypatch.setattr(
        event_tag_jobs.events_service,
        "generate_and_persist_event_tags",
        lambda event_id: {
            "event_id": event_id,
            "updated": True,
            "tags": ["Work", "Meeting"],
        },
    )
    monkeypatch.setattr(
        event_tag_jobs.async_jobs,
        "mark_succeeded",
        lambda *args, **kwargs: marked.append(("succeeded", {"args": args, **kwargs})),
    )

    assert event_tag_jobs.process_due_once() is True
    assert marked == [
        (
            "succeeded",
            {
                "args": ("job-1",),
                "result": {
                    "event_id": "event:123",
                    "updated": True,
                    "tags": ["Work", "Meeting"],
                },
                "status_message": "Completed",
                "revision": 4,
            },
        )
    ]
