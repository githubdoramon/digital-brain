import re

import pytest

import commands.event as event_command
from commands.event import event_pending_key, handle_pending_event
from commands.handlers import event as event_handler
from commands.handlers.event import handle_event
from commands.parser import ParsedCommand
from commands.storage import (
    clear_pending_event,
    delete_command_data,
    get_pending_event,
    store_command_data,
    store_pending_event,
)


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


def test_handle_event_raises_when_llm_is_unavailable(monkeypatch):
    def fake_extract_event_entities(*_args, **_kwargs):
        raise event_handler.LLMUnavailableError("LLM service is unavailable")

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "name_replacements": {},
            },
            {"ambiguous_contacts": [], "suggested_relationships": []},
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
        "event_pending_key": "user@example.com:thread-123",
    }

    with pytest.raises(event_handler.LLMUnavailableError):
        handle_event(parsed, context)


def test_replace_generic_terms_handles_none_values():
    assert event_handler._replace_generic_terms_in_text(None, {"my wife": "Ana"}) == ""
    assert (
        event_handler._replace_generic_terms_in_text("Dinner with my wife", {"my wife": ""})
        == "Dinner with "
    )


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
    monkeypatch.setattr(
        "commands.handlers.event.places_service.find_best_place_match", lambda *a, **k: None
    )
    monkeypatch.setattr("commands.handlers.event.geocode_place_name", lambda *a, **k: None)

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


def test_handle_event_defers_inferred_place_without_explicit_where(monkeypatch):
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
    monkeypatch.setattr(
        "commands.handlers.event.places_service.find_best_place_match", lambda *a, **k: None
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
    assert result.get("extracted", {}).get("where") is None
    assert result.get("resolution", {}).get("matched_place") is None
    assert result.get("resolution", {}).get("inferred_location", {}).get("place_id") == "plc_home"


def test_handle_event_skips_low_confidence_inferred_known_place(monkeypatch):
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
            "confidence": "low",
            "distance_m": 120,
        },
    )
    monkeypatch.setattr(
        "commands.handlers.event.places_service.find_best_place_match", lambda *a, **k: None
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
    assert result.get("extracted", {}).get("where") is None
    assert result.get("resolution", {}).get("matched_place") is None
    assert result.get("resolution", {}).get("inferred_location", {}).get("place_id") == "plc_home"


def test_handle_event_preserves_original_preview_before_match_merge(monkeypatch):
    def fake_extract_event_entities(*_args, **_kwargs):
        return {
            "title": "Lunch with Sam",
            "summary": "Talked about next quarter planning.",
            "when": "2026-05-02T12:30:00",
            "end_when": None,
            "where": "Corner Cafe",
            "documents": [],
            "tags": ["work"],
            "types": ["meeting"],
            "need_user_input": None,
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [{"contact_id": "contact:sam", "display_name": "Sam"}],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "matched_place": None,
            },
            {"ambiguous_contacts": []},
        )

    monkeypatch.setattr(
        "commands.handlers.event._extract_event_entities_with_llm",
        fake_extract_event_entities,
    )
    monkeypatch.setattr(
        "commands.handlers.event._resolve_contacts_with_agent",
        fake_resolve_contacts,
    )
    monkeypatch.setattr("commands.handlers.event.infer_current_place", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "commands.handlers.event.places_service.find_best_place_match",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr("commands.handlers.event.geocode_place_name", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "commands.handlers.event._find_event_matches",
        lambda *_a, **_k: {
            "operation": "update",
            "existing_event_id": "event:existing-lunch",
            "matched_event": {
                "event_id": "event:existing-lunch",
                "title": "Lunch with Sam",
                "start_date": "2026-05-01T12:00:00",
                "place": {"place_id": "place:old-cafe", "name": "Old Cafe"},
                "match_score": 96,
            },
            "candidates": [],
        },
    )
    monkeypatch.setattr(
        "commands.handlers.event.events_service.get_event_by_id",
        lambda event_id: {
            "id": event_id,
            "title": "Lunch with Sam",
            "summary": "Existing summary",
            "start_date": "2026-05-01T12:00:00",
            "end_date": None,
            "place_id": "place:old-cafe",
            "tags": ["work", "planning"],
            "types": ["meeting"],
            "people": ["contact:sam", "contact:alex"],
        },
    )
    monkeypatch.setattr(
        "commands.handlers.event.places_service.get_place",
        lambda place_id: {"place_id": place_id, "name": "Old Cafe"},
    )
    monkeypatch.setattr(
        "commands.handlers.event.contacts_service.get_contact",
        lambda contact_id: {"contact_id": contact_id, "display_name": "Alex"},
    )

    result = handle_event(
        ParsedCommand(
            command="event",
            args="had lunch with Sam at Corner Cafe and talked about next quarter planning",
            raw_message="/event had lunch with Sam at Corner Cafe and talked about next quarter planning",
        ),
        {"user_email": "user@example.com", "thread_id": "thread-lunch"},
    )

    assert result["type"] == "event_confirmation"
    assert result["operation"] == "update"
    assert result["extracted"]["when"].isoformat() == "2026-05-01T12:00:00+00:00"
    assert result["extracted"]["where"] == "Old Cafe"
    assert result["original_extracted"]["when"] == "2026-05-02T12:30:00"
    assert result["original_extracted"]["where"] == "Corner Cafe"
    assert result["original_resolution"]["contacts"] == [
        {"contact_id": "contact:sam", "display_name": "Sam"}
    ]

    delete_command_data(result["preview_id"])


def test_handle_event_maps_similar_where_to_existing_place(monkeypatch):
    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        return {
            "title": "Dinner",
            "summary": "Dinner with friends",
            "when": None,
            "where": "my house",
            "tags": ["personal"],
            "types": ["memory"],
            "need_user_input": None,
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "name_replacements": {},
            },
            {"ambiguous_contacts": [], "suggested_relationships": []},
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
        "commands.handlers.event.places_service.find_best_place_match",
        lambda *_a, **_k: {
            "place_id": "plc_home",
            "name": "Home",
            "match_confidence": "high",
            "matched_via": "alias_exact",
            "match_score": 98.0,
        },
    )

    parsed = ParsedCommand(command="event", args="dinner", raw_message="/event dinner")
    context = {"user_email": "user@example.com", "event_pending_key": "user@example.com:thread-x"}

    result = handle_event(parsed, context)
    assert result.get("type") == "event_confirmation"
    assert result.get("extracted", {}).get("where") == "Home"
    assert result.get("resolution", {}).get("matched_place", {}).get("place_id") == "plc_home"
    assert result.get("resolution", {}).get("matched_place", {}).get("pending_alias") == "my house"
    assert result.get("resolution", {}).get("new_entities", {}).get("places") == []


