from __future__ import annotations

from datetime import datetime, timedelta, timezone

import conversations


def test_normalize_command_resolved_metadata_preserves_existing_shape():
    metadata = {
        "command_result": {"preview_id": "event:preview:123"},
        "command_resolved": {"status": "updated", "label": "Event updated"},
        "event_resolved": "created",
    }

    normalized = conversations._normalize_command_resolved_metadata(metadata)

    assert normalized["command_resolved"] == {
        "status": "updated",
        "label": "Event updated",
    }


def test_normalize_command_resolved_metadata_maps_legacy_event_status():
    metadata = {
        "command_result": {"preview_id": "event:preview:123"},
        "event_resolved": "created",
    }

    normalized = conversations._normalize_command_resolved_metadata(metadata)

    assert normalized["command_resolved"] == {
        "status": "created",
        "label": "Event created",
    }


def test_normalize_command_resolved_metadata_maps_legacy_contact_status():
    metadata = {
        "command_result": {"preview_id": "contact:preview:123"},
        "contact_resolved": "cancelled",
    }

    normalized = conversations._normalize_command_resolved_metadata(metadata)

    assert normalized["command_resolved"] == {
        "status": "cancelled",
        "label": "Contact update cancelled",
    }


def test_is_default_title_accepts_current_default_prefix():
    assert conversations.is_default_title("Untitled conversation - 2026-05-09 10:15 UTC") is True


def test_is_default_title_accepts_legacy_quick_chat_prefix():
    assert conversations.is_default_title("Quick Chat - 2026-05-09 10:15 UTC") is True


def test_is_main_session_timed_out_respects_idle_window():
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    stale = datetime.now(timezone.utc) - timedelta(minutes=45)

    assert conversations._is_main_session_timed_out(recent, idle_minutes=30) is False
    assert conversations._is_main_session_timed_out(stale, idle_minutes=30) is True


def test_get_active_main_session_returns_none_when_missing_thread(monkeypatch):
    cleared: list[str] = []

    monkeypatch.setattr(
        conversations,
        "get_main_session",
        lambda _user_email: {
            "current_thread_id": "thread_123",
            "thread_id": None,
            "updated_at": datetime.now(timezone.utc),
        },
    )
    monkeypatch.setattr(conversations, "clear_main_session_thread", lambda user_email: cleared.append(user_email))

    result = conversations.get_active_main_session("user@example.com")

    assert result is None
    assert cleared == ["user@example.com"]


def test_get_active_main_session_clears_timed_out_thread(monkeypatch):
    cleared: list[str] = []
    stale = datetime.now(timezone.utc) - timedelta(minutes=45)

    monkeypatch.setattr(
        conversations,
        "get_main_session",
        lambda _user_email: {
            "current_thread_id": "thread_123",
            "thread_id": "thread_123",
            "updated_at": stale,
        },
    )
    monkeypatch.setattr(conversations, "clear_main_session_thread", lambda user_email: cleared.append(user_email))

    result = conversations.get_active_main_session("user@example.com", idle_minutes=30)

    assert result is None
    assert cleared == ["user@example.com"]
