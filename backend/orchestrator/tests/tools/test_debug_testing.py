from __future__ import annotations

from tools.debug_testing import EVENT_CREATION_FLOW_CONTRACT, run_tools_page_only_tool


def test_event_creation_flow_normalizes_prompt_and_thread_id():
    normalized = EVENT_CREATION_FLOW_CONTRACT.normalize(
        {
            "prompt": "  Met with Alex about roadmap  ",
            "thread_id": "  debug-thread  ",
            "client_context": {"location": {"latitude": 10, "longitude": 20}},
        }
    )

    assert normalized == {
        "prompt": "Met with Alex about roadmap",
        "thread_id": "debug-thread",
        "client_context": {"location": {"latitude": 10, "longitude": 20}},
    }


def test_event_creation_flow_runs_command_and_returns_debug_context(monkeypatch):
    monkeypatch.setattr(
        "tools.debug_testing.handle_event",
        lambda parsed, context: {
            "type": "event_confirmation",
            "preview_id": "event:preview:abc123",
            "message": "Preview ready",
            "echo": parsed.args,
            "context_thread": context.get("thread_id"),
        },
    )
    monkeypatch.setattr(
        "tools.debug_testing.command_result_to_ui_directives",
        lambda command_result: {"fallback_text": command_result.get("message")},
    )

    result = run_tools_page_only_tool(
        "event_creation_flow",
        {"prompt": "Met with Alex about roadmap", "thread_id": "debug-thread"},
        user_email="user@example.com",
    )

    assert result["command_result"]["type"] == "event_confirmation"
    assert result["command_result"]["echo"] == "Met with Alex about roadmap"
    assert result["ui_directives"] == {"fallback_text": "Preview ready"}
    assert result["debug_context"]["thread_id"] == "debug-thread"
    assert result["debug_context"]["pending_event_key"] == "user@example.com:debug-thread"
