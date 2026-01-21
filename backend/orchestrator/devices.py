from __future__ import annotations

from uuid import uuid4

from db import get_conn


def register_device(
    user_email: str,
    expo_push_token: str,
    platform: str,
    device_name: str | None,
    app_version: str | None,
    os_version: str | None,
) -> dict:
    print("register_device", user_email, expo_push_token, platform, device_name, app_version, os_version)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_devices (
              device_id,
              user_email,
              expo_push_token,
              platform,
              device_name,
              app_version,
              os_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (expo_push_token)
            DO UPDATE SET
              user_email = EXCLUDED.user_email,
              platform = EXCLUDED.platform,
              device_name = EXCLUDED.device_name,
              app_version = EXCLUDED.app_version,
              os_version = EXCLUDED.os_version,
              updated_at = NOW(),
              last_seen_at = NOW()
            RETURNING device_id,
                      user_email,
                      expo_push_token,
                      platform,
                      device_name,
                      app_version,
                      os_version,
                      created_at,
                      updated_at,
                      last_seen_at
            """,
            (
                f"device:{uuid4()}",
                user_email,
                expo_push_token,
                platform,
                device_name,
                app_version,
                os_version,
            ),
        )
        row = cur.fetchone()
    return row


def unregister_device(user_email: str, expo_push_token: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM user_devices
            WHERE user_email = %s AND expo_push_token = %s
            """,
            (user_email, expo_push_token),
        )
        return cur.rowcount > 0
