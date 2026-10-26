# ruff: noqa: F811
"""Durable isolation, rejection diagnostics and bounded existing-data recovery."""

from pathlib import Path

import pytest

import intelligence as store
import intelligence_jobs as scheduled
import intelligence_model as model
import knowledge_jobs
from learning_failures import rejection_details
from tests.test_intelligence import FUTURE, OWNER, database, ingest, setup_owner  # noqa: F401
from tests.test_learning_batches import queue, states


@pytest.mark.parametrize("code", ["invalid_schema", "invalid_evidence", "invalid_contact_change"])
def test_scheduled_bad_source_isolated_without_pausing_healthy_sources(database, monkeypatch, code):
    setup_owner(database)
    for i in range(4):
        ingest(database, ident=str(i), body=f"Sample source {i}")
    bad = store.next_batch(OWNER, FUTURE)[1]["source_id"]
    calls = []

    def analyze(owner, batch, **kwargs):
        assert kwargs.get("durable_splits") is True
        calls.append([row["source_id"] for row in batch])
        if any(row["source_id"] == bad for row in batch):
            raise model.InvalidModelOutput(
                code, {"reason": "synthetic_rejection", "field": "evidence"}
            )
        return {}, []

    monkeypatch.setattr(model, "analyze", analyze)
    for _ in range(25):
        with database() as conn:
            conn.execute("UPDATE intelligence_runs SET next_attempt_at=NOW()")
            conn.execute("UPDATE intelligence_failures SET next_attempt_at=NOW() WHERE attempts<3")
        scheduled.requeue_deferred(OWNER)
        run = scheduled.claim_run({OWNER})
        if run:
            scheduled._process_run(run)
    with database() as conn:
        failures = list(conn.execute("SELECT * FROM intelligence_failures"))
        assert len(failures) == 1
        failure = failures[0]
        assert failure["source_id"] == bad and failure["attempts"] == 3
        assert failure["batch_limit"] == 1 and failure["next_attempt_at"] is None
        assert failure["error_details"]["details"]["reason"] == "synthetic_rejection"
        assert failure["failed_at"] is not None
        assert conn.execute("SELECT count(*) AS n FROM intelligence_processed").fetchone()["n"] == 3
    assert any(len(call) > 1 for call in calls) and [bad] in calls


@pytest.mark.parametrize("code", ["invalid_schema", "invalid_contact_change"])
def test_knowledge_split_preserves_rejection_details(database, monkeypatch, code):
    ids = queue(database, 2)

    def reject(*args, **kwargs):
        raise model.InvalidModelOutput(code, {"reason": "synthetic_rejection"})

    monkeypatch.setattr(model, "analyze", reject)
    assert knowledge_jobs.process_once([OWNER])
    rows = states(database)
    assert all(row["attempts"] == 0 and row["batch_limit"] == 1 for row in rows.values())
    assert knowledge_jobs.process_once([OWNER])
    rows = states(database)
    failed = rows[ids[0]]
    assert failed["status"] == "failed" and failed["attempts"] == 1
    assert failed["error_details"]["details"]["reason"] == "synthetic_rejection"
    assert failed["failed_at"]


def test_failure_details_redact_credentials_and_bound_size(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_TEST_KEY", "synthetic-secret-value")
    error = model.InvalidModelOutput(
        "invalid_schema", {"api_key": "private", "text": "synthetic-secret-value"}
    )
    details = rejection_details(error)
    assert details["details"] == {"api_key": "[redacted]", "text": "[redacted]"}
    error.details = {"text": "x" * 20000}
    details = rejection_details(error)
    assert details["truncated"] and len(details["preview"]) <= 11000


def test_failure_export_is_read_only_owner_scoped_and_includes_details(database, monkeypatch):
    from scripts import inspect_learning_failures as inspector

    monkeypatch.setattr(inspector, "get_conn", database)
    ids = queue(database, 1)
    with database() as conn:
        conn.execute("""UPDATE knowledge_learning SET status='failed',attempts=3,
            error_code='invalid_schema',error_details='{"reason":"synthetic_rejection"}',failed_at=NOW()""")
    before = states(database)
    result = inspector.snapshot(OWNER)
    assert len(result["failures"]) == 1
    assert result["failures"][0]["source_id"] == ids[0]
    assert result["failures"][0]["error_details"]["reason"] == "synthetic_rejection"
    assert inspector.snapshot("someone-else@example.com")["failures"] == []
    assert states(database) == before


def test_recovery_migration_retries_only_paused_validation_work(database):
    ids = queue(database, 4)
    path = Path(__file__).parents[1] / "db_migrations/0093_learning_failure_isolation.sql"
    recovery = path.read_text().split("-- One-time recovery", 1)[1]
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_learning SET status='failed',attempts=3,error_code='invalid_schema'"
        )
        conn.execute("UPDATE knowledge_learning SET status='done' WHERE source_id=%s", (ids[1],))
        conn.execute(
            "UPDATE knowledge_learning SET error_code='model_timeout' WHERE source_id=%s", (ids[2],)
        )
        conn.execute(
            "UPDATE knowledge_learning SET lease_until=NOW()+INTERVAL '1 hour' WHERE source_id=%s",
            (ids[3],),
        )
        # Execute the recovery statements against the post-migration schema.
        conn.execute("-- One-time recovery" + recovery)
    rows = states(database)
    assert rows[ids[0]]["status"] == "pending" and rows[ids[0]]["batch_limit"] == 1
    assert rows[ids[1]]["status"] == "done"
    assert rows[ids[2]]["status"] == "failed"
    assert rows[ids[3]]["status"] == "failed"


def test_scheduled_recovery_migration_wakes_covering_run_and_preserves_other_failures(database):
    setup_owner(database)
    for i in range(3):
        ingest(database, ident=str(i), body=f"Sample recovery source {i}")
    batch = store.next_batch(OWNER, FUTURE)
    path = Path(__file__).parents[1] / "db_migrations/0093_learning_failure_isolation.sql"
    recovery = "-- One-time recovery" + path.read_text().split("-- One-time recovery", 1)[1]
    with database() as conn:
        for row, code in zip(batch, ["invalid_schema", "invalid_evidence", "model_timeout"]):
            conn.execute(
                """INSERT INTO intelligence_failures
                (user_email,source_id,part,knowledge_only,attempts,error_code,next_attempt_at)
                VALUES (%s,%s,0,FALSE,3,%s,NULL)""",
                (OWNER, row["source_id"], code),
            )
        conn.execute("UPDATE intelligence_runs SET status='succeeded'")
        conn.execute(recovery)
        failures = list(conn.execute("SELECT * FROM intelligence_failures ORDER BY source_id"))
    assert all(
        row["attempts"] == 0 and row["batch_limit"] == 1 and row["next_attempt_at"]
        for row in failures[:2]
    )
    assert failures[2]["attempts"] == 3 and failures[2]["next_attempt_at"] is None
    scheduled.requeue_deferred(OWNER)
    assert scheduled.claim_run({OWNER}) is not None
    assert len(store.next_batch(OWNER, FUTURE)) == 1
