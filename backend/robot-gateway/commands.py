"""Command creation, MQTT publishing, and acknowledgement handling."""

from __future__ import annotations

import json
import uuid
from typing import Any

from db import get_conn
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


def create_command(
    robot_id: str,
    module_id: str,
    command_type: str,
    payload: dict[str, Any],
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create a command record in the database (status: pending)."""
    command_id = f"cmd_{uuid.uuid4().hex[:12]}"

    with get_conn() as conn, conn.cursor() as cur:
        # Verify robot + module exist
        cur.execute(
            "SELECT 1 FROM robot_modules WHERE robot_id = %s AND module_id = %s",
            (robot_id, module_id),
        )
        if not cur.fetchone():
            raise ValueError(f"Module {module_id} not found on robot {robot_id}")

        cur.execute(
            """
            INSERT INTO robot_commands
                (command_id, robot_id, module_id, command_type, payload, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING command_id, robot_id, module_id, command_type, payload,
                      status, created_at, sent_at, acked_at, error, created_by
            """,
            (command_id, robot_id, module_id, command_type, json.dumps(payload), created_by),
        )
        row = cur.fetchone()
        conn.commit()

    return dict(row)


def mark_command_sent(command_id: str) -> None:
    """Update command status to 'sent' with sent_at timestamp."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE robot_commands
            SET status = 'sent', sent_at = NOW()
            WHERE command_id = %s AND status = 'pending'
            """,
            (command_id,),
        )
        conn.commit()


def mark_command_failed(command_id: str, error: str) -> None:
    """Update command status to 'failed' with error message."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE robot_commands
            SET status = 'failed', error = %s
            WHERE command_id = %s AND status IN ('pending', 'sent')
            """,
            (error, command_id),
        )
        conn.commit()


async def handle_command_ack(robot_id: str, module_id: str | None, payload: dict[str, Any]) -> None:
    """MQTT handler: process command acknowledgement from robot."""
    command_id = payload.get("command_id")
    if not command_id:
        logger.warning(
            "[commands.ack] REJECTED robot_id=%s module_id=%s reason=missing_command_id payload=%s",
            robot_id, module_id, json.dumps(payload)[:200],
        )
        return

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE robot_commands
            SET status = 'acknowledged', acked_at = NOW()
            WHERE command_id = %s AND status = 'sent'
            """,
            (command_id,),
        )
        updated = cur.rowcount
        conn.commit()

    if updated == 0:
        logger.warning(
            "[commands.ack] REJECTED command_id=%s robot_id=%s module_id=%s "
            "reason=command_not_found_or_not_sent",
            command_id, robot_id, module_id,
        )
    else:
        logger.info(
            "[commands.ack] ACCEPTED command_id=%s robot_id=%s module_id=%s",
            command_id, robot_id, module_id,
        )


def get_command(command_id: str) -> dict[str, Any] | None:
    """Fetch a single command by ID."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT command_id, robot_id, module_id, command_type, payload,
                   status, created_at, sent_at, acked_at, error, created_by
            FROM robot_commands
            WHERE command_id = %s
            """,
            (command_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_commands(
    robot_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List commands for a robot, newest first."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT command_id, robot_id, module_id, command_type, payload,
                   status, created_at, sent_at, acked_at, error, created_by
            FROM robot_commands
            WHERE robot_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (robot_id, limit, offset),
        )
        return [dict(row) for row in cur.fetchall()]
