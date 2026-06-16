from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response

import daily_briefing_jobs
import daily_briefings
from auth import get_current_user, require_service_api_key
from schemas import DailyBriefingEventSummaryDebugIn, DailyBriefingIn, DailyBriefingOut


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

    @router.post("/debug/daily-briefing/event-summary")
    def debug_daily_briefing_event_summary(
        payload: DailyBriefingEventSummaryDebugIn,
        user: dict = Depends(get_current_user),
    ):
        from agents.daily_briefing.executor import build_daily_briefing_event_summary_debug

        user_email = (payload.user_email or user.get("email") or "").strip()
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        try:
            return build_daily_briefing_event_summary_debug(
                event_id=payload.event_id,
                timezone_name=payload.timezone,
                user_email=user_email,
            )
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail.startswith("Event not found:") else 400
            raise HTTPException(status_code=status_code, detail=detail) from exc

    return router


def _enqueue_daily_briefing_job(
    *,
    user_email: str,
    briefing_date: date,
    timezone: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    queued = daily_briefing_jobs.enqueue_daily_briefing_job(
        user_email=user_email,
        briefing_date=briefing_date,
        timezone_name=timezone,
    )
    job_id = str(queued.get("job_id") or "")
    should_schedule = bool(queued.get("should_schedule"))
    if should_schedule and job_id:
        background_tasks.add_task(
            daily_briefing_jobs.run_daily_briefing_job,
            job_id=job_id,
            date_value=briefing_date.isoformat(),
            timezone_name=timezone,
            user_email=user_email,
        )
    return queued


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
