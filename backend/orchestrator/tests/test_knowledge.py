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
    assert knowledge.list_items(OWNER, subject="self", profile=True) == []  # About me remains the selected profile view.


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
