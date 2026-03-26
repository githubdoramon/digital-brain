from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response

import daily_briefings
from async_jobs import enqueue_job, mark_failed, mark_running, mark_succeeded
from auth import get_current_user, require_service_api_key
from notifications import DAILY_BRIEFING_NOTIFICATION_TYPE, send_notification_to_user
from observability.logger import get_runtime_logger
from schemas import DailyBriefingIn, DailyBriefingOut

logger = get_runtime_logger(__name__)

_JOB_TYPE_DAILY_BRIEFING = "daily_briefing"


def create_daily_briefing_router(
) -> APIRouter:
    router = APIRouter()

    @router.get("/mobile/briefings/daily", response_model=DailyBriefingOut)
    def get_daily_briefing(
        response: Response,
        background_tasks: BackgroundTasks,
        date_value: str = Query(..., alias="date"),
        timezone: str | None = Query(default=None),
        user: dict = Depends(get_current_user),
    ):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        briefing_date = _parse_iso_date(date_value)
        timezone_value = (timezone or "UTC").strip() or "UTC"

        briefing = daily_briefings.get_daily_briefing(
            user_email=user_email,
            briefing_date=briefing_date,
            timezone=timezone_value,
        )
        if briefing:
            return _format_briefing_response(briefing, status="ready")

        queued = _enqueue_daily_briefing_job(
            user_email=user_email,
            briefing_date=briefing_date,
            timezone=timezone_value,
            background_tasks=background_tasks,
        )
        response.status_code = 202
        return _pending_briefing_response(
            briefing_date=briefing_date,
            timezone=timezone_value,
            job_id=str(queued.get("job_id") or ""),
            message="Briefing generation is in progress.",
            status="pending",
        )

    @router.get("/mobile/briefings/latest", response_model=DailyBriefingOut)
    def get_latest_briefing(user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        briefing = daily_briefings.get_latest_daily_briefing(user_email=user_email)
        if not briefing:
            raise HTTPException(status_code=404, detail="Briefing not found")
        return _format_briefing_response(briefing, status="ready")

    @router.post(
        "/agents/daily-briefing/run",
        response_model=DailyBriefingOut,
        dependencies=[Depends(require_service_api_key)],
    )
    def run_daily_briefing_agent(
        payload: DailyBriefingIn,
        response: Response,
        background_tasks: BackgroundTasks,
    ):
        briefing_date = _parse_iso_date(payload.date)
        timezone_value = (payload.timezone or "UTC").strip() or "UTC"
        user_email = (payload.user_email or "default_user").strip() or "default_user"

        queued = _enqueue_daily_briefing_job(
            user_email=user_email,
            briefing_date=briefing_date,
            timezone=timezone_value,
            background_tasks=background_tasks,
        )
        response.status_code = 202
        return _pending_briefing_response(
            briefing_date=briefing_date,
            timezone=timezone_value,
            job_id=str(queued.get("job_id") or ""),
            message="Daily briefing generation queued.",
            status="pending",
        )

    return router


def _run_daily_briefing_job(
    *,
    job_id: str,
    date_value: str,
    timezone: str,
    user_email: str,
) -> None:
    if not mark_running(job_id):
        logger.info("[briefing.job] Skipping run; job already active/completed: %s", job_id)
        return

    from agents.daily_briefing.executor import handle_daily_briefing_request

    logger.info(
        "[briefing.job] Starting job=%s user=%s date=%s tz=%s",
        job_id,
        user_email,
        date_value,
        timezone,
    )
    try:
        result = handle_daily_briefing_request(
            {
                "date": date_value,
                "timezone": timezone,
                "user_email": user_email,
            }
        )
        if result.get("status") == "error":
            error_message = str(result.get("message") or "Daily briefing generation failed")
            mark_failed(job_id, error=error_message, status_message="Daily briefing failed")
            logger.warning("[briefing.job] Job failed job=%s error=%s", job_id, error_message)
            return

        mark_succeeded(
            job_id,
            result={"briefing_id": result.get("briefing_id")},
            status_message="Daily briefing ready",
        )
        _notify_daily_briefing_ready(user_email=user_email, result=result)
        logger.info(
            "[briefing.job] Job completed job=%s briefing_id=%s",
            job_id,
            result.get("briefing_id"),
        )
    except Exception as exc:
        error_message = str(exc)
        mark_failed(job_id, error=error_message, status_message="Daily briefing failed")
        logger.exception("[briefing.job] Unexpected failure job=%s", job_id)


def _enqueue_daily_briefing_job(
    *,
    user_email: str,
    briefing_date: date,
    timezone: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    dedupe_key = _build_daily_briefing_dedupe_key(briefing_date=briefing_date, timezone=timezone)
    queued = enqueue_job(
        job_type=_JOB_TYPE_DAILY_BRIEFING,
        user_email=user_email,
        dedupe_key=dedupe_key,
        payload={
            "date": briefing_date.isoformat(),
            "timezone": timezone,
            "user_email": user_email,
        },
        status_message="Queued for generation",
    )
    job_id = str(queued.get("job_id") or "")
    should_schedule = bool(queued.get("should_schedule"))
    if should_schedule and job_id:
        background_tasks.add_task(
            _run_daily_briefing_job,
            job_id=job_id,
            date_value=briefing_date.isoformat(),
            timezone=timezone,
            user_email=user_email,
        )
    return queued


def _build_daily_briefing_dedupe_key(*, briefing_date: date, timezone: str) -> str:
    return f"{briefing_date.isoformat()}::{timezone}"


def _parse_iso_date(value: str | None) -> date:
    if not value:
        raise HTTPException(status_code=400, detail="date is required")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value}") from exc


def _format_briefing_response(
    briefing: dict[str, Any],
    *,
    status: str = "ready",
    job_id: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "job_id": job_id,
        "message": message,
        "briefing_id": briefing.get("briefing_id"),
        "date": briefing.get("briefing_date"),
        "timezone": briefing.get("timezone"),
        "event_count": briefing.get("event_count") or 0,
        "todo_count": briefing.get("todo_count") or 0,
        "summary": briefing.get("summary") or "",
        "markdown": briefing.get("markdown") or "",
        "news_items": briefing.get("news_items") or [],
    }


def _pending_briefing_response(
    *,
    briefing_date: date,
    timezone: str,
    job_id: str,
    message: str,
    status: str = "pending",
) -> dict[str, Any]:
    return {
        "status": status,
        "job_id": job_id,
        "message": message,
        "briefing_id": None,
        "date": briefing_date.isoformat(),
        "timezone": timezone,
        "event_count": 0,
        "todo_count": 0,
        "summary": "",
        "markdown": "",
        "news_items": [],
    }


def _notify_daily_briefing_ready(*, user_email: str, result: dict[str, Any]) -> None:
    summary = str(result.get("summary") or "").strip()
    message = summary or "Your daily briefing is ready."
    if len(message) > 220:
        message = f"{message[:217].rstrip()}..."
    try:
        send_notification_to_user(
            notification_type=DAILY_BRIEFING_NOTIFICATION_TYPE,
            user_email=user_email,
            title="Daily briefing ready",
            message=message,
        )
    except Exception:
        logger.warning("[briefing.job] Failed to send push notification user=%s", user_email)
