# ruff: noqa: F811
"""Durable tag deduplication across repeated calendar saves and concurrent work."""

from concurrent.futures import ThreadPoolExecutor

import pytest

import async_jobs
import event_tag_jobs
import events
from tests.test_background_recovery import jobs_db  # noqa: F401
from tests.test_learning_batches import database  # noqa: F401


@pytest.fixture
def event(monkeypatch):
    value = {
        "id": "event:sample",
        "title": "Sample meeting",
        "summary": "Review a report",
        "tags": [],
    }
    monkeypatch.setattr(events, "get_event_by_id", lambda _: dict(value))
    return value


def enqueue():
    return event_tag_jobs.enqueue_event_tag_enrichment("event:sample")


def current():
    return async_jobs.get_job(
        job_type=event_tag_jobs.JOB_TYPE, user_email="system", dedupe_key="event:sample"
    )


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_unchanged_calendar_polls_preserve_queue_position_and_retry(jobs_db, event, status):
    first = enqueue()
    if status != "pending":
        claim = async_jobs.claim_due_job(job_type=event_tag_jobs.JOB_TYPE)
        if status == "failed":
            async_jobs.mark_failed(
                claim["job_id"],
                revision=claim["revision"],
                error="synthetic failure",
                retry_delay_seconds=600,
            )
    before = current()
    for i in range(5):
        event["raw"] = {"updated": str(i)}
        event["start_date"] = f"2026-11-{i + 1:02d}"
        assert enqueue()["job_id"] == first["job_id"]
    assert current() == before


def test_successful_enrichment_does_not_queue_itself_and_changes_do(jobs_db, event):
    first = enqueue()
    claim = async_jobs.claim_due_job(job_type=event_tag_jobs.JOB_TYPE)
    event["tags"] = ["Work", "Meeting"]
    async_jobs.mark_succeeded(
        claim["job_id"],
        revision=claim["revision"],
        result={
            "tags": event["tags"],
            "input_fingerprint": events.event_tag_fingerprint(event),
        },
    )
    for _ in range(5):
        assert enqueue()["job_id"] == first["job_id"]
    assert current()["status"] == "succeeded"
    event["summary"] = "Discuss a different report"
    changed = enqueue()
    assert changed["revision"] == first["revision"] + 1
    assert changed["job_id"] != first["job_id"]
    # An obsolete running job cannot complete a replacement revision.
    async_jobs.mark_succeeded(claim["job_id"], revision=claim["revision"], result={})
    assert current()["status"] == "pending"
    event["summary"] = "Review a report"  # Reversions are real new work too.
    assert enqueue()["revision"] == changed["revision"] + 1


def test_existing_jobs_adopt_fingerprint_once(jobs_db, event):
    legacy = async_jobs.enqueue_job(
        job_type=event_tag_jobs.JOB_TYPE,
        user_email="system",
        dedupe_key=event["id"],
        payload={"event_id": event["id"]},
    )
    first = enqueue()
    assert first["revision"] == legacy["revision"] + 1
    assert enqueue()["job_id"] == first["job_id"]


def test_concurrent_identical_first_saves_share_one_job(jobs_db, event):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: enqueue(), range(8)))
    assert len({row["job_id"] for row in results}) == 1
    assert current()["revision"] == 1


def test_fingerprint_ignores_metadata_and_tag_order_but_tracks_model_inputs(event):
    original = events.event_tag_fingerprint({**event, "tags": ["Work", "Meeting"]})
    assert (
        events.event_tag_fingerprint({**event, "tags": ["Meeting", "Work"], "raw": {"etag": "new"}})
        == original
    )
    for field, value in [
        ("title", "Other meeting"),
        ("summary", "Other report"),
        ("tags", ["Home"]),
    ]:
        assert (
            events.event_tag_fingerprint({**event, "tags": ["Work", "Meeting"], field: value})
            != original
        )


def test_missing_event_does_not_enqueue(monkeypatch):
    monkeypatch.setattr(events, "get_event_by_id", lambda _: None)
    monkeypatch.setattr(async_jobs, "enqueue_job", lambda **_: pytest.fail("missing event queued"))
    assert enqueue() is None
