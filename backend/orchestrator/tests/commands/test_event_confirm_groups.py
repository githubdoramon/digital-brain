from datetime import datetime

import pytest
from fastapi import HTTPException

from commands.event import confirm_event_command
from schemas import EventCommandConfirmation


def _base_command_data() -> dict:
    return {
        "extracted": {
            "title": "Soccer meetup",
            "summary": "Met with the team.",
            "when": datetime(2026, 2, 18, 18, 0, 0),
            "where": None,
            "tags": ["personal"],
            "types": ["meeting"],
        },
        "resolution": {
            "contacts": [
                {
                    "contact_id": "contact-ana",
                    "display_name": "Ana",
                }
            ],
            "new_entities": {
                "contacts": [],
                "places": [],
                "documents": [],
            },
            "proposed_contact_groups": [],
        },
    }


def test_confirm_event_persists_inferred_group_on_confirmation(monkeypatch):
    command_data = _base_command_data()
    command_data["resolution"]["proposed_contact_groups"] = [
        {
            "name": "soccer team",
            "description": "Contacts associated with group 'soccer team'.",
            "aliases": ["soccer team"],
            "source": "inferred",
            "added_via": "selector_group",
            "contact_ids": ["contact-ana", "contact-bruno"],
            "replace_members": True,
            "confirmed": False,
        }
    ]

    created_event_ids: list[str] = []
    group_upserts: list[dict] = []

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr(
        "commands.event.events_service.ingest_event",
        lambda event_in: created_event_ids.append(event_in.id),
    )
    monkeypatch.setattr(
        "commands.event.contact_groups_service.upsert_group_from_selector",
        lambda **kwargs: group_upserts.append(kwargs)
        or {
            "group_id": "group:soccer-team",
            "owner_contact_id": "contact:user",
            "name": kwargs.get("name"),
            "description": kwargs.get("description"),
            "aliases": kwargs.get("aliases") or [],
            "member_count": len(kwargs.get("member_contact_ids") or []),
        },
    )

    payload = EventCommandConfirmation(
        preview_id="event:preview:group123",
        confirmed=True,
        group_confirmations={"soccer team": True},
    )
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert created_event_ids
    assert len(group_upserts) == 1
    assert group_upserts[0]["name"] == "soccer team"
    assert group_upserts[0]["source"] == "inferred"
    assert group_upserts[0]["confirmed"] is False
    assert len(result.created_groups) == 1
    assert result.created_groups[0]["name"] == "soccer team"


def test_confirm_event_skips_group_upsert_when_no_members(monkeypatch):
    command_data = _base_command_data()
    command_data["resolution"]["proposed_contact_groups"] = [
        {
            "name": "empty group",
            "description": "No members yet",
            "aliases": ["empty group"],
            "source": "inferred",
            "added_via": "selector_group",
            "contact_ids": [],
            "replace_members": True,
            "confirmed": False,
        }
    ]

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr("commands.event.events_service.ingest_event", lambda _event_in: None)

    call_count = {"count": 0}

    def _capture_upsert(**kwargs):
        call_count["count"] += 1
        return {"group_id": "group:unused"}

    monkeypatch.setattr(
        "commands.event.contact_groups_service.upsert_group_from_selector",
        _capture_upsert,
    )

    payload = EventCommandConfirmation(preview_id="event:preview:group456", confirmed=True)
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert call_count["count"] == 0
    assert result.created_groups == []


def test_confirm_event_persists_deterministic_group_without_explicit_confirmation(monkeypatch):
    command_data = _base_command_data()
    command_data["resolution"]["proposed_contact_groups"] = [
        {
            "name": "People at @acme.example",
            "description": "Contacts matched by domain",
            "aliases": ["@acme.example"],
            "source": "deterministic",
            "added_via": "selector_email_domain",
            "contact_ids": ["contact-ana", "contact-bruno"],
            "replace_members": True,
            "confirmed": True,
        }
    ]

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr("commands.event.events_service.ingest_event", lambda _event_in: None)

    upserts: list[dict] = []
    monkeypatch.setattr(
        "commands.event.contact_groups_service.upsert_group_from_selector",
        lambda **kwargs: upserts.append(kwargs) or {"group_id": "group:auto"},
    )

    payload = EventCommandConfirmation(preview_id="event:preview:group789", confirmed=True)
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert len(upserts) == 1
    assert upserts[0]["name"] == "People at @acme.example"


