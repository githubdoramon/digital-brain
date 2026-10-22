# ruff: noqa: F811
"""Historical batches and short renewable leases on disposable PostgreSQL."""

import threading
from datetime import timedelta

import pytest

import intelligence
import intelligence_jobs
import job_leases
import knowledge
import knowledge_batching
import knowledge_jobs as jobs
from tests.test_intelligence import (  # noqa: F401
    OTHER,
    OWNER,
    database,
    finding,
    ingest,
    setup_owner,
)


def queue(
    database,
    count=3,
    *,
    owner=OWNER,
    thread="archive",
    text="A small message.",
    historical=True,
    kind="chat",
    sent="2025-12-01T12:00:00+00:00",
):
    ids = []
    with database() as conn:
        for index in range(count):
            payload = {
                "text": f"{index}: {text}",
                "sent_at": sent,
                "thread_id" if kind == "chat" else "conversation_id": thread,
            }
            row = conn.execute(
                """INSERT INTO intelligence_sources(kind,entity_id,owner_email,fingerprint,payload,historical)
                VALUES(%s,%s,%s,%s,%s,%s) RETURNING source_id""",
                (
                    kind,
                    f"{owner}-{thread}-{index}-{sent}-{historical}",
                    owner,
                    f"{index}-{thread}-{sent}-{historical}",
                    intelligence.json_value(payload),
                    historical,
                ),
            ).fetchone()
            ids.append(row["source_id"])
            conn.execute(
                "INSERT INTO knowledge_learning(user_email,source_id,historical) VALUES(%s,%s,%s)",
                (owner, row["source_id"], historical),
            )
    return ids


def states(database):
    with database() as conn:
        return {row["source_id"]: row for row in conn.execute("SELECT * FROM knowledge_learning")}


@pytest.mark.parametrize("kind", ["chat", "message"])
def test_tiny_history_uses_one_analysis_and_individual_receipts(database, monkeypatch, kind):
    ids = queue(database, 8, kind=kind)
    calls = []

    def analyze(owner, batch, **kwargs):
        calls.append(batch)
        assert owner == OWNER and kwargs == {"knowledge_only": True, "durable_splits": True}
        assert [row["source_id"] for row in batch if not row["supporting"]] == ids
        assert all(not row["actionable"] for row in batch)
        return {"deferred_refs": [f"{ids[2]}:0"]}, []

    monkeypatch.setattr(jobs.intelligence_model, "analyze", analyze)
    assert jobs.process_once([OWNER])
    assert not jobs.process_once([OWNER])
    assert len(calls) == 1
    rows = states(database)
    assert [rows[ident]["status"] for ident in ids] == ["done", "done", "deferred", *["done"] * 5]
    assert all(rows[ident]["lease_token"] is None for ident in ids)
    with database() as conn:
        assert (
            conn.execute("SELECT current_streak FROM knowledge_backfills").fetchone()[
                "current_streak"
            ]
            == 0
        )


def test_batch_isolates_owner_conversation_kind_and_current_lane(database):
    ids = queue(database, 3)
    queue(database, 2, owner=OTHER)
    queue(database, 2, thread="different")
    queue(database, 2, kind="message")
    queue(database, 2, historical=False)
    # Existing fairness still prefers current work for at most two turns.
    for _ in range(2):
        current = jobs.claim([OWNER], batched=True)
        assert len(current) == 1 and not current[0]["historical"]
    batch = jobs.claim([OWNER], batched=True)
    assert [row["source_id"] for row in batch] == ids
    assert jobs.claim([OTHER], batched=True)[0]["user_email"] == OTHER


