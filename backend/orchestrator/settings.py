from __future__ import annotations

from db import get_conn


def get_user_settings(user_email: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH upsert AS (
                INSERT INTO user_settings (user_email)
                VALUES (%s)
                ON CONFLICT (user_email) DO NOTHING
                RETURNING user_email,
                          push_notifications_enabled,
                          created_at,
                          updated_at
            )
            SELECT * FROM upsert
            UNION ALL
            SELECT user_email,
                   push_notifications_enabled,
                   created_at,
                   updated_at
            FROM user_settings
            WHERE user_email = %s
            LIMIT 1
            """,
            (user_email, user_email),
        )
        row = cur.fetchone()
    return row or {
        "user_email": user_email,
        "push_notifications_enabled": False,
        "created_at": None,
        "updated_at": None,
    }


def update_push_notifications(user_email: str, enabled: bool) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_settings (user_email, push_notifications_enabled)
            VALUES (%s, %s)
            ON CONFLICT (user_email)
            DO UPDATE SET
              push_notifications_enabled = EXCLUDED.push_notifications_enabled,
              updated_at = NOW()
            RETURNING user_email,
                      push_notifications_enabled,
                      created_at,
                      updated_at
            """,
            (user_email, enabled),
        )
        row = cur.fetchone()
    return row
