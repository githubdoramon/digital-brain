from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Json

from db import get_conn
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

_DEFAULT_TITLE_PREFIX = "Untitled conversation"


def _generate_default_title() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return f"{_DEFAULT_TITLE_PREFIX} - {timestamp} UTC"


def is_default_title(title: str | None) -> bool:
    if not title:
        return True
    return title.startswith(_DEFAULT_TITLE_PREFIX)


def _generate_thread_id() -> str:
    return f"thread_{uuid4().hex}"


def _normalize_title_candidate(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return None
    # Limit title length to 80 characters similar to ChatGPT behaviour
    if len(cleaned) > 80:
        cleaned = cleaned[:77] + "..."
    return cleaned


def ensure_thread(
    thread_id: str | None,
    user_email: str,
    title: str | None = None,
) -> dict[str, Any]:
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


def list_threads(user_email: str) -> list[dict[str, Any]]:
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


def get_thread_with_messages(thread_id: str, user_email: str) -> dict[str, Any] | None:
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


def get_conversation_history(thread_id: str, user_email: str) -> list[dict[str, str]]:
    thread = get_thread_with_messages(thread_id, user_email)
    if not thread:
        return []
    history: list[dict[str, str]] = []
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
    user_metadata: dict[str, Any] | None = None,
    assistant_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

        if title_candidate:
            cur.execute(
                """
                UPDATE conversation_threads
                SET
                    updated_at = NOW(),
                    title = CASE
                        WHEN (title IS NULL OR btrim(title) = '') THEN %s
                        ELSE title
                    END
                WHERE id = %s
                """,
                (title_candidate, thread_id),
            )
        else:
            cur.execute(
                """
                UPDATE conversation_threads
                SET updated_at = NOW()
                WHERE id = %s
                """,
                (thread_id,),
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


def set_message_metadata_field(
    message_id: int,
    field: str,
    value: Any,
) -> bool:
    """Merge a single key into a message's metadata JSONB column.

    Returns True if the row was updated, False otherwise.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE conversation_messages
            SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
            WHERE message_id = %s
            RETURNING 1
            """,
            (Json({field: value}), message_id),
        )
        updated = cur.fetchone() is not None
        if updated:
            conn.commit()
        else:
            conn.rollback()
        return updated


def find_message_id_by_metadata_preview(preview_id: str) -> int | None:
    """Find the assistant message whose metadata contains a given preview_id.

    Searches ``metadata -> 'command_result' -> 'preview_id'`` in
    ``conversation_messages``. Returns the ``message_id`` or ``None``.
    """
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT message_id
            FROM conversation_messages
            WHERE role = 'assistant'
              AND metadata -> 'command_result' ->> 'preview_id' = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (preview_id,),
        )
        row = cur.fetchone()
        return row["message_id"] if row else None


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


def update_thread_title(thread_id: str, user_email: str, title: str) -> dict[str, Any] | None:
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


# ---------------------------------------------------------------------------
# Main Session (Quick Chat mode)
# ---------------------------------------------------------------------------

DEFAULT_IDLE_MINUTES = 30
RESET_TRIGGERS = ["/new"]


def parse_session_command(message: str) -> tuple[bool, str]:
    """
    Parse message for session commands like /new.
    Returns (is_reset, stripped_body).

    DEPRECATED: Use commands.parser.parse_command instead.
    This function is maintained for backward compatibility with /new command.
    """
    body = message.strip()
    body_lower = body.lower()
    for trigger in RESET_TRIGGERS:
        if body_lower == trigger:
            return (True, "")
        if body_lower.startswith(f"{trigger} "):
            return (True, body[len(trigger) :].strip())
    return (False, body)


