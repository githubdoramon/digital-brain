# ruff: noqa: F811
"""Canonical historical receipts, bounded summary refreshes and durable outage deferral."""

from pathlib import Path

import pytest

import intelligence
import intelligence_jobs
import intelligence_model
import knowledge_jobs
import llm_admission
from tests.test_intelligence import OWNER, database, setup_owner  # noqa: F401
from tests.test_learning_progress import source


@pytest.mark.parametrize("database", ["pre_efficiency"], indirect=True)
def test_migration_reuses_receipts_preserves_waits_and_recovers_provider_failures(
    database, monkeypatch
):
    setup_owner(database)
    ids = [source(database, f"legacy-{i}", kind="message") for i in range(6)]
    knowledge_jobs.initialize_owner(OWNER)
    with database() as conn:
        conn.execute("UPDATE knowledge_learning SET next_attempt_at=NOW()-INTERVAL '1 hour'")
        conn.execute(
            "UPDATE knowledge_learning SET status='deferred' WHERE source_id=%s", (ids[1],)
        )
        conn.execute(
            "UPDATE knowledge_learning SET status='failed',attempts=3,error_code='model_unavailable' WHERE source_id=%s",
            (ids[3],),
        )
        conn.execute(
            "UPDATE knowledge_learning SET next_attempt_at=NOW()+INTERVAL '1 minute' WHERE source_id=%s",
            (ids[5],),
        )
        for ident in [ids[0], ids[1], ids[5]]:
            conn.execute(
                "INSERT INTO intelligence_processed(user_email,source_id) VALUES(%s,%s)",
                (OWNER, ident),
            )
        conn.execute(
            "INSERT INTO intelligence_failures(user_email,source_id,part,knowledge_only,attempts,error_code,next_attempt_at) VALUES(%s,%s,0,TRUE,3,'invalid_evidence',NULL)",
            (OWNER, ids[2]),
        )
        conn.execute(
            "INSERT INTO intelligence_runs(run_id,user_email,slot,cutoff,knowledge_only,status,lease_token,lease_until) VALUES('legacy',%s,'knowledge-backlog',NOW(),TRUE,'running','old',NOW()+INTERVAL '1 hour')",
            (OWNER,),
        )
        before_sources = conn.execute(
            "SELECT source_id,fingerprint,payload FROM intelligence_sources ORDER BY source_id"
        ).fetchall()
        conn.execute(
            (
                Path(__file__).resolve().parents[1] / "db_migrations/0086_learning_efficiency.sql"
            ).read_text()
        )
        rows = {row["source_id"]: row for row in conn.execute("SELECT * FROM knowledge_learning")}
        assert rows[ids[0]]["status"] == "done"
        assert rows[ids[1]]["status"] == "deferred"
        assert rows[ids[2]]["status"] == "failed" and rows[ids[2]]["attempts"] == 3
        assert rows[ids[3]]["status"] == "pending" and rows[ids[3]]["attempts"] == 0
        assert rows[ids[5]]["status"] == "pending"  # A newer revisit is not a duplicate receipt.
        run = conn.execute("SELECT * FROM intelligence_runs WHERE run_id='legacy'").fetchone()
        assert run["status"] == "succeeded" and run["lease_token"] is None
        assert (
            conn.execute(
                "SELECT source_id,fingerprint,payload FROM intelligence_sources ORDER BY source_id"
            ).fetchall()
            == before_sources
        )
    with pytest.raises(ValueError, match="lease_expired"):
        intelligence.apply_batch(OWNER, {"run_id": "legacy", "lease_token": "old"}, [], {}, [])
    # The preservation assertions above use 0086; runtime checks need the latest schema.
    with database() as conn:
        root = Path(__file__).resolve().parents[1] / "db_migrations"
        conn.execute((root / "0087_background_learning_recovery.sql").read_text())
        conn.execute((root / "0089_learning_scope.sql").read_text())
    calls = []

    def analyze(owner, batch, **kwargs):
        calls.extend(row["source_id"] for row in batch if not row.get("supporting"))
        return {}, []

    monkeypatch.setattr(intelligence_model, "analyze", analyze)
    assert knowledge_jobs.process_once([OWNER])
    assert calls == [ids[3]]
    assert not intelligence_jobs.claim_run([OWNER])["knowledge_only"]


