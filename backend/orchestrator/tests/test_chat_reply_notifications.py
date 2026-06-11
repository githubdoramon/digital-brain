from __future__ import annotations

from unittest.mock import Mock


def test_chat_reply_notification_uses_preview_and_deep_link_data(monkeypatch):
    from notifications import chat_replies

    sent = Mock(return_value={"sent": {"push": 1, "email": 0}, "errors": []})
    monkeypatch.setattr(chat_replies, "send_notification_to_user", sent)

    chat_replies.send_chat_reply_notification(
        user_email="user@example.com",
        thread_id="thread:abc",
        answer="  Done.   I found the latest update you asked for.  ",
        is_main_session=False,
    )

    sent.assert_called_once_with(
        notification_type="chat-reply",
        user_email="user@example.com",
        title="Reply ready",
        message="Done. I found the latest update you asked for.",
        data={
            "kind": "chat_reply",
            "threadId": "thread:abc",
            "isMainSession": False,
        },
    )


def test_chat_reply_notification_truncates_long_preview(monkeypatch):
    from notifications import chat_replies

    sent = Mock(return_value={"sent": {"push": 1, "email": 0}, "errors": []})
    monkeypatch.setattr(chat_replies, "send_notification_to_user", sent)

    chat_replies.send_chat_reply_notification(
        user_email="user@example.com",
        thread_id="thread:abc",
        answer="word " * 80,
        is_main_session=True,
    )

    message = sent.call_args.kwargs["message"]
    assert len(message) <= 120
    assert message.endswith("...")
    assert sent.call_args.kwargs["data"]["isMainSession"] is True


def test_push_notification_includes_data_payload(monkeypatch):
    from notifications.channels import push

    post = Mock(
        return_value=Mock(
            status_code=200,
            json=lambda: {"data": [{"status": "ok", "id": "ticket-1"}]},
        )
    )
    monkeypatch.setattr(push.requests, "post", post)

    result = push.send_push_notification(
        "Reply ready",
        "Done.",
        ["ExponentPushToken[test]"],
        data={"kind": "chat_reply", "threadId": "thread:abc"},
    )

    assert result["success"] == 1
    payload = post.call_args.kwargs["json"][0]
    assert payload["data"] == {"kind": "chat_reply", "threadId": "thread:abc"}


def test_chat_reply_notification_type_is_registered():
    from notifications.types import CHAT_REPLY_NOTIFICATION_TYPE, list_notification_types

    assert CHAT_REPLY_NOTIFICATION_TYPE == "chat-reply"
    assert CHAT_REPLY_NOTIFICATION_TYPE in list_notification_types()
