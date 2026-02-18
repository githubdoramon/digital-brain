from datetime import datetime

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
