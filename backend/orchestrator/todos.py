from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from db import get_conn
from schemas import TodoIn


__all__ = [
    "ingest_todo",
    "list_todos",
    "get_todo",
    "delete_todo",
]


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


def list_todos() -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT todo_id, description, status, due_date, created_at, updated_at
            FROM todos
            ORDER BY
              CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
              due_date ASC NULLS LAST,
              created_at DESC
            """
        )
        rows = cur.fetchall()

        todo_ids = [row["todo_id"] for row in rows]
        link_map = _collect_todo_links(conn, todo_ids)

        todos: List[Dict[str, Any]] = []
        for row in rows:
            todo_id = row["todo_id"]
            links = link_map.get(todo_id, {})
            todos.append(
                {
                    "todo_id": todo_id,
                    "description": row["description"],
                    "status": row["status"],
                    "due_date": row["due_date"].isoformat() if row["due_date"] else None,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    "contacts": links.get("contacts", []),
                    "events": links.get("events", []),
                    "places": links.get("places", []),
                }
            )
        return todos


def get_todo(todo_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT todo_id, description, status, due_date, created_at, updated_at
            FROM todos
            WHERE todo_id = %s
            """,
            (todo_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        link_map = _collect_todo_links(conn, [todo_id])
        links = link_map.get(todo_id, {})

        return {
            "todo_id": row["todo_id"],
            "description": row["description"],
            "status": row["status"],
            "due_date": row["due_date"].isoformat() if row["due_date"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
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


def _collect_todo_links(conn, todo_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    if not todo_ids:
        return {}

    link_map: Dict[str, Dict[str, Any]] = {
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
            events = link_map.setdefault(row["todo_id"], {"contacts": [], "events": [], "places": []})[
                "events"
            ]
            event_id = row["event_id"]
            event_detail: Dict[str, Any] = {"id": event_id}
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
