import re

from commands.event import event_pending_key, handle_pending_event
from commands.handlers.event import handle_event
from commands.parser import ParsedCommand
from commands.storage import clear_pending_event, delete_command_data, get_pending_event


def test_handle_event_sets_pending_key_for_clarification(monkeypatch):
    pending_key = "user@example.com:thread-123"

    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        return {
            "title": "Project check-in",
            "summary": "Met to review progress.",
            "when": None,
            "where": "Office",
            "tags": [],
            "types": ["meeting"],
            "need_user_input": {
                "questions": ["When did this happen?"],
                "fields": [
                    {
                        "id": "when",
                        "kind": "text",
                        "label": "When",
                        "required": True,
                    }
                ],
            },
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [],
                "new_entities": {
                    "contacts": [],
                    "places": [],
                    "documents": [],
                },
                "name_replacements": {},
            },
            {
                "ambiguous_contacts": [],
                "suggested_relationships": [],
            },
        )

    monkeypatch.setattr(
        "commands.handlers.event._extract_event_entities_with_llm",
        fake_extract_event_entities,
    )
    monkeypatch.setattr(
        "commands.handlers.event._resolve_contacts_with_agent",
        fake_resolve_contacts,
    )

    parsed = ParsedCommand(
        command="event",
        args="Met with Alex to discuss roadmap",
        raw_message="/event Met with Alex to discuss roadmap",
    )
    context = {
        "user_email": "user@example.com",
        "event_pending_key": pending_key,
    }

    result = handle_event(parsed, context)

    clarification_id = result.get("clarification_id")
    assert result.get("type") == "need_user_input"
    assert clarification_id
    assert get_pending_event(pending_key) == clarification_id

    clear_pending_event(pending_key)
    delete_command_data(clarification_id)


def test_pending_clarification_accepts_plain_follow_up(monkeypatch):
    user_email = "user@example.com"
    thread_id = "thread-123"
    pending_key = event_pending_key(user_email, thread_id)
    preview_id = "event:clarification:abc12345"

    from commands.storage import store_command_data, store_pending_event

    store_command_data(
        preview_id,
        {
            "original_message": "met with Alex about the roadmap",
            "thread_id": thread_id,
            "extracted": {},
            "resolution": {},
        },
    )
    store_pending_event(pending_key, preview_id)

    parse_inputs: list[str] = []

    def fake_parse_command(message: str):
        parse_inputs.append(message)
        return object() if message.startswith("/event ") else None

    class _Registry:
        @staticmethod
        def execute(parsed, context):
            assert parsed is not None
            assert context.get("thread_id") == thread_id
            return {"type": "need_user_input", "message": "follow-up accepted"}

    monkeypatch.setattr("commands.event.parse_command", fake_parse_command)
    monkeypatch.setattr("commands.event.get_command_registry", lambda: _Registry())
    monkeypatch.setattr("commands.event.conversations.record_exchange", lambda *args, **kwargs: None)

    result = handle_pending_event(
        question="It was yesterday at 3pm",
        user_email=user_email,
        user={"email": user_email},
        thread_id=thread_id,
        pending_event_id=preview_id,
        command_response_text=lambda command_result: command_result.get("message", ""),
        command_assistant_metadata=lambda command_result: ({}, None),
    )

    assert result is not None
    assert len(parse_inputs) >= 2
    combined_message = parse_inputs[1]
    assert combined_message.startswith("/event met with Alex about the roadmap")
    assert "Additional details: It was yesterday at 3pm" in combined_message
    assert "[clarification_id:event:clarification:" in combined_message
    assert get_pending_event(pending_key) is None

    match = re.search(r"\[clarification_id:([^\]]+)\]", combined_message)
    if match:
        delete_command_data(match.group(1))
    clear_pending_event(pending_key)
