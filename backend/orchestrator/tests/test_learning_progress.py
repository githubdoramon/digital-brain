# ruff: noqa: F811
"""Progress must distinguish incomplete history, active leases and waiting work."""

import json
import logging
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

import intelligence
import knowledge_jobs as jobs
import learning_progress as progress
from tests.test_intelligence import OTHER, OWNER, database, setup_owner  # noqa: F401


def source(
    database, ident, *, owner=OWNER, historical=True, kind="chat", sent="2025-12-01T12:00:00+00:00"
):
    with database() as conn:
        return conn.execute(
            """INSERT INTO intelligence_sources(kind,entity_id,owner_email,fingerprint,payload,historical)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING source_id""",
            (
                kind,
                ident,
                owner,
                ident,
                intelligence.json_value({"text": "Private anonymous evidence.", "sent_at": sent}),
                historical,
            ),
        ).fetchone()["source_id"]


def test_uninitialized_does_not_claim_empty_history_is_caught_up(database):
    report = progress.snapshot(OWNER)
    assert not report["initialized"] and not report["history_caught_up"]
    assert report["learning"]["history"]["state"] == "initializing"


def test_census_includes_history_not_yet_enqueued(database):
    setup_owner(database)
    with database() as conn:
        conn.execute(
            "INSERT INTO documents(document_id,title,content) SELECT 'manual-'||n,'Device manual','Retained manual.' FROM generate_series(1,550) n"
        )
    jobs.initialize_owner(OWNER)
    with database() as conn:
        conn.execute("UPDATE knowledge_learning SET status='done'")
    report = progress.snapshot(OWNER)
    history = report["learning"]["history"]
    assert history["total"] == 550 and history["processed"] == 500
    assert history["not_enqueued"] == history["remaining"] == 50
    assert history["state"] == "pending" and not report["history_caught_up"]
    # A deployment must not invent a last-success timestamp for old receipts.
    assert history["last_success_at"] is None
    jobs.initialize_owner(OWNER)
    assert progress.snapshot(OWNER)["learning"]["history"]["ready"] == 50


def test_learning_states_ownership_expired_leases_and_original_dates(database):
    jobs.initialize_owner(OWNER)
    jobs.initialize_owner(OTHER)
    phases = [
        ("done", 1, None, "NOW()", True),
        ("pending", 0, None, "NOW()", True),
        ("running", 3, "NOW()+INTERVAL '15 minutes'", "NOW()", True),
        ("running", 3, "NOW()-INTERVAL '1 second'", "NOW()", True),
        ("failed", 1, None, "NOW()+INTERVAL '5 minutes'", True),
        ("failed", 3, None, "NOW()", True),
        ("deferred", 1, None, "NOW()", True),
        ("pending", 0, None, "NOW()", False),
    ]
    for i, (status, attempts, lease, due, historical) in enumerate(phases):
        ident = source(
            database,
            str(i),
            historical=historical,
            sent="2025-12-01T12:00:00+00:00" if historical else "2100-01-01T00:00:00+00:00",
        )
        with database() as conn:
            conn.execute(
                # Time expressions are constants in this synthetic test matrix.
                f"""INSERT INTO knowledge_learning(user_email,source_id,historical,status,attempts,
                    lease_until,next_attempt_at,started_at,last_success_at)
                VALUES(%s,%s,%s,%s,%s,{lease or "NULL"},{due},NOW()-INTERVAL '1 minute',
                    CASE WHEN %s='done' THEN NOW()-INTERVAL '2 minutes' END)""",
                (OWNER, ident, historical, status, attempts, status),
            )
    foreign = source(database, "foreign", owner=OTHER)
    with database() as conn:
        conn.execute(
            "INSERT INTO knowledge_learning(user_email,source_id) VALUES(%s,%s)", (OTHER, foreign)
        )
    report = progress.snapshot(OWNER)
    lane = report["learning"]["history"]
    assert lane["total"] == 7 and lane["remaining"] == 6
    assert [
        lane[name] for name in ("processed", "ready", "running", "retrying", "paused", "deferred")
    ] == [1, 2, 1, 1, 1, 1]
    assert len(lane["active"]) == 1 and lane["active"][0]["elapsed_s"] >= 60
    assert lane["oldest_pending_source_at"].year == 2025
    assert lane["oldest_pending_observed_at"].year >= 2026
    assert lane["last_success_at"] is not None and lane["next_retry_at"] is not None
    assert report["learning"]["ongoing"]["total"] == 1
    assert report["counts"] == {"done": 1, "pending": 2, "running": 2, "failed": 2, "deferred": 1}


