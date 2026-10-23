# ruff: noqa: F811
"""Durable briefing ownership, recovery, fair admission and truthful API status."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import async_jobs
import daily_briefing_jobs as jobs
import daily_briefings
import llm_admission as gate
from agents.daily_briefing import executor
from routes import daily_briefing as routes
from tests.test_background_recovery import jobs_db  # noqa: F401
from tests.test_intelligence import OTHER, OWNER, database  # noqa: F401


@pytest.fixture
def briefing_db(jobs_db, monkeypatch):
    monkeypatch.setattr(jobs, "get_conn", jobs_db)
    monkeypatch.setattr(daily_briefings, "get_conn", jobs_db)
    with jobs_db() as conn:
        conn.execute(
            """CREATE TABLE daily_briefings(
            briefing_id TEXT PRIMARY KEY,user_email TEXT,briefing_date DATE,timezone TEXT,
            markdown TEXT,summary TEXT,event_count INTEGER,todo_count INTEGER,
            updated_at TIMESTAMPTZ DEFAULT NOW(),UNIQUE(user_email,briefing_date,timezone))"""
        )
    return jobs_db


def enqueue(owner=OWNER, day=date(2026, 1, 3)):
    return jobs.enqueue_daily_briefing_job(user_email=owner, briefing_date=day, timezone_name="UTC")


def test_interrupted_claim_recovers_without_stealing_live_or_other_jobs(briefing_db):
    queued = enqueue()
    old = async_jobs.claim_due_job(job_type=jobs.JOB_TYPE)
    live = enqueue(OTHER)
    async_jobs.claim_due_job(job_type=jobs.JOB_TYPE)
    with briefing_db() as conn:
        conn.execute(
            "UPDATE async_jobs SET updated_at=NOW()-INTERVAL '2 days' WHERE job_id=%s",
            (old["job_id"],),
        )
    assert async_jobs.recover_stale_jobs(job_type=jobs.JOB_TYPE, user_email=OTHER) == 0
    assert async_jobs.recover_stale_jobs(job_type=jobs.JOB_TYPE, user_email=OWNER) == 1
    row = async_jobs.get_job(job_type=jobs.JOB_TYPE, user_email=OWNER, dedupe_key="2026-01-03::UTC")
    assert row["job_id"] == queued["job_id"] and row["revision"] == old["revision"] + 1
    assert row["status"] == "pending" and row["attempts"] == 0
    assert not async_jobs.heartbeat_job(old["job_id"], old["revision"])
    assert not async_jobs.mark_succeeded(old["job_id"], revision=old["revision"])
    with briefing_db() as conn:
        assert (
            conn.execute(
                "SELECT status FROM async_jobs WHERE job_id=%s", (live["job_id"],)
            ).fetchone()["status"]
            == "running"
        )


def test_heartbeat_prevents_recovery_and_superseded_worker_cannot_publish(briefing_db):
    enqueue()
    claimed = async_jobs.claim_due_job(job_type=jobs.JOB_TYPE)
    assert async_jobs.heartbeat_job(claimed["job_id"], claimed["revision"])
    assert async_jobs.recover_stale_jobs(job_type=jobs.JOB_TYPE) == 0
    token = async_jobs._ACTIVE_WORKER.set({"job": claimed})
    try:
        with briefing_db() as conn:
            conn.execute("UPDATE async_jobs SET updated_at=NOW()-INTERVAL '3 minutes'")
        assert async_jobs.recover_stale_jobs(job_type=jobs.JOB_TYPE) == 1
        with pytest.raises(async_jobs.JobSuperseded):
            daily_briefings.upsert_daily_briefing(
                user_email=OWNER,
                briefing_date=date(2026, 1, 3),
                timezone="UTC",
                markdown="Old result",
                summary="Old result",
                event_count=0,
                todo_count=0,
            )
        with briefing_db() as conn:
            assert not conn.execute("SELECT 1 FROM daily_briefings").fetchone()
    finally:
        async_jobs._ACTIVE_WORKER.reset(token)


def test_due_briefing_gets_admission_after_history_and_prioritizes_latest_day(
    briefing_db, monkeypatch
):
    endpoint = "https://models.example.test/v1"
    monkeypatch.setenv("LLM_BASE_URL", endpoint)
    monkeypatch.setattr(gate, "_STATES", {})
    enqueue(day=date(2026, 1, 1))
    newest = enqueue(day=date(2026, 1, 3))
    generated = []

    def generate(payload):
        with gate.request_slot(endpoint):
            generated.append(payload["date"])
        return {"briefing_id": "briefing:new", "date": payload["date"], "timezone": "UTC"}

    monkeypatch.setattr(executor, "handle_daily_briefing_request", generate)
    notify = MagicMock()
    monkeypatch.setattr(jobs, "_notify_daily_briefing_ready", notify)
    with gate.background_work("history"):
        with gate.request_slot(endpoint):
            assert not jobs.process_due_once()
            assert not generated
    assert jobs.process_due_once()
    assert generated == ["2026-01-03"]
    notify.assert_called_once()
    with briefing_db() as conn:
        assert (
            conn.execute(
                "SELECT status FROM async_jobs WHERE job_id=%s", (newest["job_id"],)
            ).fetchone()["status"]
            == "succeeded"
        )


def test_briefings_keep_fifo_position_with_real_poll_cadence_and_sustained_contention(
    briefing_db,
    monkeypatch,
):
    endpoint = "https://models.example.test/v1"
    monkeypatch.setenv("LLM_BASE_URL", endpoint)
    monkeypatch.setattr(gate, "_STATES", {})
    clock = [1000.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: clock[0])
    generated = []

    def generate(payload):
        with gate.request_slot(endpoint):
            generated.append(payload["date"])
        return {"briefing_id": "briefing:new", "date": payload["date"], "timezone": "UTC"}

    monkeypatch.setattr(executor, "handle_daily_briefing_request", generate)
    monkeypatch.setattr(jobs, "_notify_daily_briefing_ready", MagicMock())
    competitors = ["knowledge", "summaries", "event_tag_jobs"]
    # Several sustained busy periods, 2-second competitors and 60-second briefing
    # polls plus realistic overhead. All time is virtual; no actual LLM/sleeps.
    for cycle in range(4):
        day = date(2026, 1, 3) + timedelta(days=cycle)
        queued = enqueue(day=day)
        start = clock[0]
        next_briefing = start
        with gate.background_work("inflight-extraction"):
            with gate.request_slot(endpoint):
                for offset in range(0, 236, 2):
                    clock[0] = start + offset
                    if clock[0] >= next_briefing:
                        assert not jobs.process_due_once()
                        next_briefing = clock[0] + jobs.POLL_SECONDS + 0.25
                    for lane in competitors:
                        with gate.background_work(lane, poll_seconds=2):
                            assert not gate.background_ready()
        # Faster pollers return before the briefing's next poll. They must not
        # steal its FIFO position when the long-running workflow releases.
        clock[0] = start + 236
        for lane in competitors:
            with gate.background_work(lane, poll_seconds=2):
                assert not gate.background_ready()
        clock[0] = next_briefing
        assert jobs.process_due_once()
        assert generated[-1] == day.isoformat()
        with briefing_db() as conn:
            row = conn.execute(
                "SELECT status,attempts FROM async_jobs WHERE job_id=%s", (queued["job_id"],)
            ).fetchone()
            assert row["status"] == "succeeded" and row["attempts"] == 1
        # Completing the briefing releases its turn and the faster lanes proceed.
        for lane in competitors:
            with gate.background_work(lane, poll_seconds=2):
                assert gate.background_ready()
                with gate.request_slot(endpoint):
                    pass
        clock[0] += 1
    assert len(generated) == 4


def test_provider_deferral_releases_claim_without_publishing_or_notifying(briefing_db, monkeypatch):
    monkeypatch.setattr(gate, "_STATES", {})
    queued = enqueue()

    def generate(payload):
        raise gate.BackgroundDeferred("provider_cooldown", 30)

    monkeypatch.setattr(executor, "handle_daily_briefing_request", generate)
    notify = MagicMock()
    monkeypatch.setattr(jobs, "_notify_daily_briefing_ready", notify)
    assert jobs.process_due_once()
    with briefing_db() as conn:
        row = conn.execute(
            "SELECT * FROM async_jobs WHERE job_id=%s", (queued["job_id"],)
        ).fetchone()
        assert row["status"] == "pending" and row["attempts"] == 0 and row["error"] is None
    notify.assert_not_called()


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("pending", "queued"),
        ("running", "being prepared"),
        ("failed", "retry shortly"),
    ],
)
def test_api_reports_job_state_and_never_launches_an_ungated_generation(
    monkeypatch, status, message
):
    monkeypatch.setattr(daily_briefings, "get_daily_briefing", lambda **kw: None)
    monkeypatch.setattr(
        jobs,
        "enqueue_daily_briefing_job",
        lambda **kw: {"job_id": "job", "status": status, "should_schedule": True},
    )
    generation = MagicMock(side_effect=AssertionError("API must only enqueue"))
    monkeypatch.setattr(executor, "handle_daily_briefing_request", generation)
    app = FastAPI()
    app.include_router(routes.create_daily_briefing_router())
    app.dependency_overrides[routes.get_current_user] = lambda: {"email": OWNER}
    with TestClient(app) as client:
        response = client.get("/mobile/briefings/daily?date=2026-01-03&timezone=UTC")
    assert response.status_code == 202 and response.json()["status"] == "pending"
    assert message in response.json()["message"]
    generation.assert_not_called()


def test_completed_job_without_briefing_is_requeued_instead_of_staying_pending(monkeypatch):
    monkeypatch.setattr(daily_briefings, "get_daily_briefing", lambda **kw: None)
    calls = []

    def enqueue(**kwargs):
        calls.append(kwargs)
        return {
            "job_id": "job",
            "status": "pending" if kwargs.get("replace_existing") else "succeeded",
        }

    monkeypatch.setattr(jobs, "enqueue_daily_briefing_job", enqueue)
    app = FastAPI()
    app.include_router(routes.create_daily_briefing_router())
    app.dependency_overrides[routes.get_current_user] = lambda: {"email": OWNER}
    with TestClient(app) as client:
        response = client.get("/mobile/briefings/daily?date=2026-01-03&timezone=UTC")
    assert response.status_code == 202 and "queued" in response.json()["message"]
    assert len(calls) == 2 and calls[1]["replace_existing"]


def raw_job(owner, day, tz="UTC", *, job_type=jobs.JOB_TYPE):
    return async_jobs.enqueue_job(
        job_type=job_type,
        user_email=owner,
        dedupe_key=f"{day}::{tz}",
        payload={"date": day, "timezone": tz, "user_email": owner},
    )


@pytest.mark.parametrize("status", ["pending", "failed", "running"])
def test_past_briefing_is_cancelled_only_when_newer_day_is_queued(briefing_db, status):
    old = raw_job(OWNER, "2026-01-03")
    newer = raw_job(OWNER, "2026-01-04")
    with briefing_db() as conn:
        conn.execute("UPDATE async_jobs SET status=%s WHERE job_id=%s", (status, old["job_id"]))
    stored = daily_briefings.upsert_daily_briefing(
        user_email=OWNER,
        briefing_date=date(2026, 1, 3),
        timezone="UTC",
        markdown="Already saved briefing",
        summary="Already saved briefing",
        event_count=0,
        todo_count=0,
    )
    cancelled = jobs.cancel_superseded_briefings(
        now_utc=datetime(2026, 1, 4, 9, tzinfo=timezone.utc)
    )
    assert cancelled == {old["job_id"]}
    assert not async_jobs.mark_succeeded(old["job_id"], revision=old["revision"])
    with briefing_db() as conn:
        row = conn.execute("SELECT * FROM async_jobs WHERE job_id=%s", (old["job_id"],)).fetchone()
        assert row["status"] == "cancelled" and row["revision"] == old["revision"] + 1
        assert row["finished_at"] is not None
        assert (
            conn.execute(
                "SELECT status FROM async_jobs WHERE job_id=%s", (newer["job_id"],)
            ).fetchone()["status"]
            == "pending"
        )
        assert (
            conn.execute("SELECT briefing_id FROM daily_briefings").fetchone()["briefing_id"]
            == stored["briefing_id"]
        )


def test_cancellation_keeps_today_future_other_owners_and_unrelated_jobs(briefing_db):
    rows = [
        raw_job(OWNER, "2026-01-04"),
        raw_job(OWNER, "2026-01-05"),
        raw_job(OTHER, "2026-01-03"),
        raw_job(OWNER, "2026-01-03", job_type="tag_test"),
    ]
    assert not jobs.cancel_superseded_briefings(
        now_utc=datetime(2026, 1, 4, 9, tzinfo=timezone.utc)
    )
    with briefing_db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM async_jobs WHERE status='pending'"
        ).fetchone()["n"] == len(rows)


@pytest.mark.parametrize(
    ("tz", "cancelled"), [("Pacific/Kiritimati", True), ("America/Los_Angeles", False)]
)
def test_cancellation_uses_each_briefings_local_day(briefing_db, tz, cancelled):
    old = raw_job(OWNER, "2026-01-03", tz)
    raw_job(OWNER, "2026-01-04", tz)
    result = jobs.cancel_superseded_briefings(
        now_utc=datetime(2026, 1, 3, 10, 30, tzinfo=timezone.utc)
    )
    assert (old["job_id"] in result) == cancelled


def test_cancelled_inflight_briefing_stops_before_next_llm_call(briefing_db, monkeypatch):
    endpoint = "https://models.example.test/v1"
    monkeypatch.setenv("LLM_BASE_URL", endpoint)
    monkeypatch.setattr(gate, "_STATES", {})
    old = raw_job(OWNER, "2026-01-03")
    requests = []

    def generate(payload):
        with gate.request_slot(endpoint):
            requests.append("first")
        raw_job(OWNER, "2026-01-04")
        assert jobs.cancel_superseded_briefings(
            now_utc=datetime(2026, 1, 4, 9, tzinfo=timezone.utc)
        ) == {old["job_id"]}
        with gate.request_slot(endpoint):
            requests.append("must not run")
        pytest.fail("Cancelled generation continued")

    monkeypatch.setattr(executor, "handle_daily_briefing_request", generate)
    notify = MagicMock()
    monkeypatch.setattr(jobs, "_notify_daily_briefing_ready", notify)
    assert jobs.process_due_once()
    assert requests == ["first"]
    notify.assert_not_called()
    with briefing_db() as conn:
        assert not conn.execute("SELECT 1 FROM daily_briefings").fetchone()
        assert (
            conn.execute(
                "SELECT status FROM async_jobs WHERE job_id=%s", (old["job_id"],)
            ).fetchone()["status"]
            == "cancelled"
        )
