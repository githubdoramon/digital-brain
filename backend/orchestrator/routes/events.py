from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

import events as events_service
import todos as todos_service
from auth import get_current_user, require_service_api_key
from commands.event import confirm_event_command as confirm_event_command_impl
from db import get_conn
from observability.logger import get_runtime_logger
from schemas import (
    EventCommandConfirmation,
    EventCommandResult,
    EventIn,
    ExternalEventPayload,
    MeetingIn,
)

logger = get_runtime_logger(__name__)


def create_events_router(
) -> APIRouter:
    router = APIRouter()

    @router.post("/ingest/event")
    @router.post("/mobile/ingest/event")
    def ingest_event(e: EventIn, user: dict = Depends(get_current_user)):
        events_service.ingest_event(e)
        return {"ok": True, "id": e.id}

    @router.post("/ingest/events/notes")
    def ingest_meeting_notes(
        meetings: list[MeetingIn],
        _: None = Depends(require_service_api_key),
    ):
        logger.debug("[meeting_notes] Ingestion request received with %d meeting(s)", len(meetings))
        ids = events_service.ingest_meeting_notes(meetings, todo_writer=todos_service.ingest_todo)
        logger.debug("[meeting_notes] Ingestion completed with %d event id(s)", len(ids))
        return {"ok": True, "ids": ids}

    @router.post("/ingest/event/external")
    def ingest_external_event(
        payload: ExternalEventPayload,
        _: None = Depends(require_service_api_key),
    ):
        try:
            event_id = events_service.ingest_external_event(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "id": event_id}

    @router.get("/events/search")
    @router.get("/mobile/events/search")
    def search_events(
        user: dict = Depends(get_current_user),
        query: str | None = Query(default=None),
        limit: int = Query(default=10, ge=1, le=50),
        offset: int = Query(default=0, ge=0),
    ):
        trimmed = (query or "").strip()
        with get_conn() as conn, conn.cursor() as cur:
            if trimmed:
                like = f"%{trimmed}%"
                cur.execute(
                    """
                    SELECT e.id,
                           e.title,
                           e.summary,
                           e.start_date,
                           e.end_date
                    FROM events e
                    WHERE unaccent(COALESCE(e.title, '')) ILIKE unaccent(%s)
                       OR unaccent(COALESCE(e.summary, '')) ILIKE unaccent(%s)
                    ORDER BY e.start_date DESC NULLS LAST, e.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (like, like, limit + 1, offset),
                )
            else:
                cur.execute(
                    """
                    SELECT e.id,
                           e.title,
                           e.summary,
                           e.start_date,
                           e.end_date
                    FROM events e
                    ORDER BY e.start_date DESC NULLS LAST, e.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit + 1, offset),
                )
            fetched_rows: list[dict[str, Any]] = [dict(row) for row in cur.fetchall()]
        has_more = len(fetched_rows) > limit
        rows = fetched_rows[:limit]
        next_offset = offset + len(rows)
        return {
            "events": rows,
            "has_more": has_more,
            "next_offset": next_offset,
        }

    @router.get("/events/{event_id}")
    @router.get("/mobile/events/{event_id}")
    def get_event_detail(event_id: str, user: dict = Depends(get_current_user)):
        events = events_service.get_events([event_id])
        if not events:
            raise HTTPException(status_code=404, detail="Event not found")
        return events[0]

    @router.get("/meetings/{meeting_id}")
    def get_meeting(meeting_id: str, user: dict = Depends(get_current_user)):
        meeting = events_service.get_meeting(meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        return meeting

    @router.post("/commands/event/confirm", response_model=EventCommandResult)
    @router.post("/mobile/commands/event/confirm", response_model=EventCommandResult)
    def confirm_event_command(
        payload: EventCommandConfirmation,
        user: dict = Depends(get_current_user),
    ):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        return confirm_event_command_impl(payload, user_email)

    return router
