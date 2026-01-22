from __future__ import annotations

from typing import Any

from notifications.channels.email import send_email_notification
from notifications.channels.push import fetch_push_tokens, send_push_notification
from notifications.subscriptions import list_subscriptions


def send_notification(notification_type: str, title: str, message: str) -> dict[str, Any]:
    subscriptions = list_subscriptions(notification_type)
    results: dict[str, Any] = {
        "notification_type": notification_type,
        "sent": {"push": 0, "email": 0},
        "errors": [],
    }

    for subscription in subscriptions:
        user_email = subscription["user_email"]
        channels = {channel.lower() for channel in subscription.get("notification_channels", [])}
        if "push" in channels:
            tokens = fetch_push_tokens(user_email)
            if tokens:
                push_result = send_push_notification(title, message, tokens)
                results["sent"]["push"] += push_result.get("success", 0)
                errors = push_result.get("errors") or []
                results["errors"].extend([f"push:{user_email}:{err}" for err in errors])
        if "email" in channels:
            email_result = send_email_notification(user_email, title, message)
            results["sent"]["email"] += email_result.get("sent", 0)
            errors = email_result.get("errors") or []
            results["errors"].extend([f"email:{user_email}:{err}" for err in errors])

    return results
