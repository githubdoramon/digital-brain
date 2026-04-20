from commands.contact import confirm_contact_command
from commands.handlers.contact import handle_contact
from commands.parser import ParsedCommand
from commands.storage import clear_pending_event, delete_command_data, get_command_data
from schemas import ContactCommandConfirmation
from ui_dsl.clarification import SUPPORTED_CLARIFICATION_FIELD_KINDS


def test_handle_contact_builds_contact_confirmation_and_derives_family_links(monkeypatch):
    parsed = ParsedCommand(
        command="contact",
        args="Dana is the father of Isa",
        raw_message="/contact Dana is the father of Isa",
    )
    context = {
        "user_email": "user@example.com",
        "thread_id": "thread-123",
        "event_pending_key": "user@example.com:thread-123",
    }

    monkeypatch.setattr(
        "commands.handlers.contact._llm_extract_contact_changes",
        lambda *_args, **_kwargs: {
            "main_contact_name": "Dana",
            "related_contact_name": "Isa",
            "relationship_type": "father",
            "birth_date_text": None,
            "place_text": None,
            "place_role": None,
            "contact_updates": [
                {
                    "contact_name": "Dana",
                    "aliases": ["Izzy"],
                    "emails": ["dana@example.com"],
                    "phones": ["+351123"],
                    "links": ["https://dana.example.com"],
                    "tags": ["family"],
                    "profession": "Engineer",
                    "comments": "Prefers morning calls",
                }
            ],
            "need_user_input": None,
        },
    )
    monkeypatch.setattr(
        "commands.handlers.contact.contacts_service.search_contacts",
        lambda name, **_kwargs: [
            {"contact_id": "contact:dana", "display_name": "Dana", "match_score": 100}
        ]
        if name == "Dana"
        else [],
    )

    def fake_get_contact(contact_id):
        if contact_id == "contact:dana":
            return {
                "contact_id": "contact:dana",
                "display_name": "Dana",
                "relationships": [
                    {"contact_id": "contact:sage", "type": "spouse", "other_type": "spouse"},
                    {"contact_id": "contact:joao", "type": "father", "other_type": "child"},
                ],
            }
        if contact_id == "contact:sage":
            return {"contact_id": "contact:sage", "display_name": "Sage", "relationships": []}
        if contact_id == "contact:joao":
            return {"contact_id": "contact:joao", "display_name": "Joao", "relationships": []}
        return None

    monkeypatch.setattr("commands.handlers.contact.contacts_service.get_contact", fake_get_contact)

    result = handle_contact(parsed, context)

    assert result["type"] == "contact_confirmation"
    assert any("Dana -> father -> Isa" in line for line in result["explicit_change_lines"])
    assert any("Sage -> parent -> Isa" in line for line in result["derived_change_lines"])
    assert any("Joao -> grandfather -> Isa" in line for line in result["derived_change_lines"])
    assert any(field["id"] == "aliases" for field in result["edit_fields"])
    assert all(field["kind"] in SUPPORTED_CLARIFICATION_FIELD_KINDS for field in result["edit_fields"])
    assert any(field["id"] == "derived_" + rel["proposal_id"] for field in result["edit_fields"] for rel in result["proposal"]["derived_relationships"])

    preview_id = result["preview_id"]
    stored = get_command_data(preview_id)
    assert stored is not None
    assert stored["command_name"] == "contact"

    delete_command_data(preview_id)
    clear_pending_event(context["event_pending_key"])


