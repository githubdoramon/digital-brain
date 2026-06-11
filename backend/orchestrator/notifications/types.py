from __future__ import annotations

EMERGENCY_STOCK_NOTIFICATION_TYPE = "emergency-stock"
DAILY_BRIEFING_NOTIFICATION_TYPE = "daily-briefing"
CHAT_REPLY_NOTIFICATION_TYPE = "chat-reply"
PROPOSED_EVENTS_READY_NOTIFICATION_TYPE = "proposed-events-ready"

NOTIFICATION_TYPE_TITLES: dict[str, str] = {
    CHAT_REPLY_NOTIFICATION_TYPE: "Chat replies",
    DAILY_BRIEFING_NOTIFICATION_TYPE: "Daily briefing ready",
    EMERGENCY_STOCK_NOTIFICATION_TYPE: "Emergency stock alerts",
    PROPOSED_EVENTS_READY_NOTIFICATION_TYPE: "Proposed events ready",
}


def list_notification_types() -> list[str]:
    return list(NOTIFICATION_TYPE_TITLES.keys())


def get_notification_type_title(notification_type: str) -> str:
    return NOTIFICATION_TYPE_TITLES.get(notification_type, notification_type)
