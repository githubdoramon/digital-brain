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


def _revision_cursor(monkeypatch, row):
    from unittest.mock import MagicMock
    conn, cur = MagicMock(), MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.return_value = row
    monkeypatch.setattr(conversations, "get_conn", lambda: conn)
    return conn, cur


def test_chat_revision_is_owner_scoped_and_does_not_load_messages_or_touch_session(monkeypatch):
    now = datetime.now(timezone.utc)
    conn, cur = _revision_cursor(monkeypatch, {
        "id": "thread:sample", "updated_at": now, "session_updated_at": now,
    })
    assert conversations.get_chat_state("alex@example.com") == {
        "thread_id": "thread:sample", "revision": now.isoformat(),
    }
    query, args = cur.execute.call_args.args
    assert args == ("alex@example.com",)
    assert "t.user_email=ms.user_email" in query
    assert "conversation_messages" not in query and "UPDATE" not in query
    conn.commit.assert_not_called()


def test_expired_main_revision_is_empty_without_extending_the_idle_window(monkeypatch):
    now = datetime.now(timezone.utc)
    conn, _ = _revision_cursor(monkeypatch, {
        "id": "thread:old", "updated_at": now,
        "session_updated_at": now - timedelta(minutes=31),
    })
    assert conversations.get_chat_state("alex@example.com") == {"thread_id": None, "revision": None}
    conn.commit.assert_not_called()


def test_explicit_thread_probe_checks_owner_and_does_not_apply_main_timeout(monkeypatch):
    now = datetime.now(timezone.utc) - timedelta(days=1)
    _, cur = _revision_cursor(monkeypatch, {"id": "thread:history", "updated_at": now})
    assert conversations.get_chat_state("alex@example.com", "thread:history")["revision"] == now.isoformat()
    query, args = cur.execute.call_args.args
    assert "user_email=%s" in query and args == ("thread:history", "alex@example.com")
    _revision_cursor(monkeypatch, None)
    assert conversations.get_chat_state("other@example.com", "thread:history") == {"thread_id": None, "revision": None}


def test_metadata_edits_invalidate_chat_revision_atomically(monkeypatch):
    conn, cur = _revision_cursor(monkeypatch, {"thread_id": "thread:sample"})
    assert conversations.set_message_metadata_field(1, "command_resolved", {"status": "created"})
    calls = cur.execute.call_args_list
    assert len(calls) == 2
    assert "RETURNING thread_id" in calls[0].args[0]
    assert "updated_at=NOW()" in calls[1].args[0]
    assert calls[1].args[1] == ("thread:sample",)
    conn.commit.assert_called_once()
