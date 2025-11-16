from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Json

from db import get_conn

_DEFAULT_TITLE_PREFIX = "Untitled conversation"


def _generate_default_title() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return f"{_DEFAULT_TITLE_PREFIX} - {timestamp} UTC"


def is_default_title(title: Optional[str]) -> bool:
    if not title:
        return True
    return title.startswith(_DEFAULT_TITLE_PREFIX)


def _generate_thread_id() -> str:
    return f"thread_{uuid4().hex}"


def _normalize_title_candidate(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return None
    # Limit title length to 80 characters similar to ChatGPT behaviour
    if len(cleaned) > 80:
        cleaned = cleaned[:77] + "..."
    return cleaned


def ensure_thread(thread_id: Optional[str], user_email: str, title: Optional[str] = None) -> Dict[str, Any]:
    """
    Ensure a thread exists for the given user. If thread_id is None, a new thread is created.
    Returns the thread row as a dict.
    """
    if not user_email:
        raise ValueError("user_email is required to ensure a thread")

    if thread_id:
        with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, user_email, title, created_at, updated_at
                FROM conversation_threads
                WHERE id = %s
                """,
                (thread_id,),
            )
            row = cur.fetchone()
            if not row:
                raise LookupError("Thread not found")
            if row["user_email"] != user_email:
                raise PermissionError("Thread does not belong to the authenticated user")
            return row

    new_thread_id = thread_id or _generate_thread_id()
    normalized_title = _normalize_title_candidate(title)
    if not normalized_title:
        normalized_title = _generate_default_title()

    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO conversation_threads (id, user_email, title)
            VALUES (%s, %s, %s)
            RETURNING id, user_email, title, created_at, updated_at
            """,
            (new_thread_id, user_email, normalized_title),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def list_threads(user_email: str) -> List[Dict[str, Any]]:
    if not user_email:
        return []
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                t.id,
                t.title,
                t.created_at,
                t.updated_at,
                (
                    SELECT LEFT(content, 160)
                    FROM conversation_messages m
                    WHERE m.thread_id = t.id
                    ORDER BY m.created_at DESC, m.message_id DESC
                    LIMIT 1
                ) AS last_message_preview
            FROM conversation_threads t
            WHERE t.user_email = %s
            ORDER BY t.updated_at DESC, t.created_at DESC
            """,
            (user_email,),
        )
        rows = cur.fetchall()
    return rows


def get_thread_with_messages(thread_id: str, user_email: str) -> Optional[Dict[str, Any]]:
    if not thread_id or not user_email:
        return None

    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, user_email, title, created_at, updated_at
            FROM conversation_threads
            WHERE id = %s AND user_email = %s
            """,
            (thread_id, user_email),
        )
        thread = cur.fetchone()
        if not thread:
            return None

        cur.execute(
            """
            SELECT
                message_id,
                role,
                content,
                metadata,
                created_at
            FROM conversation_messages
            WHERE thread_id = %s
            ORDER BY created_at ASC, message_id ASC
            """,
            (thread_id,),
        )
        messages = cur.fetchall()

    thread["messages"] = messages
    thread.pop("user_email", None)
    return thread


def get_conversation_history(thread_id: str, user_email: str) -> List[Dict[str, str]]:
    thread = get_thread_with_messages(thread_id, user_email)
    if not thread:
        return []
    history: List[Dict[str, str]] = []
    for message in thread["messages"]:
        role = message.get("role")
        content = message.get("content")
        if not role or not content:
            continue
        history.append({"role": role, "content": content})
    return history


def record_exchange(
    thread_id: str,
    user_email: str,
    user_message: str,
    assistant_message: str,
    user_metadata: Optional[Dict[str, Any]] = None,
    assistant_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not thread_id or not user_email:
        raise ValueError("thread_id and user_email are required")

    title_candidate = _normalize_title_candidate(user_message)

    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                id,
                title,
                (
                    SELECT COUNT(*)
                    FROM conversation_messages
                    WHERE thread_id = %s
                ) AS message_count
            FROM conversation_threads
            WHERE id = %s AND user_email = %s
            """,
            (thread_id, thread_id, user_email),
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError("Thread not found for user")

        current_title = row.get("title")
        message_count_before = row.get("message_count") or 0

        cur.execute(
            """
            INSERT INTO conversation_messages (thread_id, role, content, metadata)
            VALUES
                (%s, 'user', %s, %s),
                (%s, 'assistant', %s, %s)
            RETURNING message_id, role
            """,
            (
                thread_id,
                user_message,
                Json(user_metadata or {}),
                thread_id,
                assistant_message,
                Json(assistant_metadata or {}),
            ),
        )
        inserted = cur.fetchall()

        cur.execute(
            """
            UPDATE conversation_threads
            SET
                updated_at = NOW(),
                title = CASE
                    WHEN (title IS NULL OR btrim(title) = '') AND %s IS NOT NULL THEN %s
                    ELSE title
                END
            WHERE id = %s
            """,
            (title_candidate, title_candidate, thread_id),
        )

        conn.commit()

    user_id = assistant_id = -1
    for row in inserted:
        if row["role"] == "user":
            user_id = row["message_id"]
        elif row["role"] == "assistant":
            assistant_id = row["message_id"]
    return {
        "user_message_id": user_id,
        "assistant_message_id": assistant_id,
        "message_count_before": message_count_before,
        "previous_title": current_title,
    }


def delete_thread(thread_id: str, user_email: str) -> bool:
    if not thread_id or not user_email:
        return False

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM conversation_threads
            WHERE id = %s AND user_email = %s
            RETURNING 1
            """,
            (thread_id, user_email),
        )
        deleted = cur.fetchone() is not None
        if deleted:
            conn.commit()
        else:
            conn.rollback()
        return deleted


def update_thread_title(thread_id: str, user_email: str, title: str) -> Optional[Dict[str, Any]]:
    normalized = _normalize_title_candidate(title)
    if not normalized:
        return None

    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE conversation_threads
            SET title = %s, updated_at = NOW()
            WHERE id = %s AND user_email = %s
            RETURNING id, user_email, title, created_at, updated_at
            """,
            (normalized, thread_id, user_email),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return None
        conn.commit()
    return row