def test_confirm_event_persists_pending_alias_for_matched_place(monkeypatch):
    command_data = _base_command_data()
    command_data["extracted"]["where"] = "Home"
    command_data["resolution"]["matched_place"] = {
        "place_id": "plc_home",
        "name": "Home",
        "pending_alias": "my house",
    }

    alias_calls: list[tuple[str, str]] = []

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr("commands.event.events_service.ingest_event", lambda _event_in: None)
    monkeypatch.setattr(
        "commands.event.places_service.add_place_alias",
        lambda place_id, alias: alias_calls.append((place_id, alias)) or True,
    )

    payload = EventCommandConfirmation(preview_id="event:preview:place-alias", confirmed=True)
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert alias_calls == [("plc_home", "my house")]


def test_confirm_event_persists_pending_contact_place_link(monkeypatch):
    command_data = _base_command_data()
    command_data["extracted"]["where"] = "Home"
    command_data["resolution"]["matched_place"] = {
        "place_id": "plc_home",
        "name": "Home",
    }
    command_data["resolution"]["pending_contact_place_link"] = {
        "contact_id": "contact:jose",
        "role": "home",
        "source": "event_inference",
        "confidence": "high",
    }

    link_calls: list[dict] = []

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr("commands.event.events_service.ingest_event", lambda _event_in: None)
    monkeypatch.setattr(
        "commands.event.places_service.upsert_contact_place",
        lambda **kwargs: link_calls.append(kwargs),
    )

    payload = EventCommandConfirmation(preview_id="event:preview:contact-place", confirmed=True)
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert link_calls == [
        {
            "contact_id": "contact:jose",
            "place_id": "plc_home",
            "role": "home",
            "source": "event_inference",
            "confidence": "high",
        }
    ]


def test_confirm_event_applies_end_when_modification(monkeypatch):
    command_data = _base_command_data()
    captured_event = {"event": None}

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr(
        "commands.event.events_service.ingest_event",
        lambda event_in: captured_event.__setitem__("event", event_in),
    )

    payload = EventCommandConfirmation(
        preview_id="event:preview:end-when",
        confirmed=True,
        modifications={"end_when": "2026-02-18T19:15:00"},
    )
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert captured_event["event"] is not None
    assert captured_event["event"].end_date == datetime(2026, 2, 18, 19, 15)


def test_confirm_event_rejects_end_when_before_start(monkeypatch):
    command_data = _base_command_data()

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)

    payload = EventCommandConfirmation(
        preview_id="event:preview:end-before-start",
        confirmed=True,
        modifications={"end_when": "2026-02-18T17:15:00"},
    )

    with pytest.raises(HTTPException) as exc_info:
        confirm_event_command(payload, "user@example.com")

    assert exc_info.value.status_code == 400
    assert "after the start" in str(exc_info.value.detail)


def test_confirm_event_handles_mixed_timezone_awareness(monkeypatch):
    command_data = _base_command_data()
    captured_event = {"event": None}

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr(
        "commands.event.events_service.ingest_event",
        lambda event_in: captured_event.__setitem__("event", event_in),
    )

    payload = EventCommandConfirmation(
        preview_id="event:preview:mixed-tz-awareness",
        confirmed=True,
        modifications={"end_when": "2026-02-18T19:15:00Z"},
    )
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert captured_event["event"] is not None
    assert captured_event["event"].start_date.tzinfo is not None
    assert captured_event["event"].end_date.tzinfo is not None


def test_confirm_event_uses_explicit_place_id_modification(monkeypatch):
    command_data = _base_command_data()
    command_data["extracted"]["where"] = "my place"
    command_data["resolution"]["new_entities"]["places"] = [
        {
            "name": "my place",
        }
    ]
    command_data["resolution"]["matched_place"] = {
        "place_id": "plc_old",
        "name": "Old Place",
    }

    captured_event = {"event": None}
    created_places: list[str] = []

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr(
        "commands.event.events_service.ingest_event",
        lambda event_in: captured_event.__setitem__("event", event_in),
    )
    monkeypatch.setattr(
        "commands.event.places_service.get_place",
        lambda place_id: {"place_id": place_id} if place_id == "plc_selected" else None,
    )
    monkeypatch.setattr(
        "commands.event.places_service.ingest_place",
        lambda place_in: created_places.append(place_in.place_id),
    )

    payload = EventCommandConfirmation(
        preview_id="event:preview:explicit-place",
        confirmed=True,
        modifications={
            "where": "custom location text",
            "place_id": "plc_selected",
        },
    )
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert captured_event["event"] is not None
    assert captured_event["event"].place_id == "plc_selected"
    assert created_places == []


