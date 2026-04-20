"""Contact-command confirmation helpers."""

from __future__ import annotations

import copy
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

import contacts as contacts_service
import conversations
import places as places_service
from observability.logger import get_runtime_logger
from schemas import (
    ContactCommandConfirmation,
    ContactCommandResult,
    ContactIn,
    ContactRelationshipIn,
    PlaceIn,
)

from .event import _safe_entity_slug
from .storage import clear_pending_event_by_preview_id, delete_command_data, get_command_data

logger = get_runtime_logger(__name__)
_CONTACT_LIST_FIELDS = ("aliases", "emails", "phones", "links", "tags")


def _relationship_label(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    return " ".join(
        "-".join(segment.capitalize() for segment in part.replace("_", "-").split("-") if segment)
        for part in text.split()
        if part
    )


def _persist_contact_resolved(preview_id: str, status: str) -> None:
    try:
        msg_id = conversations.find_message_id_by_metadata_preview(preview_id)
        if msg_id is not None:
            conversations.set_message_metadata_field(msg_id, "contact_resolved", status)
    except Exception as exc:
        logger.warning(
            "[contact_confirm] Could not persist contact_resolved=%s for %s: %s",
            status,
            preview_id,
            exc,
        )


def _string_list(raw_value: Any) -> list[str]:
    if isinstance(raw_value, list):
        values = raw_value
    else:
        text = str(raw_value or "").strip()
        values = [part.strip() for part in text.replace(";", ",").split(",")] if text else []
    output: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _relationship_exists(from_contact_id: str, to_contact_id: str, relationship_type: str) -> bool:
    contact = contacts_service.get_contact(from_contact_id)
    if not contact:
        return False
    related_types = set(contacts_service.find_related_types(relationship_type))
    for rel in contact.get("relationships") or []:
        if str(rel.get("contact_id") or "") != to_contact_id:
            continue
        rel_type = str(rel.get("type") or "").strip().lower()
        other_type = str(rel.get("other_type") or "").strip().lower()
        if rel_type in related_types or other_type in related_types:
            return True
    return False


def _merge_existing_with_proposal(existing: dict[str, Any] | None, merged: dict[str, Any]) -> ContactIn:
    current = dict(existing or {})
    display_name = str(merged.get("display_name") or current.get("display_name") or "").strip()
    return ContactIn(
        contact_id=str(merged.get("contact_id") or current.get("contact_id") or "").strip(),
        display_name=display_name,
        aliases=_string_list(merged.get("aliases") or current.get("aliases") or []),
        birthday=merged.get("birthday") or current.get("birthday"),
        emails=_string_list(merged.get("emails") or current.get("emails") or []),
        phones=_string_list(merged.get("phones") or current.get("phones") or []),
        links=_string_list(merged.get("links") or current.get("links") or []),
        tags=_string_list(merged.get("tags") or current.get("tags") or []),
        comments=str(merged.get("comments") or current.get("comments") or "").strip() or None,
        external_id=current.get("external_id"),
    )


def _ensure_contact_reference(
    ref: str,
    proposal_contacts: list[dict[str, Any]],
    reference_map: dict[str, str],
) -> str:
    normalized = str(ref or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Missing contact reference in proposal.")
    if normalized in reference_map:
        return reference_map[normalized]
    if not normalized.startswith("new_contact:"):
        reference_map[normalized] = normalized
        return normalized

    create_item = next(
        (
            item
            for item in proposal_contacts
            if str(item.get("reference") or "").strip() == normalized
            and str(item.get("operation") or "").strip() == "create"
        ),
        None,
    )
    update_item = next(
        (
            item
            for item in proposal_contacts
            if str(item.get("reference") or "").strip() == normalized
            and str(item.get("operation") or "").strip() == "update"
        ),
        None,
    )
    display_name = str(
        ((update_item or {}).get("merged") or {}).get("display_name")
        or (create_item or {}).get("display_name")
        or (update_item or {}).get("display_name")
        or ""
    ).strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="Contact proposal is missing display_name.")
    contact_id = f"contact:{_safe_entity_slug(display_name) or 'contact'}-{uuid4().hex[:6]}"
    seed = {
        "contact_id": contact_id,
        "display_name": display_name,
        "aliases": [],
        "birthday": None,
        "emails": [],
        "phones": [],
        "links": [],
        "tags": [],
        "comments": None,
    }
    if isinstance((update_item or {}).get("merged"), dict):
        seed.update((update_item or {}).get("merged") or {})
        seed["contact_id"] = contact_id
        seed["display_name"] = display_name
    contacts_service.ingest_contact(_merge_existing_with_proposal(None, seed))
    reference_map[normalized] = contact_id
    return contact_id


def _ensure_place_reference(
    ref: str,
    proposal_places: list[dict[str, Any]],
    reference_map: dict[str, str],
) -> str:
    normalized = str(ref or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Missing place reference in proposal.")
    if normalized in reference_map:
        return reference_map[normalized]
    if not normalized.startswith("new_place:"):
        reference_map[normalized] = normalized
        return normalized

    match = next(
        (
            item
            for item in proposal_places
            if str(item.get("reference") or "").strip() == normalized
            and str(item.get("operation") or "").strip() == "create"
        ),
        None,
    )
    if not match:
        raise HTTPException(status_code=400, detail="Place proposal reference not found.")

    place_name = str(match.get("name") or "").strip()
    place_id = f"plc_{_safe_entity_slug(place_name) or 'place'}_{uuid4().hex[:6]}"
    place_in = PlaceIn(
        place_id=place_id,
        name=place_name,
        aliases=[],
        address=str(match.get("address") or "").strip() or None,
        city=None,
        country=None,
        lat=None,
        lon=None,
        geohash=None,
    )
    places_service.ingest_place(place_in)
    reference_map[normalized] = place_id
    return place_id


def _upsert_contact_update_if_needed(
    proposal_contact: dict[str, Any],
    proposal_contacts: list[dict[str, Any]],
    reference_map: dict[str, str],
    updated_contact_ids: list[str],
) -> None:
    if str(proposal_contact.get("operation") or "") != "update":
        return
    reference = str(proposal_contact.get("reference") or "").strip()
    actual_contact_id = _ensure_contact_reference(reference, proposal_contacts, reference_map)
    existing = contacts_service.get_contact(actual_contact_id)
    merged_raw = proposal_contact.get("merged")
    merged = dict(merged_raw) if isinstance(merged_raw, dict) else {}
    merged["contact_id"] = actual_contact_id
    contact_in = _merge_existing_with_proposal(existing, merged)
    contacts_service.ingest_contact(contact_in)
    if actual_contact_id not in updated_contact_ids:
        updated_contact_ids.append(actual_contact_id)


def _apply_modifications_to_proposal(
    proposal: dict[str, Any],
    modifications: dict[str, Any] | None,
) -> dict[str, Any]:
    updated = copy.deepcopy(proposal)
    mods = modifications if isinstance(modifications, dict) else {}
    if not mods:
        return updated

    edit_context_raw = updated.get("edit_context")
    edit_context = dict(edit_context_raw) if isinstance(edit_context_raw, dict) else {}
    main_ref = str(edit_context.get("main_contact_reference") or "").strip()
    related_ref = str(edit_context.get("related_contact_reference") or "").strip()
    primary_relationship_id = str(edit_context.get("primary_relationship_id") or "").strip()
    place_reference = str(edit_context.get("place_reference") or "").strip()

    def _find_update(reference: str) -> dict[str, Any] | None:
        for item in updated.get("contacts") or []:
            if str(item.get("reference") or "") == reference and str(item.get("operation") or "") == "update":
                return item
        return None

    def _find_create(reference: str) -> dict[str, Any] | None:
        for item in updated.get("contacts") or []:
            if str(item.get("reference") or "") == reference and str(item.get("operation") or "") == "create":
                return item
        return None

    def _ensure_update(reference: str, display_name: str) -> dict[str, Any]:
        existing = _find_update(reference)
        if existing:
            return existing
        merged = {
            "contact_id": reference if not reference.startswith("new_contact:") else "",
            "display_name": display_name,
            "aliases": [],
            "birthday": None,
            "emails": [],
            "phones": [],
            "links": [],
            "tags": [],
            "comments": None,
            "external_id": None,
        }
        item = {
            "proposal_id": f"contact_update:{uuid4().hex[:8]}",
            "source": "explicit",
            "operation": "update",
            "reference": reference,
            "display_name": display_name,
            "fields": {},
            "merged": merged,
        }
        updated.setdefault("contacts", []).append(item)
        return item

    def _apply_contact_mods(
        reference: str,
        display_name_override: str | None = None,
        *,
        apply_detail_fields: bool = True,
    ) -> None:
        if not reference:
            return
        create_item = _find_create(reference)
        display_name = display_name_override or str((create_item or {}).get("display_name") or "").strip()
        update_item = _ensure_update(reference, display_name)
        merged_raw = update_item.get("merged")
        merged = dict(merged_raw) if isinstance(merged_raw, dict) else {}
        if display_name_override:
            if create_item:
                create_item["display_name"] = display_name_override
            update_item["display_name"] = display_name_override
            merged["display_name"] = display_name_override
        if apply_detail_fields:
            for field_name in _CONTACT_LIST_FIELDS:
                if field_name in mods:
                    merged[field_name] = _string_list(mods.get(field_name))
            if "birth_date" in mods:
                merged["birthday"] = str(mods.get("birth_date") or "").strip() or None
            if "comments" in mods:
                merged["comments"] = str(mods.get("comments") or "").strip() or None
        update_item["merged"] = merged

    if main_ref:
        main_display_name = str(mods.get("main_display_name") or "").strip() or None
        _apply_contact_mods(main_ref, main_display_name, apply_detail_fields=True)
        if main_display_name:
            for rel in updated.get("relationships") or []:
                if str(rel.get("from_reference") or "") == main_ref:
                    rel["from_display_name"] = main_display_name
            for link in updated.get("contact_place_links") or []:
                if str(link.get("contact_reference") or "") == main_ref:
                    link["contact_display_name"] = main_display_name

    if related_ref:
        related_display_name = str(mods.get("related_display_name") or "").strip() or None
        if related_display_name:
            _apply_contact_mods(related_ref, related_display_name, apply_detail_fields=False)
            for rel in updated.get("relationships") or []:
                if str(rel.get("to_reference") or "") == related_ref:
                    rel["to_display_name"] = related_display_name
            for rel in updated.get("derived_relationships") or []:
                if str(rel.get("to_reference") or "") == related_ref:
                    rel["to_display_name"] = related_display_name

    if primary_relationship_id and "relationship_type" in mods:
        next_type = _relationship_label(mods.get("relationship_type"))
        for rel in updated.get("relationships") or []:
            if str(rel.get("proposal_id") or "") == primary_relationship_id and next_type:
                rel["relationship_type"] = next_type

    if place_reference:
        next_place_name = str(mods.get("place_name") or "").strip()
        next_place_role = str(mods.get("place_role") or "").strip()
        for place in updated.get("places") or []:
            if str(place.get("reference") or "") == place_reference and next_place_name:
                place["name"] = next_place_name
                place["address"] = next_place_name
        for link in updated.get("contact_place_links") or []:
            if str(link.get("place_reference") or "") == place_reference:
                if next_place_name:
                    link["place_name"] = next_place_name
                if next_place_role:
                    link["role"] = next_place_role

    contact_updates_mods = mods.get("contacts") if isinstance(mods.get("contacts"), list) else []
    if contact_updates_mods:
        contacts_by_id = {
            str(item.get("proposal_id") or "").strip(): item
            for item in updated.get("contacts") or []
            if isinstance(item, dict) and str(item.get("proposal_id") or "").strip()
        }
        for raw_mod in contact_updates_mods:
            if not isinstance(raw_mod, dict):
                continue
            proposal_id = str(raw_mod.get("proposal_id") or "").strip()
            proposal_contact = contacts_by_id.get(proposal_id)
            if not proposal_contact:
                continue
            new_reference = str(raw_mod.get("reference") or "").strip()
            if new_reference:
                proposal_contact["reference"] = new_reference
                if not new_reference.startswith("new_contact:"):
                    proposal_contact["operation"] = "update"
            reference = str(proposal_contact.get("reference") or "").strip()
            display_name_override = str(raw_mod.get("display_name") or "").strip() or None
            _apply_contact_mods(reference, display_name_override, apply_detail_fields=False)
            update_item = _find_update(reference)
            if not update_item:
                continue
            merged = update_item.get("merged") if isinstance(update_item.get("merged"), dict) else {}
            if "birth_date" in raw_mod:
                merged["birthday"] = str(raw_mod.get("birth_date") or "").strip() or None
            if "comments" in raw_mod:
                merged["comments"] = str(raw_mod.get("comments") or "").strip() or None
            for field_name in _CONTACT_LIST_FIELDS:
                if field_name in raw_mod:
                    merged[field_name] = _string_list(raw_mod.get(field_name))
            update_item["merged"] = merged
            if display_name_override:
                for rel in updated.get("relationships") or []:
                    if str(rel.get("from_reference") or "") == reference:
                        rel["from_display_name"] = display_name_override
                    if str(rel.get("to_reference") or "") == reference:
                        rel["to_display_name"] = display_name_override
                for rel in updated.get("derived_relationships") or []:
                    if str(rel.get("from_reference") or "") == reference:
                        rel["from_display_name"] = display_name_override
                    if str(rel.get("to_reference") or "") == reference:
                        rel["to_display_name"] = display_name_override
                for link in updated.get("contact_place_links") or []:
                    if str(link.get("contact_reference") or "") == reference:
                        link["contact_display_name"] = display_name_override

    relationship_mods = mods.get("relationships") if isinstance(mods.get("relationships"), list) else []
    if relationship_mods:
        explicit_by_id = {
            str(item.get("proposal_id") or "").strip(): item
            for item in updated.get("relationships") or []
            if isinstance(item, dict) and str(item.get("proposal_id") or "").strip()
        }
        derived_by_id = {
            str(item.get("proposal_id") or "").strip(): item
            for item in updated.get("derived_relationships") or []
            if isinstance(item, dict) and str(item.get("proposal_id") or "").strip()
        }
        kept_derived_ids: set[str] = set()
        for raw_mod in relationship_mods:
            if not isinstance(raw_mod, dict):
                continue
            proposal_id = str(raw_mod.get("proposal_id") or "").strip()
            relationship = explicit_by_id.get(proposal_id) or derived_by_id.get(proposal_id)
            if not relationship:
                continue
            from_reference = str(raw_mod.get("from_reference") or "").strip()
            to_reference = str(raw_mod.get("to_reference") or "").strip()
            if from_reference:
                relationship["from_reference"] = from_reference
            if to_reference:
                relationship["to_reference"] = to_reference
            enabled = raw_mod.get("enabled")
            if relationship in derived_by_id.values():
                if enabled is False:
                    continue
                kept_derived_ids.add(proposal_id)
            next_type = _relationship_label(raw_mod.get("relationship_type"))
            if next_type:
                relationship["relationship_type"] = next_type
            from_name = str(raw_mod.get("from_display_name") or "").strip()
            to_name = str(raw_mod.get("to_display_name") or "").strip()
            if from_name:
                relationship["from_display_name"] = from_name
            if to_name:
                relationship["to_display_name"] = to_name
        if derived_by_id:
            updated["derived_relationships"] = [
                rel
                for rel in updated.get("derived_relationships") or []
                if str(rel.get("proposal_id") or "").strip() in kept_derived_ids
                or str(rel.get("proposal_id") or "").strip() not in {str(item.get("proposal_id") or "").strip() for item in relationship_mods if isinstance(item, dict)}
            ]

    place_mods = mods.get("places") if isinstance(mods.get("places"), list) else []
    if place_mods:
        places_by_id = {
            str(item.get("proposal_id") or "").strip(): item
            for item in updated.get("places") or []
            if isinstance(item, dict) and str(item.get("proposal_id") or "").strip()
        }
        for raw_mod in place_mods:
            if not isinstance(raw_mod, dict):
                continue
            place = places_by_id.get(str(raw_mod.get("proposal_id") or "").strip())
            if not place:
                continue
            next_reference = str(raw_mod.get("reference") or "").strip()
            if next_reference:
                place["reference"] = next_reference
                if not next_reference.startswith("new_place:"):
                    place["operation"] = "existing"
            next_name = str(raw_mod.get("name") or "").strip()
            next_address = str(raw_mod.get("address") or "").strip()
            if next_name:
                place["name"] = next_name
            if next_address:
                place["address"] = next_address

    link_mods = mods.get("contact_place_links") if isinstance(mods.get("contact_place_links"), list) else []
    if link_mods:
        links_by_id = {
            str(item.get("proposal_id") or "").strip(): item
            for item in updated.get("contact_place_links") or []
            if isinstance(item, dict) and str(item.get("proposal_id") or "").strip()
        }
        for raw_mod in link_mods:
            if not isinstance(raw_mod, dict):
                continue
            link = links_by_id.get(str(raw_mod.get("proposal_id") or "").strip())
            if not link:
                continue
            next_contact_reference = str(raw_mod.get("contact_reference") or "").strip()
            next_contact_display_name = str(raw_mod.get("contact_display_name") or "").strip()
            next_place_reference = str(raw_mod.get("place_reference") or "").strip()
            if next_contact_reference:
                link["contact_reference"] = next_contact_reference
            if next_contact_display_name:
                link["contact_display_name"] = next_contact_display_name
            if next_place_reference:
                link["place_reference"] = next_place_reference
            next_role = str(raw_mod.get("role") or "").strip()
            next_place_name = str(raw_mod.get("place_name") or "").strip()
            if next_role:
                link["role"] = next_role
            if next_place_name:
                link["place_name"] = next_place_name
                for place in updated.get("places") or []:
                    if str(place.get("reference") or "") == str(link.get("place_reference") or ""):
                        place["name"] = next_place_name
                        if not str(place.get("address") or "").strip():
                            place["address"] = next_place_name

    derived_keep: list[dict[str, Any]] = []
    for rel in updated.get("derived_relationships") or []:
        rel_id = str(rel.get("proposal_id") or "").strip()
        decision = str(mods.get(f"derived_{rel_id}") or "yes").strip().lower()
        if decision != "no":
            derived_keep.append(rel)
    updated["derived_relationships"] = derived_keep
    return updated


def confirm_contact_command(
    payload: ContactCommandConfirmation,
    user_email: str,
) -> ContactCommandResult:
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    if not payload.confirmed:
        delete_command_data(payload.preview_id)
        clear_pending_event_by_preview_id(payload.preview_id)
        _persist_contact_resolved(payload.preview_id, "cancelled")
        return ContactCommandResult(success=False, error="Contact update cancelled by user")

    command_data = get_command_data(payload.preview_id)
    if not command_data:
        raise HTTPException(
            status_code=404,
            detail="Contact preview not found or expired. Please try the /contact command again.",
        )

    raw_proposal_data = command_data.get("proposal")
    raw_proposal = dict(raw_proposal_data) if isinstance(raw_proposal_data, dict) else {}
    proposal = _apply_modifications_to_proposal(raw_proposal, payload.modifications)
    proposal_contacts = list(proposal.get("contacts") or [])
    proposal_places = list(proposal.get("places") or [])
    proposal_relationships = list(proposal.get("relationships") or []) + list(
        proposal.get("derived_relationships") or []
    )
    proposal_contact_place_links = list(proposal.get("contact_place_links") or [])

    created_contact_ids: list[str] = []
    updated_contact_ids: list[str] = []
    created_place_ids: list[str] = []
    applied_relationship_ids: list[str] = []
    applied_contact_place_links: list[dict[str, str]] = []
    contact_reference_map: dict[str, str] = {}
    place_reference_map: dict[str, str] = {}

    try:
        for proposal_contact in proposal_contacts:
            if not isinstance(proposal_contact, dict):
                continue
            if str(proposal_contact.get("operation") or "") != "create":
                continue
            reference = str(proposal_contact.get("reference") or "").strip()
            if not reference.startswith("new_contact:"):
                contact_reference_map[reference] = reference
                continue
            contact_id = _ensure_contact_reference(reference, proposal_contacts, contact_reference_map)
            if contact_id not in created_contact_ids:
                created_contact_ids.append(contact_id)

        for proposal_contact in proposal_contacts:
            if not isinstance(proposal_contact, dict):
                continue
            _upsert_contact_update_if_needed(
                proposal_contact,
                proposal_contacts,
                contact_reference_map,
                updated_contact_ids,
            )

        for proposal_place in proposal_places:
            if not isinstance(proposal_place, dict):
                continue
            if str(proposal_place.get("operation") or "") != "create":
                continue
            reference = str(proposal_place.get("reference") or "").strip()
            if not reference.startswith("new_place:"):
                place_reference_map[reference] = reference
                continue
            actual_place_id = _ensure_place_reference(
                reference,
                proposal_places,
                place_reference_map,
            )
            if actual_place_id not in created_place_ids:
                created_place_ids.append(actual_place_id)

        for relationship in proposal_relationships:
            if not isinstance(relationship, dict):
                continue
            relationship_type = _relationship_label(relationship.get("relationship_type"))
            if not relationship_type:
                continue
            from_contact_id = _ensure_contact_reference(
                str(relationship.get("from_reference") or ""),
                proposal_contacts,
                contact_reference_map,
            )
            to_contact_id = _ensure_contact_reference(
                str(relationship.get("to_reference") or ""),
                proposal_contacts,
                contact_reference_map,
            )
            if _relationship_exists(from_contact_id, to_contact_id, relationship_type):
                continue
            rel_id = f"rel:{uuid4().hex}"
            contacts_service.upsert_contact_relationship(
                ContactRelationshipIn(
                    relationship_id=rel_id,
                    from_contact_id=from_contact_id,
                    to_contact_id=to_contact_id,
                    relationship_type=relationship_type,
                    reciprocal_type=_relationship_label(relationship.get("reciprocal_type")) or None,
                )
            )
            applied_relationship_ids.append(rel_id)

        for link in proposal_contact_place_links:
            if not isinstance(link, dict):
                continue
            contact_id = _ensure_contact_reference(
                str(link.get("contact_reference") or ""),
                proposal_contacts,
                contact_reference_map,
            )
            place_id = _ensure_place_reference(
                str(link.get("place_reference") or ""),
                proposal_places,
                place_reference_map,
            )
            role = str(link.get("role") or "").strip() or None
            places_service.upsert_contact_place(
                contact_id=contact_id,
                place_id=place_id,
                role=role,
                source="contact_command",
                confidence="high" if str(link.get("source") or "") != "derived" else "medium",
            )
            applied_contact_place_links.append(
                {"contact_id": contact_id, "place_id": place_id, "role": role or ""}
            )

        delete_command_data(payload.preview_id)
        clear_pending_event_by_preview_id(payload.preview_id)
        _persist_contact_resolved(payload.preview_id, "created")

        return ContactCommandResult(
            success=True,
            updated_contact_ids=updated_contact_ids,
            created_contact_ids=created_contact_ids,
            created_place_ids=created_place_ids,
            applied_relationship_ids=applied_relationship_ids,
            applied_contact_place_links=applied_contact_place_links,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[contact_confirm] Failed to apply contact changes: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to apply contact changes: {str(exc)}")
