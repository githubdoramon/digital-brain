"""Telemetry ingestion from MQTT and query logic."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import psycopg.errors

from db import get_conn
from observability.logger import get_runtime_logger
from schemas import TelemetryPayload

logger = get_runtime_logger(__name__)


async def handle_telemetry(robot_id: str, module_id: str | None, payload: dict[str, Any]) -> None:
    """MQTT handler: persist a telemetry message and update last_seen."""
    if not module_id:
        logger.warning("[telemetry] Received telemetry without module_id for robot %s", robot_id)
        return

    try:
        msg = TelemetryPayload(**payload)
    except Exception:
        logger.warning(
            "[telemetry] Invalid telemetry payload from %s/%s: %s",
            robot_id, module_id, json.dumps(payload)[:200],
        )
        return

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO robot_telemetry (robot_id, module_id, measured_at, payload, payload_type)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (robot_id, module_id, msg.measured_at, json.dumps(msg.data), msg.payload_type),
            )
            # Update last_seen on robot and module
            cur.execute(
                "UPDATE robots SET last_seen_at = NOW(), updated_at = NOW() WHERE robot_id = %s",
                (robot_id,),
            )
            cur.execute(
                """
                UPDATE robot_modules SET last_seen_at = NOW(), updated_at = NOW()
                WHERE robot_id = %s AND module_id = %s
                """,
                (robot_id, module_id),
            )
            conn.commit()
    except psycopg.errors.ForeignKeyViolation:
        logger.warning(
            "[telemetry] Dropped telemetry from unregistered robot/module %s/%s",
            robot_id, module_id,
        )
        return

    logger.debug("[telemetry] Stored %s from %s/%s", msg.payload_type, robot_id, module_id)


async def handle_status(robot_id: str, module_id: str | None, payload: dict[str, Any]) -> None:
    """MQTT handler: update robot or module status."""
    new_status = payload.get("status")
    if not new_status:
        return

    with get_conn() as conn, conn.cursor() as cur:
        if module_id:
            cur.execute(
                """
                UPDATE robot_modules
                SET status = %s, last_seen_at = NOW(), updated_at = NOW()
                WHERE robot_id = %s AND module_id = %s
                """,
                (new_status, robot_id, module_id),
            )
        else:
            cur.execute(
                """
                UPDATE robots
                SET status = %s, last_seen_at = NOW(), updated_at = NOW()
                WHERE robot_id = %s
                """,
                (new_status, robot_id),
            )
        updated = cur.rowcount
        conn.commit()

    if updated == 0:
        logger.warning(
            "[telemetry] Status update for unregistered %s/%s ignored",
            robot_id, module_id or "(robot)",
        )
    else:
        logger.info("[telemetry] Status update: %s/%s → %s", robot_id, module_id or "(robot)", new_status)


def query_telemetry(
    robot_id: str,
    module_id: str | None = None,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    payload_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query stored telemetry with optional filters."""
    conditions = ["robot_id = %s"]
    params: list[Any] = [robot_id]

    if module_id:
        conditions.append("module_id = %s")
        params.append(module_id)
    if since:
        conditions.append("measured_at >= %s")
        params.append(since)
    if until:
        conditions.append("measured_at <= %s")
        params.append(until)
    if payload_type:
        conditions.append("payload_type = %s")
        params.append(payload_type)

    where = " AND ".join(conditions)
    params.extend([limit, offset])

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, robot_id, module_id, measured_at, received_at, payload, payload_type
            FROM robot_telemetry
            WHERE {where}
            ORDER BY measured_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]


def get_latest_telemetry(robot_id: str, module_id: str) -> dict[str, Any] | None:
    """Get the most recent telemetry reading for a specific module."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, robot_id, module_id, measured_at, received_at, payload, payload_type
            FROM robot_telemetry
            WHERE robot_id = %s AND module_id = %s
            ORDER BY measured_at DESC
            LIMIT 1
            """,
            (robot_id, module_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
