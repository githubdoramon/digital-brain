# ruff: noqa: F811
"""Shared knowledge contracts on disposable PostgreSQL; no live model or personal data."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import intelligence
import knowledge
import knowledge_jobs as jobs
from tests.test_intelligence import (  # noqa: F401
    OTHER,
    OWNER,
    context,
    database,
    finding,
    ingest,
    run,
    setup_owner,
)


def save(database, **changes):
    setup_owner(database)
    ingest(database)
    batch = intelligence.next_batch(OWNER, intelligence.utcnow() + timedelta(days=36500))
    candidate = {**finding(batch[0]), **changes}
    ctx = intelligence.context_for(OWNER, batch)
    findings = intelligence.validate_findings({"findings": [candidate]}, batch, ctx)
    intelligence.apply_batch(OWNER, run(database), batch, ctx, findings)
    return knowledge.list_items(OWNER, include_deleted=True)[0]


@pytest.mark.parametrize("database", ["legacy"], indirect=True)
def test_migration_preserves_legacy_values_sources_and_hard_rules(database):
    rows = knowledge.list_items(OWNER, subject="self", profile=True)
    assert len(rows) == 1
    assert rows[0]["knowledge_id"] == "uf_old"
    assert rows[0]["statement"] == "Prefers quiet keyboards"
    assert rows[0]["category"] == "preference" and rows[0]["importance"] == 7
    assert rows[0]["source_thread_id"] == "old-thread"
    assert rows[0]["evidence"][0]["kind"] == "legacy_fact"
    with database() as conn:
        assert (
            conn.execute("SELECT fact_mode FROM user_facts WHERE fact_id='rule_old'").fetchone()[
                "fact_mode"
            ]
            == "hard_rule"
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM intelligence_knowledge WHERE knowledge_id='rule_old'"
            ).fetchone()["n"]
            == 0
        )


def test_new_entities_require_grounded_labels_and_people_cannot_be_created():
    f = {
        "subject_ref": "new:keyboard",
        "entities": [{"ref": "new:keyboard", "kind": "product", "label": "Quiet Keys"}],
    }
    result = knowledge.validate_entities(
        f, {}, [{"quote": "I returned the Quiet Keys after two days."}]
    )
    assert result["subject_ref"].startswith("entity:")
    for entity in [
        {"ref": "new:alex", "kind": "person", "label": "Alex"},
        {"ref": "new:keyboard", "kind": "product", "label": "Invented Keys"},
    ]:
        with pytest.raises(ValueError):
            knowledge.validate_entities(
                {**f, "entities": [entity]}, {}, [{"quote": "I returned Quiet Keys."}]
            )


def test_multi_entity_fact_is_visible_from_either_entity(database):
    setup_owner(database)
    ingest(database, body="Orbit Game does not run on my Slate laptop.")
    batch = intelligence.next_batch(OWNER, intelligence.utcnow() + timedelta(days=36500))
    f = {
        **finding(batch[0]),
        "subject_ref": "new:game",
        "related_subjects": ["new:laptop", "self"],
        "category": "compatibility",
        "entities": [
            {"ref": "new:game", "kind": "game", "label": "Orbit Game"},
            {"ref": "new:laptop", "kind": "device", "label": "Slate laptop"},
        ],
    }
    ctx = intelligence.context_for(OWNER, batch)
    fs = intelligence.validate_findings({"findings": [f]}, batch, ctx)
    intelligence.apply_batch(OWNER, run(database), batch, ctx, fs)
    refs = fs[0]["related_subjects"]
    assert len(knowledge.list_items(OWNER, subject=refs[0])) == 1
    assert len(knowledge.list_items(OWNER, subject=fs[0]["subject_ref"])) == 1
    assert len(knowledge.list_items(OWNER, query="Slate")) == 1
    assert knowledge.list_items(OTHER) == []
    assert (
        knowledge.list_items(OWNER, subject="self", profile=True) == []
    )  # About me remains the selected profile view.


def test_delete_blocks_old_evidence_but_new_events_can_relearn(database):
    item = save(database)
    ident = item["knowledge_id"]
    assert knowledge.change(OTHER, ident, delete=True) is None
    knowledge.change(OWNER, ident, delete=True)
    assert not knowledge.list_items(OWNER)
    with database() as conn:
        row = conn.execute(
            "SELECT * FROM intelligence_knowledge WHERE knowledge_id=%s", (ident,)
        ).fetchone()
        assert not knowledge.permits_relearning(row, item["evidence"])
        future = intelligence.utcnow() + timedelta(days=1)
        f = {
            **finding({"ref": "original", "text": item["statement"]}),
            "existing_id": ident,
            "evidence": [{**item["evidence"][0], "sent_at": future.isoformat()}],
        }
        assert intelligence._save_knowledge(conn.cursor(), OWNER, f) == "facts_updated"
    assert knowledge.list_items(OWNER)[0]["deleted_at"] is None


def test_correction_protects_statement_and_invalidates_summary(database):
    item = save(
        database, inferred=True, inference_reason="A repeated pattern in the cited reports."
    )
    with database() as conn:
        conn.execute(
            "UPDATE knowledge_summaries SET summary='Old summary',rendered_version=version"
        )
    knowledge.change(OWNER, item["knowledge_id"], statement="Alex has not received the quote.")
    updated = knowledge.list_items(OWNER)[0]
    assert updated["manual"] and not updated["inferred"]
    assert knowledge.summary_for(OWNER, "self")["summary"] is None
    with database() as conn:
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM intelligence_knowledge_revisions").fetchone()[
                "n"
            ]
            >= 1
        )


def test_chat_capture_is_transactional_and_never_captures_assistant(database):
    with database() as conn:
        conn.execute("INSERT INTO conversation_threads VALUES(%s,%s)", ("thread", OWNER))
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content) VALUES('thread','user','My laptop is a Slate.'),('thread','assistant','You might prefer another laptop.')"
        )
    with database() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM intelligence_sources WHERE kind='chat'"
            ).fetchone()["n"]
            == 1
        )
        assert conn.execute("SELECT COUNT(*) AS n FROM knowledge_learning").fetchone()["n"] == 1
    with pytest.raises(RuntimeError), database() as conn:
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content) VALUES('thread','user','This must roll back.')"
        )
        raise RuntimeError("rollback")
    with database() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM intelligence_sources WHERE kind='chat'"
            ).fetchone()["n"]
            == 1
        )


def test_chat_clarification_retries_deferred_source_with_original_evidence(database, monkeypatch):
    setup_owner(database)
    with database() as conn:
        conn.execute("INSERT INTO conversation_threads VALUES(%s,%s)", ("thread", OWNER))
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content) VALUES('thread','user','Alex likes quiet keyboards.')"
        )
    first = []

    def defer(owner, batch, **kwargs):
        first.append(batch[0])
        return {"deferred_refs": [batch[0]["ref"]]}, []

    monkeypatch.setattr(jobs.intelligence_model, "analyze", defer)
    assert jobs.process_once([OWNER])
    assert jobs.progress(OWNER)["deferred"] == 1
    assert knowledge.list_items(OWNER) == []
    with database() as conn:
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content) VALUES('thread','user','I mean Alex Carter.')"
        )

    def learn(owner, batch, **kwargs):
        assert any("I mean Alex Carter." in r["text"] for r in batch)
        original = next(r for r in batch if r["source_id"] == first[0]["source_id"])
        evidence = [
            {
                "source_id": r["source_id"],
                "part": r["part"],
                "quote": r["text"],
                "sent_at": r["payload"]["sent_at"],
            }
            for r in batch
        ]
        return {}, [{**finding(original), "subject_ref": "contact:alex", "evidence": evidence}]

    monkeypatch.setattr(jobs.intelligence_model, "analyze", learn)
    assert jobs.process_once([OWNER])
    assert len(knowledge.list_items(OWNER, subject="contact:alex")) == 1
    assert knowledge.list_items(OWNER)[0]["evidence"][0]["source_id"] == first[0]["source_id"]


def test_ambiguous_person_is_skipped_without_dropping_clear_fact(database):
    setup_owner(database)
    ingest(database, body="Alex likes quiet keyboards and I prefer small laptops.")
    batch = intelligence.next_batch(OWNER, intelligence.utcnow() + timedelta(days=36500))
    ctx = context(batch)
    ctx["contacts"] = [
        {"contact_id": "alex", "display_name": "Alex Carter"},
        {"contact_id": "other-alex", "display_name": "Alex Morgan"},
    ]
    unclear = {**finding(batch[0]), "subject_ref": "contact:alex"}
    clear = {**finding(batch[0]), "predicate": "laptop_preference"}
    fs = intelligence.validate_findings({"findings": [unclear, clear]}, batch, ctx)
    assert len(fs) == 1 and fs[0]["subject_ref"] == "self"
    assert ctx["deferred_refs"] == [batch[0]["ref"]]


def test_backfill_revisits_all_sources_once_and_yields_to_live_chat(database):
    setup_owner(database)
    ingest(database)
    with database() as conn:
        conn.execute(
            "UPDATE contacts SET comments='Alex enjoys quiet keyboards.' WHERE contact_id='alex'"
        )
        conn.execute("INSERT INTO conversation_threads VALUES('thread',%s)", (OWNER,))
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content) VALUES('thread','user','My laptop is a Slate.')"
        )
        conn.execute(
            "INSERT INTO events VALUES('event','Meeting','Discussed a laptop.','2026-01-01',FALSE)"
        )
        conn.execute(
            "INSERT INTO documents(document_id,title,content) VALUES('document','Manual','Slate laptop manual')"
        )
    jobs.initialize_owner(OWNER)
    with database() as conn:
        kinds = {
            r["kind"]
            for r in conn.execute(
                "SELECT DISTINCT s.kind FROM knowledge_learning j JOIN intelligence_sources s USING(source_id)"
            )
        }
        assert kinds == {"chat", "message", "event", "document", "contact_note"}
        before = conn.execute("SELECT COUNT(*) AS n FROM knowledge_learning").fetchone()["n"]
    jobs.initialize_owner(OWNER)
    with database() as conn:
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM knowledge_learning").fetchone()["n"] == before
        )
    claimed = jobs.claim([OWNER])
    assert not claimed["historical"]
    assert knowledge.list_items(OTHER) == []


def test_expired_running_job_is_recovered_and_errors_never_advance_receipt(database, monkeypatch):
    setup_owner(database)
    with database() as conn:
        conn.execute("INSERT INTO conversation_threads VALUES('thread',%s)", (OWNER,))
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content) VALUES('thread','user','My laptop is a Slate.')"
        )
    job = jobs.claim([OWNER])
    with database() as conn:
        conn.execute("UPDATE knowledge_learning SET lease_until=NOW()-INTERVAL '1 minute'")
    assert jobs.claim([OWNER])["lease_token"] != job["lease_token"]
    with database() as conn:
        conn.execute("UPDATE knowledge_learning SET lease_until=NOW()-INTERVAL '1 minute'")
    monkeypatch.setattr(
        jobs.intelligence_model,
        "analyze",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("private text")),
    )
    jobs.process_once([OWNER])
    assert jobs.progress(OWNER)["failed"] == 1
    assert knowledge.list_items(OWNER) == []


def test_chat_context_uses_nearest_replies_and_keeps_primary_evidence_complete(database):
    setup_owner(database)
    with database() as conn:
        conn.execute("INSERT INTO conversation_threads VALUES('context-thread',%s)", (OWNER,))
        for index in range(9):
            conn.execute(
                "INSERT INTO conversation_messages(thread_id,role,content) VALUES('context-thread','user',%s)",
                (f"Message {index}: " + "A" * 2500,),
            )
    job = jobs.claim([OWNER])
    batch = jobs.source_batch(job)
    assert len(batch) == 4
    assert not batch[0]["supporting"] and len(batch[0]["text"]) > 2500
    assert all(row["supporting"] and len(row["text"]) == 1500 for row in batch[1:])
    assert [row["text"].split(":", 1)[0] for row in batch] == [
        "Message 0",
        "Message 1",
        "Message 2",
        "Message 3",
    ]


def test_supporting_evidence_alone_cannot_create_a_finding_for_primary_job(database, monkeypatch):
    setup_owner(database)
    with database() as conn:
        conn.execute("INSERT INTO conversation_threads VALUES('support-thread',%s)", (OWNER,))
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content) VALUES('support-thread','user','My laptop is a Slate.'),('support-thread','user','Thanks for the help.')"
        )
        conn.execute(
            "UPDATE knowledge_learning SET status='done' WHERE source_id=(SELECT min(source_id) FROM knowledge_learning)"
        )
    job = jobs.claim([OWNER])
    batch = jobs.source_batch(job)
    support = next(row for row in batch if row["supporting"])
    candidate = finding(support)
    monkeypatch.setattr(
        jobs.intelligence_model, "call_llm_json", lambda *a, **k: {"findings": [candidate]}
    )
    _, findings = jobs.intelligence_model.analyze(OWNER, batch, knowledge_only=True)
    assert findings == []


@pytest.mark.parametrize("database", ["pre_context"], indirect=True)
def test_context_recovery_migration_preserves_other_failures_and_receipts(database):
    from pathlib import Path

    save(database)
    for index in range(3):
        ingest(database, ident=f"context-{index}")
    with database() as conn:
        sources = conn.execute(
            "SELECT source_id FROM intelligence_sources ORDER BY source_id"
        ).fetchall()
        for row, status, code in zip(
            sources,
            ("failed", "failed", "done", "deferred"),
            ("context_length_exceeded", "invalid_evidence", None, None),
            strict=True,
        ):
            conn.execute(
                "INSERT INTO knowledge_learning(user_email,source_id,status,error_code,attempts) VALUES(%s,%s,%s,%s,3)",
                (OWNER, row["source_id"], status, code),
            )
        for row, code in zip(
            sources[:2], ("context_length_exceeded", "invalid_evidence"), strict=True
        ):
            conn.execute(
                "INSERT INTO intelligence_failures(user_email,source_id,part,knowledge_only,error_code,attempts) VALUES(%s,%s,0,TRUE,%s,3)",
                (OWNER, row["source_id"], code),
            )
        receipts = conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"]
        conn.execute(
            (
                Path(__file__).resolve().parents[1]
                / "db_migrations/0082_intelligence_context_recovery.sql"
            ).read_text()
        )
        assert [
            row["status"]
            for row in conn.execute("SELECT status FROM knowledge_learning ORDER BY source_id")
        ] == ["pending", "failed", "done", "deferred"]
        assert [
            row["attempts"]
            for row in conn.execute("SELECT attempts FROM intelligence_failures ORDER BY source_id")
        ] == [0, 3]
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"]
            == receipts
        )


def test_summary_cannot_publish_after_concurrent_correction(database, monkeypatch):
    item = save(database)

    def summarize(*args, **kwargs):
        assert kwargs["sensitive_payload"]
        knowledge.change(OWNER, item["knowledge_id"], statement="The quote is still pending.")
        return {"summary": "Alex received the quote."}

    monkeypatch.setattr("llm_helpers.call_llm_json", summarize)
    with database() as conn:
        conn.execute("UPDATE knowledge_summaries SET next_attempt_at=NOW()")
    assert jobs.summarize_once([OWNER])
    assert knowledge.summary_for(OWNER, "self")["summary"] is None


def test_validation_recovery_retries_only_retained_validation_failures(database):
    from pathlib import Path

    item = save(database)
    knowledge.change(OWNER, item["knowledge_id"], statement="The quote is still pending.")
    for index in range(5):
        ingest(database, ident=f"validation-{index}")
    with database() as conn:
        sources = conn.execute(
            "SELECT source_id FROM intelligence_sources ORDER BY source_id"
        ).fetchall()
        for row, status, code in zip(
            sources,
            ("failed", "failed", "failed", "done", "deferred", "running"),
            (
                "invalid_subject",
                "invalid_evidence",
                "invalid_request",
                None,
                None,
                "invalid_subject",
            ),
            strict=True,
        ):
            conn.execute(
                "INSERT INTO knowledge_learning(user_email,source_id,status,error_code,attempts) VALUES(%s,%s,%s,%s,3)",
                (OWNER, row["source_id"], status, code),
            )
        for row, code in zip(
            sources[:3], ("invalid_subject", "invalid_evidence", "invalid_request"), strict=True
        ):
            conn.execute(
                "INSERT INTO intelligence_failures(user_email,source_id,part,knowledge_only,error_code,attempts) VALUES(%s,%s,0,TRUE,%s,3)",
                (OWNER, row["source_id"], code),
            )
        conn.execute("UPDATE intelligence_runs SET status='succeeded',knowledge_only=TRUE")
        receipts = conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"]
        conn.execute(
            (
                Path(__file__).resolve().parents[1]
                / "db_migrations/0083_learning_validation_recovery.sql"
            ).read_text()
        )
        assert [
            row["status"]
            for row in conn.execute("SELECT status FROM knowledge_learning ORDER BY source_id")
        ] == ["pending", "pending", "failed", "done", "deferred", "running"]
        assert [
            row["attempts"]
            for row in conn.execute("SELECT attempts FROM intelligence_failures ORDER BY source_id")
        ] == [0, 0, 3]
        runs = conn.execute("SELECT slot,status FROM intelligence_runs").fetchall()
        assert any(
            row["slot"] != "knowledge-backlog" and row["status"] == "pending" for row in runs
        )
        assert all(
            row["status"] == "succeeded" for row in runs if row["slot"] == "knowledge-backlog"
        )
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"]
            == receipts
        )
    assert knowledge.list_items(OWNER)[0]["statement"] == "The quote is still pending."


def test_summary_claim_excludes_concurrent_workers_and_repairs_bad_shape(database, monkeypatch):
    save(database)
    with database() as conn:
        conn.execute("UPDATE knowledge_summaries SET next_attempt_at=NOW()")
    calls = []

    def summarize(*args, **kwargs):
        calls.append(kwargs)
        assert not jobs.summarize_once([OWNER])
        return {"summary": None} if len(calls) == 1 else {"summary": "The quote was sent."}

    monkeypatch.setattr("llm_helpers.call_llm_json", summarize)
    assert jobs.summarize_once([OWNER])
    assert len(calls) == 2 and calls[0]["timeout"] == 300
    assert knowledge.summary_for(OWNER, "self")["summary"] == "The quote was sent."
    with database() as conn:
        row = conn.execute("SELECT * FROM knowledge_summaries WHERE subject_ref='self'").fetchone()
        assert row["lease_token"] is row["lease_until"] is row["error_code"] is None


def test_summary_failure_preserves_pending_version_and_private_reason(
    database, monkeypatch, caplog
):
    from llm_policy import LLMRequestRejected

    save(database)
    with database() as conn:
        conn.execute("UPDATE knowledge_summaries SET next_attempt_at=NOW()")
    monkeypatch.setattr(
        "llm_helpers.call_llm_json",
        lambda *a, **k: (_ for _ in ()).throw(LLMRequestRejected("invalid_request")),
    )
    assert jobs.summarize_once([OWNER])
    with database() as conn:
        row = conn.execute("SELECT * FROM knowledge_summaries WHERE subject_ref='self'").fetchone()
        assert row["version"] != row["rendered_version"]
        assert row["error_code"] == "invalid_request" and row["lease_token"] is None
        assert row["next_attempt_at"] > intelligence.utcnow()
    assert "reason=invalid_request" in caplog.text


def test_expired_summary_owner_cannot_publish_or_release_replacement_lease(database, monkeypatch):
    save(database)
    with database() as conn:
        conn.execute("UPDATE knowledge_summaries SET next_attempt_at=NOW()")

    def summarize(*args, **kwargs):
        with database() as conn:
            conn.execute(
                "UPDATE knowledge_summaries SET lease_token='replacement',lease_until=NOW()+INTERVAL '15 minutes'"
            )
        return {"summary": "A stale result."}

    monkeypatch.setattr("llm_helpers.call_llm_json", summarize)
    assert jobs.summarize_once([OWNER])
    with database() as conn:
        row = conn.execute("SELECT * FROM knowledge_summaries WHERE subject_ref='self'").fetchone()
        assert row["lease_token"] == "replacement"
        assert row["version"] != row["rendered_version"]
        assert row["summary"] is None


def test_reasoning_only_summary_retries_once_without_exposing_reasoning(database, monkeypatch):
    from llm_helpers import LLMEmptyContentError

    save(database)
    with database() as conn:
        conn.execute("UPDATE knowledge_summaries SET next_attempt_at=NOW()")
    calls = []

    def summarize(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise LLMEmptyContentError("LLM returned empty content")
        return {"summary": "The quote was sent."}

    monkeypatch.setattr("llm_helpers.call_llm_json", summarize)
    assert jobs.summarize_once([OWNER])
    assert [call["reasoning_effort"] for call in calls] == ["medium", "low"]
    assert knowledge.summary_for(OWNER, "self")["summary"] == "The quote was sent."


def test_retry_wakes_failed_learning_and_summaries_for_owner_only(database):
    save(database)
    with database() as conn:
        source = conn.execute("SELECT source_id FROM intelligence_sources LIMIT 1").fetchone()[
            "source_id"
        ]
        for owner in (OWNER, OTHER):
            conn.execute(
                "INSERT INTO knowledge_learning(user_email,source_id,status,attempts,error_code) VALUES(%s,%s,'failed',3,'invalid_request')",
                (owner, source),
            )
        conn.execute(
            "UPDATE knowledge_summaries SET error_code='invalid_summary',next_attempt_at=NOW()+INTERVAL '1 day'"
        )
    assert jobs.retry_failed(OWNER) == {"queued": 1, "historical_queued": 0, "summaries_queued": 1}
    assert jobs.progress(OWNER) == {"pending": 1}
    assert jobs.progress(OTHER) == {"failed": 1}
    with database() as conn:
        assert (
            conn.execute("SELECT error_code FROM knowledge_summaries").fetchone()["error_code"]
            is None
        )


@pytest.mark.parametrize("database", ["pre_reliability"], indirect=True)
def test_reliability_migration_requeues_failures_and_preserves_completed_evidence(database):
    from pathlib import Path

    item = save(database)
    knowledge.change(OWNER, item["knowledge_id"], statement="The quote is still pending.")
    for index in range(3):
        ingest(database, ident=f"extra-{index}")
    with database() as conn:
        sources = conn.execute(
            "SELECT source_id FROM intelligence_sources ORDER BY source_id"
        ).fetchall()
        for row, status in zip(sources, ("failed", "done", "deferred", "running"), strict=True):
            conn.execute(
                "INSERT INTO knowledge_learning(user_email,source_id,status,attempts) VALUES(%s,%s,%s,3)",
                (OWNER, row["source_id"], status),
            )
        conn.execute(
            "INSERT INTO intelligence_failures(user_email,source_id,part,knowledge_only,attempts,error_code,next_attempt_at) VALUES(%s,%s,0,TRUE,3,'invalid_evidence',NULL)",
            (OWNER, sources[0]["source_id"]),
        )
        conn.execute(
            "UPDATE intelligence_runs SET status='failed',knowledge_only=TRUE,cutoff='2100-01-01'"
        )
        receipts = conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"]
        migration = (
            Path(__file__).resolve().parents[1] / "db_migrations/0081_learning_reliability.sql"
        )
        conn.execute(migration.read_text())
        statuses = [
            r["status"]
            for r in conn.execute(
                "SELECT status FROM knowledge_learning ORDER BY source_id"
            ).fetchall()
        ]
        assert statuses == ["pending", "done", "deferred", "running"]
        assert (
            conn.execute("SELECT attempts FROM intelligence_failures").fetchone()["attempts"] == 0
        )
        assert all(
            r["status"] == "pending" for r in conn.execute("SELECT status FROM intelligence_runs")
        )
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"]
            == receipts
        )
    assert knowledge.list_items(OWNER)[0]["statement"] == "The quote is still pending."


def test_api_enforces_owner_scope_and_private_cache_headers(database):
    from routes.knowledge import create_knowledge_router, get_current_user

    item = save(database)
    app = FastAPI()
    app.include_router(create_knowledge_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": OTHER}
    with TestClient(app) as client:
        assert client.get("/mobile/knowledge").json()["knowledge"] == []
        assert (
            client.patch(
                "/mobile/knowledge/" + item["knowledge_id"],
                json={"statement": "Wrong owner change"},
            ).status_code
            == 404
        )
        assert client.delete("/mobile/knowledge/" + item["knowledge_id"]).status_code == 404
        app.dependency_overrides[get_current_user] = lambda: {"email": OWNER}
        response = client.get("/mobile/knowledge")
        assert response.headers["cache-control"] == "no-store"
        assert len(response.json()["knowledge"]) == 1
        assert client.delete("/mobile/knowledge/" + item["knowledge_id"]).status_code == 204
        assert client.get("/mobile/knowledge").json()["knowledge"] == []


def test_subjects_follow_confirmed_identity_links_and_invalidate_old_summary(database):
    setup_owner(database)
    ingest(database, outgoing=False)
    with database() as conn:
        identity = conn.execute(
            "SELECT identity_id FROM messaging_identities WHERE NOT is_self LIMIT 1"
        ).fetchone()["identity_id"]
        cur = conn.cursor()
        intelligence._save_knowledge(
            cur,
            OWNER,
            {
                "subject_ref": "identity:" + identity,
                "predicate": "preference",
                "statement": "Prefers quiet keyboards",
                "status": "current",
                "category": "preference",
                "evidence": [],
            },
        )
        conn.execute(
            "UPDATE messaging_identities SET contact_id='alex' WHERE identity_id=%s", (identity,)
        )
        conn.execute(
            "UPDATE knowledge_summaries SET summary='Old person summary',rendered_version=version WHERE subject_ref='contact:alex'"
        )
    assert len(knowledge.list_items(OWNER, subject="contact:alex")) == 1
    with database() as conn:
        conn.execute(
            "UPDATE messaging_identities SET contact_id=NULL WHERE identity_id=%s", (identity,)
        )
    assert knowledge.list_items(OWNER, subject="contact:alex") == []
    assert knowledge.summary_for(OWNER, "contact:alex")["summary"] is None


def test_contact_merge_preserves_fact_ids_and_links(database):
    item = save(database, subject_ref="contact:alex")
    with database() as conn:
        conn.execute(
            "INSERT INTO contacts(contact_id,display_name) VALUES('primary','Alex Morgan')"
        )
        knowledge.relink_contacts(conn.cursor(), "primary", "alex")
    assert (
        knowledge.list_items(OWNER, subject="contact:primary")[0]["knowledge_id"]
        == item["knowledge_id"]
    )
    assert knowledge.list_items(OWNER, subject="contact:alex") == []


def test_owner_contact_includes_self_knowledge(database):
    item = save(database)
    with database() as conn:
        conn.execute("UPDATE contacts SET emails=ARRAY[%s] WHERE contact_id='alex'", (OWNER,))
    assert (
        knowledge.list_items(OWNER, subject="contact:alex")[0]["knowledge_id"]
        == item["knowledge_id"]
    )
    assert knowledge.list_items(OTHER, subject="contact:alex") == []


def test_accepted_profession_uses_knowledge_and_preserves_notes(database):
    setup_owner(database)
    with database() as conn:
        conn.execute("UPDATE contacts SET comments='Met at a workshop.' WHERE contact_id='alex'")
        knowledge.accept_profession(
            conn.cursor(),
            OWNER,
            {
                "proposal_id": "profession-proposal",
                "contact_id": "alex",
                "suggested_value": "Engineer",
            },
        )
    assert knowledge.list_items(OWNER, subject="contact:alex")[0]["statement"] == "Engineer"
    with database() as conn:
        assert (
            conn.execute("SELECT comments FROM contacts WHERE contact_id='alex'").fetchone()[
                "comments"
            ]
            == "Met at a workshop."
        )


@pytest.mark.parametrize("database", ["legacy"], indirect=True)
def test_legacy_reclassification_preserves_current_text(database):
    jobs.initialize_owner(OWNER)
    job = jobs.claim([OWNER])
    batch = jobs.source_batch(job)
    ctx = intelligence.context_for(OWNER, batch)
    f = {
        **finding(batch[0]),
        "existing_id": "uf_old",
        "subject_ref": "new:keyboards",
        "statement": "Prefers all expensive keyboards",
        "entities": [{"ref": "new:keyboards", "kind": "product", "label": "quiet keyboards"}],
        "category": "preference",
    }
    fs = intelligence.validate_findings({"findings": [f]}, batch, ctx, knowledge_only=True)
    with database() as conn:
        intelligence._save_knowledge(conn.cursor(), OWNER, fs[0])
    row = knowledge.list_items(OWNER)[0]
    assert row["knowledge_id"] == "uf_old"
    assert row["statement"] == "Prefers quiet keyboards"
    assert row["subject_ref"].startswith("entity:")


def test_secondary_identity_link_invalidates_contact_summary(database):
    setup_owner(database)
    ingest(database, outgoing=False)
    with database() as conn:
        identity = conn.execute(
            "SELECT identity_id FROM messaging_identities WHERE NOT is_self LIMIT 1"
        ).fetchone()["identity_id"]
        conn.execute(
            "UPDATE messaging_identities SET contact_id='alex' WHERE identity_id=%s", (identity,)
        )
        conn.execute(
            "UPDATE knowledge_summaries SET summary='Outdated',rendered_version=version WHERE subject_ref='contact:alex'"
        )
        intelligence._save_knowledge(
            conn.cursor(),
            OWNER,
            {
                "subject_ref": "self",
                "related_subjects": ["identity:" + identity],
                "predicate": "collaboration",
                "statement": "Worked together on a workshop.",
                "status": "current",
                "evidence": [],
            },
        )
    assert knowledge.summary_for(OWNER, "contact:alex")["summary"] is None
    entities = knowledge.entities_for(OWNER)
    assert not any(e["entity_ref"] == "identity:" + identity for e in entities)
    assert any(e["entity_ref"] == "contact:alex" for e in entities)


def test_backfill_enqueue_is_bounded_and_receipt_based(database):
    setup_owner(database)
    with database() as conn:
        conn.execute(
            "INSERT INTO documents(document_id,title,content) SELECT 'manual-'||n,'Device manual','A retained device manual.' FROM generate_series(1,550) n"
        )
    jobs.initialize_owner(OWNER)
    assert sum(jobs.progress(OWNER).values()) == 500
    jobs.initialize_owner(OWNER)
    assert sum(jobs.progress(OWNER).values()) == 550
    with database() as conn:
        conn.execute(
            "INSERT INTO documents(document_id,title,content) VALUES('late','Late manual','A retained older manual.')"
        )
        conn.execute(
            "UPDATE intelligence_sources SET observed_at=NOW()-INTERVAL '1 day' WHERE entity_id='late'"
        )
    jobs.initialize_owner(OWNER)
    assert sum(jobs.progress(OWNER).values()) == 551
