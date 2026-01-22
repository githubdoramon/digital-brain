from __future__ import annotations

from typing import Any

import requests

from db import get_conn

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_BATCH_SIZE = 100


def send_push_notification(title: str, message: str, tokens: list[str]) -> dict[str, Any]:
    if not tokens:
        return {"sent": 0, "success": 0, "errors": []}

    payloads = [
        {
            "to": token,
            "title": title,
            "body": message,
            "sound": "default",
        }
        for token in tokens
    ]

    errors: list[str] = []
    success = 0
    sent = 0

    for batch in _chunk(payloads, EXPO_BATCH_SIZE):
        response = requests.post(EXPO_PUSH_URL, json=batch, timeout=15)
        sent += len(batch)
        if response.status_code >= 400:
            errors.append(f"{response.status_code} {response.text}")
            continue

        try:
            data = response.json()
        except ValueError:
            errors.append("Invalid JSON response from Expo")
            continue

        results = data.get("data") or []
        for result in results:
            if result.get("status") == "ok":
                success += 1
            else:
                message = result.get("message") or "Unknown error"
                errors.append(str(message))

    return {
        "sent": sent,
        "success": success,
        "errors": errors,
    }


def fetch_push_tokens(user_email: str) -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT expo_push_token
            FROM user_devices
            WHERE user_email = %s
              AND expo_push_token IS NOT NULL
            """,
            (user_email,),
        )
        rows = cur.fetchall() or []
    return [row["expo_push_token"] for row in rows if row.get("expo_push_token")]


def has_push_device(user_email: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM user_devices
            WHERE user_email = %s
              AND expo_push_token IS NOT NULL
            LIMIT 1
            """,
            (user_email,),
        )
        return cur.fetchone() is not None


def _chunk(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
