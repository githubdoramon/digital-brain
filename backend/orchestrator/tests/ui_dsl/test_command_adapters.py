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
    assert "edit:event:preview:abcd1234" in option_ids
    assert "adjust:event:preview:abcd1234" in option_ids
    assert "cancel:event:preview:abcd1234" in option_ids


def test_clarification_maps_to_form_directive():
    command_result = {
        "type": "need_user_input",
        "need_user_input": {
            "kind": "clarification",
            "prompt": "What happened?",
            "questions": ["What happened?", "When was it?"],
            "fields": [
                {
                    "id": "meeting_outcome",
                    "kind": "textarea",
                    "label": "What was the outcome?",
                    "required": True,
                },
                {
                    "id": "happened_at",
                    "kind": "datetime",
                    "label": "When did this happen?",
                    "required": True,
                },
            ],
            "action_id": "event_clarification_submit:event:clarification:xyz98765",
            "context": {"clarification_id": "event:clarification:xyz98765"},
            "submission_mode": "ui_submission",
        },
    }

    directive = command_result_to_ui_directives(command_result)

    assert directive is not None
    assert directive["fallback_text"] == "What happened?"
    block = directive["blocks"][0]
    assert block["type"] == "clarification_form"
    assert block["action_id"] == "event_clarification_submit:event:clarification:xyz98765"
    field_ids = {field["id"] for field in block["fields"]}
    assert "meeting_outcome" in field_ids
    assert "happened_at" in field_ids


def test_clarification_falls_back_to_question_inference_when_no_llm_fields():
    command_result = {
        "type": "need_user_input",
        "need_user_input": {
            "kind": "clarification",
            "prompt": "When did this happen?",
            "questions": ["When did this happen?", "Where did this happen?"],
            "submission_mode": "ui_submission",
            "action_id": "event_clarification_submit:event:clarification:xyz98765",
            "context": {"clarification_id": "event:clarification:xyz98765"},
        },
    }

    directive = command_result_to_ui_directives(command_result)

    assert directive is not None
    block = directive["blocks"][0]
    field_kinds = {field["kind"] for field in block["fields"]}
    assert "datetime" in field_kinds
    assert "text" in field_kinds


def test_non_event_command_result_returns_none():
    directive = command_result_to_ui_directives({"type": "system_command"})
    assert directive is None


def test_need_user_input_payload_maps_even_without_clarification_type():
    command_result = {
        "type": "data_query",
        "need_user_input": {
            "kind": "selection",
            "prompt": "Pick a date range.",
            "questions": ["Which date range should I use?"],
            "fields": [
                {
                    "id": "date_range",
                    "kind": "select",
                    "label": "Date range",
                    "required": True,
                    "options": [
                        {"id": "7d", "label": "Last 7 days"},
                        {"id": "30d", "label": "Last 30 days"},
                    ],
                }
            ],
            "submission_mode": "ui_submission",
            "action_id": "range_selection_submit",
        },
    }

    directive = command_result_to_ui_directives(command_result)

    assert directive is not None
    block = directive["blocks"][0]
    assert block["type"] == "clarification_form"
    assert block["action_id"] == "range_selection_submit"
    assert block["fields"][0]["id"] == "date_range"
