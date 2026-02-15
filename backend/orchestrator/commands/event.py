"""Event-command orchestration helpers extracted from app.py."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

import contacts as contacts_service
import conversations
import events as events_service
import places as places_service
from observability.logger import get_runtime_logger
from schemas import (
    ContactIn,
    ContactRelationshipIn,
    EventCommandConfirmation,
    EventCommandResult,
    EventIn,
    PlaceIn,
)

from . import get_command_registry, parse_command
from .storage import (
    clear_pending_event,
    clear_pending_event_by_preview_id,
    delete_command_data,
    get_command_data,
    get_pending_event,
    store_command_data,
    store_command_thread,
)

logger = get_runtime_logger(__name__)

CommandResultPayload = tuple[dict[str, Any], str, dict[str, Any] | None]
CommandResponseTextFn = Callable[[dict[str, Any]], str]
CommandAssistantMetadataFn = Callable[
    [dict[str, Any]],
    tuple[dict[str, Any], dict[str, Any] | None],
]


def event_pending_key(user_email: str, thread_id: str | None) -> str:
    resolved_thread = thread_id or "main"
    return f"{user_email}:{resolved_thread}"


def handle_pending_event(
    question: str,
    user_email: str,
    user: dict[str, Any],
    thread_id: str | None,
    pending_event_id: str | None,
    *,
    command_response_text: CommandResponseTextFn,
    command_assistant_metadata: CommandAssistantMetadataFn,
) -> CommandResultPayload | None:
    if parse_command(question):
        return None

    key = event_pending_key(user_email, thread_id)
    preview_id = pending_event_id or get_pending_event(key)
    if not preview_id:
        return None

    command_data = get_command_data(preview_id)
    if not command_data:
        if not pending_event_id:
            clear_pending_event(key)
        return None

    command_thread_id = command_data.get("thread_id") or thread_id
    if command_thread_id:
        key = event_pending_key(user_email, command_thread_id)

    clarification_id = f"event:clarification:{uuid4().hex[:8]}"
    store_command_data(clarification_id, command_data)
    delete_command_data(preview_id)
    clear_pending_event(key)

    original_message = command_data.get("original_message") or question
    combined_message = (
        f"/event {original_message}\n\nAdditional details: {question}\n\n"
        f"[clarification_id:{clarification_id}]"
    )

    parsed_cmd = parse_command(combined_message)
    if not parsed_cmd:
        return None

    if not command_thread_id:
        command_thread = conversations.ensure_thread(None, user_email, title="Command: /event")
        command_thread_id = command_thread["id"]
        store_command_thread(key, command_thread_id)
    else:
        store_command_thread(key, command_thread_id)

    registry = get_command_registry()
    context = {
        "user_email": user_email,
        "user": user,
        "thread_id": command_thread_id,
        "event_pending_key": key,
    }
    command_result = registry.execute(parsed_cmd, context)

    assistant_metadata, ui_directives = command_assistant_metadata(command_result)
    try:
        conversations.record_exchange(
            command_thread_id,
            user_email,
            question,
            command_response_text(command_result),
            assistant_metadata=assistant_metadata,
        )
    except Exception as exc:
        logger.warning("[command_thread] Failed to record exchange: %s", exc, exc_info=exc)

    return command_result, command_thread_id, ui_directives


def _string_list_from_modification(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(
            status_code=400, detail="Event modifications list fields must be arrays."
        )

    values: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            values.append(text)
    return values


def _normalize_event_modifications(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, Any] = {}

    if "title" in raw:
        normalized["title"] = str(raw.get("title") or "").strip()
    if "summary" in raw:
        normalized["summary"] = str(raw.get("summary") or "").strip()
    if "where" in raw:
        normalized["where"] = str(raw.get("where") or "").strip()
    if "when" in raw:
        when_raw = raw.get("when")
        if when_raw in (None, ""):
            normalized["when"] = None
        else:
            when_text = str(when_raw).strip()
            try:
                normalized["when"] = datetime.fromisoformat(when_text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid event modification 'when': {when_text}",
                ) from exc
    if "tags" in raw:
        normalized["tags"] = _string_list_from_modification(raw.get("tags"))
    if "types" in raw:
        normalized["types"] = _string_list_from_modification(raw.get("types"))
    if "contact_ids" in raw:
        normalized["contact_ids"] = _string_list_from_modification(raw.get("contact_ids"))

    confirmed_relationships = raw.get("confirmed_relationships")
    if isinstance(confirmed_relationships, list):
        normalized["confirmed_relationships"] = [
            relationship
            for relationship in confirmed_relationships
            if isinstance(relationship, dict)
        ]

    return normalized


def _safe_entity_slug(raw: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (raw or "").strip().lower())
    return normalized.strip("-")


def _persist_event_resolved(preview_id: str, status: str) -> None:
    """Best-effort: stamp ``event_resolved`` on the DB message for this preview."""
    try:
        msg_id = conversations.find_message_id_by_metadata_preview(preview_id)
        if msg_id is not None:
            conversations.set_message_metadata_field(msg_id, "event_resolved", status)
    except Exception as exc:
        logger.warning(
            "[event_confirm] Could not persist event_resolved=%s for %s: %s",
            status,
            preview_id,
            exc,
        )


def confirm_event_command(
    payload: EventCommandConfirmation,
    user_email: str,
) -> EventCommandResult:
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    if not payload.confirmed:
        delete_command_data(payload.preview_id)
        clear_pending_event_by_preview_id(payload.preview_id)
        _persist_event_resolved(payload.preview_id, "cancelled")
        return EventCommandResult(
            success=False,
            error="Event creation cancelled by user",
        )

    command_data = get_command_data(payload.preview_id)
    if not command_data:
        raise HTTPException(
            status_code=404,
            detail="Event preview not found or expired. Please try the /event command again.",
        )

    extracted = command_data["extracted"]
    resolution = command_data["resolution"]
    normalized_modifications = _normalize_event_modifications(payload.modifications)

    if "title" in normalized_modifications:
        extracted["title"] = normalized_modifications["title"]
    if "summary" in normalized_modifications:
        extracted["summary"] = normalized_modifications["summary"]
    if "when" in normalized_modifications:
        extracted["when"] = normalized_modifications["when"]
    if "where" in normalized_modifications:
        extracted["where"] = normalized_modifications["where"]
    if "tags" in normalized_modifications:
        extracted["tags"] = normalized_modifications["tags"]
    if "types" in normalized_modifications:
        extracted["types"] = normalized_modifications["types"]
    participant_override_enabled = "contact_ids" in normalized_modifications
    participant_override_ids = normalized_modifications.get("contact_ids", [])

    try:
        created_contacts = []
        contact_id_map = {}

        new_contacts_to_create = (
            []
            if participant_override_enabled
            else list(resolution.get("new_entities", {}).get("contacts", []))
        )

        for new_contact in new_contacts_to_create:
            display_name = new_contact["display_name"]
            inferred_profession = new_contact.get("inferred_profession")
            comments = new_contact.get("comments")
            if inferred_profession:
                has_profession = bool(comments) and re.search(
                    r"\bprofession\b", comments, re.IGNORECASE
                )
                has_match = bool(comments) and re.search(
                    rf"\b{re.escape(inferred_profession)}\b", comments, re.IGNORECASE
                )
                if not has_profession and not has_match:
                    profession_line = f"Profession: {inferred_profession}"
                    comments = (
                        f"{comments}\n\n{profession_line}".strip() if comments else profession_line
                    )
            contact_slug = _safe_entity_slug(display_name) or "contact"
            contact_id = f"contact:{contact_slug}-{uuid4().hex[:6]}"

            contact_in = ContactIn(
                contact_id=contact_id,
                display_name=display_name,
                aliases=[],
                emails=[],
                phones=[],
                links=[],
                tags=[],
                comments=comments,
            )

            contacts_service.ingest_contact(contact_in)
            created_contacts.append({"contact_id": contact_id, "display_name": display_name})
            contact_id_map[display_name] = contact_id

        confirmed_relationships = normalized_modifications.get("confirmed_relationships") or []

        if confirmed_relationships:
            existing_contact_map = {
                contact["display_name"]: contact["contact_id"]
                for contact in resolution.get("contacts", [])
                if contact.get("display_name") and contact.get("contact_id")
            }
            all_contact_map = {**existing_contact_map, **contact_id_map}

            def _resolve_relationship_contact_id(
                rel: dict[str, Any],
                key_prefix: str,
            ) -> str | None:
                contact_id = rel.get(f"{key_prefix}_contact_id")
                if contact_id:
                    return contact_id
                display_name = rel.get(f"{key_prefix}_display_name")
                if display_name:
                    return all_contact_map.get(display_name)
                return None

            for relationship in confirmed_relationships:
                if not isinstance(relationship, dict):
                    continue

                from_contact_id = _resolve_relationship_contact_id(relationship, "from")
                to_contact_id = _resolve_relationship_contact_id(relationship, "to")
                relationship_type = relationship.get("relationship_type") or relationship.get(
                    "type"
                )
                reciprocal_type = relationship.get("reciprocal_type") or relationship.get(
                    "other_type"
                )

                if not from_contact_id or not to_contact_id or not relationship_type:
                    continue

                rel_in = ContactRelationshipIn(
                    relationship_id=f"rel:{uuid4().hex}",
                    from_contact_id=from_contact_id,
                    to_contact_id=to_contact_id,
                    relationship_type=relationship_type,
                    reciprocal_type=reciprocal_type,
                )
                contacts_service.upsert_contact_relationship(rel_in)

        created_places = []
        place_id_map = {}

        for new_place in resolution["new_entities"]["places"]:
            place_name = new_place["name"]
            place_slug = _safe_entity_slug(place_name) or "place"
            place_id = f"plc_{place_slug}_{uuid4().hex[:6]}"

            place_in = PlaceIn(
                place_id=place_id,
                name=place_name,
                city=None,
                country=None,
                lat=None,
                lon=None,
                geohash=None,
            )

            places_service.ingest_place(place_in)
            created_places.append({"place_id": place_id, "name": place_name})
            place_id_map[place_name] = place_id

        if participant_override_enabled:
            all_contact_ids = list(participant_override_ids)
        else:
            all_contact_ids = []
            for existing_contact in resolution["contacts"]:
                all_contact_ids.append(existing_contact["contact_id"])
            for created_contact in created_contacts:
                all_contact_ids.append(created_contact["contact_id"])

        place_id = None
        where = extracted.get("where")
        if where:
            place_id = place_id_map.get(where)

        event_id = f"event:{uuid4().hex}"
        when = extracted.get("when")

        event_in = EventIn(
            id=event_id,
            startDate=when if when else datetime.now(),
            endDate=None,
            placeId=place_id,
            people=all_contact_ids,
            tags=extracted.get("tags", []),
            types=extracted.get("types", ["generic"]),
            title=extracted.get("title", ""),
            summary=extracted.get("summary", ""),
            raw={"source": "event_command"},
        )

        events_service.ingest_event(event_in)

        delete_command_data(payload.preview_id)
        clear_pending_event_by_preview_id(payload.preview_id)
        _persist_event_resolved(payload.preview_id, "created")

        return EventCommandResult(
            success=True,
            event_id=event_id,
            created_contacts=created_contacts,
            created_places=created_places,
        )

    except Exception as exc:
        logger.exception("[event_confirm] Failed to create event: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to create event: {str(exc)}")
