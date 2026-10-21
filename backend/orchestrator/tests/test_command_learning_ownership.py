# ruff: noqa: F811
"""Explicit updates retain evidence but have exactly one execution owner."""

from datetime import date

import pytest

import contact_command_knowledge
import intelligence
import knowledge
import knowledge_jobs
from commands.handlers import contact as handler
from commands.parser import ParsedCommand
from commands.storage import delete_command_data, get_command_data
from tests.test_intelligence import OWNER, database, setup_owner  # noqa: F401


def test_command_and_clarification_are_not_learned_or_supporting_sources(database):
    setup_owner(database)
    with database() as conn:
        conn.execute("INSERT INTO conversation_threads VALUES('chat',%s)", (OWNER,))
        for text, metadata in [
            ("/contact Set Alex Carter's birthday to today, born in 1987.", {}),
            ("I already said today", {"learning_owner": "contact"}),
            ("I prefer quiet rooms.", {}),
        ]:
            conn.execute(
                "INSERT INTO conversation_messages(thread_id,role,content,metadata) VALUES('chat','user',%s,%s)",
                (text, intelligence.json_value(metadata)),
            )
    knowledge_jobs.initialize_owner(OWNER)
    job = knowledge_jobs.claim([OWNER])
    batch = knowledge_jobs.source_batch(job)
    assert [row["text"] for row in batch] == ["I prefer quiet rooms."]
    assert knowledge_jobs.claim([OWNER]) is None
    with database() as conn:
        sources = conn.execute(
            "SELECT * FROM intelligence_sources WHERE kind='chat' ORDER BY source_id"
        ).fetchall()
        assert len(sources) == 3
        assert sources[0]["payload"]["learning_owner"] == "contact"
        assert not knowledge.source_is_current(conn.cursor(), sources[0], OWNER)
        assert conn.execute("SELECT COUNT(*) AS n FROM knowledge_learning").fetchone()["n"] == 1


def test_confirmation_records_reviewed_effects_once_without_extraction(database, monkeypatch):
    setup_owner(database)
    monkeypatch.setattr(contact_command_knowledge, "get_conn", database)
    monkeypatch.setattr(
        "intelligence_model.analyze",
        lambda *a, **k: pytest.fail("Explicit confirmation must not invoke extraction"),
    )
    with database() as conn:
        conn.execute("UPDATE contacts SET birthday='1987-05-12' WHERE contact_id='alex'")
    data = {
        "original_message": "Set Alex Carter's birthday to today, born in 1987.",
        "request_time": {"sent_at": "2028-05-12T10:00:00+00:00", "timezone": "UTC"},
    }
    proposal = {
        "contacts": [
            {
                "reference": "alex",
                "fields": {"birthday": "1987-05-12"},
                "merged": {"display_name": "Alex Carter", "birthday": "1987-05-12"},
            }
        ]
    }
    for _ in range(2):
        contact_command_knowledge.record_confirmation(
            OWNER, "preview:test", data, proposal, {"alex": "alex"}
        )
    knowledge_jobs.initialize_owner(OWNER)
    assert knowledge_jobs.claim([OWNER]) is None
    facts = knowledge.list_items(OWNER, subject="contact:alex")
    assert len(facts) == 1 and facts[0]["manual"]
    assert "May 12" in facts[0]["statement"]
    assert facts[0]["evidence"][0]["quote"] == data["original_message"]
    with database() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM intelligence_sources WHERE kind='manual_fact'"
            ).fetchone()["n"]
            == 1
        )
        assert conn.execute("SELECT birthday FROM contacts WHERE contact_id='alex'").fetchone()[
            "birthday"
        ] == date(1987, 5, 12)
        assert (
            conn.execute(
                "SELECT version FROM knowledge_summaries WHERE subject_ref='contact:alex'"
            ).fetchone()["version"]
            > 0
        )


