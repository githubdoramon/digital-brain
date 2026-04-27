"""Tests for UI DSL validation/sanitization."""

from ui_dsl.validator import (
    sanitize_ui_directives_payload,
    sanitize_ui_submission_payload,
    validate_ui_directive_tool_param,
)


def test_accepts_valid_choice_buttons_directive():
    payload = {
        "version": "1.0",
        "fallback_text": "Pick one option.",
        "blocks": [
            {
                "id": "range_picker",
                "type": "choice_buttons",
                "title": "Time range",
                "options": [
                    {"id": "7d", "label": "Last 7 days"},
                    {"id": "30d", "label": "Last 30 days"},
                ],
            }
        ],
    }

    sanitized, errors = sanitize_ui_directives_payload(payload)
    assert errors == []
    assert sanitized is not None
    assert sanitized["version"] == "1.0"
    valid, validator_errors = validate_ui_directive_tool_param(payload)
    assert valid is True
    assert validator_errors == []


def test_validate_ui_directive_tool_param_returns_detailed_errors():
    """Repair feedback hinges on the validator surfacing concrete error paths."""
    payload = {
        "fallback_text": "...",
        "form": [{"name": "clarification", "type": "textarea"}],
        "type": "clarification_form",
    }
    valid, validator_errors = validate_ui_directive_tool_param(payload)
    assert valid is False
    assert any("blocks" in err for err in validator_errors)


def test_validate_ui_directive_tool_param_rejects_non_dict():
    """Top-level type errors come back as detailed messages, not booleans."""
    valid, validator_errors = validate_ui_directive_tool_param("clarification_form")
    assert valid is False
    assert any("must be an object" in err for err in validator_errors)


def test_rejects_non_https_link():
    payload = {
        "version": "1.0",
        "fallback_text": "See details in plain text.",
        "blocks": [
            {
                "id": "card_1",
                "type": "info_card",
                "title": "Market snapshot",
                "links": [{"label": "Open", "url": "http://example.com"}],
            }
        ],
    }

    _, errors = sanitize_ui_directives_payload(payload)
    assert errors
    assert any("https://" in err for err in errors)


def test_submission_accepts_structured_or_text_fallback():
    structured_payload = {
        "block_id": "range_picker",
        "action_id": "select",
        "values": {"range": "7d"},
    }
    structured, structured_errors = sanitize_ui_submission_payload(structured_payload)
    assert structured_errors == []
    assert structured is not None
    assert structured["values"]["range"] == "7d"

    fallback_payload = {
        "text_fallback": "Last 7 days please",
    }
    fallback, fallback_errors = sanitize_ui_submission_payload(fallback_payload)
    assert fallback_errors == []
    assert fallback is not None
    assert fallback["text_fallback"] == "Last 7 days please"


def test_submission_rejects_empty_payload():
    _, errors = sanitize_ui_submission_payload({})
    assert errors
    assert any("text_fallback" in err or "block_id" in err for err in errors)


def test_accepts_time_field_kind():
    payload = {
        "version": "1.0",
        "fallback_text": "Share time details.",
        "blocks": [
            {
                "id": "time_form",
                "type": "clarification_form",
                "fields": [
                    {
                        "id": "start_time",
                        "kind": "time",
                        "label": "Start time",
                        "required": False,
                    }
                ],
            }
        ],
    }

    sanitized, errors = sanitize_ui_directives_payload(payload)
    assert errors == []
    assert sanitized is not None
    assert sanitized["blocks"][0]["fields"][0]["kind"] == "time"
