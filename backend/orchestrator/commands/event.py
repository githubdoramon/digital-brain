"""Event-command orchestration helpers extracted from app.py."""

from __future__ import annotations

import re
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, tzinfo
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

import contact_groups as contact_groups_service
import contacts as contacts_service
import conversations
import event_photos as event_photos_service
import events as events_service
import places as places_service
from chat_media import (
    delete_staged_chat_media_attachments,
    load_staged_chat_media_attachment,
    merge_staged_chat_media_attachments,
)
from commands.event_datetime import (
    DEFAULT_EVENT_TIMEZONE,
    event_timezone_from_context,
    parse_event_datetime,
)
from observability.logger import get_runtime_logger
from schemas import (
    ContactIn,
    ContactRelationshipIn,
    EventCommandConfirmation,
    EventCommandResult,
    EventIn,
    PlaceIn,
)
from search_normalization import normalize_search_text

from . import get_command_registry, parse_command
from .state import ensure_restored_thread_exists, get_recoverable_command_data
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
ProgressCallbackFn = Callable[[str], None]


def event_pending_key(user_email: str, thread_id: str | None) -> str:
    resolved_thread = thread_id or "main"
    return f"{user_email}:{resolved_thread}"


def _event_command_data_from_result(
    command_result: dict[str, Any],
    exchange: dict[str, Any],
    user_email: str,
) -> dict[str, Any] | None:
    if command_result.get("type") != "event_confirmation":
        return None

    return {
        "command_name": "event",
        "extracted": deepcopy(command_result.get("extracted") or {}),
        "resolution": deepcopy(command_result.get("resolution") or {}),
        "user_email": user_email,
        "client_context": None,
        "relationship_suggestions": deepcopy(
            command_result.get("relationship_suggestions") or []
        ),
        "original_message": exchange.get("user_message") or "",
        "thread_id": exchange.get("thread_id"),
        "clarification_messages": [],
        "requested_field_ids": [],
        "original_extracted": deepcopy(command_result.get("original_extracted") or {}),
        "original_resolution": deepcopy(command_result.get("original_resolution") or {}),
        "operation": command_result.get("operation") or "create",
        "existing_event_id": command_result.get("existing_event_id"),
        "matched_event": deepcopy(command_result.get("matched_event")),
        "candidate_events": deepcopy(command_result.get("candidate_events") or []),
        "media_attachments": [],
    }


def _get_event_command_data(preview_id: str, user_email: str) -> dict[str, Any] | None:
    return get_recoverable_command_data(
        preview_id,
        user_email,
        get_cached_data=get_command_data,
        build_from_command_result=_event_command_data_from_result,
    )


