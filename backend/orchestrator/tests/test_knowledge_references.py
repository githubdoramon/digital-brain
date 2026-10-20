# ruff: noqa: F811
"""Opaque prefixed IDs must keep the same identity through both model phases."""

import json

import pytest

import intelligence
import intelligence_context
import intelligence_model
import knowledge
from knowledge_references import contact_ref, normalize_finding, resolve_contact_ref
from tests.test_contact_learning import TEXT, candidate
from tests.test_intelligence import (  # noqa: F401
    FUTURE,
    OWNER,
    database,
    finding,
    ingest,
    run,
    setup_owner,
)


def test_prompt_supplies_exact_subject_refs_and_retains_opaque_ids():
    context = {
        "contacts": [{"contact_id": "contact:alex", "display_name": "Alex Carter"}],
        "people": [{"identity_id": "author", "contact_id": "contact:alex"}],
        "contact_relationships": [{"from_contact_id": "contact:alex", "to_contact_id": "mia"}],
    }
    prompt = intelligence_context.compact_context(context, extraction=True)
    assert prompt["contacts"][0]["contact_id"] == "contact:alex"
    assert prompt["contacts"][0]["subject_ref"] == "contact:contact:alex"
    assert prompt["people"][0]["subject_ref"] == "identity:author"
    assert prompt["contact_relationships"][0]["from_ref"] == "contact:contact:alex"
    assert context["contacts"][0] == {"contact_id": "contact:alex", "display_name": "Alex Carter"}


def test_exact_canonical_refs_win_over_opaque_id_alias_collisions():
    contacts = [{"contact_id": "alex"}, {"contact_id": "contact:alex"}]
    assert resolve_contact_ref("contact:alex", contacts) == "contact:alex"
    assert resolve_contact_ref("contact:contact:alex", contacts) == "contact:contact:alex"
    assert resolve_contact_ref("contact:unknown", contacts) == "contact:unknown"
    assert resolve_contact_ref("alex", contacts) == "alex"
    only_prefixed = {"contacts": [{"contact_id": "contact:alex", "display_name": "Alex Carter"}]}
    supplied = {
        "subject_ref": "contact:alex",
        "related_subjects": ["contact:alex", "contact:contact:alex"],
    }
    normalized = normalize_finding(supplied, only_prefixed)
    assert normalized["subject_ref"] == contact_ref("contact:alex")
    assert normalized["related_subjects"] == ["contact:contact:alex"]
    assert supplied["subject_ref"] == "contact:alex"
    context_with_existing_ref = {**only_prefixed, "entities": [{"entity_ref": "contact:alex"}]}
    assert normalize_finding(supplied, context_with_existing_ref)["subject_ref"] == "contact:alex"
    with pytest.raises(ValueError, match="invalid_subject"):
        knowledge.validate_entities({"subject_ref": "contact:unknown"}, only_prefixed, [])


def test_alias_normalization_cannot_bypass_name_ambiguity():
    context = {
        "contacts": [
            {"contact_id": "contact:alex", "display_name": "Alex Carter"},
            {"contact_id": "contact:other", "display_name": "Alex Lane"},
        ]
    }
    assert knowledge.ambiguous_contact(
        {"subject_ref": "contact:alex"}, context, [{"quote": "Alex likes keyboards."}]
    )


def test_prefixed_contacts_survive_extract_reconcile_validate_without_repair(database, monkeypatch):
    setup_owner(database)
    with database() as conn:
        conn.execute("DELETE FROM contacts WHERE contact_id='alex'")
        conn.execute(
            "INSERT INTO contacts(contact_id,display_name) VALUES('contact:alex','Alex Carter')"
        )
    ingest(database, body="Alex Carter prefers quiet keyboards.")
    batch = intelligence.next_batch(OWNER, FUTURE)
    f = {
        **finding(batch[0]),
        "subject_ref": "contact:alex",
        "assignee_kind": "contact",
        "assignee_ref": "contact:contact:alex",
    }
    payloads = []

    def respond(prompt, **kwargs):
        payloads.append(json.loads(prompt))
        return {"findings": [f], "deferred_refs": []}

    monkeypatch.setattr(intelligence_model, "call_llm_json", respond)
    ctx, findings = intelligence_model.analyze(OWNER, batch)
    assert len(payloads) == 2
    assert all("validation_feedback" not in payload for payload in payloads)
    assert all(
        any(c["subject_ref"] == "contact:contact:alex" for c in payload["context"]["contacts"])
        for payload in payloads
    )
    assert findings[0]["subject_ref"] == "contact:contact:alex"
    assert findings[0]["assignee_ref"] == "contact:alex"
    intelligence.apply_batch(OWNER, run(database), batch, ctx, findings)
    assert len(knowledge.list_items(OWNER, subject="contact:contact:alex")) == 1


def test_birthday_and_relationship_refs_share_the_same_normalization(database):
    setup_owner(database)
    with database() as conn:
        conn.execute("DELETE FROM contacts WHERE contact_id='alex'")
        conn.execute(
            "INSERT INTO contacts(contact_id,display_name) VALUES('contact:alex','Alex Carter')"
        )
    ingest(database, body=TEXT)
    batch = intelligence.next_batch(OWNER, FUTURE)
    ctx = intelligence.context_for(OWNER, batch)
    fs = intelligence.validate_findings(
        {"findings": [candidate(batch[0])]}, batch, ctx, knowledge_only=True
    )
    assert fs[0]["contact_changes"]["people"][0]["ref"] == "contact:contact:alex"
    assert fs[0]["contact_changes"]["relationships"][0]["to_ref"] == "contact:contact:alex"
    assert (
        intelligence.apply_batch(OWNER, run(database), batch, ctx, fs)["contact_changes_applied"]
        == 1
    )
    with database() as conn:
        assert (
            conn.execute("SELECT to_contact_id FROM contact_relationships").fetchone()[
                "to_contact_id"
            ]
            == "contact:alex"
        )
