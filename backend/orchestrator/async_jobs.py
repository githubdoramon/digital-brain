from __future__ import annotations

import json
from contextvars import ContextVar
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Any
from uuid import uuid4

import llm_admission
from db import get_conn
from job_leases import LEASE_SECONDS
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

_ACTIVE_WORKER = ContextVar("async_llm_worker", default=None)


def background_worker(lane, *, poll_seconds=0, check_claim=False):
    """Coordinate durable LLM jobs before claim and preserve retries on deferral."""

    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            with llm_admission.background_work(
                lane,
                poll_seconds=poll_seconds,
                before_request=check_current_job if check_claim else None,
            ):
                if not llm_admission.background_ready():
                    return False
                tracker = {"job": None}
                token = _ACTIVE_WORKER.set(tracker)
                try:
                    result = function(*args, **kwargs)
                    if exc := llm_admission.current_deferral():
                        raise exc
                    return result
                except llm_admission.BackgroundDeferred as exc:
                    logger.info(
                        "[async_jobs] llm_deferred lane=%s reason=%s delay_s=%d",
                        lane,
                        exc.reason,
                        exc.delay,
                    )
                    job = tracker["job"]
                    if job:
                        defer_job(job["job_id"], revision=job["revision"], delay=exc.delay)
                    return bool(job)
                finally:
                    _ACTIVE_WORKER.reset(token)

        return run

    return decorate


def defer_job(job_id, *, revision, delay):
    """Release only this claim; revisions protect newer queued work."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE async_jobs SET status='pending',status_message='Waiting to retry',
            attempts=GREATEST(0,attempts-1),next_run_at=NOW()+(%s * INTERVAL '1 second'),
            started_at=NULL,finished_at=NULL,error=NULL,updated_at=NOW()
            WHERE job_id=%s AND revision=%s AND status='running'""",
            (delay, job_id, revision),
        )
        conn.commit()


class AsyncJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
                revision,
                attempts,
                next_run_at,
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
    delay_seconds: int = 0,
    replace_existing: bool = False,
) -> dict[str, Any]:
    """Create a pending job.

    By default, pending/running jobs are left untouched. When replace_existing is set,
    the existing row is updated in place, its revision is incremented, and its delay
    restarts. Revision-aware completion calls then ignore superseded in-flight work.
    """
    payload_json = _to_json(payload or {})
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                job_id,
                status,
                revision,
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

        if (
            existing
            and not replace_existing
            and str(existing.get("status") or "")
            in {
                AsyncJobStatus.PENDING.value,
                AsyncJobStatus.RUNNING.value,
            }
        ):
            conn.commit()
            return {
                "job_id": existing.get("job_id"),
                "status": existing.get("status"),
                "revision": existing.get("revision"),
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
                    revision = revision + 1,
                    attempts = 0,
                    payload = %s::jsonb,
                    result = NULL,
                    error = NULL,
                    requested_at = NOW(),
                    next_run_at = NOW() + (%s * INTERVAL '1 second'),
                    started_at = NULL,
                    finished_at = NULL,
                    updated_at = NOW()
                WHERE job_type = %s
                  AND user_email = %s
                  AND dedupe_key = %s
                RETURNING job_id, status, revision, next_run_at
                """,
                (
                    job_id,
                    AsyncJobStatus.PENDING.value,
                    status_message,
                    payload_json,
                    delay_seconds,
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
                    next_run_at,
                    requested_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    NOW() + (%s * INTERVAL '1 second'),
                    NOW(),
                    NOW()
                )
                RETURNING job_id, status, revision, next_run_at
                """,
                (
                    job_id,
                    job_type,
                    user_email,
                    dedupe_key,
                    AsyncJobStatus.PENDING.value,
                    status_message,
                    payload_json,
                    delay_seconds,
                ),
            )

        row_raw = cur.fetchone()
        row = dict(row_raw) if row_raw else {}
        conn.commit()

    return {
        "job_id": row.get("job_id") or job_id,
        "status": row.get("status") or AsyncJobStatus.PENDING.value,
        "revision": row.get("revision"),
        "next_run_at": _serialize_datetime(row.get("next_run_at")),
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


def claim_due_job(*, job_type: str, newest_first: bool = False) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT job_id, job_type, user_email, dedupe_key, revision, attempts, payload
            FROM async_jobs
            WHERE job_type = %s
              AND status IN (%s, %s)
              AND next_run_at <= NOW()
            ORDER BY {"requested_at DESC," if newest_first else ""} next_run_at ASC, updated_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (
                job_type,
                AsyncJobStatus.PENDING.value,
                AsyncJobStatus.FAILED.value,
            ),
        )
        row_raw = cur.fetchone()
        if not row_raw:
            conn.commit()
            return None

        row = dict(row_raw)
        cur.execute(
            """
            UPDATE async_jobs
            SET
                status = %s,
                status_message = %s,
                attempts = attempts + 1,
                started_at = NOW(),
                finished_at = NULL,
                updated_at = NOW()
            WHERE job_id = %s
              AND revision = %s
              AND status IN (%s, %s)
            """,
            (
                AsyncJobStatus.RUNNING.value,
                "Running",
                row["job_id"],
                row["revision"],
                AsyncJobStatus.PENDING.value,
                AsyncJobStatus.FAILED.value,
            ),
        )
        if cur.rowcount == 0:
            conn.commit()
            return None
        conn.commit()
        if tracker := _ACTIVE_WORKER.get():
            tracker["job"] = row
        return row