def test_handle_contact_requests_clarification_for_ambiguous_birth_date(monkeypatch):
    parsed = ParsedCommand(
        command="contact",
        args="Bia was born at 7/5/1994",
        raw_message="/contact Bia was born at 7/5/1994",
    )
    context = {
        "user_email": "user@example.com",
        "thread_id": "thread-234",
        "event_pending_key": "user@example.com:thread-234",
    }

    monkeypatch.setattr(
        "commands.handlers.contact._llm_extract_contact_changes",
        lambda *_args, **_kwargs: {
            "main_contact_name": "Bia",
            "related_contact_name": None,
            "relationship_type": None,
            "birth_date_text": "7/5/1994",
            "place_text": None,
            "place_role": None,
            "contact_updates": [],
            "need_user_input": None,
        },
    )
    monkeypatch.setattr(
        "commands.handlers.contact.contacts_service.search_contacts",
        lambda *_args, **_kwargs: [],
    )

    result = handle_contact(parsed, context)

    assert result["type"] == "need_user_input"
    prompt = result["need_user_input"]["prompt"]
    assert "birth date" in prompt.lower()

    clarification_id = result["clarification_id"]
    delete_command_data(clarification_id)
    clear_pending_event(context["event_pending_key"])


def test_handle_contact_supports_multiple_contacts_and_place_links(monkeypatch):
    parsed = ParsedCommand(
        command="contact",
        args="Ana and Bruno are lawyers and live at Rua X",
        raw_message="/contact Ana and Bruno are lawyers and live at Rua X",
    )
    context = {
        "user_email": "user@example.com",
        "thread_id": "thread-multi-place",
        "event_pending_key": "user@example.com:thread-multi-place",
    }

    monkeypatch.setattr(
        "commands.handlers.contact._llm_extract_contact_changes",
        lambda *_args, **_kwargs: {
            "contacts": [
                {"contact_name": "Ana", "profession": "Lawyer"},
                {"contact_name": "Bruno", "profession": "Lawyer"},
            ],
            "relationships": [],
            "contact_place_links": [
                {"contact_name": "Ana", "place_text": "Rua X", "place_role": "home"},
                {"contact_name": "Bruno", "place_text": "Rua X", "place_role": "home"},
            ],
            "need_user_input": None,
        },
    )
    monkeypatch.setattr(
        "commands.handlers.contact.contacts_service.search_contacts",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "commands.handlers.contact.places_service.find_best_place_match",
        lambda *_args, **_kwargs: None,
    )

    result = handle_contact(parsed, context)

    assert result["type"] == "contact_confirmation"
    proposal = result["proposal"]
    created_contacts = [item for item in proposal["contacts"] if item.get("operation") == "create"]
    assert {item["display_name"] for item in created_contacts} == {"Ana", "Bruno"}
    assert len(proposal["contact_place_links"]) == 2
    assert len(proposal["places"]) == 1
    assert any("Add profession for Ana: Lawyer" in line for line in result["explicit_change_lines"])
    assert any("Link Bruno to Rua X as home" in line for line in result["explicit_change_lines"])

    delete_command_data(result["preview_id"])
    clear_pending_event(context["event_pending_key"])


def test_handle_contact_supports_independent_relationship_pairs(monkeypatch):
    parsed = ParsedCommand(
        command="contact",
        args="Ana is Bruno's sister and Carlos is Diana's father",
        raw_message="/contact Ana is Bruno's sister and Carlos is Diana's father",
    )
    context = {
        "user_email": "user@example.com",
        "thread_id": "thread-multi-rel",
        "event_pending_key": "user@example.com:thread-multi-rel",
    }

    monkeypatch.setattr(
        "commands.handlers.contact._llm_extract_contact_changes",
        lambda *_args, **_kwargs: {
            "contacts": [],
            "relationships": [
                {
                    "from_contact_name": "Ana",
                    "to_contact_name": "Bruno",
                    "relationship_type": "sister",
                },
                {
                    "from_contact_name": "Carlos",
                    "to_contact_name": "Diana",
                    "relationship_type": "father",
                },
            ],
            "contact_place_links": [],
            "need_user_input": None,
        },
    )
    monkeypatch.setattr(
        "commands.handlers.contact.contacts_service.search_contacts",
        lambda *_args, **_kwargs: [],
    )

    result = handle_contact(parsed, context)

    assert result["type"] == "contact_confirmation"
    relationships = result["proposal"]["relationships"]
    assert len(relationships) == 2
    assert {(rel["from_display_name"], rel["relationship_type"], rel["to_display_name"]) for rel in relationships} == {
        ("Ana", "sister", "Bruno"),
        ("Carlos", "father", "Diana"),
    }

    delete_command_data(result["preview_id"])
    clear_pending_event(context["event_pending_key"])