def test_clarification_preserves_original_clock_across_midnight(monkeypatch):
    calls = []

    def extract(message, **kwargs):
        calls.append(kwargs)
        return {"contacts": [], "need_user_input": {"prompt": "Which Alex?", "fields": []}}

    monkeypatch.setattr(handler, "_llm_extract_contact_changes", extract)
    first = handler.handle_contact(
        ParsedCommand(command="contact", args="Set Alex's birthday to today", raw_message=""),
        {
            "user_email": OWNER,
            "request_time": {"sent_at": "2028-05-12T23:59:00+00:00", "timezone": "UTC"},
        },
    )
    ident = first["clarification_id"]
    try:
        original = get_command_data(ident)["request_time"]
        second = handler.handle_contact(
            ParsedCommand(
                command="contact",
                args=f"Set Alex's birthday to today\nAdditional details: Alex Carter\n[clarification_id:{ident}]",
                raw_message="",
            ),
            {
                "user_email": OWNER,
                "request_time": {"sent_at": "2028-05-13T00:01:00+00:00", "timezone": "UTC"},
            },
        )
        assert calls[1]["request_time"] == original
        assert calls[1]["conversation_messages"][-1]["sent_at"].startswith("2028-05-13")
        delete_command_data(second["clarification_id"])
    finally:
        delete_command_data(ident)


@pytest.mark.parametrize("database", ["pre_command_ownership"], indirect=True)
def test_upgrade_recovers_cancelled_followup_ownership_and_revokes_leases(database):
    from pathlib import Path

    setup_owner(database)
    with database() as conn:
        conn.execute("INSERT INTO conversation_threads VALUES('legacy',%s)", (OWNER,))
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content) VALUES('legacy','user','Alex Carter, I mean')"
        )
        conn.execute(
            "INSERT INTO conversation_messages(thread_id,role,content,metadata) VALUES('legacy','assistant','Please review',%s)",
            (
                intelligence.json_value(
                    {
                        "command_result": {"type": "contact_confirmation"},
                        "contact_resolved": "cancelled",
                    }
                ),
            ),
        )
        conn.execute(
            "UPDATE knowledge_learning SET status='running',lease_token='old-worker',lease_until=NOW()+INTERVAL '10 minutes'"
        )
        conn.execute(
            (
                Path(__file__).resolve().parents[1]
                / "db_migrations/0088_command_learning_ownership.sql"
            ).read_text()
        )
        row = conn.execute("SELECT * FROM knowledge_learning").fetchone()
        assert row["status"] == "done" and row["lease_token"] is None
        assert (
            conn.execute("SELECT metadata FROM conversation_messages WHERE role='user'").fetchone()[
                "metadata"
            ]["learning_owner"]
            == "contact"
        )
        # Even a fresh historical seeder must respect ownership.
        conn.execute("DELETE FROM knowledge_learning")
    knowledge_jobs.initialize_owner(OWNER)
    assert knowledge_jobs.claim([OWNER]) is None


