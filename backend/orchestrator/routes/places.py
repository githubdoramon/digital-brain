from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

import contacts as contacts_service
import places as places_service
from auth import get_current_user
from schemas import PlaceIn


def create_places_router() -> APIRouter:
    router = APIRouter()

    @router.post("/ingest/place")
    @router.post("/mobile/ingest/place")
    def ingest_place(p: PlaceIn, user: dict = Depends(get_current_user)):
        places_service.ingest_place(p)
        return {"ok": True}

    @router.get("/places/{place_id}")
    @router.get("/mobile/places/{place_id}")
    def get_place(place_id: str, user: dict = Depends(get_current_user)):
        place = places_service.get_place(place_id)
        if place is None:
            raise HTTPException(status_code=404, detail="Place not found")
        return place

    @router.get("/mobile/places")
    def list_mobile_places(
        user: dict = Depends(get_current_user),
        q: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=500),
    ):
        return {"places": places_service.list_places(q, limit=limit)}

    @router.get("/mobile/places/{place_id}/contacts")
    def list_mobile_place_contacts(place_id: str, user: dict = Depends(get_current_user)):
        place = places_service.get_place(place_id)
        if place is None:
            raise HTTPException(status_code=404, detail="Place not found")

        rows = places_service.list_place_contacts(place_id)
        contacts = [
            row
            for row in rows
            if not contacts_service.is_external_placeholder(str(row.get("display_name") or ""))
        ]
        return {"contacts": contacts}

    @router.delete("/places/{place_id}")
    @router.delete("/mobile/places/{place_id}")
    def delete_place(place_id: str, user: dict = Depends(get_current_user)):
        place = places_service.get_place(place_id)
        if place is None:
            raise HTTPException(status_code=404, detail="Place not found")

        places_service.delete_place(place_id)
        return {"ok": True}

    @router.delete("/mobile/places/{place_id}/contacts/{contact_id}")
    def unlink_mobile_place_contact(
        place_id: str,
        contact_id: str,
        user: dict = Depends(get_current_user),
    ):
        place = places_service.get_place(place_id)
        if place is None:
            raise HTTPException(status_code=404, detail="Place not found")

        deleted = places_service.unlink_contact_place(contact_id=contact_id, place_id=place_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Contact-place link not found")
        return {"ok": True}

    return router
