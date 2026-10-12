from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import google_calendar
from auth import get_current_user


class GoogleCalendarCredentialIn(BaseModel):
    refresh_token: str = Field(alias="refreshToken", min_length=1)


class GoogleCalendarSelectionIn(BaseModel):
    calendar_ids: list[str] = Field(alias="calendarIds")


def create_google_calendar_router() -> APIRouter:
    router = APIRouter()

    def _user_email(user: dict[str, Any]) -> str:
        email = str(user.get("email") or user.get("user_email") or "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Authenticated user email is missing")
        return email

    @router.get("/integrations/google-calendar/connection")
    def read_connection(user: dict = Depends(get_current_user)):
        return google_calendar.get_connection_status(_user_email(user))

    @router.post("/integrations/google-calendar/connection")
    def save_connection(
        payload: GoogleCalendarCredentialIn,
        user: dict = Depends(get_current_user),
    ):
        try:
            return google_calendar.store_connection(_user_email(user), payload.refresh_token)
        except google_calendar.GoogleCalendarError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/integrations/google-calendar/calendars")
    def read_calendars(user: dict = Depends(get_current_user)):
        email = _user_email(user)
        if not google_calendar.get_connection_status(email).get("connected"):
            raise HTTPException(status_code=409, detail="Connect Google Calendar first")
        try:
            return {"calendars": google_calendar.list_calendars(email)}
        except google_calendar.GoogleCalendarError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.put("/integrations/google-calendar/calendars")
    def save_calendars(
        payload: GoogleCalendarSelectionIn,
        user: dict = Depends(get_current_user),
    ):
        email = _user_email(user)
        try:
            available = {
                str(calendar["id"])
                for calendar in google_calendar.list_calendars(email)
                if calendar.get("id")
            }
            requested = list(dict.fromkeys(str(value).strip() for value in payload.calendar_ids))
            unknown = [calendar_id for calendar_id in requested if calendar_id not in available]
            if unknown:
                raise HTTPException(status_code=400, detail="One or more calendars are unavailable")
            return google_calendar.set_selected_calendars(email, requested)
        except google_calendar.GoogleCalendarError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.delete("/integrations/google-calendar/connection")
    def remove_connection(user: dict = Depends(get_current_user)):
        google_calendar.disconnect(_user_email(user))
        return {"connected": False}

    return router