def test_handle_event_geocodes_new_where_when_not_matched(monkeypatch):
    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        return {
            "title": "Lunch",
            "summary": "Lunch note",
            "when": None,
            "where": "best burger place",
            "tags": ["food"],
            "types": ["memory"],
            "need_user_input": None,
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "name_replacements": {},
            },
            {"ambiguous_contacts": [], "suggested_relationships": []},
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
        "commands.handlers.event.places_service.find_best_place_match", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "commands.handlers.event.geocode_place_name",
        lambda *_a, **_k: {
            "place_name": "Burger Palace",
            "city": "Aurora",
            "country": "Westoria",
            "lat": 38.72,
            "lon": -9.14,
            "provider": "geoapify",
            "source": "geoapify_forward_geocode",
        },
    )

    parsed = ParsedCommand(command="event", args="lunch", raw_message="/event lunch")
    context = {
        "user_email": "user@example.com",
        "event_pending_key": "user@example.com:thread-y",
        "client_context": {"location": {"lat": 38.72, "lon": -9.13}},
    }

    result = handle_event(parsed, context)
    places = result.get("resolution", {}).get("new_entities", {}).get("places", [])
    assert result.get("extracted", {}).get("where") == "Burger Palace"
    assert places
    assert places[0].get("city") == "Aurora"
    assert places[0].get("country") == "Westoria"


def test_handle_event_uses_contact_place_relation_before_global_match(monkeypatch):
    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        return {
            "title": "Visited",
            "summary": "Visited Jordan",
            "when": None,
            "where": "Jordan's house",
            "tags": ["personal"],
            "types": ["memory"],
            "need_user_input": None,
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [
                    {
                        "contact_id": "contact:jose",
                        "display_name": "Jordan",
                        "query": "Jordan",
                        "confidence": "high",
                    }
                ],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "name_replacements": {},
            },
            {"ambiguous_contacts": [], "suggested_relationships": []},
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
        "commands.handlers.event.places_service.resolve_contact_place",
        lambda *_a, **_k: {
            "place_id": "plc_jose_home",
            "name": "Jordan Home",
            "confidence": "high",
            "matched_via": "contact_place_relation",
        },
    )
    monkeypatch.setattr(
        "commands.handlers.event.places_service.find_best_place_match",
        lambda *_a, **_k: {
            "place_id": "plc_global_home",
            "name": "Global Home",
            "match_confidence": "high",
            "matched_via": "alias_exact",
            "match_score": 98.0,
        },
    )

    parsed = ParsedCommand(command="event", args="visit", raw_message="/event visit")
    context = {"user_email": "user@example.com", "event_pending_key": "user@example.com:thread-z"}

    result = handle_event(parsed, context)
    assert result.get("type") == "event_confirmation"
    assert result.get("extracted", {}).get("where") == "Jordan Home"
    assert result.get("resolution", {}).get("matched_place", {}).get("place_id") == "plc_jose_home"
    pending_link = result.get("resolution", {}).get("pending_contact_place_link")
    assert pending_link
    assert pending_link.get("contact_id") == "contact:jose"
    assert pending_link.get("role") == "home"


def test_handle_event_maps_curly_apostrophe_place_to_contact_home(monkeypatch):
    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        return {
            "title": "Visited",
            "summary": "Visited Dana",
            "when": None,
            "where": "Dana’s place",
            "tags": ["personal"],
            "types": ["memory"],
            "need_user_input": None,
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [
                    {
                        "contact_id": "contact:dana",
                        "display_name": "Dana Lewis",
                        "query": "Dana Lewis",
                        "confidence": "high",
                    }
                ],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "name_replacements": {},
            },
            {"ambiguous_contacts": [], "suggested_relationships": []},
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
        "commands.handlers.event.places_service.resolve_contact_place",
        lambda *_a, **_k: {
            "place_id": "plc_dana_home",
            "name": "Dana Lewis Home",
            "confidence": "high",
            "matched_via": "contact_place_relation",
        },
    )
    monkeypatch.setattr(
        "commands.handlers.event.places_service.find_best_place_match",
        lambda *_a, **_k: None,
    )

    parsed = ParsedCommand(command="event", args="visit", raw_message="/event visit")
    context = {"user_email": "user@example.com", "event_pending_key": "user@example.com:thread-curly"}

    result = handle_event(parsed, context)
    assert result.get("type") == "event_confirmation"
    assert result.get("extracted", {}).get("where") == "Dana Lewis Home"
    assert result.get("resolution", {}).get("matched_place", {}).get("place_id") == "plc_dana_home"


