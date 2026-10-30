# ruff: noqa: F811
"""Synthetic conversations: later replies, owner isolation and delayed proposals."""

from datetime import datetime, timedelta, timezone

import pytest

import intelligence as store
import intelligence_context
import intelligence_model as model
from tests.test_intelligence import (  # noqa: F401
    FUTURE,
    OTHER,
    OWNER,
    database,
    finding,
    ingest,
    run,
    setup_owner,
)

START = datetime(2026, 1, 3, 10, tzinfo=timezone.utc)


def request(database):
    setup_owner(database)
    ingest(
        database,
        ident="request",
        body="Please send the full quote to Alex.",
        sent=START.isoformat(),
        outgoing=False,
    )
    return store.next_batch(OWNER, FUTURE)


def task(batch, context):
    f = finding(batch[0], kind="task")
    f["statement"] = "Send the full quote to Alex"
    return store.validate_findings({"findings": [f]}, batch, context)[0]


def completion(database, **kwargs):
    ingest(
        database,
        ident="reply",
        body="Done, I sent the full quote to Alex.",
        sent=(START + timedelta(hours=1)).isoformat(),
        **kwargs,
    )


def test_context_reads_both_directions_and_keeps_short_replies_in_extraction(database):
    batch = request(database)
    ingest(
        database,
        ident="before",
        body="The quote needs the updated pricing.",
        sent=(START - timedelta(hours=1)).isoformat(),
    )
    completion(database)
    context = store.context_for(OWNER, batch)
    assert {m["direction"] for m in context["lookback"]} == {"before", "after"}
    assert all(m["anchor_ref"] == batch[0]["ref"] for m in context["lookback"])
    projected = intelligence_context.compact_context(
        context, extraction=True, query="unrelated request words"
    )
    assert len(projected["lookback"]) == 2
    assert projected["tasks"] == []
    assert "body_hash" not in projected["lookback"][0]


def test_context_prioritizes_explicit_late_reply_and_stays_bounded(database):
    batch = request(database)
    for index in range(20):
        ingest(
            database,
            ident=f"noise-{index}",
            body=f"Unrelated exchange {index}",
            sent=(START + timedelta(minutes=index + 1)).isoformat(),
        )
    ingest(
        database,
        ident="late-reply",
        body="Done, the full quote was sent.",
        sent=(START + timedelta(days=9)).isoformat(),
    )
    with database() as conn:
        conn.execute(
            "UPDATE messaging_messages SET reply_id='request' WHERE external_id='late-reply'"
        )
    context = store.context_for(OWNER, batch)
    assert len(context["lookback"]) <= 18
    assert any("full quote was sent" in m["body"] for m in context["lookback"])


def test_context_excludes_other_owners_deleted_and_future_messages(database, monkeypatch):
    batch = request(database)
    completion(database)
    ingest(
        database,
        owner=OTHER,
        ident="private",
        body="Other owner private reply",
        sent=(START + timedelta(minutes=1)).isoformat(),
    )
    ingest(
        database, ident="future", body="Future reply", sent=(START + timedelta(days=3)).isoformat()
    )
    with database() as conn:
        conn.execute("UPDATE messaging_messages SET deleted=TRUE WHERE external_id='reply'")
    monkeypatch.setattr(store, "utcnow", lambda: START + timedelta(days=1))
    assert store.context_for(OWNER, batch)["lookback"] == []


