from __future__ import annotations

from typing import Any

from db import get_conn

VALID_CHANNELS = {"push", "email"}


def list_subscriptions(notification_type: str) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_email,
                   notification_channels,
                   created_at,
                   updated_at
            FROM notification_subscriptions
            WHERE notification_type = %s
            """,
            (notification_type,),
        )
        rows = cur.fetchall() or []
    return [
        {
            "user_email": row["user_email"],
            "notification_channels": list(row.get("notification_channels") or []),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        for row in rows
    ]


def get_subscription(user_email: str, notification_type: str) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_email,
                   notification_channels,
                   created_at,
                   updated_at
            FROM notification_subscriptions
            WHERE user_email = %s AND notification_type = %s
            """,
            (user_email, notification_type),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "user_email": row["user_email"],
        "notification_channels": list(row.get("notification_channels") or []),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def upsert_subscription(
    user_email: str, notification_type: str, channels: list[str]
) -> dict[str, Any] | None:
    normalized = _normalize_channels(channels)
    if not normalized:
        delete_subscription(user_email, notification_type)
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO notification_subscriptions (
              user_email,
              notification_type,
              notification_channels
            )
            VALUES (%s, %s, %s)
            ON CONFLICT (user_email, notification_type)
            DO UPDATE SET
              notification_channels = EXCLUDED.notification_channels,
              updated_at = NOW()
            RETURNING user_email,
                      notification_channels,
                      created_at,
                      updated_at
            """,
            (user_email, notification_type, normalized),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "user_email": row["user_email"],
        "notification_channels": list(row.get("notification_channels") or []),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def delete_subscription(user_email: str, notification_type: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM notification_subscriptions
            WHERE user_email = %s AND notification_type = %s
            """,
            (user_email, notification_type),
        )
        conn.commit()


def update_channel(
    user_email: str,
    notification_type: str,
    channel: str,
    enabled: bool,
) -> dict[str, Any] | None:
    channel = channel.strip().lower()
    if channel not in VALID_CHANNELS:
        raise ValueError(f"Unknown notification channel: {channel}")
    current = get_subscription(user_email, notification_type)
    channels = set(current.get("notification_channels") if current else [])
    if enabled:
        channels.add(channel)
    else:
        channels.discard(channel)
    return upsert_subscription(user_email, notification_type, sorted(channels))


def _normalize_channels(channels: list[str]) -> list[str]:
    normalized = {channel.strip().lower() for channel in channels if channel}
    return sorted(channel for channel in normalized if channel in VALID_CHANNELS)