def test_confirm_contact_command_applies_updates(monkeypatch):
    command_data = {
        "proposal": {
            "contacts": [
                {
                    "proposal_id": "contact-create-isa",
                    "operation": "create",
                    "reference": "new_contact:isa",
                    "display_name": "Isa",
                },
                {
                    "proposal_id": "contact-update-paul",
                    "operation": "update",
                    "reference": "contact:paul",
                    "display_name": "Paul",
                    "merged": {
                        "contact_id": "contact:paul",
                        "display_name": "Paul",
                        "aliases": [],
                        "birthday": "1994-05-07",
                        "emails": [],
                        "phones": [],
                        "links": [],
                        "tags": [],
                        "comments": "Likes gardening",
                    },
                },
            ],
            "relationships": [
                {
                    "proposal_id": "rel-explicit",
                    "from_reference": "contact:dana",
                    "to_reference": "new_contact:isa",
                    "relationship_type": "father",
                    "reciprocal_type": "child",
                }
            ],
            "derived_relationships": [
                {
                    "proposal_id": "rel-derived",
                    "from_reference": "contact:sage",
                    "to_reference": "new_contact:isa",
                    "relationship_type": "parent",
                    "reciprocal_type": "child",
                }
            ],
            "places": [
                {
                    "proposal_id": "place-create-1",
                    "operation": "create",
                    "reference": "new_place:rua-da-horta-44",
                    "name": "44 Garden Lane",
                    "address": "44 Garden Lane",
                }
            ],
            "contact_place_links": [
                {
                    "proposal_id": "place-link-1",
                    "contact_reference": "contact:paul",
                    "place_reference": "new_place:rua-da-horta-44",
                    "role": "home",
                }
            ],
            "edit_context": {
                "main_contact_reference": "contact:paul",
                "related_contact_reference": "new_contact:isa",
                "primary_relationship_id": "rel-explicit",
                "place_reference": "new_place:rua-da-horta-44",
            },
        }
    }

    ingested_contacts = []
    ingested_places = []
    relationships = []
    contact_place_links = []

    monkeypatch.setattr("commands.contact.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.contact.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr("commands.contact.clear_pending_event_by_preview_id", lambda _preview_id: None)
    monkeypatch.setattr("commands.contact._persist_contact_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr(
        "commands.contact.contacts_service.ingest_contact",
        lambda contact_in: ingested_contacts.append(contact_in),
    )
    monkeypatch.setattr(
        "commands.contact.places_service.ingest_place",
        lambda place_in: ingested_places.append(place_in),
    )
    monkeypatch.setattr(
        "commands.contact.contacts_service.upsert_contact_relationship",
        lambda rel_in: relationships.append(rel_in),
    )
    monkeypatch.setattr(
        "commands.contact.places_service.upsert_contact_place",
        lambda **kwargs: contact_place_links.append(kwargs),
    )

    def fake_get_contact(contact_id):
        if contact_id == "contact:paul":
            return {
                "contact_id": "contact:paul",
                "display_name": "Paul",
                "aliases": [],
                "emails": [],
                "phones": [],
                "links": [],
                "tags": [],
                "comments": None,
                "external_id": None,
                "relationships": [],
            }
        if contact_id in {"contact:dana", "contact:sage"}:
            return {
                "contact_id": contact_id,
                "display_name": contact_id.split(":", 1)[1].title(),
                "relationships": [],
            }
        return None

    monkeypatch.setattr("commands.contact.contacts_service.get_contact", fake_get_contact)
    monkeypatch.setattr("commands.contact.contacts_service.find_related_types", lambda value: [value])

    payload = ContactCommandConfirmation(
        preview_id="contact:preview:abc123",
        confirmed=True,
        modifications={
            "main_display_name": "Paulo",
            "emails": "paulo@example.com",
            "phones": "+351999",
            "aliases": "Paulinho",
            "tags": "close-family",
            "comments": "Updated note",
            "place_name": "12 New Road",
            "place_role": "family_home",
            "derived_rel-derived": "no",
        },
    )
    result = confirm_contact_command(payload, "user@example.com")

    assert result.success is True
    assert len(ingested_contacts) == 2
    assert len(ingested_places) == 1
    assert len(relationships) == 1
    assert len(contact_place_links) == 1
    assert ingested_contacts[-1].display_name == "Paulo"
    assert ingested_contacts[-1].emails == ["paulo@example.com"]
    assert ingested_contacts[-1].phones == ["+351999"]
    assert ingested_contacts[-1].aliases == ["Paulinho"]
    assert ingested_contacts[-1].tags == ["close-family"]
    assert contact_place_links[0]["role"] == "family_home"


def test_confirm_contact_command_supports_multiple_contact_edits(monkeypatch):
    command_data = {
        "proposal": {
            "contacts": [
                {
                    "proposal_id": "contact-update-dana",
                    "operation": "update",
                    "reference": "contact:dana",
                    "display_name": "Dana",
                    "merged": {
                        "contact_id": "contact:dana",
                        "display_name": "Dana",
                        "aliases": ["Izzy"],
                        "birthday": None,
                        "emails": [],
                        "phones": [],
                        "links": [],
                        "tags": [],
                        "comments": None,
                    },
                },
                {
                    "proposal_id": "contact-update-sage",
                    "operation": "update",
                    "reference": "contact:sage",
                    "display_name": "Sage",
                    "merged": {
                        "contact_id": "contact:sage",
                        "display_name": "Sage",
                        "aliases": [],
                        "birthday": None,
                        "emails": [],
                        "phones": [],
                        "links": [],
                        "tags": [],
                        "comments": None,
                    },
                },
            ],
            "relationships": [],
            "derived_relationships": [],
            "places": [],
            "contact_place_links": [],
        }
    }
    ingested_contacts = []

    monkeypatch.setattr("commands.contact.get_command_data", lambda _preview_id: command_data)
    monkeypatch.setattr("commands.contact.delete_command_data", lambda _preview_id: None)
    monkeypatch.setattr("commands.contact.clear_pending_event_by_preview_id", lambda _preview_id: None)
    monkeypatch.setattr("commands.contact._persist_contact_resolved", lambda _preview_id, _status: None)
    monkeypatch.setattr(
        "commands.contact.contacts_service.ingest_contact",
        lambda contact_in: ingested_contacts.append(contact_in),
    )
    monkeypatch.setattr(
        "commands.contact.contacts_service.get_contact",
        lambda contact_id: {
            "contact_id": contact_id,
            "display_name": contact_id.split(":", 1)[1].title(),
            "aliases": [],
            "emails": [],
            "phones": [],
            "links": [],
            "tags": [],
            "comments": None,
            "external_id": None,
            "relationships": [],
        },
    )
    monkeypatch.setattr("commands.contact.contacts_service.find_related_types", lambda value: [value])

    payload = ContactCommandConfirmation(
        preview_id="contact:preview:multi123",
        confirmed=True,
        modifications={
            "contacts": [
                {
                    "proposal_id": "contact-update-dana",
                    "display_name": "Dana Stone",
                    "emails": ["dana@example.com"],
                },
                {
                    "proposal_id": "contact-update-sage",
                    "display_name": "Patricia",
                    "phones": ["+351444"],
                    "comments": "Updated from editor",
                },
            ]
        },
    )
    result = confirm_contact_command(payload, "user@example.com")

    assert result.success is True
    assert len(ingested_contacts) == 2
    assert ingested_contacts[0].display_name == "Dana Stone"
    assert ingested_contacts[0].emails == ["dana@example.com"]
    assert ingested_contacts[1].display_name == "Patricia"
    assert ingested_contacts[1].phones == ["+351444"]
    assert ingested_contacts[1].comments == "Updated from editor"
