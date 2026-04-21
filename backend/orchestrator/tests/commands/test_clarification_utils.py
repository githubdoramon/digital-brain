from commands.handlers import clarification_utils
from commands.handlers.clarification_utils import build_clarification_storage_payload


def test_build_clarification_storage_payload_seeds_user_and_assistant_messages():
    payload = build_clarification_storage_payload(
        original_message="met with Alex",
        assistant_prompt="When was it?",
        requested_fields=[{"id": "when", "label": "When", "required": True}],
        extra_payload={"thread_id": "thread-1"},
    )

    assert payload["original_message"] == "met with Alex"
    assert payload["requested_fields"] == [{"id": "when", "label": "When", "required": True}]
    assert payload["conversation_messages"] == [
        {"role": "user", "content": "met with Alex"},
        {"role": "assistant", "content": "When was it?"},
    ]
    assert payload["thread_id"] == "thread-1"


def test_build_clarification_storage_payload_supports_custom_message_key():
    payload = build_clarification_storage_payload(
        original_message="met with Alex",
        assistant_prompt="Which Alex?",
        existing_messages=[{"role": "user", "content": "met with Alex"}],
        requested_fields=[],
        message_key="clarification_messages",
    )

    assert "conversation_messages" not in payload
    assert payload["clarification_messages"] == [
        {"role": "user", "content": "met with Alex"},
        {"role": "assistant", "content": "Which Alex?"},
    ]


def test_build_clarification_result_wraps_payload():
    result = clarification_utils.build_clarification_result(
        "event:clarification:abc12345",
        {"prompt": "When was it?"},
        {"original_message": "met with Alex"},
    )

    assert result == {
        "type": "need_user_input",
        "clarification_id": "event:clarification:abc12345",
        "need_user_input": {"prompt": "When was it?"},
        "original_message": "met with Alex",
    }


def test_create_clarification_preview_id_uses_command_prefix():
    preview_id = clarification_utils.create_clarification_preview_id("contact")

    assert preview_id.startswith("contact:clarification:")


def test_store_clarification_preview_persists_and_sets_pending(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "commands.storage.store_command_data",
        lambda key, payload: captured.setdefault("command_data", (key, payload)),
    )
    monkeypatch.setattr(
        "commands.storage.store_pending_event",
        lambda pending_key, preview_id: captured.setdefault(
            "pending_event", (pending_key, preview_id)
        ),
    )

    clarification_utils.store_clarification_preview(
        "contact:clarification:abc12345",
        {"original_message": "Rita is my physiotherapist"},
        "user@example.com:thread-1",
    )

    assert captured["command_data"] == (
        "contact:clarification:abc12345",
        {"original_message": "Rita is my physiotherapist"},
    )
    assert captured["pending_event"] == (
        "user@example.com:thread-1",
        "contact:clarification:abc12345",
    )
