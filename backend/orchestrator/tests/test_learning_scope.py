# ruff: noqa: F811
"""Exact retry ownership, bounded history, and demand-driven original evidence."""

from datetime import timedelta

import pytest

import intelligence as store
import intelligence_jobs as jobs
import intelligence_model as model
import knowledge_demand
import knowledge_jobs
import learning_ownership
import learning_progress
import llm_admission
from tests.test_intelligence import FUTURE, OWNER, database, ingest, setup_owner  # noqa: F401
from tests.test_learning_batches import queue, states


def test_skipped_sources_commit_bounded_receipts_while_provider_is_busy(database, monkeypatch):
    setup_owner(database)
    monkeypatch.setattr(llm_admission, "_STATES", {})
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example.test/v1")
    llm_admission._state("https://models.example.test/v1").background = True
    with database() as conn:
        conn.execute("UPDATE intelligence_runs SET reviewed=TRUE")
        conn.execute(
            """INSERT INTO intelligence_sources(kind,entity_id,owner_email,fingerprint,payload,historical)
            SELECT 'event','removed-'||n,%s,'removed-'||n,
                '{"text":"Cancelled meeting.","deleted":true,"sent_at":"2026-01-03T12:00:00Z"}'::jsonb,FALSE
            FROM generate_series(1,205) n""",
            (OWNER,),
        )

    def unexpected_model(*args, **kwargs):
        pytest.fail("Deleted sources must drain without invoking the model")

    monkeypatch.setattr(model, "analyze", unexpected_model)
    assert jobs.process_once([OWNER])
    with database() as conn:
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"] == 192
        )
        conn.execute("UPDATE intelligence_runs SET next_attempt_at=NOW()")
    assert jobs.process_once([OWNER])
    with database() as conn:
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"] == 205
        )
        assert (
            conn.execute("SELECT status FROM intelligence_runs").fetchone()["status"] == "succeeded"
        )
        assert not conn.execute("SELECT 1 FROM intelligence_failures").fetchone()
        assert not conn.execute("SELECT 1 FROM intelligence_proposals").fetchone()


def reviewed_failure(database):
    setup_owner(database)
    for index in range(14):
        ingest(database, ident=f"message-{index}")
    with database() as conn:
        conn.execute(
            "INSERT INTO intelligence_processed(user_email,source_id,part) SELECT %s,source_id,0 FROM intelligence_sources",
            (OWNER,),
        )
        source = conn.execute(
            "SELECT source_id FROM intelligence_sources ORDER BY source_id DESC LIMIT 1"
        ).fetchone()["source_id"]
        conn.execute("UPDATE intelligence_runs SET status='succeeded',reviewed=TRUE")
        conn.execute(
            """INSERT INTO intelligence_failures(user_email,source_id,part,review,knowledge_only,error_code,next_attempt_at)
            VALUES(%s,%s,0,TRUE,FALSE,'invalid_evidence',NOW())""",
            (OWNER, source),
        )
    return source


def test_review_retry_selects_failed_part_even_outside_normal_lookback_and_drains(
    database, monkeypatch
):
    source = reviewed_failure(database)
    calls = []
    monkeypatch.setattr(
        model,
        "analyze",
        lambda owner, batch, **kw: calls.append([r["source_id"] for r in batch]) or ({}, []),
    )
    jobs.requeue_deferred(OWNER)
    assert jobs.process_once([OWNER])
    with database() as conn:
        conn.execute("UPDATE intelligence_runs SET next_attempt_at=NOW()")
    assert jobs.process_once([OWNER])  # Final drain after the retry turn.
    jobs.requeue_deferred(OWNER)
    assert not jobs.process_once([OWNER])
    assert calls == [[source]]
    with database() as conn:
        assert not conn.execute("SELECT 1 FROM intelligence_failures").fetchone()
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"] == 14
        )