def handle_pending_event(
    question: str,
    user_email: str,
    user: dict[str, Any],
    thread_id: str | None,
    pending_event_id: str | None,
    *,
    client_context: dict[str, Any] | None = None,
    media_attachments: list[dict[str, Any]] | None = None,
    user_metadata: dict[str, Any] | None = None,
    command_response_text: CommandResponseTextFn,
    command_assistant_metadata: CommandAssistantMetadataFn,
    progress_callback: ProgressCallbackFn | None = None,
) -> CommandResultPayload | None:
    if parse_command(question):
        return None

    key = event_pending_key(user_email, thread_id)
    preview_id = pending_event_id or get_pending_event(key)
    if not preview_id:
        return None

    command_data = _get_event_command_data(preview_id, user_email)
    if not command_data:
        if not pending_event_id:
            clear_pending_event(key)
        return None

    command_data = deepcopy(command_data)
    command_data["media_attachments"] = merge_staged_chat_media_attachments(
        _get_command_media_attachments(command_data),
        media_attachments or [],
    )

    command_thread_id = command_data.get("thread_id") or thread_id
    if command_thread_id and command_data.get("thread_id"):
        try:
            command_thread_id = ensure_restored_thread_exists(command_data, user_email)
        except LookupError:
            logger.warning(
                "[command_thread] Pending event thread missing; creating replacement thread"
            )
            command_thread_id = None
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail="Conversation thread does not belong to user",
            ) from exc
    if command_thread_id:
        key = event_pending_key(user_email, str(command_thread_id))

    clarification_id = f"event:clarification:{uuid4().hex[:8]}"
    store_command_data(clarification_id, command_data)
    delete_command_data(preview_id)
    clear_pending_event(key)

    original_message = command_data.get("original_message") or question
    command_name = str(command_data.get("command_name") or "event").strip().lower() or "event"
    combined_message = (
        f"/{command_name} {original_message}\n\nAdditional details: {question}\n\n"
        f"[clarification_id:{clarification_id}]"
    )

    parsed_cmd = parse_command(combined_message)
    if not parsed_cmd:
        return None

    if not command_thread_id:
        command_thread = conversations.ensure_thread(
            None,
            user_email,
            title=f"Command: /{command_name}",
        )
        command_thread_id = command_thread["id"]
        store_command_thread(key, command_thread_id)
    else:
        store_command_thread(key, command_thread_id)
    if thread_id is None:
        conversations.set_main_session_thread(user_email, str(command_thread_id))

    registry = get_command_registry()
    context = {
        "user_email": user_email,
        "user": user,
        "thread_id": command_thread_id,
        "event_pending_key": key,
        "client_context": client_context,
        "media_attachments": media_attachments or [],
        "progress_callback": progress_callback,
    }
    command_result = registry.execute(parsed_cmd, context)
    if command_result.get("type") == "error" and media_attachments:
        delete_staged_chat_media_attachments(media_attachments)

    assistant_metadata, ui_directives = command_assistant_metadata(command_result)
    try:
        conversations.record_exchange(
            command_thread_id,
            user_email,
            question,
            command_response_text(command_result),
            user_metadata=user_metadata,
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


def _normalize_event_modifications(
    raw: Any,
    *,
    default_tz: tzinfo = DEFAULT_EVENT_TIMEZONE,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, Any] = {}

    if "title" in raw:
        normalized["title"] = str(raw.get("title") or "").strip()
    if "summary" in raw:
        normalized["summary"] = str(raw.get("summary") or "").strip()
    if "where" in raw:
        normalized["where"] = str(raw.get("where") or "").strip()
    if "place_id" in raw:
        place_id_raw = raw.get("place_id")
        if place_id_raw in (None, ""):
            normalized["place_id"] = None
        else:
            normalized["place_id"] = str(place_id_raw).strip()
    if "when" in raw:
        when_raw = raw.get("when")
        if when_raw in (None, ""):
            normalized["when"] = None
        else:
            when_text = str(when_raw).strip()
            try:
                normalized["when"] = parse_event_datetime(when_text, default_tz=default_tz)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid event modification 'when': {when_text}",
                ) from exc
    if "end_when" in raw:
        end_when_raw = raw.get("end_when")
        if end_when_raw in (None, ""):
            normalized["end_when"] = None
        else:
            end_when_text = str(end_when_raw).strip()
            try:
                normalized["end_when"] = parse_event_datetime(end_when_text, default_tz=default_tz)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid event modification 'end_when': {end_when_text}",
                ) from exc
    if "tags" in raw:
        normalized["tags"] = _string_list_from_modification(raw.get("tags"))
    if "types" in raw:
        normalized["types"] = _string_list_from_modification(raw.get("types"))
    if "contact_ids" in raw:
        normalized["contact_ids"] = _string_list_from_modification(raw.get("contact_ids"))

    if "operation" in raw:
        operation_raw = raw.get("operation")
        if operation_raw in (None, ""):
            normalized["operation"] = None
        else:
            operation_text = str(operation_raw).strip().lower()
            if operation_text not in {"create", "update"}:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid event modification 'operation': {operation_raw}",
                )
            normalized["operation"] = operation_text

    if "existing_event_id" in raw:
        existing_raw = raw.get("existing_event_id")
        if existing_raw in (None, ""):
            normalized["existing_event_id"] = None
        else:
            normalized["existing_event_id"] = str(existing_raw).strip()

    group_confirmations = raw.get("group_confirmations")
    if isinstance(group_confirmations, dict):
        normalized["group_confirmations"] = {
            str(key): bool(value) for key, value in group_confirmations.items() if str(key).strip()
        }

    confirmed_relationships = raw.get("confirmed_relationships")
    if isinstance(confirmed_relationships, list):
        normalized["confirmed_relationships"] = [
            relationship
            for relationship in confirmed_relationships
            if isinstance(relationship, dict)
        ]

    return normalized