@pytest.mark.parametrize("changed", [None, "deleted", "edited", "forwarded"])
def test_verified_later_completion_suppresses_only_fresh_matching_task(
    database, monkeypatch, changed
):
    batch = request(database)
    completion(database)
    context = store.context_for(OWNER, batch)
    f = task(batch, context)
    reply = next(m for m in context["lookback"] if m["direction"] == "after")
    calls = []

    def review(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return {
            "completed": True,
            "message_id": reply["message_id"],
            "quote": "I sent the full quote to Alex.",
        }

    monkeypatch.setattr(model, "call_llm_json", review)
    model._review_resolved_request(f, batch, context)
    assert f.get("verified_resolution") and len(calls) == 1
    assert calls[0][1]["reasoning_effort"] == "none"
    with database() as conn:
        if changed == "deleted":
            conn.execute(
                "UPDATE messaging_messages SET deleted=TRUE WHERE message_id=%s",
                (reply["message_id"],),
            )
        elif changed == "edited":
            conn.execute(
                "UPDATE messaging_messages SET body=body || ' Actually, that was a different quote.' WHERE message_id=%s",
                (reply["message_id"],),
            )
        elif changed == "forwarded":
            conn.execute(
                "UPDATE messaging_messages SET forwarded=TRUE WHERE message_id=%s",
                (reply["message_id"],),
            )
    counts = store.apply_batch(OWNER, run(database), batch, context, [f])
    assert counts["proposals_suppressed"] == (1 if changed is None else 0)
    assert len(store.list_proposals(OWNER)) == (0 if changed is None else 1)
    if changed:
        assert "verified_resolution" not in store.list_proposals(OWNER)[0]["payload"]


@pytest.mark.parametrize("outgoing,forwarded", [(False, False), (True, True)])
def test_wrong_actor_or_forwarded_reply_never_suppresses_task(
    database, monkeypatch, outgoing, forwarded
):
    batch = request(database)
    completion(database, outgoing=outgoing, forwarded=forwarded)
    context = store.context_for(OWNER, batch)
    f = task(batch, context)
    monkeypatch.setattr(
        model, "call_llm_json", lambda *a, **k: pytest.fail("ineligible completion reviewed")
    )
    model._review_resolved_request(f, batch, context)
    assert not f.get("verified_resolution")
    store.apply_batch(OWNER, run(database), batch, context, [f])
    assert len(store.list_proposals(OWNER)) == 1


@pytest.mark.parametrize(
    "answer",
    [
        {"completed": False, "message_id": None, "quote": None},
        {"completed": True, "message_id": "invented", "quote": "Done"},
    ],
)
def test_uncertain_or_invented_review_cannot_suppress_task(database, monkeypatch, answer):
    batch = request(database)
    completion(database)
    context = store.context_for(OWNER, batch)
    f = task(batch, context)
    monkeypatch.setattr(model, "call_llm_json", lambda *a, **k: answer)
    model._review_resolved_request(f, batch, context)
    store.apply_batch(OWNER, run(database), batch, context, [f])
    assert len(store.list_proposals(OWNER)) == 1


@pytest.mark.parametrize("body", ["Done.", "Done, I sent the full quote to Alex."])
def test_completion_before_delayed_proposal_creation_expires_proposal(database, body):
    batch = request(database)
    context = store.context_for(OWNER, batch)
    store.apply_batch(OWNER, run(database), batch, context, [task(batch, context)])
    proposal = store.list_proposals(OWNER)[0]
    ingest(database, ident="reply", body=body, sent=(START + timedelta(hours=1)).isoformat())
    batch = store.next_batch(OWNER, FUTURE)
    context = store.context_for(OWNER, batch)
    known = next(t for t in context["tasks"] if t.get("is_proposal"))
    assert known["requested_at"] == START and known["created_at"] > START + timedelta(hours=1)
    f = finding(batch[0], kind="complete")
    f["existing_id"] = proposal["proposal_id"]
    f = store.validate_findings({"findings": [f]}, batch, context)[0]
    f["verified_completion"] = True
    store.apply_batch(OWNER, run(database), batch, context, [f])
    assert store.list_proposals(OWNER) == []


def test_normal_task_still_requires_completion_after_creation(database):
    request(database)
    completion(database)
    with database() as conn:
        conn.execute(
            "INSERT INTO todos(todo_id,description,created_at) VALUES ('manual','Send the full quote to Alex',NOW())"
        )
    batch = [r for r in store.next_batch(OWNER, FUTURE) if r["payload"]["text"].startswith("Done")]
    context = store.context_for(OWNER, batch)
    f = finding(batch[0], kind="complete")
    f["existing_id"] = "manual"
    f = store.validate_findings({"findings": [f]}, batch, context)[0]
    f["verified_completion"] = True
    store.apply_batch(OWNER, run(database), batch, context, [f])
    with database() as conn:
        assert (
            conn.execute("SELECT status FROM todos WHERE todo_id='manual'").fetchone()["status"]
            == "pending"
        )


def test_short_quotes_remain_invalid_for_new_work_and_facts():
    from learning_validation import LearningValidationError

    row = {"ref": "1:0", "text": "Done.", "source_id": 1, "part": 0}
    for kind in ("task", "knowledge"):
        with pytest.raises(LearningValidationError, match="invalid_evidence"):
            store.evidence_for(
                {"kind": kind, "evidence": [{"ref": "1:0", "quote": "Done."}]}, [row]
            )
