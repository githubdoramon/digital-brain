# ruff: noqa: F811
"""Continuous new arrivals cannot starve eligible historical extraction."""

import intelligence
import intelligence_jobs
import intelligence_model
import knowledge_jobs
import learning_fairness
import learning_progress
from tests.test_intelligence import (  # noqa: F401
    FUTURE,
    OTHER,
    OWNER,
    database,
    ingest,
    setup_owner,
)
from tests.test_learning_progress import source


def queue(database, name, *, owner=OWNER, historical=False):
    ident = source(
        database,
        name,
        owner=owner,
        historical=historical,
        sent="2025-12-01T12:00:00+00:00" if historical else intelligence.utcnow().isoformat(),
    )
    with database() as conn:
        conn.execute(
            "INSERT INTO knowledge_learning(user_email,source_id,historical) VALUES(%s,%s,%s)",
            (owner, ident, historical),
        )
    return ident


def release(database, job):
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_learning SET status='done',lease_until=NULL WHERE user_email=%s AND source_id=%s",
            (job["user_email"], job["source_id"]),
        )


def test_knowledge_history_gets_every_third_turn_during_continuous_arrivals(database):
    knowledge_jobs.initialize_owner(OWNER)
    for i in range(3):
        queue(database, f"history-{i}", historical=True)
    lanes = []
    for i in range(9):
        queue(database, f"new-{i}")
        job = knowledge_jobs.claim([OWNER])
        lanes.append(job["historical"])
        release(database, job)
        if i == 1:
            # It is database state, not an in-process counter, that forces the next lane.
            with database() as conn:
                assert (
                    conn.execute(
                        "SELECT current_streak FROM knowledge_backfills WHERE user_email=%s",
                        (OWNER,),
                    ).fetchone()["current_streak"]
                    == 2
                )
    assert lanes == [False, False, True] * 3
    report = learning_progress.snapshot(OWNER)
    assert report["fairness"]["knowledge"]["last_history_claimed_at"] is not None
    assert report["fairness"]["max_current_turns"] == 2


def test_history_backoff_is_respected_then_eligible_history_is_immediate(database):
    knowledge_jobs.initialize_owner(OWNER)
    history = queue(database, "history", historical=True)
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_learning SET status='failed',attempts=1,next_attempt_at=NOW()+INTERVAL '5 minutes' WHERE source_id=%s",
            (history,),
        )
    for i in range(5):
        queue(database, f"new-{i}")
        job = knowledge_jobs.claim([OWNER])
        assert not job["historical"]
        release(database, job)
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_learning SET next_attempt_at=NOW() WHERE source_id=%s", (history,)
        )
    queue(database, "one-more-new")
    assert knowledge_jobs.claim([OWNER])["source_id"] == history


def test_knowledge_owners_take_turns_without_sharing_fairness_counters(database):
    for owner in (OWNER, OTHER):
        knowledge_jobs.initialize_owner(owner)
        for i in range(3):
            queue(database, f"{owner}-new-{i}", owner=owner)
        queue(database, f"{owner}-history", owner=owner, historical=True)
    claimed = []
    for _ in range(6):
        job = knowledge_jobs.claim([OWNER, OTHER])
        claimed.append((job["user_email"], job["historical"]))
        release(database, job)
    for owner in (OWNER, OTHER):
        assert [history for who, history in claimed if who == owner] == [False, False, True]


def test_intelligence_history_gets_a_turn_while_scheduled_runs_keep_arriving(database):
    setup_owner(database)
    with database() as conn:
        conn.execute(
            "INSERT INTO intelligence_runs(run_id,user_email,slot,cutoff) VALUES('current',%s,'current',%s)",
            (OWNER, FUTURE),
        )
    lanes = []
    for i in range(9):
        run = intelligence_jobs.claim_run([OWNER])
        lanes.append(run["knowledge_only"])
        intelligence_jobs.finish_run(run, more=True)
        with database() as conn:
            conn.execute("UPDATE intelligence_runs SET next_attempt_at=NOW()")
            conn.execute(
                "INSERT INTO intelligence_runs(run_id,user_email,slot,cutoff) VALUES(%s,%s,%s,%s)",
                (f"new-{i}", OWNER, f"new-{i}", FUTURE),
            )
    assert lanes == [False, False, True] * 3
    report = learning_progress.snapshot(OWNER)
    assert report["fairness"]["intelligence"]["last_history_claimed_at"] is not None


def test_intelligence_yields_after_one_model_batch(database, monkeypatch):
    setup_owner(database)
    for i in range(2):
        ingest(database, ident=str(i))
    with database() as conn:
        conn.execute("UPDATE intelligence_runs SET knowledge_only=FALSE,cutoff=%s", (FUTURE,))
    monkeypatch.setattr(intelligence, "BATCH_SIZE", 1)
    calls = []

    def analyze(*args, **kwargs):
        calls.append(1)
        return {}, []

    monkeypatch.setattr(intelligence_model, "analyze", analyze)
    assert intelligence_jobs.process_once([OWNER])
    assert len(calls) == learning_fairness.INTELLIGENCE_BATCHES_PER_TURN == 1
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"] == 1
        assert (
            conn.execute("SELECT status FROM intelligence_runs").fetchone()["status"] == "pending"
        )


def test_expired_historical_run_is_reclaimed_when_its_turn_arrives(database):
    setup_owner(database)
    with database() as conn:
        conn.execute("UPDATE intelligence_state SET current_streak=2 WHERE user_email=%s", (OWNER,))
        conn.execute(
            "UPDATE intelligence_runs SET status='running',lease_token='old',lease_until=NOW()-INTERVAL '1 second'"
        )
        conn.execute(
            "INSERT INTO intelligence_runs(run_id,user_email,slot,cutoff) VALUES('current',%s,'current',%s)",
            (OWNER, FUTURE),
        )
    run = intelligence_jobs.claim_run([OWNER])
    assert run["knowledge_only"] and run["lease_token"] != "old"
    assert intelligence_jobs.claim_run([OWNER]) is None
