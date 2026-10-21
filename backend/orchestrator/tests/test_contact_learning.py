# ruff: noqa: F811
"""Contact changes use real transactions with invented evidence, never a live model."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

import contact_learning
import intelligence
import intelligence_model
import knowledge
import knowledge_jobs
from birthdays import birthday_value, parse_birthday
from schemas import ContactIn
from tests.test_intelligence import (  # noqa: F401
    OTHER,
    OWNER,
    database,
    finding,
    ingest,
    run,
    setup_owner,
)

TEXT = "Alex Carter's daughter Mia has her birthday on May 12."


def candidate(row, **changes):
    return {
        **finding(row),
        "kind": "contact_change",
        "subject_ref": "general",
        "status": "current",
        "confidence": "high",
        "contact_changes": {
            "people": [
                {"ref": "contact:alex", "display_name": "Alex Carter", "birthday": None},
                {"ref": "new:mia", "display_name": "Mia", "birthday": "--05-12"},
            ],
            "relationships": [
                {
                    "from_ref": "new:mia",
                    "to_ref": "contact:alex",
                    "relationship_type": "Daughter",
                    "reciprocal_type": "Parent",
                }
            ],
        },
        **changes,
    }


def prepare(database, **changes):
    setup_owner(database)
    ingest(database, body=TEXT)
    batch = intelligence.next_batch(OWNER, intelligence.utcnow() + timedelta(days=36500))
    context = intelligence.context_for(OWNER, batch)
    fs = intelligence.validate_findings(
        {"findings": [candidate(batch[0], **changes)]}, batch, context, knowledge_only=True
    )
    return batch, context, fs


def apply(database, **changes):
    batch, context, fs = prepare(database, **changes)
    return intelligence.apply_batch(OWNER, run(database), batch, context, fs)


def test_confident_daughter_creation_relationship_and_partial_birthday_are_atomic(database):
    result = apply(database)
    assert result["contact_changes_applied"] == 1
    with database() as conn:
        daughter = conn.execute("SELECT * FROM contacts WHERE display_name='Mia'").fetchone()
        assert daughter["birthday"] is None
        assert birthday_value(daughter) == "--05-12"
        edge = conn.execute("SELECT * FROM contact_relationships").fetchone()
        assert edge["from_contact_id"] == daughter["contact_id"] and edge["to_contact_id"] == "alex"
        assert edge["relationship_type"] == "Daughter" and edge["reciprocal_type"] == "Parent"
        receipt = conn.execute("SELECT * FROM intelligence_proposals").fetchone()
        assert receipt["status"] == "accepted" and receipt["resolution_evidence"]["automatic"]
    assert len(knowledge.list_items(OWNER, subject="contact:" + daughter["contact_id"])) == 2
    assert len(knowledge.list_items(OWNER, subject="contact:alex")) == 1
    assert not intelligence.list_proposals(OWNER)
    # Replaying the same source cannot recreate a contact the user later deleted.
    with database() as conn:
        conn.execute("DELETE FROM contacts WHERE contact_id=%s", (daughter["contact_id"],))
        assert (
            contact_learning.save(
                conn.cursor(), OWNER, {**receipt["payload"], "evidence": receipt["evidence"]}
            )
            is None
        )
        assert conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"] == 1


@pytest.mark.parametrize(
    "changes",
    [{"confidence": "medium"}, {"confidence": "low"}, {"status": "historical"}],
)
def test_uncertain_changes_are_reviewable_without_partial_creation(database, changes):
    result = apply(database, **changes)
    assert result["proposals_created"] == 1 and not result["contact_changes_applied"]
    proposal = intelligence.list_proposals(OWNER)[0]
    assert proposal["kind"] == "contact_change"
    assert proposal["payload"]["contact_changes"]["people"][1]["birthday"] == "--05-12"
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM contact_relationships").fetchone()["n"] == 0
    assert intelligence.resolve_proposal(OTHER, proposal["proposal_id"], accept=True) is None
    assert intelligence.resolve_proposal(OWNER, proposal["proposal_id"], accept=True)["accepted"]
    assert len(knowledge.list_items(OWNER, query="Mia")) == 2


def test_same_name_requires_explicit_identity_choice_and_reuses_selected_contact(database):
    with database() as conn:
        conn.execute("INSERT INTO contacts(contact_id,display_name) VALUES('mia','Mia Brooks')")
    apply(database)
    proposal = intelligence.list_proposals(OWNER)[0]
    assert proposal["payload"]["contact_plan"]["choices"][0]["ref"] == "new:mia"
    assert not intelligence.resolve_proposal(OWNER, proposal["proposal_id"], accept=True)[
        "accepted"
    ]
    result = intelligence.resolve_proposal(
        OWNER, proposal["proposal_id"], accept=True, contact_resolutions={"new:mia": "mia"}
    )
    assert result["accepted"] and result["contacts"]["new:mia"] == "mia"
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"] == 2
        assert (
            birthday_value(conn.execute("SELECT * FROM contacts WHERE contact_id='mia'").fetchone())
            == "--05-12"
        )


def test_existing_child_is_matched_by_parent_relationship(database):
    with database() as conn:
        conn.execute(
            "INSERT INTO contacts(contact_id,display_name) VALUES('mia','Mia Brooks'),('other-mia','Mia Lake')"
        )
        conn.execute(
            "INSERT INTO contact_relationships(relationship_id,from_contact_id,to_contact_id,relationship_type,reciprocal_type) VALUES('family','alex','mia','Parent','Daughter')"
        )
    result = apply(database)
    assert result["contact_changes_applied"] == 1
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"] == 3
        assert conn.execute("SELECT COUNT(*) AS n FROM contact_relationships").fetchone()["n"] == 1
        assert (
            birthday_value(conn.execute("SELECT * FROM contacts WHERE contact_id='mia'").fetchone())
            == "--05-12"
        )


def test_conflicting_birthday_becomes_proposal_and_manual_changes_are_protected(database):
    with database() as conn:
        conn.execute(
            "INSERT INTO contacts(contact_id,display_name,birthday) VALUES('mia','Mia Brooks','2011-06-12')"
        )
        conn.execute(
            "INSERT INTO contact_relationships(relationship_id,from_contact_id,to_contact_id,relationship_type,reciprocal_type) VALUES('family','mia','alex','Daughter','Parent')"
        )
    apply(database)
    proposal = intelligence.list_proposals(OWNER)[0]
    assert "birthday_conflict" in proposal["payload"]["contact_plan"]["review_reasons"]
    with database() as conn:
        conn.execute("UPDATE contacts SET birthday='2011-07-12' WHERE contact_id='mia'")
    result = intelligence.resolve_proposal(OWNER, proposal["proposal_id"], accept=True)
    assert not result["accepted"] and result["reason"] == "contact_changed"


def test_partial_birthday_does_not_erase_known_birth_year(database):
    with database() as conn:
        conn.execute(
            "INSERT INTO contacts(contact_id,display_name,birthday) VALUES('mia','Mia Brooks','2011-05-12')"
        )
        conn.execute(
            "INSERT INTO contact_relationships(relationship_id,from_contact_id,to_contact_id,relationship_type,reciprocal_type) VALUES('family','mia','alex','Daughter','Parent')"
        )
    assert apply(database)["contact_changes_supported"] == 1
    with database() as conn:
        assert conn.execute("SELECT birthday FROM contacts WHERE contact_id='mia'").fetchone()[
            "birthday"
        ] == date(2011, 5, 12)


def test_changed_source_cannot_apply_contact_changes(database):
    batch, context, fs = prepare(database)
    with database() as conn:
        conn.execute("UPDATE messaging_messages SET deleted=TRUE")
    counts = intelligence.apply_batch(OWNER, run(database), batch, context, fs)
    assert counts["findings_skipped"] == 1
    assert not knowledge.list_items(OWNER)


def test_pending_acceptance_rechecks_source_and_dismissal_survives_replay(database):
    batch, ctx, fs = prepare(database, confidence="medium")
    intelligence.apply_batch(OWNER, run(database), batch, ctx, fs)
    proposal = intelligence.list_proposals(OWNER)[0]
    with database() as conn:
        conn.execute("UPDATE messaging_messages SET deleted=TRUE")
    assert (
        intelligence.resolve_proposal(OWNER, proposal["proposal_id"], accept=True)["reason"]
        == "source_changed"
    )
    intelligence.resolve_proposal(OWNER, proposal["proposal_id"], accept=False)
    with database() as conn:
        assert contact_learning.save(conn.cursor(), OWNER, fs[0]) is None


def test_chat_worker_applies_structured_contact_changes(database, monkeypatch):
    with database() as conn:
        conn.execute("INSERT INTO conversation_threads VALUES('chat',%s)", (OWNER,))
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content) VALUES('chat','user',%s)",
            (TEXT,),
        )

    def analyze(owner, batch, **kwargs):
        ctx = intelligence.context_for(owner, batch)
        return ctx, intelligence.validate_findings(
            {"findings": [candidate(batch[0])]}, batch, ctx, knowledge_only=kwargs["knowledge_only"]
        )

    monkeypatch.setattr(intelligence_model, "analyze", analyze)
    assert knowledge_jobs.process_once([OWNER])
    with database() as conn:
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM contacts WHERE display_name='Mia'").fetchone()[
                "n"
            ]
            == 1
        )
        assert conn.execute("SELECT status FROM knowledge_learning").fetchone()["status"] == "done"


@pytest.mark.parametrize("value", ["--02-30", "--13-02", "2019-02-29", "May 12"])
def test_invalid_birthday_is_rejected(value):
    with pytest.raises((ValueError, ValidationError)):
        ContactIn(contact_id="fake", display_name="Mia Brooks", birthday=value)


def test_birthday_schema_keeps_partial_and_full_values():
    assert (
        ContactIn(contact_id="fake", display_name="Mia Brooks", birthday="--02-29").birthday
        == "--02-29"
    )
    assert parse_birthday("2012-02-29") == date(2012, 2, 29)


def test_confident_inferences_keep_their_origin_and_explanation(database):
    assert (
        apply(
            database,
            inferred=True,
            inference_reason="The cited family introduction establishes the relationship.",
        )["contact_changes_applied"]
        == 1
    )
    assert all(f["inferred"] and f["inference_reason"] for f in knowledge.list_items(OWNER))


def test_new_parent_and_named_child_can_be_created_together(database):
    setup_owner(database)
    ingest(database, body="Robin Lake's daughter Nora has her birthday on May 12.")
    batch = intelligence.next_batch(OWNER, intelligence.utcnow() + timedelta(days=36500))
    ctx = intelligence.context_for(OWNER, batch)
    f = candidate(
        batch[0],
        contact_changes={
            "people": [
                {"ref": "new:parent", "display_name": "Robin Lake"},
                {"ref": "new:child", "display_name": "Nora", "birthday": "--05-12"},
            ],
            "relationships": [
                {
                    "from_ref": "new:child",
                    "to_ref": "new:parent",
                    "relationship_type": "Daughter",
                    "reciprocal_type": "Parent",
                }
            ],
        },
    )
    fs = intelligence.validate_findings({"findings": [f]}, batch, ctx, knowledge_only=True)
    assert (
        intelligence.apply_batch(OWNER, run(database), batch, ctx, fs)["contact_changes_applied"]
        == 1
    )


def test_identical_full_names_cannot_be_chosen_automatically(database):
    with database() as conn:
        conn.execute(
            "INSERT INTO contacts(contact_id,display_name) VALUES('duplicate-alex','Alex Carter')"
        )
    assert apply(database)["proposals_created"] == 1
    proposal = intelligence.list_proposals(OWNER)[0]
    assert {
        c["contact_id"] for c in proposal["payload"]["contact_plan"]["choices"][0]["candidates"]
    } == {"alex", "duplicate-alex"}


def test_invented_birth_year_and_ungrounded_person_are_rejected(database):
    batch, ctx, _ = prepare(database)
    f = candidate(batch[0])
    f["contact_changes"]["people"][1]["birthday"] = "2012-05-12"
    with pytest.raises(ValueError, match="invalid_contact_change"):
        intelligence.validate_findings({"findings": [f]}, batch, ctx)
    f["contact_changes"]["people"][1].update(display_name="Invented Child", birthday="--05-12")
    with pytest.raises(ValueError, match="invalid_contact_change"):
        intelligence.validate_findings({"findings": [f]}, batch, ctx)


def test_partial_birthday_survives_contact_api_roundtrip(database, monkeypatch):
    import contacts

    monkeypatch.setattr(contacts, "get_conn", database)
    monkeypatch.setattr(contacts, "_generate_contact_embedding", lambda value: [0.0])
    monkeypatch.setattr(contacts, "_enqueue_contact_tag_enrichment", lambda value: None)
    contacts.ingest_contact(
        ContactIn(contact_id="mia", display_name="Mia Brooks", birthday="--02-29")
    )
    saved = contacts.get_contact("mia")
    assert saved["birthday"] == "--02-29"
    saved["display_name"] = "Mia Lake"
    contacts.ingest_contact(ContactIn.model_validate(saved))
    assert contacts.get_contact("mia")["birthday"] == "--02-29"
    assert contacts.list_contacts()[1]["birthday"] == "--02-29"


def test_partial_birthday_is_in_normal_reminders_across_year_boundary(database, monkeypatch):
    from agents.daily_briefing import executor

    monkeypatch.setattr(executor, "get_conn", database)
    with database() as conn:
        conn.execute(
            "INSERT INTO contacts(contact_id,display_name,birthday_month,birthday_day) VALUES('mia','Mia Brooks',1,2),('leap','Robin Lake',2,29)"
        )
    result = executor._fetch_upcoming_birthdays(date(2026, 12, 30), lookahead_days=7)
    assert len(result) == 1
    assert result[0]["birthday"] == "--01-02" and result[0]["days_away"] == 3
    assert executor._fetch_upcoming_birthdays(date(2027, 2, 25)) == []
    assert executor._fetch_upcoming_birthdays(date(2028, 2, 25))[0]["days_away"] == 4


def test_graph_and_receipt_roll_back_together(database, monkeypatch):
    batch, ctx, fs = prepare(database)
    monkeypatch.setattr(
        intelligence,
        "_save_knowledge",
        lambda *args: (_ for _ in ()).throw(RuntimeError("injected write failure")),
    )
    with pytest.raises(RuntimeError, match="injected write failure"):
        intelligence.apply_batch(OWNER, run(database), batch, ctx, fs)
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM contact_relationships").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM intelligence_proposals").fetchone()["n"] == 0


def test_parallel_replays_create_only_one_contact_graph(database):
    from concurrent.futures import ThreadPoolExecutor

    _, _, fs = prepare(database)

    def save():
        with database() as conn:
            return contact_learning.save(conn.cursor(), OWNER, fs[0])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: save(), range(2)))
    assert results.count("contact_changes_applied") == 1 and results.count(None) == 1
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) AS n FROM contact_relationships").fetchone()["n"] == 1


def test_proposal_route_accepts_explicit_contact_selection(database):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes.proposals import create_proposals_router, get_current_user

    with database() as conn:
        conn.execute("INSERT INTO contacts(contact_id,display_name) VALUES('mia','Mia Brooks')")
    apply(database)
    proposal = intelligence.list_proposals(OWNER)[0]
    app = FastAPI()
    app.include_router(create_proposals_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": OWNER}
    with TestClient(app) as client:
        url = f"/mobile/proposals/{proposal['proposal_id']}/accept"
        assert client.post(url).status_code == 409
        assert client.post(url, json={"contact_resolutions": {"new:mia": "mia"}}).status_code == 200


def test_existing_unknown_year_can_be_filled_without_a_conflict(database):
    setup_owner(database)
    ingest(database, body="Alex Carter was born on May 12, 1987.")
    with database() as conn:
        conn.execute("UPDATE contacts SET birthday_month=5,birthday_day=12 WHERE contact_id='alex'")
    batch = intelligence.next_batch(OWNER, intelligence.utcnow() + timedelta(days=36500))
    ctx = intelligence.context_for(OWNER, batch)
    f = candidate(
        batch[0],
        contact_changes={
            "people": [
                {"ref": "contact:alex", "display_name": "Alex Carter", "birthday": "1987-05-12"}
            ],
            "relationships": [],
        },
    )
    fs = intelligence.validate_findings({"findings": [f]}, batch, ctx, knowledge_only=True)
    assert (
        intelligence.apply_batch(OWNER, run(database), batch, ctx, fs)["contact_changes_applied"]
        == 1
    )
    with database() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE contact_id='alex'").fetchone()
        assert birthday_value(row) == "1987-05-12" and row["birthday_month"] is None


def test_legacy_contact_enrichment_queues_the_shared_learner(database):
    import contact_enrichment_jobs

    contact_enrichment_jobs.analyze_contact_enrichment(
        user_email=OWNER, source_text=TEXT, contact_ids=["alex"]
    )
    contact_enrichment_jobs.analyze_contact_enrichment(
        user_email=OWNER, source_text=TEXT, contact_ids=["alex"]
    )
    with database() as conn:
        sources = conn.execute(
            "SELECT * FROM intelligence_sources WHERE kind='manual_fact'"
        ).fetchall()
        assert len(sources) == 1
        assert sources[0]["payload"]["text"] == TEXT
        assert sources[0]["payload"]["confirmed_contact_ids"] == ["alex"]
        assert conn.execute("SELECT COUNT(*) AS n FROM knowledge_learning").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM intelligence_proposals").fetchone()["n"] == 0


def test_new_contact_retries_other_deferred_facts_without_another_message(database, monkeypatch):
    text = TEXT + " Mia likes puzzles."
    with database() as conn:
        conn.execute("INSERT INTO conversation_threads VALUES('chat',%s)", (OWNER,))
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content) VALUES('chat','user',%s)",
            (text,),
        )
    calls = []

    def analyze(owner, batch, **kwargs):
        ctx = intelligence.context_for(owner, batch)
        calls.append(1)
        if len(calls) == 1:
            ctx["deferred_refs"] = [batch[0]["ref"]]
            f = candidate(batch[0])
        else:
            child = next(c for c in ctx["contacts"] if c["display_name"] == "Mia")
            f = {
                **finding(batch[0]),
                "subject_ref": "contact:" + child["contact_id"],
                "predicate": "interest.puzzles",
                "statement": "Mia likes puzzles.",
                "category": "preference",
            }
        return ctx, intelligence.validate_findings(
            {"findings": [f]}, batch, ctx, knowledge_only=kwargs["knowledge_only"]
        )

    monkeypatch.setattr(intelligence_model, "analyze", analyze)
    assert knowledge_jobs.process_once([OWNER])
    with database() as conn:
        assert (
            conn.execute("SELECT status FROM knowledge_learning").fetchone()["status"] == "pending"
        )
    assert knowledge_jobs.process_once([OWNER])
    assert knowledge.list_items(OWNER, query="puzzles")[0]["statement"] == "Mia likes puzzles."
    assert not knowledge_jobs.process_once([OWNER])


def test_reviewed_contact_creation_wakes_deferred_source(database):
    apply(database, confidence="medium")
    proposal = intelligence.list_proposals(OWNER)[0]
    with database() as conn:
        conn.execute(
            "INSERT INTO knowledge_learning(user_email,source_id,status) VALUES(%s,%s,'deferred')",
            (OWNER, proposal["source_evidence"][0]["source_id"]),
        )
    assert intelligence.resolve_proposal(OWNER, proposal["proposal_id"], accept=True)["accepted"]
    with database() as conn:
        assert (
            conn.execute("SELECT status FROM knowledge_learning").fetchone()["status"] == "pending"
        )
