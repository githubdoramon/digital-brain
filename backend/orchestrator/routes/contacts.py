from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

import contact_groups as contact_groups_service
import contacts as contacts_service
import immich_client
import places as places_service
from auth import get_current_user, require_service_api_key
from db import get_conn
from llm_helpers import LLMUnavailableError
from observability.logger import get_runtime_logger
from schemas import (
    ContactCommandConfirmation,
    ContactCommandResult,
    ContactEnsureIn,
    ContactGroupIn,
    ContactGroupOut,
    ContactIn,
    ContactMergeIn,
    ContactPlaceLinkIn,
    ContactRelationshipIn,
    ExternalContactWebhook,
)

logger = get_runtime_logger(__name__)


def _clean_id_list(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [str(value).strip() for value in values if str(value).strip()]


def _search_mobile_contacts(
    *,
    query: str | None,
    contact_ids: list[str] | None,
    place_ids: list[str] | None,
    event_ids: list[str] | None,
    include_voice_profile: bool = False,
) -> list[dict[str, Any]]:
    filters = [
        """
        (c.display_name IS NULL OR LOWER(c.display_name) NOT LIKE %s)
        """
    ]
    params: list[Any] = [f"{contacts_service.EXTERNAL_CONTACT_PREFIX}%"]
    clean_query = str(query or "").strip()

    if clean_query:
        like = f"%{clean_query}%"
        filters.append(
            """
            (
                unaccent(COALESCE(c.display_name, '')) ILIKE unaccent(%s)
                OR EXISTS (
                    SELECT 1
                    FROM unnest(COALESCE(c.aliases, ARRAY[]::TEXT[])) AS alias
                    WHERE unaccent(alias) ILIKE unaccent(%s)
                )
                OR EXISTS (
                    SELECT 1
                    FROM unnest(COALESCE(c.tags, ARRAY[]::TEXT[])) AS tag
                    WHERE unaccent(tag) ILIKE unaccent(%s)
                )
                OR unaccent(COALESCE(c.comments, '')) ILIKE unaccent(%s)
                OR EXISTS (
                    SELECT 1
                    FROM unnest(COALESCE(c.emails, ARRAY[]::TEXT[])) AS email
                    WHERE unaccent(email) ILIKE unaccent(%s)
                )
                OR EXISTS (
                    SELECT 1
                    FROM unnest(COALESCE(c.phones, ARRAY[]::TEXT[])) AS phone
                    WHERE unaccent(phone) ILIKE unaccent(%s)
                )
            )
            """
        )
        params.extend([like, like, like, like, like, like])

    if contact_ids is not None:
        if not contact_ids:
            return []
        filters.append("c.contact_id = ANY(%s)")
        params.append(contact_ids)

    if place_ids is not None:
        if not place_ids:
            return []
        filters.append(
            """
            EXISTS (
                SELECT 1
                FROM contact_places cp
                WHERE cp.contact_id = c.contact_id
                  AND cp.place_id = ANY(%s)
            )
            """
        )
        params.append(place_ids)

    if event_ids is not None:
        if not event_ids:
            return []
        filters.append(
            """
            EXISTS (
                SELECT 1
                FROM event_contacts ec
                WHERE ec.contact_id = c.contact_id
                  AND ec.event_id = ANY(%s)
            )
            """
        )
        params.append(event_ids)

    where_clause = " AND ".join(filters)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.contact_id
            FROM contacts c
            WHERE {where_clause}
            ORDER BY c.display_name
            """,
            tuple(params),
        )
        matched_contact_ids = [str(row["contact_id"]) for row in cur.fetchall()]

    if not matched_contact_ids:
        return []

    contacts = contacts_service._load_contacts(
        contact_ids=matched_contact_ids,
        include_voice_profile=include_voice_profile,
    )
    order = {contact_id: index for index, contact_id in enumerate(matched_contact_ids)}
    contacts.sort(key=lambda item: order.get(str(item.get("contact_id")), len(order)))
    return contacts


def _raise_http_for_llm_unavailable(exc: Exception) -> None:
    raise HTTPException(
        status_code=503,
        detail="The LLM service is currently unavailable. Please try again shortly.",
    ) from exc


def _is_mobile_request(request: Request) -> bool:
    return request.url.path.startswith("/mobile/")


def create_contacts_router(
) -> APIRouter:
    router = APIRouter()

    @router.post("/ingest/contact")
    @router.post("/mobile/ingest/contact")
    def ingest_contact(c: ContactIn | ContactEnsureIn, user: dict = Depends(get_current_user)):
        if isinstance(c, ContactIn):
            contacts_service.ingest_contact(c)
            return {"ok": True, "contact_id": c.contact_id, "created": False}

        contact_id, created = contacts_service.ensure_contact_for_email(
            c.email,
            display_name=c.display_name,
        )
        if not contact_id:
            raise HTTPException(status_code=400, detail="Valid contact email is required")
        return {"ok": True, "contact_id": contact_id, "created": created}

    @router.get("/contacts")
    @router.get("/mobile/contacts")
    def list_contacts(
        request: Request,
        user: dict = Depends(get_current_user),
        query: str | None = Query(default=None),
        contact_ids: list[str] | None = Query(default=None),
        place_ids: list[str] | None = Query(default=None),
        event_ids: list[str] | None = Query(default=None),
    ):
        include_voice_profile = not _is_mobile_request(request)
        clean_contact_ids = _clean_id_list(contact_ids)
        clean_place_ids = _clean_id_list(place_ids)
        clean_event_ids = _clean_id_list(event_ids)
        if query or clean_contact_ids is not None or clean_place_ids is not None or clean_event_ids is not None:
            return {
                "contacts": _search_mobile_contacts(
                    query=query,
                    contact_ids=clean_contact_ids,
                    place_ids=clean_place_ids,
                    event_ids=clean_event_ids,
                    include_voice_profile=include_voice_profile,
                )
            }
        return {"contacts": contacts_service.list_contacts(include_voice_profile=include_voice_profile)}

    @router.get("/contact-groups")
    @router.get("/mobile/contact-groups")
    def list_contact_groups(
        include_archived: bool = Query(default=False, alias="includeArchived"),
        user: dict = Depends(get_current_user),
    ):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        groups = contact_groups_service.list_contact_groups(
            user_email,
            include_archived=include_archived,
        )
        return {"groups": groups}

    @router.post("/contact-groups", response_model=ContactGroupOut)
    @router.post("/mobile/contact-groups", response_model=ContactGroupOut)
    def create_contact_group(payload: ContactGroupIn, user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        group = contact_groups_service.create_contact_group(
            user_email=user_email,
            name=payload.name,
            member_contact_ids=list(payload.member_contact_ids or []),
            aliases=list(payload.aliases or []),
            description=payload.description,
        )
        if not group:
            raise HTTPException(status_code=400, detail="Failed to create contact group")

        full_group = contact_groups_service.get_contact_group(
            user_email,
            str(group.get("group_id") or ""),
        )
        if not full_group:
            raise HTTPException(status_code=404, detail="Created contact group not found")
        return ContactGroupOut(**full_group)

    @router.get("/contact-groups/{group_id}", response_model=ContactGroupOut)
    @router.get("/mobile/contact-groups/{group_id}", response_model=ContactGroupOut)
    def get_contact_group(group_id: str, user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        group = contact_groups_service.get_contact_group(user_email, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Contact group not found")
        return ContactGroupOut(**group)

    @router.delete("/contact-groups/{group_id}")
    @router.delete("/mobile/contact-groups/{group_id}")
    def archive_contact_group(group_id: str, user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        archived = contact_groups_service.archive_contact_group(user_email, group_id)
        if not archived:
            raise HTTPException(status_code=404, detail="Contact group not found")
        return {"ok": True}

    @router.get("/contacts/merge-candidates")
    def list_merge_candidates(user: dict = Depends(get_current_user)):
        return contacts_service.list_contact_merge_candidates()

    @router.post("/contacts/merge")
    def merge_contacts_endpoint(payload: ContactMergeIn, user: dict = Depends(get_current_user)):
        try:
            contact = contacts_service.merge_contacts(
                payload.primary_contact_id,
                payload.duplicate_contact_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="One or both contacts not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "contact": contact}

    @router.get("/contacts/{contact_id}")
    @router.get("/mobile/contacts/{contact_id}")
    def get_contact(contact_id: str, request: Request, user: dict = Depends(get_current_user)):
        contact = contacts_service.get_contact(
            contact_id,
            include_voice_profile=not _is_mobile_request(request),
        )
        if contact is None or contacts_service.is_external_placeholder(contact.get("display_name")):
            raise HTTPException(status_code=404, detail="Contact not found")
        return contact

    @router.get("/mobile/contacts/{contact_id}/places")
    def list_mobile_contact_places(contact_id: str, user: dict = Depends(get_current_user)):
        contact = contacts_service.get_contact(contact_id)
        if contact is None or contacts_service.is_external_placeholder(contact.get("display_name")):
            raise HTTPException(status_code=404, detail="Contact not found")
        return {"places": places_service.list_contact_places(contact_id)}

    @router.post("/mobile/contacts/{contact_id}/places")
    def upsert_mobile_contact_place(
        contact_id: str,
        payload: ContactPlaceLinkIn,
        user: dict = Depends(get_current_user),
    ):
        contact = contacts_service.get_contact(contact_id)
        if contact is None or contacts_service.is_external_placeholder(contact.get("display_name")):
            raise HTTPException(status_code=404, detail="Contact not found")

        place = places_service.get_place(payload.place_id)
        if place is None:
            raise HTTPException(status_code=404, detail="Place not found")

        places_service.upsert_contact_place(
            contact_id=contact_id,
            place_id=payload.place_id,
            role=payload.role,
            source=payload.source,
            confidence=payload.confidence,
        )
        return {"ok": True}

    @router.delete("/mobile/contacts/{contact_id}/places/{place_id}")
    def unlink_mobile_contact_place(
        contact_id: str,
        place_id: str,
        user: dict = Depends(get_current_user),
    ):
        contact = contacts_service.get_contact(contact_id)
        if contact is None or contacts_service.is_external_placeholder(contact.get("display_name")):
            raise HTTPException(status_code=404, detail="Contact not found")

        deleted = places_service.unlink_contact_place(contact_id=contact_id, place_id=place_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Contact-place link not found")
        return {"ok": True}

    @router.get("/mobile/contacts/{contact_id}/avatar")
    def get_contact_avatar(contact_id: str, _: dict = Depends(get_current_user)):
        contact = contacts_service.get_contact(contact_id)
        if contact is None or contacts_service.is_external_placeholder(contact.get("display_name")):
            raise HTTPException(status_code=404, detail="Contact not found")

        external_id = contact.get("external_id")
        if not external_id:
            raise HTTPException(status_code=404, detail="Avatar not available")

        try:
            result = immich_client.fetch_person_thumbnail(external_id)
        except immich_client.ImmichClientError as exc:
            logger.exception("[get_contact_avatar] error=%s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if not result:
            raise HTTPException(status_code=404, detail="Avatar not available")

        content, content_type = result
        return Response(content=content, media_type=content_type)

    @router.post("/mobile/contacts/{contact_id}/relationships")
    def upsert_contact_relationship_mobile(
        contact_id: str,
        rel: ContactRelationshipIn,
        _: dict = Depends(get_current_user),
    ):
        if rel.from_contact_id != contact_id:
            raise HTTPException(status_code=400, detail="from_contact_id must match contact_id")
        contacts_service.upsert_contact_relationship(rel)
        return {"ok": True}

    @router.post("/commands/contact/confirm", response_model=ContactCommandResult)
    @router.post("/mobile/commands/contact/confirm", response_model=ContactCommandResult)
    def confirm_contact_command_endpoint(
        payload: ContactCommandConfirmation,
        user: dict = Depends(get_current_user),
    ):
        from commands.contact import confirm_contact_command

        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        return confirm_contact_command(payload, user_email)

    @router.delete("/mobile/contacts/{contact_id}/relationships/{relationship_id}")
    def delete_contact_relationship_mobile(
        contact_id: str,
        relationship_id: str,
        _: dict = Depends(get_current_user),
    ):
        deleted = contacts_service.delete_contact_relationship(relationship_id, contact_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Relationship not found")
        return {"ok": True}

    @router.delete("/contacts/{contact_id}")
    @router.delete("/mobile/contacts/{contact_id}")
    def delete_contact(contact_id: str, user: dict = Depends(get_current_user)):
        deleted = contacts_service.delete_contact(contact_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Contact not found")
        return {"ok": True}

    @router.post("/contacts/resolve")
    def resolve_contacts_endpoint(
        request_data: dict[str, Any],
        user: dict = Depends(get_current_user),
    ):
        from contact_resolution_service import resolve_contacts_request

        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        request_data["user_email"] = user_email
        try:
            return resolve_contacts_request(request_data)
        except LLMUnavailableError as exc:
            _raise_http_for_llm_unavailable(exc)

    @router.post("/webhooks/contacts")
    def receive_contact_webhook(
        payload: ExternalContactWebhook,
        _: None = Depends(require_service_api_key),
    ):
        event_name = (payload.event_name or "").lower()
        payload_body = payload.payload
        person = payload_body.person if payload_body else None
        if not person or not person.id:
            raise HTTPException(status_code=400, detail="Webhook payload is missing person information")

        external_id = str(person.id)

        if person.is_hidden:
            existing = contacts_service.get_contact_by_external_id(external_id)
            if existing:
                display_name = (existing.get("display_name") or "").strip().lower()
                if display_name.startswith("external contact"):
                    try:
                        deleted = contacts_service.delete_contact(existing["contact_id"])
                    except Exception as exc:
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to delete hidden contact: {exc}",
                        ) from exc
                    if deleted:
                        return {"ok": True, "action": "deleted"}
            return {"ok": True, "action": "ignored"}

        if event_name == "persondelete":
            try:
                updated = contacts_service.unlink_external_contact(external_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to unlink external contact: {exc}",
                ) from exc
            return {"ok": True, "action": "unlinked" if updated else "ignored"}

        if event_name in {"personcreate", "personupdate"}:
            try:
                contact = contacts_service.sync_external_contact(
                    person,
                    payload_body.previous if payload_body else None,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to process contact webhook: {exc}",
                ) from exc
            return {"ok": True, "contact": contact}

        raise HTTPException(status_code=400, detail=f"Unsupported eventName: {payload.event_name}")

    return router
