"""Tests for UI tool handlers."""

from agent.state import AgentState
from tools.handlers.ui import handle_emit_ui_directive


def test_emit_ui_directive_updates_state_with_valid_payload():
    state = AgentState(goal="Ask a follow-up question")
    args = {
        "directive": {
            "version": "1.0",
            "fallback_text": "Which range should I use?",
            "blocks": [
                {
                    "id": "range_picker",
                    "type": "choice_buttons",
                    "title": "Choose range",
                    "options": [
                        {"id": "7d", "label": "Last 7 days"},
                        {"id": "30d", "label": "Last 30 days"},
                    ],
                }
            ],
        }
    }

    result = handle_emit_ui_directive(args, state=state)

    assert result.get("success") is True
    assert state.ui_directives is not None
    assert state.ui_directives.get("blocks", [])[0]["id"] == "range_picker"
    assert state.pending_questions


def test_emit_ui_directive_rejects_invalid_payload():
    state = AgentState(goal="Ask a follow-up question")
    args = {
        "directive": {
            "version": "1.0",
            "fallback_text": "Open details below.",
            "blocks": [
                {
                    "id": "card_1",
                    "type": "info_card",
                    "title": "Card",
                    "links": [{"label": "Open", "url": "http://insecure.example"}],
                }
            ],
        }
    }

    result = handle_emit_ui_directive(args, state=state)

    assert result.get("success") is False
    assert "error" in result
    assert state.ui_directives is None