class JobSuperseded(RuntimeError):
    pass


def recover_stale_jobs(*, job_type, user_email=None, timeout_seconds=LEASE_SECONDS):
    """Revisions fence interrupted claims; live workers renew updated_at."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE async_jobs SET status='pending',revision=revision+1,
            attempts=GREATEST(0,attempts-1),status_message='Queued after interruption',
            started_at=NULL,finished_at=NULL,error=NULL,next_run_at=NOW(),updated_at=NOW()
            WHERE job_type=%s AND (%s::text IS NULL OR user_email=%s)
                AND status='running' AND updated_at<NOW()-(%s*INTERVAL '1 second')""",
            (job_type, user_email, user_email, timeout_seconds),
        )
        count = cur.rowcount
        conn.commit()
    if count:
        logger.info("[async_jobs] stale_jobs_recovered job_type=%s count=%d", job_type, count)
    return count


def heartbeat_job(job_id, revision, *, timeout_seconds=LEASE_SECONDS):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE async_jobs SET updated_at=NOW() WHERE job_id=%s AND revision=%s
            AND status='running' AND updated_at>=NOW()-(%s*INTERVAL '1 second')""",
            (job_id, revision, timeout_seconds),
        )
        renewed = bool(cur.rowcount)
        conn.commit()
    return renewed


def require_current_job(cur):
    """Lock the active revision in the same transaction as result publication."""
    if exc := llm_admission.current_deferral():
        raise exc
    tracker = _ACTIVE_WORKER.get()
    if tracker and (job := tracker["job"]):
        cur.execute(
            """SELECT 1 FROM async_jobs WHERE job_id=%s AND revision=%s
            AND status='running' AND updated_at>=NOW()-(%s*INTERVAL '1 second') FOR UPDATE""",
            (job["job_id"], job["revision"], LEASE_SECONDS),
        )
        if not cur.fetchone():
            raise JobSuperseded("Job claim is no longer current")


def check_current_job():
    with get_conn() as conn, conn.cursor() as cur:
        require_current_job(cur)


def mark_succeeded(
    job_id: str,
    *,
    result: dict[str, Any] | None = None,
    status_message: str = "Completed",
    revision: int | None = None,
) -> bool:
    if exc := llm_admission.current_deferral():
        raise exc
    with get_conn() as conn, conn.cursor() as cur:
        params: list[Any] = [
            AsyncJobStatus.SUCCEEDED.value,
            status_message,
            _to_json(result or {}),
            job_id,
        ]
        revision_clause = ""
        if revision is not None:
            revision_clause = "AND revision = %s"
            params.append(revision)
        query = """
        UPDATE async_jobs
        SET
            status = %s,
            status_message = %s,
            result = %s::jsonb,
            error = NULL,
            finished_at = NOW(),
            updated_at = NOW()
        WHERE job_id = %s
        """
        if revision_clause:
            query = f"{query}\n          {revision_clause}"
        cur.execute(
            query,
            params,
        )
        changed = bool(cur.rowcount)
        conn.commit()
    return changed


def clear_succeeded_payload(job_id: str, *, revision: int | None = None) -> None:
    """Clear a completed job's payload when it contained temporary user context."""
    params: list[Any] = [job_id, AsyncJobStatus.SUCCEEDED.value]
    revision_clause = ""
    if revision is not None:
        revision_clause = "AND revision = %s"
        params.append(revision)
    query = """
        UPDATE async_jobs
        SET payload = '{}'::jsonb, updated_at = NOW()
        WHERE job_id = %s AND status = %s
    """
    if revision_clause:
        query = f"{query} {revision_clause}"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        conn.commit()


def mark_failed(
    job_id: str,
    *,
    error: str,
    status_message: str = "Failed",
    revision: int | None = None,
    retry_delay_seconds: int | None = None,
) -> None:
    if exc := llm_admission.current_deferral():
        if revision is not None:
            defer_job(job_id, revision=revision, delay=exc.delay)
        return
    with get_conn() as conn, conn.cursor() as cur:
        params: list[Any] = [
            AsyncJobStatus.FAILED.value,
            status_message,
            error,
        ]
        next_run_sql = ""
        if retry_delay_seconds is not None:
            next_run_sql = "next_run_at = NOW() + (%s * INTERVAL '1 second'),"
            params.append(retry_delay_seconds)
        params.append(job_id)
        revision_clause = ""
        if revision is not None:
            revision_clause = "AND revision = %s"
            params.append(revision)
        query = """
        UPDATE async_jobs
        SET
            status = %s,
            status_message = %s,
            error = %s,
        """
        if next_run_sql:
            query = f"{query}\n            {next_run_sql}"
        query = f"""
        {query}
            finished_at = NOW(),
            updated_at = NOW()
        WHERE job_id = %s
        """
        if revision_clause:
            query = f"{query}\n          {revision_clause}"
        cur.execute(query, params)
        conn.commit()


def _serialize_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in (
        "requested_at",
        "next_run_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ):
        data[key] = _serialize_datetime(data.get(key))
    return data


def _to_json(value: dict[str, Any]) -> str:
    return json.dumps(value, default=str)


def _serialize_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)