def _is_offset_aware(value: datetime) -> bool:
    tzinfo = value.tzinfo
    return tzinfo is not None and tzinfo.utcoffset(value) is not None


def _align_datetime_awareness(
    start_when: datetime,
    end_when: datetime | None,
) -> tuple[datetime, datetime | None]:
    """Ensure datetimes have compatible offset-awareness for comparisons/storage."""
    if end_when is None:
        return start_when, None

    start_is_aware = _is_offset_aware(start_when)
    end_is_aware = _is_offset_aware(end_when)

    if start_is_aware == end_is_aware:
        return start_when, end_when
    if start_is_aware:
        return start_when, end_when.replace(tzinfo=start_when.tzinfo)
    return start_when.replace(tzinfo=end_when.tzinfo), end_when


def _safe_entity_slug(raw: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (raw or "").strip().lower())
    return normalized.strip("-")


def _safe_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _persist_event_resolved(preview_id: str, status: str) -> None:
    """Best-effort: stamp command resolution metadata on the DB message."""
    try:
        msg_id = conversations.find_message_id_by_metadata_preview(preview_id)
        if msg_id is not None:
            conversations.set_message_metadata_field(msg_id, "event_resolved", status)
            label = {
                "created": "Event created",
                "updated": "Event updated",
                "cancelled": "Event cancelled",
            }.get(status, "Event updated")
            conversations.set_message_metadata_field(
                msg_id,
                "command_resolved",
                {"status": status, "label": label},
            )
    except Exception as exc:
        logger.warning(
            "[event_confirm] Could not persist command_resolved=%s for %s: %s",
            status,
            preview_id,
            exc,
        )