def test_handle_event_suggests_new_contact_scoped_place_when_unresolved(monkeypatch):
    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        return {
            "title": "Visited",
            "summary": "Visited Dana",
            "when": None,
            "where": "Dana's place",
            "tags": ["personal"],
            "types": ["memory"],
            "need_user_input": None,
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [
                    {
                        "contact_id": "contact:dana",
                        "display_name": "Dana Lewis",
                        "query": "Dana Lewis",
                        "confidence": "high",
                    }
                ],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "name_replacements": {},
            },
            {"ambiguous_contacts": [], "suggested_relationships": []},
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
        "commands.handlers.event.places_service.resolve_contact_place",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "commands.handlers.event.places_service.find_best_place_match",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "commands.handlers.event.geocode_place_name",
        lambda *_a, **_k: None,
    )

    parsed = ParsedCommand(command="event", args="visit", raw_message="/event visit")
    context = {"user_email": "user@example.com", "event_pending_key": "user@example.com:thread-new-place"}

    result = handle_event(parsed, context)
    assert result.get("type") == "event_confirmation"
    assert result.get("extracted", {}).get("where") == "Dana Lewis Home"
    assert result.get("resolution", {}).get("matched_place") is None
    assert result.get("resolution", {}).get("new_entities", {}).get("places") == [
        {"name": "Dana Lewis Home", "query": "Dana's place"}
    ]
    pending_link = result.get("resolution", {}).get("pending_contact_place_link")
    assert pending_link
    assert pending_link.get("contact_id") == "contact:dana"
    assert pending_link.get("role") == "home"


def test_handle_event_does_not_queue_generic_alias(monkeypatch):
    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        return {
            "title": "Quick note",
            "summary": "At house",
            "when": None,
            "where": "house",
            "tags": ["personal"],
            "types": ["memory"],
            "need_user_input": None,
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "name_replacements": {},
            },
            {"ambiguous_contacts": [], "suggested_relationships": []},
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
        "commands.handlers.event.places_service.find_best_place_match",
        lambda *_a, **_k: {
            "place_id": "plc_home",
            "name": "Home",
            "match_confidence": "high",
            "matched_via": "name_exact",
            "match_score": 99.0,
        },
    )

    parsed = ParsedCommand(command="event", args="quick note", raw_message="/event quick note")
    context = {"user_email": "user@example.com", "event_pending_key": "user@example.com:thread-a"}

    result = handle_event(parsed, context)
    matched_place = result.get("resolution", {}).get("matched_place", {})
    assert matched_place.get("place_id") == "plc_home"
    assert "pending_alias" not in matched_place


def test_handle_event_contact_place_link_requires_high_contact_confidence(monkeypatch):
    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        return {
            "title": "Visit",
            "summary": "Visited Jordan",
            "when": None,
            "where": "Jordan's house",
            "tags": ["personal"],
            "types": ["memory"],
            "need_user_input": None,
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [
                    {
                        "contact_id": "contact:jose",
                        "display_name": "Jordan",
                        "query": "Jordan",
                        "confidence": "medium",
                    }
                ],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "name_replacements": {},
            },
            {"ambiguous_contacts": [], "suggested_relationships": []},
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
        "commands.handlers.event.places_service.resolve_contact_place",
        lambda *_a, **_k: {
            "place_id": "plc_jose_home",
            "name": "Jordan Home",
            "confidence": "high",
            "matched_via": "contact_place_relation",
        },
    )

    parsed = ParsedCommand(command="event", args="visit", raw_message="/event visit")
    context = {"user_email": "user@example.com", "event_pending_key": "user@example.com:thread-b"}

    result = handle_event(parsed, context)
    assert result.get("resolution", {}).get("matched_place", {}).get("place_id") == "plc_jose_home"
    assert "pending_contact_place_link" not in result.get("resolution", {})


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


def test_pending_clarification_preserves_staged_media_attachments(monkeypatch):
    user_email = "user@example.com"
    thread_id = "thread-media"
    existing_attachment = {
        "attachment_id": "chat-media-existing",
        "file_name": "existing.jpg",
        "mime_type": "image/jpeg",
        "storage_path": "/tmp/existing.jpg",
    }
    incoming_attachment = {
        "attachment_id": "chat-media-incoming",
        "file_name": "incoming.jpg",
        "mime_type": "image/jpeg",
        "storage_path": "/tmp/incoming.jpg",
    }
    stored_payloads: list[tuple[str, dict]] = []

    def fake_parse_command(message: str):
        return object() if message.startswith("/event ") else None

    class _Registry:
        @staticmethod
        def execute(parsed, context):
            assert parsed is not None
            assert context.get("thread_id") == thread_id
            return {"type": "need_user_input", "message": "follow-up accepted"}

    monkeypatch.setattr(
        "commands.event.get_command_data",
        lambda _preview_id: {
            "original_message": "met with Alex about the roadmap",
            "thread_id": thread_id,
            "extracted": {},
            "resolution": {},
            "media_attachments": [existing_attachment],
        },
    )
    monkeypatch.setattr("commands.event.store_command_data", lambda preview_id, data: stored_payloads.append((preview_id, data)))
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr("commands.event.clear_pending_event", lambda _key: None)
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
        pending_event_id="event:clarification:media1234",
        media_attachments=[incoming_attachment],
        command_response_text=lambda command_result: command_result.get("message", ""),
        command_assistant_metadata=lambda command_result: ({}, None),
    )

    assert result is not None
    assert len(stored_payloads) == 1
    stored_media = stored_payloads[0][1].get("media_attachments") or []
    assert [attachment.get("attachment_id") for attachment in stored_media] == [
        "chat-media-existing",
        "chat-media-incoming",
    ]