def test_batches_bound_count_text_and_known_time_span(database):
    ids = queue(database, 30)
    assert len(jobs.claim([OWNER], batched=True)) == knowledge_batching.max_parts()
    assert len(jobs.claim([OWNER], batched=True)) == knowledge_batching.max_parts()
    assert [row["source_id"] for row in jobs.claim([OWNER], batched=True)] == ids[24:]
    large = queue(database, 5, thread="long", text="x" * 4900)
    batch = jobs.claim([OWNER], batched=True)
    assert [row["source_id"] for row in batch] == large[:3]
    payload = knowledge_batching.sources(batch, jobs.source_batch)
    primary = [row for row in payload if not row["supporting"]]
    assert sum(len(row["text"]) for row in primary) <= knowledge_batching.MAX_TEXT_CHARS
    assert all(len(row["text"]) > 4900 for row in primary)
    assert len([row for row in payload if row["supporting"]]) <= 6
    # Keep a later episode out of the anchor's batch.
    queue(database, 1, thread="dated", sent="2025-12-01T12:00:00+00:00")
    queue(database, 1, thread="dated", sent="2025-12-03T12:00:00+00:00")
    jobs.claim([OWNER], batched=True)  # Remaining long parts.
    assert len(jobs.claim([OWNER], batched=True)) == 1


def test_failure_retries_all_parts_without_advancing_receipts(database, monkeypatch):
    ids = queue(database, 4)
    monkeypatch.setattr(
        jobs.intelligence_model,
        "analyze",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    assert jobs.process_once([OWNER])
    rows = states(database)
    assert all(
        rows[ident]["status"] == "failed" and rows[ident]["last_success_at"] is None
        for ident in ids
    )
    assert not jobs.process_once([OWNER])  # Respect the existing retry backoff.
    with database() as conn:
        conn.execute("UPDATE knowledge_learning SET next_attempt_at=NOW()")
    monkeypatch.setattr(jobs.intelligence_model, "analyze", lambda *a, **k: ({}, []))
    assert jobs.process_once([OWNER])
    assert all(
        row["status"] == "done" and row["attempts"] == 2 for row in states(database).values()
    )


def test_lost_part_lease_discards_entire_batch_and_can_recover(database, monkeypatch):
    ids = queue(database, 3)

    def steal(*args, **kwargs):
        with database() as conn:
            conn.execute(
                "UPDATE knowledge_learning SET lease_token='another-worker' WHERE source_id=%s",
                (ids[1],),
            )
        return {}, []

    monkeypatch.setattr(jobs.intelligence_model, "analyze", steal)
    assert jobs.process_once([OWNER])
    assert all(row["last_success_at"] is None for row in states(database).values())
    assert all(row["status"] == "running" for row in states(database).values())
    with database() as conn:
        conn.execute("UPDATE knowledge_learning SET lease_until=NOW()-INTERVAL '1 second'")
    monkeypatch.setattr(jobs.intelligence_model, "analyze", lambda *a, **k: ({}, []))
    assert jobs.process_once([OWNER])
    assert all(row["status"] == "done" for row in states(database).values())


def test_delayed_and_exhausted_parts_are_not_consumed_by_batch(database):
    ids = queue(database, 5)
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_learning SET next_attempt_at=NOW()+INTERVAL '1 hour' WHERE source_id=%s",
            (ids[1],),
        )
        conn.execute(
            "UPDATE knowledge_learning SET status='failed',attempts=3 WHERE source_id=%s", (ids[2],)
        )
        conn.execute(
            "UPDATE knowledge_learning SET status='deferred' WHERE source_id=%s", (ids[3],)
        )
    assert [row["source_id"] for row in jobs.claim([OWNER], batched=True)] == [ids[0], ids[4]]


def test_learning_lease_is_short_renewable_and_old_worker_cannot_finish(database):
    queue(database)
    claimed = jobs.claim([OWNER], batched=True)
    with database() as conn:
        remaining = conn.execute(
            "SELECT min(lease_until-NOW()) AS remaining FROM knowledge_learning"
        ).fetchone()["remaining"]
        assert timedelta(seconds=100) < remaining <= timedelta(seconds=job_leases.LEASE_SECONDS)
        conn.execute("UPDATE knowledge_learning SET lease_until=NOW()-INTERVAL '1 second'")
    replacement = jobs.claim([OWNER], batched=True)
    assert replacement[0]["lease_token"] != claimed[0]["lease_token"]
    jobs._finish(claimed[0], "failed", "old_worker")
    assert all(
        row["status"] == "running" and row["error_code"] is None
        for row in states(database).values()
    )


