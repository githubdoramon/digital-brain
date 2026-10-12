from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import microsoft_calendar
from auth import get_current_user


class MicrosoftCalendarCredentialIn(BaseModel):
    connected_email: str = Field(alias="connectedEmail", min_length=1)
    refresh_token: str = Field(alias="refreshToken", min_length=1)


class MicrosoftCalendarSelectionIn(BaseModel):
    calendar_ids: list[str] = Field(alias="calendarIds")


def create_microsoft_calendar_router() -> APIRouter:
    router = APIRouter()

    def owner_email(user: dict[str, Any]) -> str:
        email = str(user.get("email") or user.get("user_email") or "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Authenticated user email is missing")
        return email

    @router.get("/integrations/microsoft-calendar/connection")
    def read_connection(user: dict = Depends(get_current_user)):
        return microsoft_calendar.get_connection_status(owner_email(user))

    @router.post("/integrations/microsoft-calendar/connection")
    def save_connection(payload: MicrosoftCalendarCredentialIn, user: dict = Depends(get_current_user)):
        try:
            return microsoft_calendar.store_connection(
                owner_email(user), payload.connected_email, payload.refresh_token
            )
        except microsoft_calendar.MicrosoftCalendarError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/integrations/microsoft-calendar/calendars")
    def read_calendars(user: dict = Depends(get_current_user)):
        email = owner_email(user)
        if not microsoft_calendar.get_connection_status(email).get("connected"):
            raise HTTPException(status_code=409, detail="Connect Microsoft Calendar first")
        try:
            return {"calendars": microsoft_calendar.list_calendars(email)}
        except microsoft_calendar.MicrosoftCalendarError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.put("/integrations/microsoft-calendar/calendars")
    def save_calendars(payload: MicrosoftCalendarSelectionIn, user: dict = Depends(get_current_user)):
        email = owner_email(user)
        try:
            available = {
                str(calendar["id"])
                for calendar in microsoft_calendar.list_calendars(email)
                if calendar.get("id")
            }
            requested = list(dict.fromkeys(str(value).strip() for value in payload.calendar_ids))
            if any(calendar_id not in available for calendar_id in requested):
                raise HTTPException(status_code=400, detail="One or more calendars are unavailable")
            return microsoft_calendar.set_selected_calendars(email, requested)
        except microsoft_calendar.MicrosoftCalendarError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.delete("/integrations/microsoft-calendar/connection")
    def remove_connection(user: dict = Depends(get_current_user)):
        microsoft_calendar.disconnect(owner_email(user))
        return {"connected": False}

    return router