def test_canonical_history_includes_late_imports_and_legacy_lane_is_retired(database):
    jobs.initialize_owner(OWNER)
    old = source(database, "late-import", kind="message", historical=False)
    source(database, "chat")
    source(database, "new", kind="document", historical=False, sent="2100-01-01T00:00:00+00:00")
    jobs.initialize_owner(OWNER)
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_learning SET status='failed',attempts=3,error_code='invalid_evidence' WHERE source_id=%s",
            (old,),
        )
    report = progress.snapshot(OWNER)
    lane = report["learning"]["history"]
    assert lane["total"] == 2 and lane["paused"] == 1
    assert not report["history_caught_up"]
    assert lane["errors"] == {"invalid_evidence": 1}
    assert report["intelligence"]["history"]["retired"]
    assert report["intelligence"]["scheduled"]["total"] == 1


def test_old_source_in_ongoing_queue_still_counts_as_unfinished_history(database):
    jobs.initialize_owner(OWNER)
    ref = source(database, "older-chat", historical=False)
    with database() as conn:
        conn.execute(
            """INSERT INTO knowledge_learning(user_email,source_id,historical,status,lease_until)
            VALUES(%s,%s,FALSE,'running',NOW()+INTERVAL '15 minutes')""",
            (OWNER, ref),
        )
    report = progress.snapshot(OWNER)
    assert report["learning"]["history"]["remaining"] == 1
    assert report["learning"]["ongoing"]["total"] == 0
    assert report["learning"]["history"]["active"][0]["queue_historical"] is False
    assert not report["history_caught_up"]


def test_summary_retries_active_runs_and_current_versions(database):
    jobs.initialize_owner(OWNER)
    with database() as conn:
        conn.execute(
            """INSERT INTO knowledge_summaries(user_email,subject_ref,version,rendered_version,error_code,next_attempt_at,lease_until)
            VALUES (%s,'self',2,1,'invalid_summary',NOW()+INTERVAL '5 minutes',NULL),
                (%s,'entity:current',1,1,NULL,NOW(),NOW()+INTERVAL '15 minutes'),
                (%s,'entity:active',2,1,NULL,NOW(),NOW()+INTERVAL '15 minutes')""",
            (OWNER, OWNER, OWNER),
        )
        conn.execute(
            "INSERT INTO intelligence_runs(run_id,user_email,slot,cutoff,status,started_at,lease_until) VALUES('scheduled',%s,'scheduled',NOW(),'running',NOW()-INTERVAL '2 minutes',NOW()+INTERVAL '15 minutes')",
            (OWNER,),
        )
    report = progress.snapshot(OWNER)
    summaries = report["summaries"]
    assert summaries["total"] == 3 and summaries["remaining"] == 2
    assert summaries["processed"] == summaries["running"] == summaries["retrying"] == 1
    assert summaries["errors"] == {"invalid_summary": 1}
    assert len(summaries["active"]) == 1
    scheduled = report["intelligence"]["scheduled"]
    assert scheduled["running_runs"] == 1 and scheduled["running"] == 0
    assert scheduled["active"][0]["elapsed_s"] >= 120
    assert scheduled["state"] == "processing"
    assert report["intelligence"]["history"]["retired"]


def test_summary_debounce_is_waiting_without_failed_retry(database):
    jobs.initialize_owner(OWNER)
    with database() as conn:
        conn.execute(
            """INSERT INTO knowledge_summaries(user_email,subject_ref,next_attempt_at)
            VALUES(%s,'self',NOW()+INTERVAL '2 seconds')""",
            (OWNER,),
        )
    summaries = progress.snapshot(OWNER)["summaries"]
    assert summaries["delayed"] == summaries["remaining"] == 1
    assert summaries["retrying"] == 0 and summaries["errors"] == {}


