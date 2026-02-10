from __future__ import annotations

from ui_dsl.clarification import (
    build_need_user_input_prompt_guidance,
    clarification_fields_from_ambiguous_contacts,
    derive_clarification_questions_from_fields,
    extract_need_user_input,
    infer_clarification_fields_from_questions,
    need_user_input_json_property_template,
    normalize_clarification_fields,
)


def test_normalize_clarification_fields_sanitizes_invalid_kind_and_options():
    raw_fields = [
        {
            "id": "meeting-time",
            "kind": "datetime",
            "label": "When did this happen?",
            "required": True,
        },
        {
            "id": "priority",
            "kind": "select",
            "label": "Priority",
            "options": [
                {"id": "high", "label": "High"},
                {"id": "low", "label": "Low"},
            ],
        },
        {
            "id": "bad-kind",
            "kind": "custom",
            "label": "Custom",
        },
    ]

    fields = normalize_clarification_fields(raw_fields)

    assert fields[0]["kind"] == "datetime"
    assert fields[1]["kind"] == "select"
    assert len(fields[1]["options"]) == 2
    assert fields[2]["kind"] == "text"


def test_infer_clarification_fields_from_questions_generates_targeted_fields():
    questions = ["When exactly was it?", "Where did this happen?"]
    partial_extraction = {"when": None, "where": None}

    fields = infer_clarification_fields_from_questions(questions, partial_extraction)
    kinds = {field["kind"] for field in fields}

    assert "datetime" in kinds
    assert "text" in kinds


def test_derive_clarification_questions_from_fields():
    fields = [
        {"id": "when", "kind": "datetime", "label": "When did this happen?", "required": True},
        {"id": "where", "kind": "text", "label": "Where", "required": False},
    ]

    questions = derive_clarification_questions_from_fields(fields)
    assert questions[0] == "When did this happen?"
    assert questions[1].endswith("(optional)")


def test_extract_need_user_input_from_explicit_payload():
    payload = {
        "need_user_input": {
            "kind": "disambiguation",
            "prompt": "Which John do you mean?",
            "questions": ["Which John do you mean?"],
            "fields": [
                {
                    "id": "who_john",
                    "kind": "select",
                    "label": "Who did you mean by 'John'?",
                    "required": True,
                    "options": [
                        {"id": "c1", "label": "John Smith"},
                        {"id": "c2", "label": "John Doe"},
                    ],
                }
            ],
        }
    }

    need_user_input = extract_need_user_input(payload, default_source="resolve_contacts")

    assert need_user_input is not None
    assert need_user_input["kind"] == "disambiguation"
    assert need_user_input["prompt"] == "Which John do you mean?"
    assert need_user_input["source"] == "resolve_contacts"
    assert need_user_input["fields"][0]["kind"] == "select"


def test_clarification_fields_from_ambiguous_contacts_builds_select_options():
    ambiguous = [
        {
            "original_text": "Ana",
            "candidates": [
                {"contact_id": "1", "display_name": "Ana Silva"},
                {"contact_id": "2", "display_name": "Ana Costa"},
            ],
        }
    ]

    fields = clarification_fields_from_ambiguous_contacts(ambiguous)
    assert fields[0]["kind"] == "select"
    assert len(fields[0]["options"]) == 2


def test_build_need_user_input_prompt_guidance_includes_people_rule_when_requested():
    guidance = build_need_user_input_prompt_guidance(exclude_people=True)

    assert '"need_user_input"' in guidance
    assert "Allowed field \"kind\" values" in guidance
    assert "Do NOT ask clarification questions about people" in guidance


def test_need_user_input_json_property_template_contains_submission_mode():
    template = need_user_input_json_property_template(indent=2, trailing_comma=False)

    assert '"need_user_input": null or {' in template
    assert '"submission_mode": "ui_submission"' in template
    assert template.rstrip().endswith("}")
