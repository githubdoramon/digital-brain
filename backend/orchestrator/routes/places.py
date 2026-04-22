from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

import contacts as contacts_service
import places as places_service
from auth import get_current_user
from db import get_conn
from schemas import PlaceIn


def _clean_id_list(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [str(value).strip() for value in values if str(value).strip()]


def _search_mobile_places(
    *,
    query: str | None,
    limit: int,
    place_ids: list[str] | None,
    contact_ids: list[str] | None,
    event_ids: list[str] | None,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    clean_query = str(query or "").strip()

    if clean_query:
        like = f"%{clean_query}%"
        filters.append(
            """
            (
                unaccent(coalesce(p.name, '')) ILIKE unaccent(%s)
                OR unaccent(coalesce(p.description, '')) ILIKE unaccent(%s)
                OR unaccent(coalesce(p.address, '')) ILIKE unaccent(%s)
                OR unaccent(coalesce(p.city, '')) ILIKE unaccent(%s)
                OR unaccent(coalesce(p.country, '')) ILIKE unaccent(%s)
                OR EXISTS (
                    SELECT 1
                    FROM unnest(coalesce(p.aliases, ARRAY[]::TEXT[])) AS alias
                    WHERE unaccent(alias) ILIKE unaccent(%s)
                )
            )
            """
        )
        params.extend([like, like, like, like, like, like])

    if place_ids is not None:
        if not place_ids:
            return []
        filters.append("p.place_id = ANY(%s)")
        params.append(place_ids)

    if contact_ids is not None:
        if not contact_ids:
            return []
        filters.append(
            """
            (
                SELECT COUNT(DISTINCT cp.contact_id)
                FROM contact_places cp
                WHERE cp.place_id = p.place_id
                  AND cp.contact_id = ANY(%s)
            ) = %s
            """
        )
        params.extend([contact_ids, len(contact_ids)])

    if event_ids is not None:
        if not event_ids:
            return []
        filters.append(
            """
            EXISTS (
                SELECT 1
                FROM events e
                WHERE e.place_id = p.place_id
                  AND e.id = ANY(%s)
            )
            """
        )
        params.append(event_ids)

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT p.place_id, p.name, p.aliases, p.description, p.address, p.city, p.country, p.lat, p.lon
            FROM places p
            {where_clause}
            ORDER BY p.name NULLS LAST, p.place_id
            LIMIT %s
            """,
            (*params, limit),
        )
        return [dict(row) for row in cur.fetchall()]


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
        query: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=500),
        contact_ids: list[str] | None = Query(default=None),
        place_ids: list[str] | None = Query(default=None),
        event_ids: list[str] | None = Query(default=None),
    ):
        clean_contact_ids = _clean_id_list(contact_ids)
        clean_place_ids = _clean_id_list(place_ids)
        clean_event_ids = _clean_id_list(event_ids)
        effective_query = query if query is not None else q
        if effective_query or clean_contact_ids is not None or clean_place_ids is not None or clean_event_ids is not None:
            return {
                "places": _search_mobile_places(
                    query=effective_query,
                    limit=limit,
                    place_ids=clean_place_ids,
                    contact_ids=clean_contact_ids,
                    event_ids=clean_event_ids,
                )
            }
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
