from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from db import get_conn


class AsyncJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def get_job(
    *,
    job_type: str,
    user_email: str,
    dedupe_key: str,
) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                job_id,
                job_type,
                user_email,
                dedupe_key,
                status,
                status_message,
                payload,
                result,
                error,
                requested_at,
                started_at,
                finished_at,
                created_at,
                updated_at
            FROM async_jobs
            WHERE job_type = %s
              AND user_email = %s
              AND dedupe_key = %s
            LIMIT 1
            """,
            (job_type, user_email, dedupe_key),
        )
        row = cur.fetchone()
    return _serialize_row(row) if row else None


def enqueue_job(
    *,
    job_type: str,
    user_email: str,
    dedupe_key: str,
    payload: dict[str, Any] | None = None,
    status_message: str | None = None,
) -> dict[str, Any]:
    """Create a pending job unless one is already pending/running."""
    payload_json = _to_json(payload or {})
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                job_id,
                status,
                result,
                error
            FROM async_jobs
            WHERE job_type = %s
              AND user_email = %s
              AND dedupe_key = %s
            FOR UPDATE
            """,
            (job_type, user_email, dedupe_key),
        )
        existing_raw = cur.fetchone()
        existing = dict(existing_raw) if existing_raw else None

        if existing and str(existing.get("status") or "") in {
            AsyncJobStatus.PENDING.value,
            AsyncJobStatus.RUNNING.value,
        }:
            conn.commit()
            return {
                "job_id": existing.get("job_id"),
                "status": existing.get("status"),
                "result": existing.get("result"),
                "error": existing.get("error"),
                "should_schedule": False,
                "created": False,
            }

        job_id = f"async_job:{uuid4().hex}"
        if existing:
            cur.execute(
                """
                UPDATE async_jobs
                SET
                    job_id = %s,
                    status = %s,
                    status_message = %s,
                    payload = %s::jsonb,
                    result = NULL,
                    error = NULL,
                    requested_at = NOW(),
                    started_at = NULL,
                    finished_at = NULL,
                    updated_at = NOW()
                WHERE job_type = %s
                  AND user_email = %s
                  AND dedupe_key = %s
                RETURNING job_id, status
                """,
                (
                    job_id,
                    AsyncJobStatus.PENDING.value,
                    status_message,
                    payload_json,
                    job_type,
                    user_email,
                    dedupe_key,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO async_jobs (
                    job_id,
                    job_type,
                    user_email,
                    dedupe_key,
                    status,
                    status_message,
                    payload,
                    requested_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW())
                RETURNING job_id, status
                """,
                (
                    job_id,
                    job_type,
                    user_email,
                    dedupe_key,
                    AsyncJobStatus.PENDING.value,
                    status_message,
                    payload_json,
                ),
            )

        row_raw = cur.fetchone()
        row = dict(row_raw) if row_raw else {}
        conn.commit()

    return {
        "job_id": row.get("job_id") or job_id,
        "status": row.get("status") or AsyncJobStatus.PENDING.value,
        "should_schedule": True,
        "created": True,
    }


def mark_running(job_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE async_jobs
            SET
                status = %s,
                status_message = %s,
                started_at = NOW(),
                updated_at = NOW()
            WHERE job_id = %s
              AND status = %s
            """,
            (
                AsyncJobStatus.RUNNING.value,
                "Running",
                job_id,
                AsyncJobStatus.PENDING.value,
            ),
        )
        updated = cur.rowcount > 0
        conn.commit()
    return updated


def mark_succeeded(
    job_id: str,
    *,
    result: dict[str, Any] | None = None,
    status_message: str = "Completed",
) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE async_jobs
            SET
                status = %s,
                status_message = %s,
                result = %s::jsonb,
                error = NULL,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE job_id = %s
            """,
            (
                AsyncJobStatus.SUCCEEDED.value,
                status_message,
                _to_json(result or {}),
                job_id,
            ),
        )
        conn.commit()


def mark_failed(
    job_id: str,
    *,
    error: str,
    status_message: str = "Failed",
) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE async_jobs
            SET
                status = %s,
                status_message = %s,
                error = %s,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE job_id = %s
            """,
            (
                AsyncJobStatus.FAILED.value,
                status_message,
                error,
                job_id,
            ),
        )
        conn.commit()


def _serialize_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in (
        "requested_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ):
        value = data.get(key)
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return data


def _to_json(value: dict[str, Any]) -> str:
    return json.dumps(value)