def get_main_session(user_email: str) -> dict[str, Any] | None:
    """Get main session metadata for user, including the thread if it exists."""
    if not user_email:
        return None

    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        # Debug: verify search path before query
        cur.execute("SHOW search_path")
        sp = cur.fetchone()
        logger.debug("[conversations] get_main_session search_path=%s", sp)

        # Debug: check if table exists
        cur.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_name = 'main_sessions'
        """)
        tables = cur.fetchall()
        logger.debug("[conversations] main_sessions table locations: %s", tables)

        cur.execute(
            """
            SELECT
                ms.user_email,
                ms.current_thread_id,
                ms.updated_at,
                t.id AS thread_id,
                t.title AS thread_title,
                t.created_at AS thread_created_at
            FROM main_sessions ms
            LEFT JOIN conversation_threads t ON t.id = ms.current_thread_id
            WHERE ms.user_email = %s
            """,
            (user_email,),
        )
        return cur.fetchone()


def _upsert_main_session(user_email: str, thread_id: str) -> None:
    """Create or update main session pointer."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO main_sessions (user_email, current_thread_id, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_email) DO UPDATE
            SET current_thread_id = EXCLUDED.current_thread_id,
                updated_at = NOW()
            """,
            (user_email, thread_id),
        )
        conn.commit()


def set_main_session_thread(user_email: str, thread_id: str) -> None:
    """Set the main session thread explicitly."""
    if not user_email or not thread_id:
        return
    _upsert_main_session(user_email, thread_id)


def _touch_main_session(user_email: str) -> None:
    """Update the main session timestamp without changing the thread."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE main_sessions
            SET updated_at = NOW()
            WHERE user_email = %s
            """,
            (user_email,),
        )
        conn.commit()


def _create_thread_for_main_session(user_email: str) -> dict[str, Any]:
    """Create a new thread for the main session with a Quick Chat title."""
    new_thread_id = _generate_thread_id()
    title = f"Quick Chat - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO conversation_threads (id, user_email, title)
            VALUES (%s, %s, %s)
            RETURNING id, user_email, title, created_at, updated_at
            """,
            (new_thread_id, user_email, title),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def resolve_main_session(
    user_email: str,
    message: str,
    idle_minutes: int = DEFAULT_IDLE_MINUTES,
) -> tuple[dict[str, Any], bool, str]:
    """
    Resolve main session for user.

    Returns: (thread, is_new_session, stripped_message)
    - thread: The conversation thread dict
    - is_new_session: True if a new session was created (timeout or /new command)
    - stripped_message: The message with any command prefix removed
    """
    if not user_email:
        raise ValueError("user_email is required")

    is_reset, stripped_body = parse_session_command(message)
    main_session = get_main_session(user_email)

    # First time user or no main session exists
    if main_session is None:
        thread = _create_thread_for_main_session(user_email)
        _upsert_main_session(user_email, thread["id"])
        return (thread, True, stripped_body)

    # Check if the current thread still exists (may have been deleted)
    current_thread_id = main_session.get("current_thread_id")
    thread_exists = main_session.get("thread_id") is not None

    if not thread_exists or not current_thread_id:
        # Thread was deleted, create a new one
        thread = _create_thread_for_main_session(user_email)
        _upsert_main_session(user_email, thread["id"])
        return (thread, True, stripped_body)

    # Check idle timeout
    updated_at = main_session["updated_at"]
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - updated_at
    is_timed_out = elapsed.total_seconds() > (idle_minutes * 60)

    if is_reset or is_timed_out:
        # Create new thread, update pointer
        thread = _create_thread_for_main_session(user_email)
        _upsert_main_session(user_email, thread["id"])
        return (thread, True, stripped_body)

    # Use existing thread, update timestamp
    _touch_main_session(user_email)

    # Get the full thread data
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, user_email, title, created_at, updated_at
            FROM conversation_threads
            WHERE id = %s AND user_email = %s
            """,
            (current_thread_id, user_email),
        )
        thread = cur.fetchone()

    if not thread:
        # Shouldn't happen, but handle gracefully
        thread = _create_thread_for_main_session(user_email)
        _upsert_main_session(user_email, thread["id"])
        return (thread, True, stripped_body)

    return (thread, False, stripped_body)