def test_confirm_event_filters_relationships_by_participant_override(monkeypatch):
    command_data = _base_command_data()
    command_data["resolution"]["contacts"] = [
        {"contact_id": "contact-ana", "display_name": "Ana"},
        {"contact_id": "contact-bruno", "display_name": "Bruno"},
    ]

    created_relationships: list[dict] = []

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr("commands.event.events_service.ingest_event", lambda _event_in: None)
    monkeypatch.setattr(
        "commands.event.contacts_service.upsert_contact_relationship",
        lambda rel_in: created_relationships.append(
            {
                "from_contact_id": rel_in.from_contact_id,
                "to_contact_id": rel_in.to_contact_id,
                "relationship_type": rel_in.relationship_type,
            }
        ),
    )

    payload = EventCommandConfirmation(
        preview_id="event:preview:relationship-override",
        confirmed=True,
        modifications={
            "contact_ids": ["contact-ana"],
            "confirmed_relationships": [
                {
                    "from_display_name": "Ana",
                    "to_display_name": "Bruno",
                    "relationship_type": "colleague",
                },
                {
                    "from_display_name": "Ana",
                    "to_display_name": "Ana",
                    "relationship_type": "self",
                },
            ],
        },
    )

    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert created_relationships == [
        {
            "from_contact_id": "contact-ana",
            "to_contact_id": "contact-ana",
            "relationship_type": "self",
        }
    ]


def test_confirm_event_filters_group_members_by_participant_override(monkeypatch):
    command_data = _base_command_data()
    command_data["resolution"]["proposed_contact_groups"] = [
        {
            "name": "soccer team",
            "description": "Contacts associated with group 'soccer team'.",
            "aliases": ["soccer team"],
            "source": "inferred",
            "added_via": "selector_group",
            "contact_ids": ["contact-ana", "contact-bruno"],
            "replace_members": True,
            "confirmed": False,
        }
    ]

    upserts: list[dict] = []

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr("commands.event.events_service.ingest_event", lambda _event_in: None)
    monkeypatch.setattr(
        "commands.event.contact_groups_service.upsert_group_from_selector",
        lambda **kwargs: upserts.append(kwargs) or {"group_id": "group:filtered"},
    )

    payload = EventCommandConfirmation(
        preview_id="event:preview:group-override",
        confirmed=True,
        modifications={"contact_ids": ["contact-ana"]},
        group_confirmations={"soccer team": True},
    )
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert len(upserts) == 1
    assert upserts[0]["member_contact_ids"] == ["contact-ana"]


def test_confirm_event_creates_place_from_edited_where_when_unmatched(monkeypatch):
    command_data = _base_command_data()
    command_data["extracted"]["where"] = "Old place"

    created_places_inputs: list[dict] = []
    captured_event = {"event": None}

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr(
        "commands.event.events_service.ingest_event",
        lambda event_in: captured_event.__setitem__("event", event_in),
    )
    monkeypatch.setattr(
        "commands.event.places_service.find_best_place_match",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "commands.event.places_service.ingest_place",
        lambda place_in: created_places_inputs.append(
            {
                "place_id": place_in.place_id,
                "name": place_in.name,
            }
        ),
    )

    payload = EventCommandConfirmation(
        preview_id="event:preview:new-place-from-edit",
        confirmed=True,
        modifications={"where": "New cafe downtown"},
    )
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert len(created_places_inputs) == 1
    assert created_places_inputs[0]["name"] == "New cafe downtown"
    assert len(result.created_places) == 1
    assert result.created_places[0]["name"] == "New cafe downtown"
    assert captured_event["event"] is not None
    assert captured_event["event"].place_id == created_places_inputs[0]["place_id"]


def test_confirm_event_edited_where_does_not_persist_stale_new_place_candidates(monkeypatch):
    command_data = _base_command_data()
    command_data["extracted"]["where"] = "Old place"
    command_data["resolution"]["new_entities"]["places"] = [
        {
            "name": "Old place",
        }
    ]

    created_places_inputs: list[dict] = []
    captured_event = {"event": None}

    monkeypatch.setattr("commands.event.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.event.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr(
        "commands.event.clear_pending_event_by_preview_id", lambda _preview_id: None
    )
    monkeypatch.setattr("commands.event._persist_event_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr(
        "commands.event.events_service.ingest_event",
        lambda event_in: captured_event.__setitem__("event", event_in),
    )
    monkeypatch.setattr(
        "commands.event.places_service.find_best_place_match",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "commands.event.places_service.ingest_place",
        lambda place_in: created_places_inputs.append(
            {
                "place_id": place_in.place_id,
                "name": place_in.name,
            }
        ),
    )

    payload = EventCommandConfirmation(
        preview_id="event:preview:edited-where-no-duplicates",
        confirmed=True,
        modifications={"where": "New cafe downtown"},
    )
    result = confirm_event_command(payload, "user@example.com")

    assert result.success is True
    assert len(created_places_inputs) == 1
    assert created_places_inputs[0]["name"] == "New cafe downtown"
    assert len(result.created_places) == 1
    assert result.created_places[0]["name"] == "New cafe downtown"
    assert captured_event["event"] is not None
    assert captured_event["event"].place_id == created_places_inputs[0]["place_id"]