def test_paused_and_deferred_history_are_not_caught_up(database):
    jobs.initialize_owner(OWNER)
    for status, attempts in (("deferred", 1), ("failed", 3)):
        ref = source(database, status)
        with database() as conn:
            conn.execute(
                "INSERT INTO knowledge_learning(user_email,source_id,status,attempts) VALUES(%s,%s,%s,%s)",
                (OWNER, ref, status, attempts),
            )
    report = progress.snapshot(OWNER)
    assert report["learning"]["history"]["state"] == "waiting"
    assert not report["history_caught_up"]
    with database() as conn:
        conn.execute("UPDATE knowledge_learning SET status='done'")
    assert progress.snapshot(OWNER)["history_caught_up"]


def test_committed_learning_sets_success_time_failures_do_not(database, monkeypatch):
    jobs.initialize_owner(OWNER)
    for ident in ("successful", "failed"):
        ref = source(database, ident)
        with database() as conn:
            conn.execute(
                "INSERT INTO knowledge_learning(user_email,source_id) VALUES(%s,%s)", (OWNER, ref)
            )
    monkeypatch.setattr(jobs.intelligence_model, "analyze", lambda *a, **kw: ({}, []))
    assert jobs.process_once([OWNER])

    def fail(*a, **kw):
        raise ValueError("Synthetic failure")

    monkeypatch.setattr(jobs.intelligence_model, "analyze", fail)
    assert jobs.process_once([OWNER])
    with database() as conn:
        rows = conn.execute(
            "SELECT status,started_at,last_success_at FROM knowledge_learning ORDER BY source_id"
        ).fetchall()
    assert rows[0]["status"] == "done" and rows[0]["last_success_at"] is not None
    assert rows[1]["status"] == "failed" and rows[1]["last_success_at"] is None
    assert all(row["started_at"] is not None for row in rows)


def test_authenticated_api_exposes_same_snapshot_without_evidence(database, monkeypatch, caplog):
    from routes import intelligence as intelligence_routes
    from routes import knowledge as knowledge_routes

    jobs.initialize_owner(OWNER)
    source(database, "private-source")
    monkeypatch.setattr(intelligence_routes, "get_conn", database)
    app = FastAPI()
    app.include_router(knowledge_routes.create_knowledge_router())
    app.include_router(intelligence_routes.create_intelligence_router())
    # Other suites reload auth; override the exact dependencies bound to each router.
    for dependency in (knowledge_routes.get_current_user, intelligence_routes.get_current_user):
        app.dependency_overrides[dependency] = lambda: {"email": OWNER}
    with TestClient(app) as client:
        response = client.get("/mobile/knowledge/progress")
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        assert response.json()["details"]["learning"]["history"]["not_enqueued"] == 1
        assert client.get("/mobile/intelligence/status").json()["progress"]["history"]["total"] == 0
    caplog.set_level(logging.INFO)
    progress.log_snapshot(OWNER)
    assert "[learning] progress" in caplog.text and "lane=knowledge_history" in caplog.text
    assert '"not_enqueued": 1' in caplog.text
    assert "Private anonymous evidence" not in caplog.text + response.text
    assert "private-source" not in caplog.text + response.text
    assert "intelligence_sources" not in json.dumps(response.json())


def test_progress_heartbeat_runs_while_learning_and_summaries_are_blocked(monkeypatch):
    blocked = threading.Event()
    reported = threading.Event()
    monkeypatch.setattr(jobs, "_STOP", threading.Event())
    for name in ("_THREAD", "_SUMMARY_THREAD", "_PROGRESS_THREAD"):
        monkeypatch.setattr(jobs, name, None)
    monkeypatch.setattr(jobs, "_loop", lambda: blocked.wait(5))
    monkeypatch.setattr(jobs, "_summary_loop", lambda: blocked.wait(5))
    import intelligence_jobs

    monkeypatch.setattr(intelligence_jobs, "_owners", lambda: [OWNER])
    monkeypatch.setattr(progress, "log_snapshot", lambda owner: reported.set())
    try:
        jobs.start_worker()
        assert reported.wait(2)
        assert jobs.get_worker_status()["progress_worker_alive"]
        assert jobs.get_worker_status()["summary_worker_alive"]
    finally:
        blocked.set()
        jobs.stop_worker()
    assert not jobs.get_worker_status()["progress_worker_alive"]
