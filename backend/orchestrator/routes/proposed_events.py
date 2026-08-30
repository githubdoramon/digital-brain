from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import proposed_event_jobs
import proposed_events as proposed_events_service
from auth import get_current_user


class ProposedEventAcceptIn(BaseModel):
    title: str | None = None
    summary: str | None = None
    start_at: datetime | None = Field(default=None, alias="startAt")
    end_at: datetime | None = Field(default=None, alias="endAt")
    contact_ids: list[str] | None = Field(default=None, alias="contactIds")
    place_id: str | None = Field(default=None, alias="placeId")
    place_candidate_id: str | None = Field(default=None, alias="placeCandidateId")
    media_asset_ids: list[str] | None = Field(default=None, alias="mediaAssetIds")


class ProposedEventRunIn(BaseModel):
    target_date: date | None = Field(default=None, alias="targetDate")
    timezone: str | None = None


class ProposedEventMediaSelectionIn(BaseModel):
    media_asset_ids: list[str] = Field(default_factory=list, alias="mediaAssetIds")


def _user_email(user: dict[str, Any]) -> str:
    email = str(user.get("email") or user.get("user_email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="User email is missing")
    return email


def create_proposed_events_router() -> APIRouter:
    router = APIRouter()

    @router.get("/mobile/proposed-events")
    def list_mobile_proposed_events(
        include_resolved: bool = Query(default=False, alias="includeResolved"),
        user: dict = Depends(get_current_user),
    ):
        email = _user_email(user)
        return {
            "proposals": proposed_events_service.list_proposals(
                email,
                include_resolved=include_resolved,
            )
        }

    @router.get("/mobile/proposed-events/status")
    def read_mobile_proposed_events_status(user: dict = Depends(get_current_user)):
        email = _user_email(user)
        return proposed_event_jobs.get_scheduler_status(email)

    @router.get("/proposed-events/timeline")
    def read_proposed_events_timeline(
        target_date: date = Query(..., alias="date"),
        timezone: str | None = None,
        user: dict = Depends(get_current_user),
    ):
        email = _user_email(user)
        return proposed_events_service.get_day_timeline(
            user_email=email,
            target_date=target_date,
            timezone_name=timezone,
        )

    @router.post("/mobile/proposed-events/run")
    def run_mobile_proposed_events(
        payload: ProposedEventRunIn,
        user: dict = Depends(get_current_user),
    ):
        email = _user_email(user)
        timezone_name = payload.timezone or "UTC"
        target_date = payload.target_date or proposed_events_service.current_local_date(timezone_name)
        result = proposed_events_service.analyze_user_window(
            user_email=email,
            target_date=target_date,
            timezone_name=timezone_name,
        )
        return {"ok": True, **result}

    @router.post("/mobile/proposed-events/enqueue")
    def enqueue_mobile_proposed_events(
        payload: ProposedEventRunIn,
        user: dict = Depends(get_current_user),
    ):
        email = _user_email(user)
        timezone_name = payload.timezone or "UTC"
        target_date = payload.target_date or proposed_events_service.current_local_date(timezone_name)
        job = proposed_event_jobs.enqueue_daily_scan(
            user_email=email,
            target_date=target_date,
            timezone_name=timezone_name,
            replace_existing=True,
        )
        return {"ok": True, **job}

    @router.post("/mobile/proposed-events/{proposal_id}/accept")
    def accept_mobile_proposed_event(
        proposal_id: str,
        payload: ProposedEventAcceptIn,
        user: dict = Depends(get_current_user),
    ):
        email = _user_email(user)
        try:
            proposal = proposed_events_service.accept_proposal(
                email,
                proposal_id,
                title=payload.title,
                summary=payload.summary,
                start_at=payload.start_at,
                end_at=payload.end_at,
                contact_ids=payload.contact_ids,
                place_id=payload.place_id,
                place_candidate_id=payload.place_candidate_id,
                media_asset_ids=payload.media_asset_ids,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "proposal": proposal}

    @router.post("/mobile/proposed-events/{proposal_id}/media-selection")
    def select_mobile_proposed_event_media(
        proposal_id: str,
        payload: ProposedEventMediaSelectionIn,
        user: dict = Depends(get_current_user),
    ):
        email = _user_email(user)
        try:
            proposed_events_service.get_owned_proposal(email, proposal_id)
            media_suggestions = proposed_events_service.set_proposal_media_selection(
                proposal_id,
                payload.media_asset_ids,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "media_suggestions": media_suggestions}

    @router.post("/mobile/proposed-events/{proposal_id}/dismiss")
    def dismiss_mobile_proposed_event(
        proposal_id: str,
        user: dict = Depends(get_current_user),
    ):
        email = _user_email(user)
        try:
            proposal = proposed_events_service.dismiss_proposal(email, proposal_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "proposal": proposal}

    @router.post("/mobile/proposed-events/{proposal_id}/ignore")
    def ignore_mobile_proposed_event(
        proposal_id: str,
        user: dict = Depends(get_current_user),
    ):
        email = _user_email(user)
        try:
            proposal = proposed_events_service.ignore_proposal(email, proposal_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "proposal": proposal}

    return router