def test_provider_deferrals_do_not_exhaust_any_part_retry_budget(database, monkeypatch):
    setup_owner(database)
    source(database, "historical", kind="chat")
    knowledge_jobs.initialize_owner(OWNER)

    def unavailable(*args, **kwargs):
        raise llm_admission.BackgroundDeferred("provider_unavailable", 30)

    monkeypatch.setattr(intelligence_model, "analyze", unavailable)
    for _ in range(5):
        assert knowledge_jobs.process_once([OWNER])
        with database() as conn:
            row = conn.execute("SELECT * FROM knowledge_learning").fetchone()
            assert row["status"] == "pending" and row["attempts"] == 0
            assert row["lease_token"] is None and row["last_success_at"] is None
            conn.execute("UPDATE knowledge_learning SET next_attempt_at=NOW()")
    monkeypatch.setattr(intelligence_model, "analyze", lambda *a, **k: ({}, []))
    assert knowledge_jobs.process_once([OWNER])
    with database() as conn:
        assert conn.execute("SELECT status FROM knowledge_learning").fetchone()["status"] == "done"


def test_history_summary_coalesces_without_pushing_back_its_first_dirty_boundary(
    database, monkeypatch
):
    from tests.test_knowledge import save

    save(database)
    with database() as conn:
        conn.execute("UPDATE knowledge_summaries SET rendered_version=version,summary='Before'")
        conn.execute("SELECT set_config('brain.learning_history','on',TRUE)")
        conn.execute(
            "UPDATE intelligence_knowledge SET statement='Alex received the quote yesterday.'"
        )
        first = conn.execute("SELECT * FROM knowledge_summaries").fetchone()
        assert first["history_dirty"] and first["dirty_since"] is not None
    with database() as conn:
        conn.execute("SELECT set_config('brain.learning_history','on',TRUE)")
        conn.execute(
            "UPDATE intelligence_knowledge SET statement='Alex received the complete quote yesterday.'"
        )
        assert (
            conn.execute("SELECT dirty_since FROM knowledge_summaries").fetchone()["dirty_since"]
            == first["dirty_since"]
        )
        conn.execute("UPDATE knowledge_summaries SET next_attempt_at=NOW()")
    assert not knowledge_jobs.summarize_once([OWNER])
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_summaries SET dirty_since=NOW()-INTERVAL '16 minutes',next_attempt_at=NOW()+INTERVAL '2 seconds'"
        )
    calls = []
    monkeypatch.setattr(
        knowledge_jobs,
        "_summary_response",
        lambda *a: calls.append(1) or {"summary": "Alex received the complete quote."},
    )
    assert knowledge_jobs.summarize_once([OWNER])
    assert calls == [1]
    with database() as conn:
        current = conn.execute("SELECT * FROM knowledge_summaries").fetchone()
        assert current["rendered_version"] == current["version"]
        assert not current["history_dirty"] and current["dirty_since"] is None


def test_current_change_overrides_history_summary_delay(database, monkeypatch):
    from tests.test_knowledge import save

    save(database)
    with database() as conn:
        conn.execute("UPDATE knowledge_summaries SET rendered_version=version")
        conn.execute("SELECT set_config('brain.learning_history','on',TRUE)")
        conn.execute("UPDATE intelligence_knowledge SET statement='The quote arrived.'")
    with database() as conn:
        conn.execute("UPDATE intelligence_knowledge SET statement='The quote arrived today.'")
        row = conn.execute("SELECT * FROM knowledge_summaries").fetchone()
        assert not row["history_dirty"]
        conn.execute("UPDATE knowledge_summaries SET next_attempt_at=NOW()")
    monkeypatch.setattr(
        knowledge_jobs, "_summary_response", lambda *a: {"summary": "The quote arrived today."}
    )
    assert knowledge_jobs.summarize_once([OWNER])
