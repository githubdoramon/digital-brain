from __future__ import annotations

from typing import Any

from notifications.channels.push import has_push_device
from notifications.subscriptions import get_subscription, update_channel, upsert_subscription
from notifications.types import (
    EMERGENCY_STOCK_NOTIFICATION_TYPE,
    get_notification_type_title,
    list_notification_types,
)

VALID_NOTIFICATION_CHANNELS = {"push", "email"}


def get_push_settings(user_email: str) -> dict[str, Any]:
    notification_types = list_notification_types()
    push_enabled = any(
        "push"
        in set(
            (get_subscription(user_email, notification_type) or {}).get("notification_channels")
            or []
        )
        for notification_type in notification_types
    )
    subscription = get_subscription(user_email, EMERGENCY_STOCK_NOTIFICATION_TYPE)
    return {
        "push_notifications_enabled": bool(push_enabled and has_push_device(user_email)),
        "created_at": subscription.get("created_at") if subscription else None,
        "updated_at": subscription.get("updated_at") if subscription else None,
    }


def update_push_settings(user_email: str, enabled: bool) -> dict[str, Any]:
    subscriptions = []
    for notification_type in list_notification_types():
        subscriptions.append(update_channel(user_email, notification_type, "push", enabled))
    subscription = next((item for item in subscriptions if item), None)
    return {
        "push_notifications_enabled": has_push_device(user_email) if enabled else False,
        "created_at": subscription.get("created_at") if subscription else None,
        "updated_at": subscription.get("updated_at") if subscription else None,
    }


def get_notification_settings(user_email: str) -> dict[str, Any]:
    settings: list[dict[str, Any]] = []
    for notification_type in list_notification_types():
        subscription = get_subscription(user_email, notification_type)
        channels = sorted(subscription.get("notification_channels") or []) if subscription else []
        settings.append(
            {
                "notification_type": notification_type,
                "title": get_notification_type_title(notification_type),
                "enabled": bool(channels),
                "channels": channels,
                "created_at": subscription.get("created_at") if subscription else None,
                "updated_at": subscription.get("updated_at") if subscription else None,
            }
        )
    return {
        "push_available": has_push_device(user_email),
        "types": settings,
    }


def update_notification_channels(
    user_email: str,
    notification_type: str,
    channels: list[str],
) -> dict[str, Any]:
    normalized = sorted({channel.strip().lower() for channel in channels if channel})
    if notification_type not in set(list_notification_types()):
        raise ValueError(f"Unknown notification type: {notification_type}")
    invalid = [channel for channel in normalized if channel not in VALID_NOTIFICATION_CHANNELS]
    if invalid:
        raise ValueError(f"Unknown notification channels: {', '.join(sorted(invalid))}")

    subscription = upsert_subscription(user_email, notification_type, normalized)
    channels_value = sorted(subscription.get("notification_channels") or []) if subscription else []
    return {
        "notification_type": notification_type,
        "title": get_notification_type_title(notification_type),
        "enabled": bool(channels_value),
        "channels": channels_value,
        "created_at": subscription.get("created_at") if subscription else None,
        "updated_at": subscription.get("updated_at") if subscription else None,
    }