def test_extract_clarification_detail_strips_prefixed_original_message():
    original = "met with Alex about the roadmap"
    message = (
        "met with Alex about the roadmap\n\n"
        "Additional details: None of these. It is a new contact, named Julia"
    )

    detail = event_handler._extract_clarification_detail(message, original)

    assert detail == "None of these. It is a new contact, named Julia"


def test_extract_clarification_detail_strips_structured_field_label():
    detail = event_handler._extract_clarification_detail(
        "Additional details: Who did you mean by 'Rita'?: Rita Lake",
        "today's physiotherapy went well",
        ["Who did you mean by 'Rita'?"],
    )

    assert detail == "Rita Lake"


def test_event_clarification_follow_up_strips_structured_contact_disambiguation_label(
    monkeypatch,
):
    from commands.storage import delete_command_data, store_command_data

    clarification_id = "event:clarification:rita1234"
    captured: dict[str, str] = {}

    store_command_data(
        clarification_id,
        {
            "original_message": "today's physiotherapy at 9h went well. Rita was the Phisioterapist",
            "thread_id": "thread-rita",
            "extracted": {
                "title": "Physiotherapy Session with Rita",
                "summary": "Went well.",
                "when": "2026-04-21T09:00:00",
                "end_when": None,
                "where": None,
                "documents": [],
                "tags": ["Health"],
                "types": ["health"],
                "need_user_input": None,
            },
            "resolution": {},
            "contact_result": {"ambiguous_contacts": []},
            "clarification_messages": [
                {
                    "role": "user",
                    "content": "today's physiotherapy at 9h went well. Rita was the Phisioterapist",
                },
                {
                    "role": "assistant",
                    "content": "I found multiple matching contacts. Please choose who you meant.",
                },
            ],
            "requested_fields": [
                {
                    "id": "who_0",
                    "kind": "select",
                    "label": "Who did you mean by 'Rita'?",
                    "required": True,
                }
            ],
        },
    )

    def fake_extract(*_args, **_kwargs):
        return {
            "title": "Physiotherapy Session with Rita Lake",
            "summary": "Went well.",
            "when": "2026-04-21T09:00:00",
            "end_when": None,
            "where": None,
            "documents": [],
            "tags": ["Health"],
            "types": ["health"],
            "need_user_input": None,
        }

    def fake_resolve(contact_message, *_args, **_kwargs):
        captured["contact_message"] = contact_message
        return ({"contacts": [], "new_entities": {}, "name_replacements": {}}, {"ambiguous_contacts": []})

    monkeypatch.setattr("commands.handlers.event._extract_event_entities_with_llm", fake_extract)
    monkeypatch.setattr("commands.handlers.event._resolve_contacts_with_agent", fake_resolve)
    monkeypatch.setattr("commands.handlers.event.infer_current_place", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("commands.handlers.event.places_service.find_best_place_match", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("commands.handlers.event.geocode_place_name", lambda *_args, **_kwargs: None)

    result = handle_event(
        ParsedCommand(
            command="event",
            args=(
                "today's physiotherapy at 9h went well. Rita was the Phisioterapist\n\n"
                "Additional details: Who did you mean by 'Rita'?: Rita Lake "
                f"[clarification_id:{clarification_id}]"
            ),
            raw_message="/event follow-up",
        ),
        {"user_email": "user@example.com", "thread_id": "thread-rita"},
    )

    assert result["type"] == "event_confirmation"
    assert "Rita Lake" in captured["contact_message"]
    assert "Who did you mean by 'Rita'?" not in captured["contact_message"]
    assert "- Rita Lake" in captured["contact_message"]

    delete_command_data(result["preview_id"])
    delete_command_data(clarification_id)


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


def test_resolve_ambiguous_contacts_from_answer_supports_exclusion_intent():
    ambiguous_contacts = [
        {
            "original_text": "the company's CEO",
            "candidates": [
                {"contact_id": "contact:alpha", "display_name": "Alex Carter"},
                {"contact_id": "contact:bravo", "display_name": "Dana Lewis"},
            ],
        }
    ]

    resolved, remaining = event_handler._resolve_ambiguous_contacts_from_answer(
        ambiguous_contacts,
        "It is a person he worked with and we should not add them to this event.",
    )

    assert resolved == []
    assert remaining == ambiguous_contacts


def test_replace_generic_terms_avoids_partial_name_duplication():
    replaced = event_handler._replace_generic_terms_in_text(
        "Robin / Dana",
        {"Robin": "Robin Lake", "Dana": "Dana Lewis", "Lake": "Lake"},
    )

    assert replaced == "Robin Lake / Dana Lewis"


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


def test_follow_up_without_prior_clarification_infers_target_fields(monkeypatch):
    clarification_id = "event:clarification:infer1234"
    store_command_data(
        clarification_id,
        {
            "original_message": "met with Alex at home yesterday",
            "thread_id": "thread-123",
            "extracted": {
                "title": "Met with Alex",
                "summary": "Talked about roadmap",
                "when": None,
                "end_when": None,
                "where": "Home",
                "tags": ["work"],
                "types": ["meeting"],
                "who": ["Alex"],
            },
            "resolution": {
                "contacts": [],
                "new_entities": {
                    "contacts": [],
                    "places": [],
                    "documents": [],
                },
                "name_replacements": {},
                "proposed_contact_groups": [],
            },
            "relationship_suggestions": [{"relationship_type": "colleague"}],
        },
    )
    store_pending_event("user@example.com:thread-123", clarification_id)

    infer_called = {"value": False}
    resolve_called = {"value": False}

    def fake_infer_fields(raw_message, existing_extraction, context):
        infer_called["value"] = True
        assert "office" in raw_message.lower()
        assert existing_extraction.get("where") == "Home"
        return ["where"]

    def fake_extract_event_entities(
        event_message,
        context,
        existing_extracted=None,
        clarification_messages=None,
    ):
        base = existing_extracted or {}
        assert context.get("event_target_fields") == ["where"]
        assert context.get("event_lock_existing_fields") is True
        return {
            "title": base.get("title"),
            "summary": base.get("summary"),
            "when": base.get("when"),
            "end_when": base.get("end_when"),
            "where": "Office",
            "tags": base.get("tags"),
            "types": base.get("types"),
            "need_user_input": None,
        }

    def fail_if_resolve_called(*_args, **_kwargs):
        resolve_called["value"] = True
        raise AssertionError("Contact resolution should not run for where-only follow-up")

    monkeypatch.setattr(
        "commands.handlers.event._infer_follow_up_target_fields",
        fake_infer_fields,
    )
    monkeypatch.setattr(
        "commands.handlers.event._extract_event_entities_with_llm",
        fake_extract_event_entities,
    )
    monkeypatch.setattr(
        "commands.handlers.event._resolve_contacts_with_agent",
        fail_if_resolve_called,
    )
    monkeypatch.setattr(
        "commands.handlers.event.places_service.find_best_place_match", lambda *a, **k: None
    )
    monkeypatch.setattr("commands.handlers.event.geocode_place_name", lambda *a, **k: None)

    parsed = ParsedCommand(
        command="event",
        args="ah, the event happened at my office [clarification_id:event:clarification:infer1234]",
        raw_message="/event ah, the event happened at my office [clarification_id:event:clarification:infer1234]",
    )
    context = {
        "user_email": "user@example.com",
        "thread_id": "thread-123",
        "event_pending_key": "user@example.com:thread-123",
    }

    result = handle_event(parsed, context)

    assert result.get("type") == "event_confirmation"
    assert result.get("extracted", {}).get("where") == "Office"
    assert infer_called["value"] is True
    assert resolve_called["value"] is False

    preview_id = result.get("preview_id")
    if preview_id:
        delete_command_data(preview_id)
    clear_pending_event("user@example.com:thread-123")


def test_find_event_matches_rejects_same_family_but_unrelated_content(monkeypatch):
    extracted = {
        "title": "Family Burger Dinner",
        "summary": "Went with wife and daughter to eat burgers at Bueda Fome.",
        "when": event_handler.datetime(2026, 4, 25, 18, 15),
        "where": "Bueda Fome",
    }
    resolution = {
        "contacts": [
            {"contact_id": "contact:wife"},
            {"contact_id": "contact:daughter"},
        ],
        "matched_place": None,
    }

    monkeypatch.setattr(
        event_handler,
        "_search_event_candidates",
        lambda *_args, **_kwargs: [
            {
                "id": "event:movie",
                "title": "Family Movie Night: Super Mario Galaxy",
                "summary": "Went with family to the cinema and watched Super Mario Galaxy.",
                "score": 0.42,
                "start_date": "2026-04-25T18:00:00",
                "people": ["contact:wife", "contact:daughter"],
                "place": {"place_id": "place:cinema", "name": "Nos Cinemas"},
            }
        ],
    )
    monkeypatch.setattr(
        event_handler,
        "_classify_event_match_reference",
        lambda *_args, **_kwargs: "create_new",
    )

    match = event_handler._find_event_matches(
        "yesterday at 18h15 I went with wife and daughter eat some burgers at Bueda Fome.",
        extracted,
        resolution,
    )

    assert match == {"operation": "create", "candidates": []}


def test_find_event_matches_requires_auto_update_threshold(monkeypatch):
    extracted = {
        "title": "Morning coffee",
        "summary": "Had coffee and caught up.",
        "when": event_handler.datetime(2026, 4, 22, 9, 0),
        "where": None,
    }
    resolution = {"contacts": [], "matched_place": None}

    monkeypatch.setattr(
        event_handler,
        "_search_event_candidates",
        lambda *_args, **_kwargs: [
            {
                "id": "event:coffee",
                "title": "Coffee catch-up with Alex",
                "summary": "Met Alex for coffee.",
                "score": 0.2,
                "start_date": "2026-04-23T09:00:00",
                "people": [],
                "place": None,
            }
        ],
    )

    match = event_handler._find_event_matches(
        "coffee meetup",
        extracted,
        resolution,
    )

    assert match == {"operation": "create", "candidates": []}


def test_resolve_contacts_with_agent_skips_selector_name_replacements(monkeypatch):
    def fake_resolve_contacts_from_text(*_args, **_kwargs):
        return {
            "resolved_contacts": [
                {
                    "original_text": "from Acme",
                    "contact_id": "contact:xinu",
                    "display_name": "Xinu",
                    "matched_via": "selector_company",
                    "confidence": "high",
                },
                {
                    "original_text": "Pat",
                    "contact_id": "contact:seb",
                    "display_name": "Owen Park",
                    "matched_via": "direct_match",
                    "confidence": "high",
                },
            ],
            "new_contacts": [],
            "group_confirmation_candidates": [],
            "group_upsert_candidates": [],
        }

    monkeypatch.setattr(
        "agents.contacts.resolve_contacts_from_text",
        fake_resolve_contacts_from_text,
    )

    resolution, _contact_result = event_handler._resolve_contacts_with_agent(
        "this morning, I was fired from Acme by Pat",
        "user@example.com",
    )

    assert resolution["name_replacements"] == {"Pat": "Owen Park"}


def test_find_event_matches_rejects_calendar_day_mismatch(monkeypatch):
    extracted = {
        "title": "Lunch at Dragao with Family",
        "summary": "Returned from lunch at Dragao with family.",
        "when": event_handler.datetime.fromisoformat("2026-04-26T12:30:00+01:00"),
        "where": "Dragao",
    }
    resolution = {"contacts": [], "matched_place": None}

    monkeypatch.setattr(
        event_handler,
        "_search_event_candidates",
        lambda *_args, **_kwargs: [
            {
                "id": "event:yesterday-lunch",
                "title": "Family Burger Meal at Bueda Fome",
                "summary": "Went with family to eat burgers.",
                "score": 0.7,
                "start_date": "2026-04-25T19:15:00+01:00",
                "people": [],
                "place": {"place_id": "place:bueda", "name": "Bueda Fome"},
            }
        ],
    )

    match = event_handler._find_event_matches(
        "just came back from lunch at dragao with wife and daughter. Additional details: Date and time of the event: 2026-04-26T12:30",
        extracted,
        resolution,
    )

    assert match == {"operation": "create", "candidates": []}


def test_find_event_matches_handles_mixed_timezone_end_window(monkeypatch):
    extracted = {
        "title": "Catch-up call with Dana Lewis",
        "summary": "Discussed work opportunities.",
        "when": event_handler.datetime.fromisoformat("2026-05-28T19:00:00"),
        "end_when": event_handler.datetime.fromisoformat("2026-05-28T20:00:00"),
        "where": "Home",
    }
    resolution = {"contacts": [], "matched_place": None}

    monkeypatch.setattr(
        event_handler,
        "_search_event_candidates",
        lambda *_args, **_kwargs: [
            {
                "id": "event:dana-call",
                "title": "Catch-up call with Dana Lewis",
                "summary": "Discussed work opportunities.",
                "score": 0.91,
                "start_date": "2026-05-28T19:00:00+01:00",
                "people": [],
                "place": {"place_id": "place:home", "name": "Home"},
            }
        ],
    )

    match = event_handler._find_event_matches(
        "this is an existing event at 19h of may 28th",
        extracted,
        resolution,
    )

    assert match.get("operation") in {"update", "ambiguous", "create"}


def test_find_event_matches_honors_explicit_new_event_correction(monkeypatch):
    extracted = {
        "title": "Lunch at Dragao with Family",
        "summary": "Returned from lunch at Dragao with family.",
        "when": event_handler.datetime.fromisoformat("2026-04-26T12:30:00+01:00"),
        "where": "Dragao",
    }
    resolution = {"contacts": [], "matched_place": None}

    monkeypatch.setattr(
        event_handler,
        "_search_event_candidates",
        lambda *_args, **_kwargs: [
            {
                "id": "event:yesterday-lunch",
                "title": "Lunch at Dragao with Family",
                "summary": "Returned from lunch at Dragao with family.",
                "score": 0.95,
                "start_date": "2026-04-26T12:30:00+01:00",
                "people": [],
                "place": {"place_id": "place:dragao", "name": "Dragao"},
            }
        ],
    )
    monkeypatch.setattr(
        event_handler,
        "_classify_event_match_reference",
        lambda *_args, **_kwargs: "create_new",
    )

    match = event_handler._find_event_matches(
        "This is not the same event. One was yesterday, the other one is today.",
        extracted,
        resolution,
    )

    assert match == {"operation": "create", "candidates": []}


def test_find_event_matches_uses_structured_exact_day_search(monkeypatch):
    extracted = {
        "title": "Physiotherapy session with Rita Lake",
        "summary": "Had a physiotherapy session with Rita Lake at Beacon Physio Clinic.",
        "when": event_handler.datetime.fromisoformat("2026-05-07T00:00:00"),
        "where": "Beacon Physio Clinic",
    }
    resolution = {
        "contacts": [{"contact_id": "contact:rita", "display_name": "Rita Lake"}],
        "matched_place": {"place_id": "place:beacon-physio", "name": "Beacon Physio Clinic"},
    }
    calls = {"structured": 0, "semantic": 0}

    monkeypatch.setattr(
        event_handler,
        "_search_event_candidates",
        lambda *_args, **_kwargs: calls.__setitem__("semantic", calls["semantic"] + 1) or [],
    )

    def fake_structured(*_args, **_kwargs):
        calls["structured"] += 1
        return [
            {
                "id": "event:physio-10",
                "title": "10th physiotherapy session",
                "summary": "Physiotherapy with Rita Lake at Beacon Physio Clinic.",
                "score": 0.82,
                "start_date": "2026-05-07T08:00:00",
                "people": ["contact:rita"],
                "place": {"place_id": "place:beacon-physio", "name": "Beacon Physio Clinic"},
            }
        ]

    monkeypatch.setattr(event_handler, "_search_event_candidates_structured", fake_structured)

    match = event_handler._find_event_matches(
        "physiotherapy session today with Rita Lake at Beacon Physio Clinic",
        extracted,
        resolution,
    )

    assert calls["structured"] >= 1
    assert match.get("operation") == "update"
    assert match.get("existing_event_id") == "event:physio-10"


def test_find_event_matches_relaxes_exact_day_filters_when_existing_event_misses_one_contact(
    monkeypatch,
):
    extracted = {
        "title": "Physiotherapy session with Dana Lewis",
        "summary": "Had physiotherapy with Dana Lewis at Monserrat Clinic.",
        "when": event_handler.datetime.fromisoformat("2026-05-07T08:00:00"),
        "where": "Monserrat Clinic",
    }
    resolution = {
        "contacts": [
            {"contact_id": "contact:dana", "display_name": "Dana Lewis"},
            {"contact_id": "contact:alex", "display_name": "Alex Carter"},
        ],
        "matched_place": {"place_id": "place:monserrat", "name": "Monserrat Clinic"},
    }
    seen_filters: list[tuple[tuple[str, ...], str | None]] = []

    def fake_structured(_query, _time_start, _time_end, people_ids, place_id, _limit):
        seen_filters.append((tuple(people_ids), place_id))
        if people_ids == ["contact:dana"] and place_id == "place:monserrat":
            return [
                {
                    "id": "event:physio-11",
                    "title": "Physiotherapy session with Dana Lewis",
                    "summary": "Physiotherapy appointment at Monserrat Clinic.",
                    "score": 0.86,
                    "start_date": "2026-05-07T08:05:00",
                    "people": ["contact:dana"],
                    "place": {"place_id": "place:monserrat", "name": "Monserrat Clinic"},
                }
            ]
        return []

    monkeypatch.setattr(event_handler, "_search_event_candidates_structured", fake_structured)
    monkeypatch.setattr(event_handler, "_search_event_candidates", lambda *_args, **_kwargs: [])

    match = event_handler._find_event_matches(
        "physiotherapy today at 8 with Dana Lewis at Monserrat Clinic",
        extracted,
        resolution,
    )

    assert (("contact:dana", "contact:alex"), "place:monserrat") in seen_filters
    assert (("contact:dana",), "place:monserrat") in seen_filters
    assert match.get("operation") == "update"
    assert match.get("existing_event_id") == "event:physio-11"


def test_find_event_matches_prefers_time_bounded_candidates_before_semantic_search(monkeypatch):
    extracted = {
        "title": "Catch-up call with Dana Lewis",
        "summary": "Talked about startup progress.",
        "when": event_handler.datetime.fromisoformat("2026-05-07T19:00:00"),
        "where": None,
    }
    resolution = {
        "contacts": [{"contact_id": "contact:dana", "display_name": "Dana Lewis"}],
        "matched_place": None,
    }

    monkeypatch.setattr(
        event_handler,
        "_load_time_bounded_event_candidates",
        lambda *_args, **_kwargs: [
            {
                "id": "event:dana-call",
                "title": "Catch-up call with Dana Lewis",
                "summary": "Talked about startup progress.",
                "score": 0.0,
                "start_date": "2026-05-07T19:05:00",
                "people": ["contact:dana"],
                "place": None,
            }
        ],
    )
    monkeypatch.setattr(
        event_handler,
        "_search_event_candidates",
        lambda *_args, **_kwargs: pytest.fail("semantic search should not run before bounded ranking"),
    )
    monkeypatch.setattr(
        event_handler,
        "_search_event_candidates_structured",
        lambda *_args, **_kwargs: pytest.fail("structured semantic search should not run before bounded ranking"),
    )

    match = event_handler._find_event_matches(
        "call with Dana Lewis yesterday at 19h",
        extracted,
        resolution,
    )

    assert match.get("operation") == "update"
    assert match.get("existing_event_id") == "event:dana-call"


def test_handle_event_follow_up_rematch_excludes_previous_event(monkeypatch):
    clarification_id = "event:clarification:rematch1"
    seen: dict[str, object] = {}

    clarification_context = {
        "original_message": "call with Anthony Rose yesterday at 19h",
        "clarification_messages": [],
        "requested_field_ids": [],
        "extracted": {
            "title": "Wrong matched title",
            "summary": "Wrong summary",
            "when": event_handler.datetime.fromisoformat("2026-05-28T15:00:00"),
            "where": "Home",
            "tags": ["work"],
            "types": ["meeting"],
            "who": ["Benny"],
        },
        "resolution": {
            "contacts": [{"contact_id": "contact:benny", "display_name": "Benny"}],
            "new_entities": {"contacts": [], "places": [], "documents": []},
        },
        "contact_result": {"ambiguous_contacts": []},
        "relationship_suggestions": [],
        "media_attachments": [],
        "existing_event_id": "event:betinho",
        "matched_event": {
            "event_id": "event:betinho",
            "title": "Call with Benny",
            "start_date": "2026-05-28T15:00:00+01:00",
        },
    }

    monkeypatch.setattr(
        "commands.handlers.event._classify_follow_up_event_strategy",
        lambda *_args, **_kwargs: {"action": "rematch", "fields": []},
    )

    def fake_extract_event_entities(event_message, _context, existing_extracted=None, clarification_messages=None):
        seen["event_message"] = event_message
        seen["existing_extracted"] = existing_extracted
        return {
            "title": "Catch-up call with Anthony Rose",
            "summary": "Talked about startup work.",
            "when": event_handler.datetime.fromisoformat("2026-05-28T19:00:00"),
            "end_when": None,
            "where": None,
            "documents": [],
            "tags": ["work"],
            "types": ["meeting"],
            "need_user_input": None,
        }

    def fake_resolve_contacts(*_args, **_kwargs):
        return (
            {
                "contacts": [{"contact_id": "contact:anthony", "display_name": "Anthony Rose"}],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "name_replacements": {},
            },
            {"ambiguous_contacts": [], "suggested_relationships": []},
        )

    def fake_find_matches(_raw_message, _extracted, _resolution, *, excluded_event_ids=None):
        seen["excluded_event_ids"] = excluded_event_ids
        return {"operation": "create", "candidates": []}

    monkeypatch.setattr("commands.handlers.event._extract_event_entities_with_llm", fake_extract_event_entities)
    monkeypatch.setattr("commands.handlers.event._resolve_contacts_with_agent", fake_resolve_contacts)
    monkeypatch.setattr("commands.handlers.event._find_event_matches", fake_find_matches)
    monkeypatch.setattr("commands.handlers.event.infer_current_place", lambda *_a, **_k: None)
    monkeypatch.setattr("commands.handlers.event.places_service.find_best_place_match", lambda *_a, **_k: None)
    monkeypatch.setattr("commands.handlers.event.geocode_place_name", lambda *_a, **_k: None)
    monkeypatch.setattr("commands.handlers.event.store_command_data", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr("commands.storage.get_command_data", lambda _key: clarification_context)
    monkeypatch.setattr("commands.storage.delete_command_data", lambda _key: None)

    parsed = ParsedCommand(
        command="event",
        args=f"Not the right event, it was at 19h [clarification_id:{clarification_id}]",
        raw_message=f"/event Not the right event, it was at 19h [clarification_id:{clarification_id}]",
    )
    context = {"user_email": "user@example.com", "event_pending_key": "user@example.com:thread-rematch"}

    handle_event(parsed, context)

    assert seen["existing_extracted"] is None
    assert "Not the right event, it was at 19h" in str(seen["event_message"])
    assert seen["excluded_event_ids"] == {"event:betinho"}


def test_find_event_matches_widens_for_explicit_existing_request(monkeypatch):
    extracted = {
        "title": "Physiotherapy session with Rita Lake",
        "summary": "Had a physiotherapy session with Rita Lake at Beacon Physio Clinic.",
        "when": event_handler.datetime.fromisoformat("2026-05-07T00:00:00"),
        "where": "Beacon Physio Clinic",
    }
    resolution = {
        "contacts": [{"contact_id": "contact:rita", "display_name": "Rita Lake"}],
        "matched_place": {"place_id": "place:beacon-physio", "name": "Beacon Physio Clinic"},
    }

    def fake_search(query, time_start, time_end, limit):
        if time_start or time_end:
            return []
        return [
            {
                "id": "event:physio-9",
                "title": "9th physiotherapy session",
                "summary": "Physiotherapy with Rita Lake at Beacon Physio Clinic.",
                "score": 0.88,
                "start_date": "2026-05-05T08:00:00",
                "people": ["contact:rita"],
                "place": {"place_id": "place:beacon-physio", "name": "Beacon Physio Clinic"},
            }
        ]

    monkeypatch.setattr(event_handler, "_search_event_candidates", fake_search)
    monkeypatch.setattr(
        event_handler,
        "_search_event_candidates_structured",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        event_handler,
        "_classify_event_match_reference",
        lambda *_args, **_kwargs: "update_existing",
    )

    match = event_handler._find_event_matches(
        "physiotherapy session today with Rita Lake at Beacon Physio Clinic was the same as last one. Update the existing event.",
        extracted,
        resolution,
    )

    assert match.get("operation") == "update"
    assert match.get("existing_event_id") == "event:physio-9"


def test_find_event_matches_rejects_same_day_home_false_positive_with_conflicting_title(monkeypatch):
    extracted = {
        "title": "Family afternoon with dinner at home",
        "summary": "Spent the afternoon at home, had beers, and ordered Italian food for dinner.",
        "when": event_handler.datetime.fromisoformat("2026-05-17T14:30:00+00:00"),
        "where": "Home",
    }
    resolution = {
        "contacts": [
            {"contact_id": "contact:ramon", "display_name": "Ramon"},
            {"contact_id": "contact:marcela", "display_name": "Marcela"},
            {"contact_id": "contact:sophia", "display_name": "Sophia"},
        ],
        "matched_place": {"place_id": "place:home", "name": "Home"},
    }

    monkeypatch.setattr(event_handler, "_search_event_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        event_handler,
        "_search_event_candidates_structured",
        lambda *_args, **_kwargs: [
            {
                "id": "event:lunch",
                "title": "Japanese lunch at Caidan",
                "summary": "Had lunch earlier that day.",
                "score": 0.86,
                "start_date": "2026-05-17T12:00:00+00:00",
                "people": ["contact:ramon", "contact:marcela", "contact:sophia"],
                "place": {"place_id": "place:home", "name": "Home"},
            }
        ],
    )

    match = event_handler._find_event_matches(
        "afternoon with the children at my house. As usual having beers. We ordered Italian food for dinner.",
        extracted,
        resolution,
    )

    assert match == {"operation": "create", "candidates": []}
