import os

from commands.parser import ParsedCommand

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("LLM_CHAT_MODEL_FAST", "test-fast")
os.environ.setdefault("LLM_CHAT_MODEL_SMART", "test-smart")

from routes import chat as chat_routes


def test_main_command_thread_is_pinned_before_command_execution(monkeypatch):
    calls: list[str] = []

    def fake_ensure_thread(thread_id, user_email, title=None):
        calls.append("ensure_thread")
        assert thread_id is None
        assert user_email == "user@example.com"
        assert title == "Command: /event"
        return {"id": "thread_command"}

    def fake_set_main_session_thread(user_email, thread_id):
        calls.append("set_main_session_thread")
        assert user_email == "user@example.com"
        assert thread_id == "thread_command"

    class FakeRegistry:
        @staticmethod
        def execute(parsed_cmd, context):
            calls.append("execute_command")
            assert parsed_cmd.command == "event"
            assert context["thread_id"] == "thread_command"
            assert calls.index("set_main_session_thread") < calls.index("execute_command")
            return {
                "type": "event_confirmation",
                "preview_id": "event:preview:abc12345",
                "message": "Event proposal ready.",
            }

    monkeypatch.setattr(chat_routes.conversations, "ensure_thread", fake_ensure_thread)
    monkeypatch.setattr(
        chat_routes.conversations,
        "set_main_session_thread",
        fake_set_main_session_thread,
    )
    monkeypatch.setattr(chat_routes.conversations, "record_exchange", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "commands.parse_command",
        lambda _question: ParsedCommand(
            command="event",
            args="project check-in",
            raw_message="/event project check-in",
        ),
    )
    monkeypatch.setattr("commands.get_command_registry", lambda: FakeRegistry())

    result = chat_routes._handle_command(
        "/event project check-in",
        "user@example.com",
        {"email": "user@example.com"},
        thread_id=None,
    )

    assert result is not None
    assert calls == ["ensure_thread", "set_main_session_thread", "execute_command"]
