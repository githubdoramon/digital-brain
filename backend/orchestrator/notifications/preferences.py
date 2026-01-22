from __future__ import annotations

from typing import Any

from notifications.channels.push import has_push_device
from notifications.subscriptions import get_subscription, update_channel
from notifications.types import EMERGENCY_STOCK_NOTIFICATION_TYPE


def get_push_settings(user_email: str) -> dict[str, Any]:
    subscription = get_subscription(user_email, EMERGENCY_STOCK_NOTIFICATION_TYPE)
    return {
        "push_notifications_enabled": has_push_device(user_email),
        "created_at": subscription.get("created_at") if subscription else None,
        "updated_at": subscription.get("updated_at") if subscription else None,
    }


def update_push_settings(user_email: str, enabled: bool) -> dict[str, Any]:
    subscription = update_channel(user_email, EMERGENCY_STOCK_NOTIFICATION_TYPE, "push", enabled)
    return {
        "push_notifications_enabled": has_push_device(user_email) if enabled else False,
        "created_at": subscription.get("created_at") if subscription else None,
        "updated_at": subscription.get("updated_at") if subscription else None,
    }
