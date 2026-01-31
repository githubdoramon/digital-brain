from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from db import get_conn
from schemas import TodoIn

__all__ = [
    "ingest_todo",
    "list_todos",
    "list_event_todos",
    "list_unlinked_relevant_todos",
    "update_todo_status",
    "get_todo",
    "delete_todo",
]

PENDING_STATUSES = ("", "pending", "open", "in_progress", "todo")
COMPLETED_STATUSES = ("completed", "complete", "done", "accomplished", "closed")


def ingest_todo(todo: TodoIn) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO todos (
              todo_id,
              description,
              status,
              due_date
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (todo_id) DO UPDATE
              SET description = EXCLUDED.description,
                  status = EXCLUDED.status,
                  due_date = EXCLUDED.due_date,
                  updated_at = NOW()
            """,
            (
                todo.todo_id,
                todo.description,
                (todo.status or "pending").strip() or "pending",
                todo.due_date,
            ),
        )

        _replace_todo_links(
            cur,
            todo.todo_id,
            todo.contact_ids or [],
            todo.event_ids or [],
            todo.place_ids or [],
        )

        conn.commit()


def list_todos(*, open_only: bool = False, order: str | None = None) -> list[dict[str, Any]]:
    where_clause = ""
    params: list[Any] = []
    if open_only:
        where_clause = "WHERE lower(coalesce(status, '')) = ANY(%s)"
        params.append(list(PENDING_STATUSES))

    if order == "due":
        order_clause = (
            "ORDER BY "
            "CASE "
            "WHEN due_date IS NULL THEN 3 "
            "WHEN due_date < CURRENT_DATE THEN 0 "
            "WHEN due_date = CURRENT_DATE THEN 1 "
            "ELSE 2 END, "
            "due_date ASC NULLS LAST, "
            "created_at DESC"
        )
    else:
        order_clause = (
            "ORDER BY "
            "CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, "
            "due_date ASC NULLS LAST, "
            "created_at DESC"
        )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT todo_id, description, status, due_date, created_at, updated_at
            FROM todos
            {where_clause}
            {order_clause}
            """,
            tuple(params),
        )
        rows = [dict(row) for row in cur.fetchall()]

        todo_ids: list[str] = [str(row.get("todo_id")) for row in rows if row.get("todo_id")]
        link_map = _collect_todo_links(conn, todo_ids)

        todos: list[dict[str, Any]] = []
        for row in rows:
            todo_id = row.get("todo_id")
            if not todo_id:
                continue
            links = link_map.get(todo_id, {})
            todos.append(
                {
                    "todo_id": todo_id,
                    "description": row.get("description"),
                    "status": row.get("status"),
                    "due_date": row["due_date"].isoformat() if row.get("due_date") else None,
                    "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                    "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
                    "contacts": links.get("contacts", []),
                    "events": links.get("events", []),
                    "places": links.get("places", []),
                }
            )
        return todos


def list_event_todos(event_id: str, days: int = 14) -> list[dict[str, Any]]:
    if not event_id:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              t.todo_id,
              t.description,
              t.status,
              t.due_date,
              t.created_at,
              t.updated_at
            FROM todos AS t
            INNER JOIN todo_events AS te ON te.todo_id = t.todo_id
            WHERE te.event_id = %s
              AND (
                lower(coalesce(t.status, '')) = ANY(%s)
                OR (
                  lower(coalesce(t.status, '')) = ANY(%s)
                  AND (t.updated_at >= %s OR (t.updated_at IS NULL AND t.created_at >= %s))
                )
              )
            ORDER BY
              CASE WHEN t.due_date IS NULL THEN 1 ELSE 0 END,
              t.due_date ASC NULLS LAST,
              t.created_at DESC
            """,
            (event_id, list(PENDING_STATUSES), list(COMPLETED_STATUSES), cutoff, cutoff),
        )
        rows = [dict(row) for row in cur.fetchall()]
    return _serialize_todo_rows(rows)


def list_unlinked_relevant_todos(days: int = 14) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              t.todo_id,
              t.description,
              t.status,
              t.due_date,
              t.created_at,
              t.updated_at
            FROM todos AS t
            WHERE NOT EXISTS (
              SELECT 1
              FROM todo_events AS te
              WHERE te.todo_id = t.todo_id
            )
              AND (
                lower(coalesce(t.status, '')) = ANY(%s)
                OR (
                  lower(coalesce(t.status, '')) = ANY(%s)
                  AND (t.updated_at >= %s OR (t.updated_at IS NULL AND t.created_at >= %s))
                )
              )
            ORDER BY
              CASE WHEN t.due_date IS NULL THEN 1 ELSE 0 END,
              t.due_date ASC NULLS LAST,
              t.created_at DESC
            """,
            (list(PENDING_STATUSES), list(COMPLETED_STATUSES), cutoff, cutoff),
        )
        rows = [dict(row) for row in cur.fetchall()]
    return _serialize_todo_rows(rows)


