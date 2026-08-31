from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

import moments as moments_service
import user_locations
from auth import get_current_user
from schemas import MomentIn


def _user_email(user: dict[str, Any]) -> str:
    email = user.get("email") or user.get("user_email")
    if not email:
        raise HTTPException(status_code=400, detail="User email is missing")
    return str(email)


def _location_for_moment(*, user_email: str, moment: MomentIn) -> dict[str, Any] | None:
    if moment.location is not None:
        return moment.location.model_dump(mode="json", exclude_none=True)
    return user_locations.get_nearest_location(user_email=user_email, captured_at=moment.observed_at)


def create_moments_router() -> APIRouter:
    router = APIRouter()

    @router.post("/mobile/moments/batch")
    async def ingest_moment_batch(request: Request, user: dict = Depends(get_current_user)):
        user_email = _user_email(user)
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON") from exc
        raw_items = body.get("moments") if isinstance(body, dict) else None
        if not isinstance(raw_items, list) or not raw_items:
            raise HTTPException(status_code=422, detail="moments must be a non-empty array")
        if len(raw_items) > 50:
            raise HTTPException(status_code=422, detail="moments may contain at most 50 items")

        results: list[dict[str, Any]] = []
        for raw_item in raw_items:
            raw_id = raw_item.get("id") if isinstance(raw_item, dict) else None
            try:
                moment = MomentIn.model_validate(raw_item)
                result = moments_service.upsert_moment(
                    user_email=user_email,
                    moment=moment,
                    location=_location_for_moment(user_email=user_email, moment=moment),
                )
                results.append({"id": moment.id, "status": result.status})
            except ValidationError as exc:
                results.append(
                    {
                        "id": str(raw_id) if raw_id else None,
                        "status": "rejected",
                        "detail": exc.errors(include_url=False),
                    }
                )
            except moments_service.MomentConflictError as exc:
                results.append({"id": str(raw_id) if raw_id else None, "status": "rejected", "detail": str(exc)})
            except Exception:
                results.append({"id": str(raw_id) if raw_id else None, "status": "rejected", "detail": "Unable to store moment"})
        return {"results": results}

    @router.get("/moments")
    @router.get("/mobile/moments")
    def list_moments(
        source_type: str | None = Query(default=None),
        observed_after: datetime | None = Query(default=None),
        observed_before: datetime | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        user: dict = Depends(get_current_user),
    ):
        return moments_service.list_moments(
            user_email=_user_email(user),
            source_type=source_type,
            observed_after=observed_after,
            observed_before=observed_before,
            limit=limit,
            offset=offset,
        )

    @router.get("/moments/{moment_id}")
    @router.get("/mobile/moments/{moment_id}")
    def get_moment(moment_id: str, user: dict = Depends(get_current_user)):
        item = moments_service.get_moment(user_email=_user_email(user), moment_id=moment_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Moment not found")
        return item

    return router
