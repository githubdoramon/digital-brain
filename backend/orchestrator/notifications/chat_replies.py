from __future__ import annotations

from typing import Any

from notifications.service import send_notification_to_user
from notifications.types import CHAT_REPLY_NOTIFICATION_TYPE
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

_DEFAULT_BODY = "Your Brain has finished replying."
_MAX_PREVIEW_LENGTH = 120


def send_chat_reply_notification(
    *,
    user_email: str,
    thread_id: str,
    answer: str | None,
    is_main_session: bool,
) -> dict[str, Any]:
    if not user_email or not thread_id:
        return {
            "notification_type": CHAT_REPLY_NOTIFICATION_TYPE,
            "sent": {"push": 0, "email": 0},
            "errors": ["missing user_email or thread_id"],
        }

    message = _preview_message(answer)
    result = send_notification_to_user(
        notification_type=CHAT_REPLY_NOTIFICATION_TYPE,
        user_email=user_email,
        title="Reply ready",
        message=message,
        data={
            "kind": "chat_reply",
            "threadId": thread_id,
            "isMainSession": is_main_session,
        },
    )
    errors = result.get("errors") or []
    if errors:
        logger.warning(
            "[chat_reply_notification] errors user=%s thread=%s errors=%s",
            user_email,
            thread_id,
            errors,
        )
    else:
        logger.info(
            "[chat_reply_notification] sent user=%s thread=%s push=%s",
            user_email,
            thread_id,
            result.get("sent", {}).get("push"),
        )
    return result


def _preview_message(answer: str | None) -> str:
    if not answer:
        return _DEFAULT_BODY
    compact = " ".join(answer.split())
    if not compact:
        return _DEFAULT_BODY
    if len(compact) <= _MAX_PREVIEW_LENGTH:
        return compact
    return compact[: _MAX_PREVIEW_LENGTH - 3].rstrip() + "..."