def update_todo_status(todo_id: str, status: str) -> bool:
    if not todo_id:
        return False
    cleaned_status = (status or "").strip() or "pending"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE todos
            SET status = %s,
                updated_at = NOW()
            WHERE todo_id = %s
            """,
            (cleaned_status, todo_id),
        )
        updated = cur.rowcount > 0
        conn.commit()
    return updated


def get_todo(todo_id: str) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT todo_id, description, status, due_date, created_at, updated_at
            FROM todos
            WHERE todo_id = %s
            """,
            (todo_id,),
        )
        raw_row = cur.fetchone()
        row = dict(raw_row) if raw_row else None
        if not row:
            return None

        link_map = _collect_todo_links(conn, [todo_id])
        links = link_map.get(todo_id, {})

        return {
            "todo_id": row.get("todo_id"),
            "description": row.get("description"),
            "status": row.get("status"),
            "due_date": row["due_date"].isoformat() if row.get("due_date") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "contacts": links.get("contacts", []),
            "events": links.get("events", []),
            "places": links.get("places", []),
        }


def delete_todo(todo_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM todo_contacts WHERE todo_id = %s", (todo_id,))
        cur.execute("DELETE FROM todo_events WHERE todo_id = %s", (todo_id,))
        cur.execute("DELETE FROM todo_places WHERE todo_id = %s", (todo_id,))
        cur.execute(
            """
            DELETE FROM todos
            WHERE todo_id = %s
            """,
            (todo_id,),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted


def _replace_todo_links(
    cur,
    todo_id: str,
    contact_ids: Sequence[str],
    event_ids: Sequence[str],
    place_ids: Sequence[str],
) -> None:
    cur.execute("DELETE FROM todo_contacts WHERE todo_id = %s", (todo_id,))
    cur.execute("DELETE FROM todo_events WHERE todo_id = %s", (todo_id,))
    cur.execute("DELETE FROM todo_places WHERE todo_id = %s", (todo_id,))

    for contact_id in contact_ids:
        if contact_id:
            cur.execute(
                "INSERT INTO todo_contacts (todo_id, contact_id) VALUES (%s, %s)",
                (todo_id, contact_id),
            )

    for event_id in event_ids:
        if event_id:
            cur.execute(
                "INSERT INTO todo_events (todo_id, event_id) VALUES (%s, %s)",
                (todo_id, event_id),
            )

    for place_id in place_ids:
        if place_id:
            cur.execute(
                "INSERT INTO todo_places (todo_id, place_id) VALUES (%s, %s)",
                (todo_id, place_id),
            )


def _collect_todo_links(conn, todo_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not todo_ids:
        return {}

    link_map: dict[str, dict[str, Any]] = {
        todo_id: {"contacts": [], "events": [], "places": []} for todo_id in todo_ids
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT todo_id, contact_id
            FROM todo_contacts
            WHERE todo_id = ANY(%s)
            """,
            (list(todo_ids),),
        )
        for row in cur.fetchall():
            link_map.setdefault(row["todo_id"], {"contacts": [], "events": [], "places": []})[
                "contacts"
            ].append(row["contact_id"])

        cur.execute(
            """
            SELECT
              te.todo_id,
              te.event_id,
              ev.title,
              ev.start_date
            FROM todo_events AS te
            LEFT JOIN events AS ev ON ev.id = te.event_id
            WHERE te.todo_id = ANY(%s)
            ORDER BY ev.start_date NULLS LAST, te.event_id
            """,
            (list(todo_ids),),
        )
        for row in cur.fetchall():
            events = link_map.setdefault(
                row["todo_id"], {"contacts": [], "events": [], "places": []}
            )["events"]
            event_id = row["event_id"]
            event_detail: dict[str, Any] = {"id": event_id}
            title = row.get("title") or None
            event_detail["title"] = title or event_id
            start_date = row.get("start_date")
            if start_date:
                event_detail["start_date"] = start_date.isoformat()
            events.append(event_detail)

        cur.execute(
            """
            SELECT todo_id, place_id
            FROM todo_places
            WHERE todo_id = ANY(%s)
            """,
            (list(todo_ids),),
        )
        for row in cur.fetchall():
            link_map.setdefault(row["todo_id"], {"contacts": [], "events": [], "places": []})[
                "places"
            ].append(row["place_id"])

    return link_map


def _serialize_todo_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    todos: list[dict[str, Any]] = []
    for row in rows:
        todos.append(
            {
                "todo_id": row.get("todo_id"),
                "description": row.get("description"),
                "status": row.get("status"),
                "due_date": row["due_date"].isoformat() if row.get("due_date") else None,
                "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            }
        )
    return todos
