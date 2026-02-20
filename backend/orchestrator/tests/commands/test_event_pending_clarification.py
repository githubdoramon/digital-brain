import re

import commands.event as event_command
from commands.event import event_pending_key, handle_pending_event
from commands.handlers import event as event_handler
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


def test_handle_event_surfaces_proposed_contact_groups(monkeypatch):
    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        return {
            "title": "Soccer meetup",
            "summary": "Met with my soccer team",
            "when": None,
            "where": "Field",
            "tags": ["personal"],
            "types": ["meeting"],
            "need_user_input": None,
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [
                    {
                        "contact_id": "contact-1",
                        "display_name": "Ana",
                        "query": "my soccer team",
                        "confidence": "medium",
                    },
                    {
                        "contact_id": "contact-2",
                        "display_name": "Bruno",
                        "query": "my soccer team",
                        "confidence": "medium",
                    },
                ],
                "new_entities": {
                    "contacts": [],
                    "places": [],
                    "documents": [],
                },
                "name_replacements": {},
                "proposed_contact_groups": [
                    {
                        "name": "soccer team",
                        "contact_ids": ["contact-1", "contact-2"],
                        "source": "inferred",
                    }
                ],
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
        args="met with my soccer team",
        raw_message="/event met with my soccer team",
    )
    context = {
        "user_email": "user@example.com",
        "event_pending_key": "user@example.com:thread-abc",
    }

    result = handle_event(parsed, context)

    assert result.get("type") == "need_user_input"
    need_user_input = result.get("need_user_input") or {}
    fields = need_user_input.get("fields") or []
    assert fields
    assert any("Save reusable group" in str(field.get("label") or "") for field in fields)

    preview_id = result.get("preview_id")
    if preview_id:
        delete_command_data(preview_id)
    clear_pending_event(context["event_pending_key"])


def test_handle_event_prefills_where_from_inferred_known_place(monkeypatch):
    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        return {
            "title": "Quick standup",
            "summary": "Team standup",
            "when": None,
            "where": None,
            "tags": ["work"],
            "types": ["meeting"],
            "need_user_input": None,
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
    monkeypatch.setattr(
        "commands.handlers.event.infer_current_place",
        lambda *_args, **_kwargs: {
            "place_id": "plc_home",
            "place_name": "Home",
            "city": "Aurora",
            "country": "Westoria",
            "source": "known_place_proximity",
            "confidence": "high",
        },
    )

    parsed = ParsedCommand(
        command="event",
        args="quick standup",
        raw_message="/event quick standup",
    )
    context = {
        "user_email": "user@example.com",
        "event_pending_key": "user@example.com:thread-xyz",
        "client_context": {"location": {"lat": 38.72, "lon": -9.13}},
    }

    result = handle_event(parsed, context)
    assert result.get("type") == "event_confirmation"
    assert result.get("extracted", {}).get("where") == "Home"
    assert result.get("resolution", {}).get("matched_place", {}).get("place_id") == "plc_home"


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
    monkeypatch.setattr(
        "commands.event.conversations.record_exchange", lambda *args, **kwargs: None
    )

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


def test_extract_clarification_detail_strips_prefixed_original_message():
    original = "met with Alex about the roadmap"
    message = (
        "met with Alex about the roadmap\n\n"
        "Additional details: None of these. It is a new contact, named Julia"
    )

    detail = event_handler._extract_clarification_detail(message, original)

    assert detail == "None of these. It is a new contact, named Julia"


def test_build_contact_context_message_formats_chronological_details():
    message = event_handler._build_contact_context_message(
        "met with Alex about the roadmap",
        [
            {"role": "assistant", "content": "Which Alex did you mean?"},
            {"role": "user", "content": "None of these. It is a new contact, named Alex"},
        ],
    )

    assert "Original event description: met with Alex about the roadmap" in message
    assert "Clarification details (chronological, oldest first):" in message
    assert "- None of these. It is a new contact, named Alex" in message


def test_format_clarification_history_is_chronological_transcript():
    history = event_handler._format_clarification_history(
        [
            {"role": "assistant", "content": "Which Julia did you mean?"},
            {"role": "user", "content": "None of these. New contact named Julia."},
        ]
    )

    assert "Clarification transcript (chronological, oldest first):" in history
    assert "- assistant: Which Julia did you mean?" in history
    assert "- user: None of these. New contact named Julia." in history


def test_safe_entity_slug_removes_reserved_characters():
    assert event_command._safe_entity_slug("Julia #1 / New Contact") == "julia-1-new-contact"


def test_apply_group_confirmation_from_answer_single_group_yes():
    groups = [
        {
            "name": "soccer team",
            "contact_ids": ["contact-1", "contact-2"],
            "source": "inferred",
        }
    ]

    updated, changed = event_handler._apply_group_confirmation_from_answer(
        groups,
        "yes, save this group",
    )

    assert changed is True
    assert updated[0].get("confirmed") is True


def test_apply_group_confirmation_from_answer_explicit_name_no():
    groups = [
        {
            "name": "soccer team",
            "contact_ids": ["contact-1", "contact-2"],
            "source": "inferred",
        },
        {
            "name": "startup friends",
            "contact_ids": ["contact-3", "contact-4"],
            "source": "inferred",
        },
    ]

    updated, changed = event_handler._apply_group_confirmation_from_answer(
        groups,
        "soccer team: no",
    )

    assert changed is True
    soccer = next(group for group in updated if group.get("name") == "soccer team")
    startup = next(group for group in updated if group.get("name") == "startup friends")
    assert soccer.get("confirmed") is False
    assert "confirmed" not in startup