def _get_command_media_attachments(command_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(command_data, dict):
        return []
    attachments = command_data.get("media_attachments")
    if not isinstance(attachments, list):
        return []
    return [attachment for attachment in attachments if isinstance(attachment, dict)]


def _delete_command_media_attachments(command_data: dict[str, Any] | None) -> None:
    delete_staged_chat_media_attachments(_get_command_media_attachments(command_data))


def confirm_event_command(
    payload: EventCommandConfirmation,
    user_email: str,
) -> EventCommandResult:
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    if not payload.confirmed:
        existing_command_data = _get_event_command_data(payload.preview_id, user_email)
        _delete_command_media_attachments(existing_command_data)
        delete_command_data(payload.preview_id)
        clear_pending_event_by_preview_id(payload.preview_id)
        _persist_event_resolved(payload.preview_id, "cancelled")
        return EventCommandResult(
            success=False,
            error="Event creation cancelled by user",
        )

    command_data = _get_event_command_data(payload.preview_id, user_email)
    if not command_data:
        raise HTTPException(
            status_code=404,
            detail="Event preview not found or expired. Please try the /event command again.",
        )

    extracted = command_data["extracted"]
    resolution = command_data["resolution"]
    media_attachments = _get_command_media_attachments(command_data)
    command_timezone = event_timezone_from_context(command_data)
    normalized_modifications = _normalize_event_modifications(
        payload.modifications,
        default_tz=command_timezone,
    )
    group_confirmations = payload.group_confirmations or {}
    if not group_confirmations:
        raw_group_confirmations = normalized_modifications.get("group_confirmations")
        if isinstance(raw_group_confirmations, dict):
            group_confirmations = {
                str(key): bool(value) for key, value in raw_group_confirmations.items()
            }

    logger.info(
        "[event_confirm] preview_id=%s, raw_modifications=%s",
        payload.preview_id,
        payload.modifications,
    )
    logger.info(
        "[event_confirm] resolution_contacts=%d, new_entity_contacts=%d, "
        "normalized_modification_keys=%s",
        len(resolution.get("contacts", [])),
        len(resolution.get("new_entities", {}).get("contacts", [])),
        list(normalized_modifications.keys()),
    )

    if "title" in normalized_modifications:
        extracted["title"] = normalized_modifications["title"]
    if "summary" in normalized_modifications:
        extracted["summary"] = normalized_modifications["summary"]
    if "when" in normalized_modifications:
        extracted["when"] = normalized_modifications["when"]
    if "end_when" in normalized_modifications:
        extracted["end_when"] = normalized_modifications["end_when"]
    if "where" in normalized_modifications:
        extracted["where"] = normalized_modifications["where"]
    if "tags" in normalized_modifications:
        extracted["tags"] = normalized_modifications["tags"]
    if "types" in normalized_modifications:
        extracted["types"] = normalized_modifications["types"]

    # Resolve the final create-vs-update decision. The handler stamps an initial
    # operation on the preview, but the user can override in the editor (e.g.
    # "create new instead" or "pick a different candidate").
    operation = str(command_data.get("operation") or "create").strip().lower()
    existing_event_id = str(command_data.get("existing_event_id") or "").strip() or None
    if "operation" in normalized_modifications:
        override_operation = normalized_modifications.get("operation")
        if override_operation in ("create", "update"):
            operation = override_operation
    if "existing_event_id" in normalized_modifications:
        existing_event_id = normalized_modifications.get("existing_event_id") or None
    if operation == "update" and not existing_event_id:
        raise HTTPException(
            status_code=400,
            detail="Event update requested without an existing event id.",
        )
    if operation == "update":
        existing_event = events_service.get_event_by_id(existing_event_id)
        if not existing_event:
            raise HTTPException(
                status_code=404,
                detail="The event being updated no longer exists.",
            )

    participant_override_enabled = "contact_ids" in normalized_modifications
    participant_override_ids = normalized_modifications.get("contact_ids", [])
    participant_override_id_set: set[str] = set()

    try:
        created_contacts = []
        contact_id_map = {}
        created_groups = []
        attached_photos = []
        photo_errors = []

        # When participant override is enabled, the client sends the full list
        # of desired contact IDs.  IDs prefixed with ``new:`` are placeholder
        # tokens for contacts that haven't been created yet – the display_name
        # is encoded after the prefix.  We need to:
        #   1. Create those new contacts (matching them against new_entities
        #      from the original resolution where possible).
        #   2. Replace the placeholder IDs with real IDs in the override list.
        if participant_override_enabled:
            new_entity_lookup: dict[str, dict] = {}
            for entity in resolution.get("new_entities", {}).get("contacts", []):
                name = (entity.get("display_name") or "").strip()
                if name:
                    new_entity_lookup[name] = entity

            resolved_override_ids: list[str] = []
            for oid in participant_override_ids:
                if oid.startswith("new:"):
                    placeholder_name = oid[4:]
                    entity = new_entity_lookup.get(placeholder_name, {})
                    display_name = entity.get("display_name") or placeholder_name
                    inferred_profession = entity.get("inferred_profession")
                    comments = entity.get("comments")
                    if inferred_profession:
                        has_profession = bool(comments) and re.search(
                            r"\bprofession\b", comments, re.IGNORECASE
                        )
                        has_match = bool(comments) and re.search(
                            rf"\b{re.escape(inferred_profession)}\b",
                            comments,
                            re.IGNORECASE,
                        )
                        if not has_profession and not has_match:
                            profession_line = f"Profession: {inferred_profession}"
                            comments = (
                                f"{comments}\n\n{profession_line}".strip()
                                if comments
                                else profession_line
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
                    created_contacts.append(
                        {"contact_id": contact_id, "display_name": display_name}
                    )
                    contact_id_map[display_name] = contact_id
                    resolved_override_ids.append(contact_id)
                    logger.info(
                        "[event_confirm] Created new contact from override placeholder: %s -> %s",
                        placeholder_name,
                        contact_id,
                    )
                else:
                    resolved_override_ids.append(oid)
            participant_override_ids = resolved_override_ids
            participant_override_id_set = set(participant_override_ids)
            new_contacts_to_create: list[dict] = []
        else:
            new_contacts_to_create = list(resolution.get("new_entities", {}).get("contacts", []))

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

        confirmed_relationships_present = "confirmed_relationships" in normalized_modifications
        if confirmed_relationships_present:
            confirmed_relationships = normalized_modifications.get("confirmed_relationships") or []
        else:
            confirmed_relationships = command_data.get("relationship_suggestions") or []

        if confirmed_relationships:
            existing_contact_map = {
                contact["display_name"]: contact["contact_id"]
                for contact in resolution.get("contacts", [])
                if contact.get("display_name") and contact.get("contact_id")
                and (
                    not participant_override_enabled
                    or str(contact.get("contact_id") or "").strip() in participant_override_id_set
                )
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
        where = extracted.get("where")
        where_text_for_resolution_places = str(where or "").strip()
        where_was_modified = "where" in normalized_modifications
        explicit_place_id = normalized_modifications.get("place_id")
        selected_where_normalized = (
            normalize_search_text(where_text_for_resolution_places)
            if where_text_for_resolution_places
            else ""
        )

        for new_place in resolution.get("new_entities", {}).get("places", []):
            place_name = str(new_place.get("name") or "").strip()
            if not place_name:
                continue

            # If the user explicitly selected a place, or edited the "where"
            # field, don't blindly create all originally proposed places.
            # This prevents stale preview candidates from being persisted.
            if "place_id" in normalized_modifications:
                continue
            if where_was_modified:
                if not selected_where_normalized:
                    continue
                if normalize_search_text(place_name) != selected_where_normalized:
                    continue

            existing_place_id = str(new_place.get("existing_place_id") or "").strip()
            if existing_place_id:
                place_id_map[place_name] = existing_place_id
                continue

            place_slug = _safe_entity_slug(place_name) or "place"
            place_id = f"plc_{place_slug}_{uuid4().hex[:6]}"

            place_in = PlaceIn(
                place_id=place_id,
                name=place_name,
                aliases=[str(new_place.get("query") or "").strip()]
                if str(new_place.get("query") or "").strip()
                and str(new_place.get("query") or "").strip().casefold() != place_name.casefold()
                else [],
                address=str(new_place.get("address") or "").strip() or None,
                city=str(new_place.get("city") or "").strip() or None,
                country=str(new_place.get("country") or "").strip() or None,
                lat=_safe_optional_float(new_place.get("lat")),
                lon=_safe_optional_float(new_place.get("lon")),
                geohash=None,
            )

            places_service.ingest_place(place_in)
            created_places.append({"place_id": place_id, "name": place_name})
            place_id_map[place_name] = place_id

        if participant_override_enabled:
            all_contact_ids = list(participant_override_ids)
            # Defensive: if override produced zero contacts but the original
            # resolution had contacts, fall back to the non-override path so
            # we don't silently drop everyone.
            if not all_contact_ids and (resolution.get("contacts") or created_contacts):
                logger.warning(
                    "[event_confirm] Participant override produced 0 contacts "
                    "but resolution had %d + %d created — falling back to "
                    "non-override path",
                    len(resolution.get("contacts", [])),
                    len(created_contacts),
                )
                all_contact_ids = []
                for existing_contact in resolution["contacts"]:
                    all_contact_ids.append(existing_contact["contact_id"])
                for created_contact in created_contacts:
                    all_contact_ids.append(created_contact["contact_id"])
        else:
            all_contact_ids = []
            for existing_contact in resolution["contacts"]:
                all_contact_ids.append(existing_contact["contact_id"])
            for created_contact in created_contacts:
                all_contact_ids.append(created_contact["contact_id"])

        logger.info(
            "[event_confirm] Participant override=%s, contact_ids=%d, "
            "resolution_contacts=%d, created_contacts=%d",
            participant_override_enabled,
            len(all_contact_ids),
            len(resolution.get("contacts", [])),
            len(created_contacts),
        )

        place_id = None
        matched_place = resolution.get("matched_place") if isinstance(resolution, dict) else None

        if "place_id" in normalized_modifications:
            if explicit_place_id:
                if places_service.get_place(explicit_place_id) is None:
                    raise HTTPException(status_code=400, detail="Selected place was not found.")
                place_id = explicit_place_id
            else:
                place_id = None

        if where:
            place_id = place_id_map.get(where)
        if place_id is None and isinstance(matched_place, dict):
            matched_place_id = str(matched_place.get("place_id") or "").strip()
            if matched_place_id:
                place_id = matched_place_id

        if "place_id" in normalized_modifications:
            place_id = explicit_place_id or None

        if place_id is None and where_was_modified:
            where_text = str(where or "").strip()
            if where_text:
                best_match = places_service.find_best_place_match(
                    where_text,
                    fuzzy_threshold=98,
                )
                matched_place_id = str((best_match or {}).get("place_id") or "").strip()
                matched_place_name = str((best_match or {}).get("name") or "").strip()
                if matched_place_id:
                    place_id = matched_place_id
                    if (
                        matched_place_name
                        and normalize_search_text(where_text)
                        != normalize_search_text(matched_place_name)
                    ):
                        places_service.add_place_alias(matched_place_id, where_text)
                else:
                    place_slug = _safe_entity_slug(where_text) or "place"
                    place_id = f"plc_{place_slug}_{uuid4().hex[:6]}"
                    place_in = PlaceIn(
                        place_id=place_id,
                        name=where_text,
                        aliases=[],
                        address=None,
                        city=None,
                        country=None,
                        lat=None,
                        lon=None,
                        geohash=None,
                    )
                    places_service.ingest_place(place_in)
                    created_places.append({"place_id": place_id, "name": where_text})
                    place_id_map[where_text] = place_id
                    logger.info(
                        "[event_confirm] Created new place from edited where: %s -> %s",
                        where_text,
                        place_id,
                    )

        if isinstance(matched_place, dict):
            alias_to_add = str(matched_place.get("pending_alias") or "").strip()
            if place_id and alias_to_add:
                places_service.add_place_alias(place_id, alias_to_add)

        pending_contact_place_link = (
            resolution.get("pending_contact_place_link") if isinstance(resolution, dict) else None
        )
        if place_id and isinstance(pending_contact_place_link, dict):
            contact_id = str(pending_contact_place_link.get("contact_id") or "").strip()
            if contact_id:
                logger.info(
                    "[event_confirm] Upserting contact-place link contact_id=%s place_id=%s role=%s",
                    contact_id,
                    place_id,
                    str(pending_contact_place_link.get("role") or "").strip() or None,
                )
                places_service.upsert_contact_place(
                    contact_id=contact_id,
                    place_id=place_id,
                    role=str(pending_contact_place_link.get("role") or "").strip() or None,
                    source=str(pending_contact_place_link.get("source") or "event_inference"),
                    confidence=str(pending_contact_place_link.get("confidence") or "high"),
                )

        is_update = operation == "update" and existing_event_id
        event_id = existing_event_id if is_update else f"event:{uuid4().hex}"
        when = extracted.get("when")
        end_when = extracted.get("end_when")
        if when:
            start_when = when
        elif isinstance(end_when, datetime) and _is_offset_aware(end_when):
            start_when = datetime.now(tz=end_when.tzinfo)
        else:
            start_when = datetime.now()

        start_when, end_when = _align_datetime_awareness(start_when, end_when)

        logger.info(
            "[event_confirm] Final event payload operation=%s place_id=%s start=%s end=%s tags=%d types=%d",
            operation,
            place_id,
            start_when,
            end_when,
            len(extracted.get("tags", [])),
            len(extracted.get("types", [])),
        )

        if end_when and end_when < start_when:
            raise HTTPException(
                status_code=400,
                detail="Event end date/time must be after the start date/time.",
            )

        if is_update:
            logger.info(
                "[event_confirm] Updating event %s with %d people: %s",
                event_id,
                len(all_contact_ids),
                all_contact_ids[:10],
            )
            events_service.update_event(
                event_id,
                {
                    "start_date": start_when,
                    "end_date": end_when,
                    "place_id": place_id,
                    "people": all_contact_ids,
                    "tags": extracted.get("tags", []),
                    "types": extracted.get("types", ["generic"]),
                    "title": extracted.get("title", ""),
                    "summary": extracted.get("summary", ""),
                    "raw": {
                        "source": "event_command",
                        "operation": "update",
                        "original_message": command_data.get("original_message"),
                        "inferred_location": resolution.get("inferred_location"),
                    },
                },
            )
        else:
            event_in = EventIn(
                id=event_id,
                startDate=start_when,
                endDate=end_when,
                placeId=place_id,
                people=all_contact_ids,
                tags=extracted.get("tags", []),
                types=extracted.get("types", ["generic"]),
                title=extracted.get("title", ""),
                summary=extracted.get("summary", ""),
                raw={
                    "source": "event_command",
                    "inferred_location": resolution.get("inferred_location"),
                },
            )

            logger.info(
                "[event_confirm] Creating event %s with %d people: %s",
                event_id,
                len(all_contact_ids),
                all_contact_ids[:10],  # Log first 10 to avoid huge log lines
            )
            events_service.ingest_event(event_in)

        for media_attachment in media_attachments:
            try:
                image_bytes, attachment_meta = load_staged_chat_media_attachment(media_attachment)
                attached_photo = event_photos_service.attach_event_photo(
                    event_id,
                    image_bytes=image_bytes,
                    filename=str(attachment_meta.get("file_name") or "event-photo.jpg"),
                    mime_type=str(attachment_meta.get("mime_type") or "").strip() or None,
                    captured_at=attachment_meta.get("captured_at"),
                    local_asset_id=str(attachment_meta.get("local_asset_id") or "").strip() or None,
                    source=str(attachment_meta.get("source") or "chat_event_command").strip()
                    or "chat_event_command",
                )
                attached_photos.append(attached_photo)
            except Exception as exc:
                file_name = str(media_attachment.get("file_name") or "photo").strip() or "photo"
                logger.warning(
                    "[event_confirm] Failed to attach staged photo %s to %s: %s",
                    file_name,
                    event_id,
                    exc,
                    exc_info=exc,
                )
                photo_errors.append(f"{file_name}: {exc}")

        for proposed_group in resolution.get("proposed_contact_groups", []):
            if not isinstance(proposed_group, dict):
                continue
            group_name = str(proposed_group.get("name") or "").strip()
            if not group_name:
                continue
            is_deterministic = (
                str(proposed_group.get("source") or "").strip().lower() == "deterministic"
            )
            should_persist = bool(proposed_group.get("confirmed", False))
            if group_name in group_confirmations:
                should_persist = bool(group_confirmations.get(group_name))
            elif is_deterministic:
                should_persist = True
            if not should_persist:
                continue
            member_contact_ids = [
                str(contact_id or "").strip()
                for contact_id in (proposed_group.get("contact_ids") or [])
                if str(contact_id or "").strip()
            ]
            if participant_override_enabled:
                member_contact_ids = [
                    contact_id
                    for contact_id in member_contact_ids
                    if contact_id in participant_override_id_set
                ]
            if not member_contact_ids:
                continue
            created_group = contact_groups_service.upsert_group_from_selector(
                user_email=user_email,
                name=group_name,
                member_contact_ids=member_contact_ids,
                aliases=[
                    str(alias).strip()
                    for alias in (proposed_group.get("aliases") or [])
                    if str(alias).strip()
                ],
                description=str(proposed_group.get("description") or "").strip() or None,
                source=str(proposed_group.get("source") or "inferred"),
                confirmed=bool(proposed_group.get("confirmed", False)),
                replace_members=bool(proposed_group.get("replace_members", True)),
                added_via=str(proposed_group.get("added_via") or "selector_group"),
                confidence=0.75,
            )
            if created_group:
                created_groups.append(created_group)

        _delete_command_media_attachments(command_data)
        delete_command_data(payload.preview_id)
        clear_pending_event_by_preview_id(payload.preview_id)
        _persist_event_resolved(payload.preview_id, "updated" if is_update else "created")

        return EventCommandResult(
            success=True,
            event_id=event_id,
            created_contacts=created_contacts,
            created_places=created_places,
            created_groups=created_groups,
            attached_photos=attached_photos,
            photo_errors=photo_errors,
            operation="update" if is_update else "create",
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[event_confirm] Failed to create event: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to create event: {exc!s}")
