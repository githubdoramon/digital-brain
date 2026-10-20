# ruff: noqa: F811
"""Derived prose uses names; opaque entity links and original evidence stay intact."""

import pytest

import intelligence
import knowledge
import knowledge_jobs
import knowledge_prose as prose
from tests.test_intelligence import (  # noqa: F401
    OTHER,
    OWNER,
    database,
    finding,
    ingest,
    run,
    setup_owner,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Alex Carter (identity:opaque-id, contact:external:example-id) dislikes loud keyboards.",
            "Alex Carter dislikes loud keyboards.",
        ),
        ("contact:contact:alex works remotely.", "Alex Carter works remotely."),
        (
            "identity:unresolved-person said the package arrived.",
            "this person said the package arrived.",
        ),
        ("Identity " + "a" * 64 + " prefers quiet rooms.", "Dana Lewis prefers quiet rooms."),
        ("The import failed (see related knowledge abc12345).", "The import failed."),
        (
            "The integration URL is https://example.test/contact:alex.",
            "The integration URL is https://example.test/contact:alex.",
        ),
        (
            "The keyboard (Slate Mini) failed on 2026-01-02.",
            "The keyboard (Slate Mini) failed on 2026-01-02.",
        ),
    ],
)
def test_clean_metadata_without_erasing_dates_or_semantic_parentheses(text, expected):
    assert (
        prose.clean(
            text, {"contact:contact:alex": "Alex Carter", "identity:" + "a" * 64: "Dana Lewis"}
        )
        == expected
    )


def test_validation_persists_readable_fact_with_opaque_links_and_exact_quotes(database):
    setup_owner(database)
    ingest(database, body="Alex Carter (contact:alex) prefers quiet keyboards.")
    batch = intelligence.next_batch(OWNER, intelligence.utcnow())
    context = intelligence.context_for(OWNER, batch)
    candidate = finding(batch[0])
    candidate.update(
        statement="Alex Carter (contact:alex) prefers quiet keyboards.", subject_ref="contact:alex"
    )
    result = intelligence.validate_findings({"findings": [candidate]}, batch, context)
    assert result[0]["statement"] == "Alex Carter prefers quiet keyboards."
    assert result[0]["subject_ref"] == "contact:alex"
    assert result[0]["evidence"][0]["quote"] == batch[0]["text"]
    intelligence.apply_batch(OWNER, run(database), batch, context, result)
    with database() as conn:
        stored = conn.execute("SELECT statement,evidence FROM intelligence_knowledge").fetchone()
        assert stored["statement"] == "Alex Carter prefers quiet keyboards."
        assert stored["evidence"][0]["quote"] == batch[0]["text"]
        assert (
            conn.execute("SELECT subject_ref FROM knowledge_subjects").fetchone()["subject_ref"]
            == "contact:alex"
        )


def test_existing_text_is_returned_as_stored_without_read_time_rewriting(database):
    from tests.test_knowledge import save

    fact = save(database)
    original = "Alex Carter (contact:alex) received the quote."
    with database() as conn:
        conn.execute(
            "UPDATE intelligence_knowledge SET statement=%s WHERE knowledge_id=%s",
            (original, fact["knowledge_id"]),
        )
        conn.execute(
            "UPDATE knowledge_summaries SET summary=%s WHERE user_email=%s", (original, OWNER)
        )
    assert knowledge.list_items(OWNER)[0]["statement"] == original
    assert knowledge.summary_for(OWNER, "self")["summary"] == original
    with database() as conn:
        assert (
            conn.execute("SELECT statement FROM intelligence_knowledge").fetchone()["statement"]
            == original
        )
    knowledge.change(OWNER, fact["knowledge_id"], statement=original)
    assert knowledge.list_items(OWNER)[0]["statement"] == original


def test_private_identity_names_never_resolve_from_another_owners_archive(database):
    ingest(database, owner=OTHER)
    with database() as conn:
        identity = conn.execute("SELECT identity_id FROM messaging_identities LIMIT 1").fetchone()[
            "identity_id"
        ]
        text = f"identity:{identity} said the parcel arrived."
        assert (
            prose.clean_rows(conn.cursor(), OWNER, [{"statement": text}])[0]["statement"]
            == "this person said the parcel arrived."
        )


def test_summary_and_both_extraction_phases_share_prose_guidance(monkeypatch):
    prompts = []

    def call(*args, **kwargs):
        prompts.append(kwargs["system_prompt"])
        return {"summary": "A readable summary."}

    monkeypatch.setattr("llm_helpers.call_llm_json", call)
    knowledge_jobs._summary_response([], {"type": "object"}, 0)
    assert prose.PROSE_INSTRUCTIONS in prompts[0]
    import intelligence_model

    monkeypatch.setattr(
        intelligence_model,
        "call_llm_json",
        lambda *a, **kw: (
            prompts.append(kw["system_prompt"]) or {"findings": [], "deferred_refs": []}
        ),
    )
    for phase in ("Extract", "Reconcile"):
        intelligence_model._ask_validated({"sources": []}, phase=phase)
    assert all(prose.PROSE_INSTRUCTIONS in prompt for prompt in prompts)
