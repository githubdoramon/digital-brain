from __future__ import annotations

from ui_dsl.command_adapters import command_result_to_ui_directives


def test_event_confirmation_maps_to_ui_directives():
    command_result = {
        "type": "event_confirmation",
        "preview_id": "event:preview:abcd1234",
        "message": "Please confirm",
        "extracted": {
            "title": "Lunch with Ana",
            "summary": "Discussed roadmap",
            "when": "2026-02-09T12:30:00",
            "where": "Downtown",
            "who": ["Ana"],
            "tags": ["work"],
            "types": ["meeting"],
        },
        "resolution": {
            "new_entities": {
                "contacts": [{"display_name": "Ana", "query": "ana"}],
                "places": [{"name": "Downtown", "query": "downtown"}],
                "documents": [],
            }
        },
    }

    directive = command_result_to_ui_directives(command_result)

    assert directive is not None
    blocks = directive["blocks"]
    assert any(block["type"] == "info_card" for block in blocks)
    choice_block = next(block for block in blocks if block["type"] == "choice_buttons")
    assert choice_block["action_id"] == "event_confirmation_action"
    option_ids = {option["id"] for option in choice_block["options"]}
    assert "confirm:event:preview:abcd1234" in option_ids
    assert "cancel:event:preview:abcd1234" in option_ids


def test_clarification_maps_to_form_directive():
    command_result = {
        "type": "clarification_needed",
        "clarification_id": "event:clarification:xyz98765",
        "questions": ["What happened?", "When was it?"],
    }

    directive = command_result_to_ui_directives(command_result)

    assert directive is not None
    assert directive["fallback_text"] == "What happened?"
    block = directive["blocks"][0]
    assert block["type"] == "clarification_form"
    assert block["action_id"] == "event_clarification_submit:event:clarification:xyz98765"
    field_kinds = {field["kind"] for field in block["fields"]}
    assert "textarea" in field_kinds
    assert "datetime" in field_kinds


def test_non_event_command_result_returns_none():
    directive = command_result_to_ui_directives({"type": "system_command"})
    assert directive is None
