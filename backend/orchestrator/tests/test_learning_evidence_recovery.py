# ruff: noqa: F811
"""Bounded summary fallback and independent recovery of invalid evidence parts."""

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import intelligence_model as model
import knowledge_jobs as jobs
import llm_helpers
from tests.test_intelligence import OWNER, database  # noqa: F401
from tests.test_knowledge import save
from tests.test_learning_batches import queue, states


@pytest.mark.parametrize("knowledge_only", [False, True])
def test_wire_citations_require_null_quotes_and_explicit_span_indices(knowledge_only):
    evidence = model.request_schema(knowledge_only)["$defs"]["Evidence"]
    validator = Draft202012Validator(evidence)
    assert validator.is_valid({"ref": "1:0", "span_index": 0, "quote": None})
    for citation in [
        {"ref": "1:0", "span_index": None, "quote": "Source statement."},
        {"ref": "1:0", "span_index": 0, "quote": "Corrected statement."},
        {"ref": "1:0", "quote": None},
    ]:
        assert not validator.is_valid(citation)


def test_summary_retry_disables_reasoning_and_reduces_input(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_helpers, "call_llm_json", lambda *a, **k: calls.append((a, k)))
    facts = [{"statement": "Mira prefers quiet keyboards."}, {"statement": "Mira uses Linux."}]
    jobs._summary_response(facts, {}, 0)
    jobs._summary_response(facts, {}, 1)
    assert all(options["reasoning_effort"] == "none" for _, options in calls)
    assert "Linux" in calls[0][0][0] and "Linux" not in calls[1][0][0]


@pytest.mark.parametrize("recovered", [True, False])
def test_exhausted_summary_gets_one_retry_then_publishes_without_endless_requeue(
    database, monkeypatch, recovered
):
    save(database, inferred=True, inference_reason="Repeated direct observations.")
    with database() as conn:
        conn.execute("UPDATE knowledge_summaries SET next_attempt_at=NOW()")
    attempts = []

    def response(facts, schema, attempt):
        attempts.append(attempt)
        if attempt and recovered:
            return {"summary": "Inferred: Mira prefers quiet keyboards."}
        raise llm_helpers.LLMOutputExhaustedError("output_exhausted")

    monkeypatch.setattr(jobs, "_summary_response", response)
    assert jobs.summarize_once([OWNER])
    assert attempts == [0, 1]
    with database() as conn:
        row = conn.execute("SELECT * FROM knowledge_summaries").fetchone()
        assert row["rendered_version"] == row["version"] and row["error_code"] is None
        assert row["summary"].startswith("Inferred:")
    assert not jobs.summarize_once([OWNER])


def test_invalid_evidence_splits_before_retrying_and_only_bad_part_pauses(database, monkeypatch):
    ids = queue(database, 4)
    bad = ids[1]
    calls = []

    def analyze(owner, batch, **kwargs):
        primary = [r["source_id"] for r in batch if not r.get("supporting")]
        calls.append(primary)
        if bad in primary:
            raise model.InvalidModelOutput("invalid_evidence")
        return {}, []

    monkeypatch.setattr(model, "_analyze", analyze)
    assert jobs.process_once([OWNER])
    assert calls == [ids]  # No second full-batch repair before splitting.
    assert all(r["attempts"] == 0 and r["batch_limit"] == 2 for r in states(database).values())
    for _ in range(15):
        if not jobs.process_once([OWNER]):
            with database() as conn:
                conn.execute("UPDATE knowledge_learning SET next_attempt_at=NOW() WHERE attempts<3")
    rows = states(database)
    assert rows[bad]["attempts"] == 3 and rows[bad]["status"] == "failed"
    assert rows[bad]["batch_limit"] == 1
    assert all(rows[i]["status"] == "done" and rows[i]["attempts"] == 1 for i in ids if i != bad)


def test_recovery_migration_requeues_48_parts_and_preserves_other_failure_classes(database):
    ids = queue(database, 52)
    with database() as conn:
        conn.execute(
            """UPDATE knowledge_learning SET status='failed',attempts=3,
            error_code='invalid_evidence',batch_limit=48"""
        )
        conn.execute(
            "UPDATE knowledge_learning SET error_code='invalid_schema' WHERE source_id=%s",
            (ids[48],),
        )
        conn.execute("UPDATE knowledge_learning SET batch_limit=1 WHERE source_id=%s", (ids[49],))
        conn.execute("UPDATE knowledge_learning SET status='done' WHERE source_id=%s", (ids[50],))
        conn.execute(
            "UPDATE knowledge_learning SET lease_until=NOW()+INTERVAL '1 hour' WHERE source_id=%s",
            (ids[51],),
        )
        migration = Path(__file__).parents[1] / "db_migrations/0090_learning_evidence_recovery.sql"
        conn.execute(migration.read_text())
    rows = states(database)
    assert all(
        rows[i]["status"] == "pending" and rows[i]["attempts"] == 0 and rows[i]["batch_limit"] == 12
        for i in ids[:48]
    )
    assert rows[ids[48]]["error_code"] == "invalid_schema"
    assert rows[ids[49]]["attempts"] == 3
    assert rows[ids[50]]["status"] == "done"
    assert rows[ids[51]]["attempts"] == 3
