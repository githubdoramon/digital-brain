# ruff: noqa: F811
"""Generated preparation is retrievable context, never independent learning evidence."""

from pathlib import Path

import pytest

import intelligence
import intelligence_model
import knowledge_jobs
from evidence_provenance import LEGACY_PREPARATION_SIGNATURE, is_app_generated
from learning_failures import rejection_details
from llm_helpers import LLMDegenerateOutputError
from tests.test_intelligence import OWNER, database, setup_owner  # noqa: F401


@pytest.mark.parametrize(
    "reason", ["whitespace_loop", "trailing_output", "response_size", "repeated_syntax"]
)
def test_exact_output_guard_reason_survives_durable_failure_wrapping(reason):
    error = LLMDegenerateOutputError(reason)
    assert rejection_details(error)["details"] == {"reason": reason}
    wrapped = intelligence_model.SourceBatchSplit(1, error.code)
    wrapped.__cause__ = error
    assert rejection_details(wrapped)["details"] == {"reason": reason}


def test_legacy_preparation_and_structured_generated_documents_are_not_eligible():
    rows = [
        {"kind": "document", "payload": {"evidence_origin": "app_generated"}, "text": "Draft."},
        {
            "kind": "document",
            "payload": {"text": "Draft.\n*" + LEGACY_PREPARATION_SIGNATURE},
            "text": "Draft.",
        },
        {"kind": "document", "payload": {}, "text": "Meeting prep: original handwritten notes."},
        {
            "kind": "message",
            "payload": {"evidence_origin": "app_generated"},
            "text": "A direct message.",
        },
    ]
    assert intelligence_model.eligible_sources(rows) == rows[2:]
    assert not is_app_generated(
        {
            "kind": "document",
            "payload": {
                "text": "An original note quoting '" + LEGACY_PREPARATION_SIGNATURE + "' inline."
            },
        }
    )


def test_generated_document_cannot_be_cited_even_if_model_proposes_a_fact():
    row = {
        "source_id": 1,
        "part": 0,
        "ref": "1:0",
        "kind": "document",
        "entity_id": "draft",
        "text": "Prepare a budget review.",
        "payload": {"evidence_origin": "app_generated"},
    }
    finding = {
        "kind": "knowledge",
        "evidence": [{"ref": "1:0", "quote": "Prepare a budget review."}],
    }
    with pytest.raises(ValueError, match="invalid_evidence") as exc:
        intelligence.evidence_for(finding, [row])
    assert exc.value.details["reason"] == "app_generated_source"


def test_existing_generated_document_is_marked_and_paused_work_drains_without_model(
    database, monkeypatch
):
    setup_owner(database)
    content = "Suggested agenda for a fictional planning session.\n*" + LEGACY_PREPARATION_SIGNATURE
    with database() as conn:
        conn.execute(
            "INSERT INTO documents(document_id,title,content) VALUES('prep','Planning notes',%s)",
            (content,),
        )
        conn.execute(
            "INSERT INTO documents(document_id,title,content) VALUES('original','Meeting Prep','Original meeting notes.')"
        )
    knowledge_jobs.initialize_owner(OWNER)
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_learning SET status='failed',attempts=3,error_code='output_degenerate' WHERE source_id IN (SELECT source_id FROM intelligence_sources WHERE entity_id='prep')"
        )
        migration = (
            Path(__file__).resolve().parents[1]
            / "db_migrations/0095_generated_document_provenance.sql"
        )
        conn.execute(migration.read_text())
        assert (
            conn.execute(
                "SELECT raw_metadata->>'evidence_origin' AS origin FROM documents WHERE document_id='prep'"
            ).fetchone()["origin"]
            == "app_generated"
        )
        assert (
            conn.execute("SELECT content FROM documents WHERE document_id='prep'").fetchone()[
                "content"
            ]
            == content
        )
        assert (
            conn.execute(
                "SELECT raw_metadata FROM documents WHERE document_id='original'"
            ).fetchone()["raw_metadata"]
            == {}
        )
        row = conn.execute(
            "SELECT * FROM knowledge_learning WHERE source_id IN (SELECT source_id FROM intelligence_sources WHERE entity_id='prep')"
        ).fetchone()
        assert row["status"] == "pending" and row["attempts"] == 0

    def unexpected(*args, **kwargs):
        pytest.fail("Generated preparation must not wait for or request the model")

    monkeypatch.setattr(knowledge_jobs.llm_admission, "background_ready", unexpected)
    monkeypatch.setattr(intelligence_model, "_ask", unexpected)
    assert knowledge_jobs.process_once([OWNER])
    with database() as conn:
        row = conn.execute(
            "SELECT status,error_code FROM knowledge_learning WHERE source_id IN (SELECT source_id FROM intelligence_sources WHERE entity_id='prep')"
        ).fetchone()
        assert row == {"status": "done", "error_code": None}
        assert conn.execute("SELECT COUNT(*) AS n FROM intelligence_knowledge").fetchone()["n"] == 0


def test_exact_guard_reason_is_retained_in_paused_job_and_export(database, monkeypatch):
    from scripts import inspect_learning_failures

    setup_owner(database)
    with database() as conn:
        conn.execute(
            "INSERT INTO documents(document_id,title,content) VALUES('manual','Device manual','Original device operating instructions.')"
        )
    knowledge_jobs.initialize_owner(OWNER)
    with database() as conn:
        conn.execute("UPDATE knowledge_learning SET attempts=2")

    def fail(*args, **kwargs):
        raise LLMDegenerateOutputError("whitespace_loop")

    monkeypatch.setattr(intelligence_model, "analyze", fail)
    assert knowledge_jobs.process_once([OWNER])
    monkeypatch.setattr(inspect_learning_failures, "get_conn", database)
    failure = inspect_learning_failures.snapshot(OWNER)["failures"][0]
    assert failure["attempts"] == 3
    assert failure["error_details"]["details"] == {"reason": "whitespace_loop"}