def test_intelligence_restart_recovers_short_lease_without_stealing_live_run(database):
    setup_owner(database)
    with database() as conn:
        conn.execute("DELETE FROM intelligence_runs WHERE user_email=%s", (OWNER,))
        conn.execute(
            "INSERT INTO intelligence_runs(run_id,user_email,slot,knowledge_only,cutoff) VALUES('history',%s,'scheduled',FALSE,NOW())",
            (OWNER,),
        )
    old = intelligence_jobs.claim_run([OWNER])
    assert intelligence_jobs.claim_run([OWNER]) is None
    with database() as conn:
        remaining = conn.execute(
            "SELECT lease_until-NOW() AS remaining FROM intelligence_runs WHERE run_id='history'"
        ).fetchone()["remaining"]
        assert timedelta(seconds=100) < remaining <= timedelta(seconds=job_leases.LEASE_SECONDS)
        conn.execute(
            "UPDATE intelligence_runs SET lease_until=NOW()-INTERVAL '1 second' WHERE run_id='history'"
        )
    new = intelligence_jobs.claim_run([OWNER])
    assert new["lease_token"] != old["lease_token"]
    intelligence_jobs.finish_run(old, failed=True)
    with database() as conn:
        row = conn.execute("SELECT * FROM intelligence_runs WHERE run_id='history'").fetchone()
        assert row["status"] == "running" and row["lease_token"] == new["lease_token"]


def test_batch_preserves_cross_message_evidence_in_committed_knowledge(database, monkeypatch):
    ingest(database, ident="one", body="I sent the complete quote.")
    ingest(database, ident="two", body="Alex confirmed receipt of that quote.")
    jobs.initialize_owner(OWNER)
    calls = []

    def extract(owner, batch, **kwargs):
        primary = [row for row in batch if not row["supporting"]]
        assert len(primary) == 2
        calls.append(primary)
        candidate = finding(primary[0])
        candidate["evidence"] = [{"ref": row["ref"], "quote": row["text"]} for row in primary]
        context = intelligence.context_for(owner, batch)
        return context, intelligence.validate_findings({"findings": [candidate]}, batch, context)

    monkeypatch.setattr(jobs.intelligence_model, "analyze", extract)
    assert jobs.process_once([OWNER])
    assert len(calls) == 1
    facts = knowledge.list_items(OWNER)
    assert len(facts) == 1 and len(facts[0]["evidence"]) == 2
    assert all(row["status"] == "done" for row in states(database).values())


def test_independent_heartbeat_renews_every_part_during_blocked_analysis(database, monkeypatch):
    queue(database, 4)
    renewed = threading.Event()

    def heartbeat(renew):
        def tracked():
            result = renew()
            renewed.set()
            return result

        return job_leases.lease_heartbeat(tracked, interval=0.01)

    monkeypatch.setattr(jobs, "lease_heartbeat", heartbeat)

    def analyze(*args, **kwargs):
        with database() as conn:
            conn.execute("UPDATE knowledge_learning SET lease_until=NOW()+INTERVAL '10 seconds'")
        renewed.clear()
        assert renewed.wait(3)
        with database() as conn:
            minimum = conn.execute(
                "SELECT min(lease_until-NOW()) AS remaining FROM knowledge_learning"
            ).fetchone()["remaining"]
            assert minimum > timedelta(seconds=100)
        assert jobs.claim([OWNER], batched=True) is None
        return {}, []

    monkeypatch.setattr(jobs.intelligence_model, "analyze", analyze)
    assert jobs.process_once([OWNER])
    assert all(row["status"] == "done" for row in states(database).values())