def test_review_failure_budget_advances_on_the_exact_failed_part_and_pauses(database, monkeypatch):
    source = reviewed_failure(database)

    def fail(owner, batch, **kw):
        assert [r["source_id"] for r in batch] == [source]
        raise model.InvalidModelOutput("invalid_evidence")

    monkeypatch.setattr(model, "analyze", fail)
    for attempt in (2, 3):
        jobs.requeue_deferred(OWNER)
        assert jobs.process_once([OWNER])
        with database() as conn:
            conn.execute("UPDATE intelligence_runs SET next_attempt_at=NOW()")
        assert jobs.process_once([OWNER])
        with database() as conn:
            row = conn.execute("SELECT * FROM intelligence_failures").fetchone()
            assert row["attempts"] == attempt
            if attempt < 3:
                conn.execute("UPDATE intelligence_failures SET next_attempt_at=NOW()")
    jobs.requeue_deferred(OWNER)
    assert not jobs.process_once([OWNER])


def test_only_one_covering_run_wakes_and_new_work_precedes_recently_claimed_retry(database):
    reviewed_failure(database)
    with database() as conn:
        conn.execute(
            "INSERT INTO intelligence_runs(run_id,user_email,slot,cutoff,status,reviewed) VALUES('new',%s,'extra',%s,'succeeded',TRUE)",
            (OWNER, FUTURE + timedelta(days=1)),
        )
    jobs.requeue_deferred(OWNER)
    with database() as conn:
        rows = conn.execute("SELECT * FROM intelligence_runs WHERE status='pending'").fetchall()
        assert len(rows) == 1 and rows[0]["run_id"] == "new" and rows[0]["reviewed"]
        conn.execute("UPDATE intelligence_runs SET started_at=NOW() WHERE run_id='new'")
        conn.execute(
            "INSERT INTO intelligence_runs(run_id,user_email,slot,cutoff,status) VALUES('fresh',%s,'fresh',%s,'pending')",
            (OWNER, FUTURE + timedelta(days=2)),
        )
    assert jobs.claim_run([OWNER])["run_id"] == "fresh"


def test_six_month_scope_keeps_old_evidence_and_on_demand_retrieval_learns_it(
    database, monkeypatch
):
    monkeypatch.setenv("KNOWLEDGE_HISTORY_MONTHS", "6")
    setup_owner(database)
    with database() as conn:
        recent = conn.execute("SELECT (NOW()-INTERVAL '1 month')::text AS t").fetchone()["t"]
        old = conn.execute("SELECT (NOW()-INTERVAL '7 months')::text AS t").fetchone()["t"]
    old_ids = queue(database, 2, thread="older", sent=old)
    recent_ids = queue(database, 2, thread="recent", sent=recent)
    knowledge_jobs.initialize_owner(OWNER)
    report = learning_progress.snapshot(OWNER)
    assert report["history_policy"] == {"automatic_months": 6, "older_on_demand": True}
    assert report["learning"]["history"]["available_on_demand"] == 2
    assert report["learning"]["history"]["remaining"] == 2
    seen = []
    monkeypatch.setattr(
        model,
        "analyze",
        lambda owner, batch, **kw: (
            seen.append([r["source_id"] for r in batch if not r["supporting"]]) or ({}, [])
        ),
    )
    assert knowledge_jobs.process_once([OWNER])
    assert seen == [recent_ids]
    assert not knowledge_jobs.process_once([OWNER])
    with database() as conn:
        entity = conn.execute(
            "SELECT entity_id FROM intelligence_sources WHERE source_id=%s", (old_ids[0],)
        ).fetchone()["entity_id"]
    monkeypatch.setattr(knowledge_demand, "get_conn", database)
    assert (
        knowledge_demand.queue_retrieved(
            "wrong-owner@example.com", [{"kind": "conversation", "id": entity}]
        )
        == 0
    )
    assert knowledge_demand.queue_retrieved(OWNER, [{"kind": "conversation", "id": entity}]) == 1
    assert knowledge_demand.queue_retrieved(OWNER, [{"kind": "conversation", "id": entity}]) == 0
    assert knowledge_jobs.process_once([OWNER])
    assert seen[-1] == [old_ids[0]]
    rows = states(database)
    assert rows[old_ids[0]]["status"] == "done" and rows[old_ids[1]]["status"] == "pending"
    assert learning_progress.snapshot(OWNER)["learning"]["history"]["remaining"] == 0


