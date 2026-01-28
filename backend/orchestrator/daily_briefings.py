from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from db import get_conn


def upsert_daily_briefing(
    *,
    user_email: str | None,
    briefing_date: date,
    timezone: str,
    markdown: str,
    summary: str,
    event_count: int,
    todo_count: int,
) -> dict[str, Any]:
    briefing_id = f"briefing:{uuid4().hex}"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO daily_briefings (
              briefing_id,
              user_email,
              briefing_date,
              timezone,
              markdown,
              summary,
              event_count,
              todo_count
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_email, briefing_date, timezone) DO UPDATE
              SET markdown = EXCLUDED.markdown,
                  summary = EXCLUDED.summary,
                  event_count = EXCLUDED.event_count,
                  todo_count = EXCLUDED.todo_count,
                  updated_at = NOW()
            RETURNING briefing_id
            """,
            (
                briefing_id,
                user_email,
                briefing_date,
                timezone,
                markdown,
                summary,
                event_count,
                todo_count,
            ),
        )
        raw_row = cur.fetchone()
        conn.commit()

    row = _row_to_dict(raw_row)
    stored_id = row.get("briefing_id") if row else briefing_id
    return {
        "briefing_id": stored_id,
        "user_email": user_email,
        "briefing_date": briefing_date.isoformat(),
        "timezone": timezone,
        "markdown": markdown,
        "summary": summary,
        "event_count": event_count,
        "todo_count": todo_count,
    }


def get_daily_briefing(
    *,
    user_email: str | None,
    briefing_date: date,
    timezone: str | None = None,
) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        if timezone:
            cur.execute(
                """
                SELECT
                  briefing_id,
                  user_email,
                  briefing_date,
                  timezone,
                  markdown,
                  summary,
                  event_count,
                  todo_count,
                  created_at,
                  updated_at
                FROM daily_briefings
                WHERE user_email = %s
                  AND briefing_date = %s
                  AND timezone = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (user_email, briefing_date, timezone),
            )
        else:
            cur.execute(
                """
                SELECT
                  briefing_id,
                  user_email,
                  briefing_date,
                  timezone,
                  markdown,
                  summary,
                  event_count,
                  todo_count,
                  created_at,
                  updated_at
                FROM daily_briefings
                WHERE user_email = %s
                  AND briefing_date = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (user_email, briefing_date),
            )
        raw_row = cur.fetchone()
    row = _row_to_dict(raw_row)
    return _serialize_briefing(row) if row else None


def get_latest_daily_briefing(*, user_email: str | None) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              briefing_id,
              user_email,
              briefing_date,
              timezone,
              markdown,
              summary,
              event_count,
              todo_count,
              created_at,
              updated_at
            FROM daily_briefings
            WHERE user_email = %s
            ORDER BY briefing_date DESC, updated_at DESC
            LIMIT 1
            """,
            (user_email,),
        )
        raw_row = cur.fetchone()
    row = _row_to_dict(raw_row)
    return _serialize_briefing(row) if row else None


def _serialize_briefing(row: dict[str, Any]) -> dict[str, Any]:
    briefing_date = row.get("briefing_date")
    created_at = row.get("created_at")
    updated_at = row.get("updated_at")
    briefing_date_value = briefing_date.isoformat() if isinstance(briefing_date, date) else None
    created_at_value = created_at.isoformat() if hasattr(created_at, "isoformat") else None
    updated_at_value = updated_at.isoformat() if hasattr(updated_at, "isoformat") else None
    return {
        "briefing_id": row.get("briefing_id"),
        "user_email": row.get("user_email"),
        "briefing_date": briefing_date_value,
        "timezone": row.get("timezone"),
        "markdown": row.get("markdown"),
        "summary": row.get("summary"),
        "event_count": row.get("event_count") or 0,
        "todo_count": row.get("todo_count") or 0,
        "created_at": created_at_value,
        "updated_at": updated_at_value,
    }


def _row_to_dict(row: Any | None) -> dict[str, Any] | None:
    if not row:
        return None
    return dict(row)