def test_additional_facts_are_reviewed_in_the_same_proposal_and_need_exact_evidence(monkeypatch):
    monkeypatch.setattr(
        handler.contacts_service,
        "search_contacts",
        lambda *a, **k: [{"contact_id": "alex", "display_name": "Alex Carter", "match_score": 100}],
    )
    monkeypatch.setattr(
        handler.contacts_service,
        "get_contact",
        lambda *a, **k: {"contact_id": "alex", "display_name": "Alex Carter"},
    )
    text = "Set Alex Carter's birthday to May 12, 1987. Alex Carter prefers quiet rooms."
    proposal, need = handler._build_proposal(
        {
            "contacts": [{"contact_name": "Alex Carter", "birthday": "1987-05-12"}],
            "additional_knowledge": [
                {
                    "contact_name": "Alex Carter",
                    "predicate": "room_preference",
                    "statement": "Alex Carter prefers quiet rooms.",
                    "quote": "Alex Carter prefers quiet rooms.",
                },
                {
                    "contact_name": "Alex Carter",
                    "predicate": "invented_preference",
                    "statement": "Alex Carter prefers loud rooms.",
                    "quote": "Alex Carter prefers loud rooms.",
                },
            ],
        },
        original_message=text,
    )
    assert need is None
    assert len(proposal["additional_knowledge"]) == 1
    assert proposal["additional_knowledge"][0]["reference"] == "alex"
    assert "Alex Carter prefers quiet rooms." in proposal["summary_lines"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_natural_updates_use_contact_workflow_without_general_agent_execution(
    monkeypatch, stream
):
    from agent.router import IntentClassification, IntentType
    from tests.agent.test_controller import _build_controller

    controller = _build_controller()

    async def classify(*args, **kwargs):
        return IntentClassification(
            intent=IntentType.CONTACT_UPDATE, confidence=0.99, allowed_tool_groups=[]
        )

    monkeypatch.setattr(controller.intent_router, "classify", classify)
    monkeypatch.setattr(controller, "_inject_inferred_location", lambda **kwargs: None)
    monkeypatch.setattr(
        controller,
        "_call_llm",
        lambda *a, **k: pytest.fail("General tool loop must not handle contact updates"),
    )
    calls = []

    def prepare(question, owner, thread_id, context, history):
        calls.append((question, owner, thread_id))
        return (
            {"type": "contact_confirmation", "preview_id": "contact:preview:test"},
            "Please review the contact update.",
            None,
        )

    monkeypatch.setattr("commands.contact_flow.prepare_update", prepare)
    kwargs = {
        "question": "Set Alex Carter's birthday to May 12, 1987",
        "user_email": OWNER,
        "session_id": "thread",
    }
    if stream:
        events = [event async for event in controller.run_stream(**kwargs)]
        result = next(event["bundle"] for event in events if event["type"] == "done")
    else:
        result = await controller.run(**kwargs)
    assert len(calls) == 1
    assert result["learning_owner"] == "contact"
    assert result["command_result"]["type"] == "contact_confirmation"


def test_natural_and_slash_requests_share_handler_preview_and_restore_state(monkeypatch):
    from commands import get_command_registry, parse_command
    from commands.contact_flow import prepare_update

    monkeypatch.setattr(
        handler,
        "_llm_extract_contact_changes",
        lambda *a, **k: {
            "contacts": [{"contact_name": "Alex Carter", "birthday": "1987-05-12"}],
            "need_user_input": None,
        },
    )
    monkeypatch.setattr(
        handler.contacts_service,
        "search_contacts",
        lambda *a, **k: [{"contact_id": "alex", "display_name": "Alex Carter", "match_score": 100}],
    )
    monkeypatch.setattr(
        handler.contacts_service,
        "get_contact",
        lambda *a, **k: {"contact_id": "alex", "display_name": "Alex Carter"},
    )
    text = "Set Alex Carter's birthday to today, born in 1987."
    natural, _, directives = prepare_update(text, OWNER, "natural", {"timezone": "UTC"})
    slash = get_command_registry().execute(
        parse_command("/contact " + text), {"user_email": OWNER, "thread_id": "slash"}
    )
    try:
        assert natural["type"] == slash["type"] == "contact_confirmation"
        assert directives is not None
        assert (
            natural["proposal"]["contacts"][0]["merged"]["birthday"]
            == slash["proposal"]["contacts"][0]["merged"]["birthday"]
        )
        state = natural["command_state"]
        assert state["request_time"]["timezone"] == "UTC"
        assert state["original_message"] == text
        assert state["conversation_messages"][0]["sent_at"]
    finally:
        for result in (natural, slash):
            delete_command_data(result["preview_id"])
        from commands.event import event_pending_key
        from commands.storage import clear_pending_event

        clear_pending_event(event_pending_key(OWNER, "natural"))