def test_demand_does_not_revive_paused_ambiguous_or_command_owned_sources(database, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_HISTORY_MONTHS", "6")
    setup_owner(database)
    ids = queue(database, 3, thread="older", sent="2020-01-01T12:00:00+00:00")
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_learning SET status='failed',attempts=3 WHERE source_id=%s", (ids[0],)
        )
        conn.execute(
            "UPDATE knowledge_learning SET status='deferred' WHERE source_id=%s", (ids[1],)
        )
        conn.execute(
            'UPDATE intelligence_sources SET payload=payload||\'{"learning_owner":"contact"}\'::jsonb WHERE source_id=%s',
            (ids[2],),
        )
        rows = conn.execute(
            "SELECT entity_id FROM intelligence_sources ORDER BY source_id"
        ).fetchall()
    monkeypatch.setattr(knowledge_demand, "get_conn", database)
    assert (
        knowledge_demand.queue_retrieved(
            OWNER, [{"kind": "conversation", "id": r["entity_id"]} for r in rows]
        )
        == 0
    )


def test_large_sparse_episodes_remain_bounded_and_preserve_reduced_limits(database, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_MAX_BATCH_PARTS", raising=False)
    ids = queue(database, 55)
    claimed = knowledge_jobs.claim([OWNER], batched=True)
    assert len(claimed) == 48
    assert {r["source_id"] for r in claimed} == set(ids[:48])
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_learning SET status='pending',lease_token=NULL,lease_until=NULL,attempts=0,batch_limit=3"
        )
    assert len(knowledge_jobs.claim([OWNER], batched=True)) == 3


def test_full_finding_response_splits_before_committing_or_reconciling(database, monkeypatch):
    setup_owner(database)
    ingest(database, ident="a")
    ingest(database, ident="b")
    batch = store.next_batch(OWNER, FUTURE)
    calls = []

    def ask(*args, **kw):
        calls.append(1)
        return {"findings": [{}] * 20, "deferred_refs": []}

    monkeypatch.setattr(model, "_ask", ask)
    with pytest.raises(model.SourceBatchSplit) as exc:
        model.analyze(OWNER, batch, knowledge_only=True, durable_splits=True)
    assert exc.value.reason == "finding_capacity"
    assert len(calls) == 1


def test_new_history_defaults_are_six_months(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_HISTORY_MONTHS", raising=False)
    assert learning_ownership.history_months() == 6


def test_demand_recovers_expired_old_claim_but_never_steals_a_live_lease(database, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_HISTORY_MONTHS", "6")
    setup_owner(database)
    ids = queue(database, 2, thread="older", sent="2020-01-01T12:00:00+00:00")
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_learning SET status='running',attempts=1,lease_token='previous-worker',lease_until=NOW()-INTERVAL '1 minute' WHERE source_id=%s",
            (ids[0],),
        )
        conn.execute(
            "UPDATE knowledge_learning SET status='running',attempts=1,lease_token='live-worker',lease_until=NOW()+INTERVAL '5 minutes' WHERE source_id=%s",
            (ids[1],),
        )
        rows = conn.execute(
            "SELECT entity_id FROM intelligence_sources ORDER BY source_id"
        ).fetchall()
    monkeypatch.setattr(knowledge_demand, "get_conn", database)
    assert (
        knowledge_demand.queue_retrieved(
            OWNER, [{"kind": "conversation", "id": r["entity_id"]} for r in rows]
        )
        == 1
    )
    jobs_by_source = states(database)
    assert jobs_by_source[ids[0]]["status"] == "pending" and jobs_by_source[ids[0]]["on_demand"]
    assert jobs_by_source[ids[0]]["lease_token"] is None
    assert (
        jobs_by_source[ids[1]]["status"] == "running"
        and jobs_by_source[ids[1]]["lease_token"] == "live-worker"
    )
