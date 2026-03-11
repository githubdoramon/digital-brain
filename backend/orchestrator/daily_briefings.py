from __future__ import annotations

from datetime import date, datetime
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
    if not row:
        return None
    briefing = _serialize_briefing(row)
    briefing["news_items"] = list_daily_briefing_news_items(
        briefing_id=str(briefing.get("briefing_id") or "")
    )
    return briefing


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
    if not row:
        return None
    briefing = _serialize_briefing(row)
    briefing["news_items"] = list_daily_briefing_news_items(
        briefing_id=str(briefing.get("briefing_id") or "")
    )
    return briefing


def list_daily_briefing_news_items(*, briefing_id: str) -> list[dict[str, Any]]:
    if not briefing_id:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              briefing_item_id,
              briefing_id,
              user_email,
              briefing_date,
              timezone,
              cluster_id,
              title,
              url,
              source,
              source_domain,
              section,
              topic_label,
              rank,
              score,
              brief_summary,
              topic_matches,
              metadata,
              created_at
            FROM daily_briefing_news_items
            WHERE briefing_id = %s
            ORDER BY rank ASC
            """,
            (briefing_id,),
        )
        rows = cur.fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        briefing_date = item.get("briefing_date")
        created_at = item.get("created_at")
        if isinstance(briefing_date, date):
            item["briefing_date"] = briefing_date.isoformat()
        if isinstance(created_at, datetime):
            item["created_at"] = created_at.isoformat()
        items.append(item)
    return items


def replace_daily_briefing_news_items(
    *,
    briefing_id: str,
    user_email: str | None,
    briefing_date: date,
    timezone: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    resolved_user_email = (user_email or "default_user").strip() or "default_user"
    if not briefing_id:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM daily_briefing_news_items WHERE briefing_id = %s",
            (briefing_id,),
        )
        for idx, item in enumerate(items, start=1):
            briefing_item_id = str(item.get("briefing_item_id") or f"briefing_news:{uuid4().hex}")
            topic_matches = item.get("topic_matches")
            if not isinstance(topic_matches, list):
                topic_matches = []
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            cur.execute(
                """
                INSERT INTO daily_briefing_news_items (
                  briefing_item_id,
                  briefing_id,
                  user_email,
                  briefing_date,
                  timezone,
                  cluster_id,
                  title,
                  url,
                  source,
                  source_domain,
                  section,
                  topic_label,
                  rank,
                  score,
                  brief_summary,
                  topic_matches,
                  metadata
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    briefing_item_id,
                    briefing_id,
                    resolved_user_email,
                    briefing_date,
                    timezone,
                    item.get("cluster_id"),
                    str(item.get("title") or "Untitled").strip(),
                    item.get("url"),
                    str(item.get("source") or "unknown").strip(),
                    item.get("source_domain"),
                    str(item.get("section") or "general").strip(),
                    item.get("topic_label"),
                    int(item.get("rank") or idx),
                    item.get("score"),
                    item.get("brief_summary"),
                    topic_matches,
                    _to_json(metadata),
                ),
            )
        conn.commit()
    return list_daily_briefing_news_items(briefing_id=briefing_id)


def _serialize_briefing(row: dict[str, Any]) -> dict[str, Any]:
    briefing_date = row.get("briefing_date")
    created_at = row.get("created_at")
    updated_at = row.get("updated_at")
    briefing_date_value = briefing_date.isoformat() if isinstance(briefing_date, date) else None
    created_at_value = created_at.isoformat() if isinstance(created_at, datetime) else None
    updated_at_value = updated_at.isoformat() if isinstance(updated_at, datetime) else None
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


def _to_json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value)
