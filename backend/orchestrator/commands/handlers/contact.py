"""Handler for the /contact command."""

from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any
from uuid import uuid4

import contacts as contacts_service
import places as places_service
from commands.parser import ParsedCommand
from commands.registry import CommandRegistry
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_text
from ui_dsl import (
    build_need_user_input,
    build_need_user_input_prompt_guidance,
    need_user_input_json_property_template,
    normalize_need_user_input,
)

logger = get_runtime_logger(__name__)

_CONTACT_CLARIFICATION_ACTION_PREFIX = "contact_clarification_submit"
_CONTACT_EDIT_ACTION_PREFIX = "contact_edit_submit"

_FIELD_KIND_MAP = {
    "birth_date": "date",
    "place_text": "short_text",
    "main_contact_name": "short_text",
    "related_contact_name": "short_text",
    "relationship_type": "short_text",
    "details": "textarea",
}

_RELATIONSHIP_RECIPROCALS = {
    "wife": "husband",
    "husband": "wife",
    "spouse": "spouse",
    "partner": "partner",
    "father": "child",
    "mother": "child",
    "parent": "child",
    "child": "parent",
    "son": "parent",
    "daughter": "parent",
    "brother": "sibling",
    "sister": "sibling",
    "sibling": "sibling",
    "grandfather": "grandchild",
    "grandmother": "grandchild",
    "grandparent": "grandchild",
    "grandchild": "grandparent",
}

_PARENT_TYPES = {"father", "mother", "parent"}
_SPOUSE_TYPES = {"spouse", "partner", "wife", "husband"}
_GRANDPARENT_MAP = {"father": "grandfather", "mother": "grandmother", "parent": "grandparent"}
_CONTACT_LIST_FIELDS = ("aliases", "emails", "phones", "links", "tags")
_YES_NO_OPTIONS = [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]


def _emit_progress(context: dict[str, Any], message: str) -> None:
    callback = context.get("progress_callback")
    if callable(callback):
        try:
            callback(message)
        except Exception:
            logger.debug("[contact_command] progress callback failed", exc_info=True)


def _extract_clarification_token(raw_args: str) -> tuple[str, str | None]:
    text = str(raw_args or "")
    match = re.search(r"\[clarification_id:([^\]]+)\]\s*$", text)
    if not match:
        return text.strip(), None
    cleaned = text[: match.start()].strip()
    return cleaned, match.group(1).strip() or None


def _extract_additional_details(text: str) -> tuple[str, str | None]:
    marker = "\n\nAdditional details:"
    if marker not in text:
        return text.strip(), None
    base, extra = text.split(marker, 1)
    return base.strip(), extra.strip() or None


def _safe_entity_slug(raw: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (raw or "").strip().lower())
    return normalized.strip("-")


def _clarification_action_id(clarification_id: str) -> str:
    if not clarification_id:
        return _CONTACT_CLARIFICATION_ACTION_PREFIX
    return f"{_CONTACT_CLARIFICATION_ACTION_PREFIX}:{clarification_id}"


def _edit_action_id(preview_id: str) -> str:
    if not preview_id:
        return _CONTACT_EDIT_ACTION_PREFIX
    return f"{_CONTACT_EDIT_ACTION_PREFIX}:{preview_id}"


def _clarification_fields(field_ids: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for field_id in field_ids:
        normalized = str(field_id or "").strip()
        if not normalized:
            continue
        output.append(
            {
                "id": normalized,
                "kind": _FIELD_KIND_MAP.get(normalized, "short_text"),
                "label": normalized.replace("_", " ").strip().title(),
                "required": True,
            }
        )
    return output or [{"id": "details", "kind": "textarea", "label": "Details", "required": True}]


def _llm_extract_contact_changes(message: str, *, user_email: str) -> dict[str, Any]:
    from llm_helpers import call_llm_json
    from prompts.context import get_self_context, get_user_facts_context
    from user_fact_rules import RuleScope

    need_user_input_guidance = build_need_user_input_prompt_guidance(exclude_people=False)
    need_user_input_template = need_user_input_json_property_template(indent=4)
    self_context = get_self_context(user_email) if user_email else None
    self_context_block = f"\n{self_context}\n" if self_context else ""
    user_facts_ctx = (
        get_user_facts_context(user_email, message, scope=RuleScope.CONTACT_RESOLUTION)
        if user_email
        else None
    )
    user_facts_block = f"\n{user_facts_ctx}\n" if user_facts_ctx else ""
    prompt = f"""You need to extract a structured proposal of information about contacts and their relationships.

{self_context_block}
{user_facts_block}
Current user message: \"{message}\"

Interpret one or more contact graph updates.

Supported updates:
- create or update contacts
- birth dates
- emails, phones, aliases, links, tags, profession, notes/comments
- relationship edges between contacts
- place creation or matching for a contact
- contact-place role such as home or work

If the statement is ambiguous, missing a required person, or contains an ambiguous numeric date, ask a clarification instead of guessing.

{need_user_input_guidance}

Return ONLY valid JSON in this exact format:
{{
{need_user_input_template}
    "contacts": [
        {{
            "contact_name": "name",
            "birthday": "YYYY-MM-DD or null",
            "comments": "comment or null",
            "profession": "profession or null",
            "aliases": ["alias"],
            "emails": ["email@example.com"],
            "phones": ["+351..."],
            "links": ["https://..."],
            "tags": ["tag"]
        }}
    ],
    "relationships": [
        {{
            "from_contact_name": "name",
            "to_contact_name": "name",
            "relationship_type": "relationship type"
        }}
    ],
    "contact_place_links": [
        {{
            "contact_name": "name",
            "place_text": "place or address",
            "place_role": "home/work/other or null"
        }}
    ],
    "summary": "one-sentence explanation"
}}
"""
    extracted = call_llm_json(prompt, timeout=45)
    extracted["need_user_input"] = normalize_need_user_input(extracted.get("need_user_input"))
    return extracted


def _string_list(raw_value: Any) -> list[str]:
    if isinstance(raw_value, list):
        items = raw_value
    else:
        text = str(raw_value or "").strip()
        if not text:
            return []
        items = re.split(r"[,\n;]+", text)
    values: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        key = normalize_search_text(text)
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
    return values


def _parse_birth_date(raw_value: str | None) -> tuple[str | None, str | None]:
    raw = str(raw_value or "").strip()
    if not raw:
        return None, None
    iso_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if iso_match:
        return raw, None
    slash_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if slash_match:
        first = int(slash_match.group(1))
        second = int(slash_match.group(2))
        year = int(slash_match.group(3))
        if first <= 12 and second <= 12:
            return None, "I found a birth date but the format is ambiguous. Please use YYYY-MM-DD or spell the month."
        parsed = date(year, second, first)
        return parsed.isoformat(), None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None, "I couldn't parse the birth date. Please use YYYY-MM-DD."
    return parsed.isoformat(), None


def _best_contact_matches(name: str) -> list[dict[str, Any]]:
    return contacts_service.search_contacts(name, fuzzy_threshold=85, limit=5)


def _resolve_contact_reference(name: str) -> dict[str, Any]:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        return {"status": "missing"}
    matches = _best_contact_matches(normalized_name)
    if not matches:
        return {
            "status": "new",
            "display_name": normalized_name,
            "temp_id": f"new_contact:{_safe_entity_slug(normalized_name) or 'contact'}",
        }
    top = matches[0]
    top_score = float(top.get("match_score") or 0)
    if len(matches) > 1:
        second_score = float(matches[1].get("match_score") or 0)
        if top_score < 98 and abs(top_score - second_score) <= 3:
            return {"status": "ambiguous", "query": normalized_name, "matches": matches[:3]}
    return {
        "status": "existing",
        "contact_id": str(top.get("contact_id") or ""),
        "display_name": str(top.get("display_name") or normalized_name),
        "match_score": top_score,
    }


def _normalize_relationship_type(value: str | None) -> str | None:
    normalized = normalize_search_text(value or "")
    if not normalized:
        return None
    alias_map = {
        "married": "spouse",
        "married to": "spouse",
        "wife": "wife",
        "husband": "husband",
        "spouse": "spouse",
        "partner": "partner",
        "father": "father",
        "mother": "mother",
        "parent": "parent",
        "son": "son",
        "daughter": "daughter",
        "child": "child",
        "brother": "brother",
        "sister": "sister",
        "sibling": "sibling",
    }
    return alias_map.get(normalized, normalized)


def _reciprocal_relationship_type(relationship_type: str) -> str | None:
    return _RELATIONSHIP_RECIPROCALS.get(relationship_type)


def _resolve_place(place_text: str | None) -> dict[str, Any] | None:
    if not str(place_text or "").strip():
        return None
    best_match = places_service.find_best_place_match(str(place_text), fuzzy_threshold=94)
    if best_match and float(best_match.get("match_score") or 0) >= 94:
        return {
            "status": "existing",
            "place_id": str(best_match.get("place_id") or ""),
            "name": str(best_match.get("name") or place_text),
            "address": str(best_match.get("address") or "").strip() or None,
        }
    return {
        "status": "new",
        "temp_id": f"new_place:{_safe_entity_slug(str(place_text)) or 'place'}",
        "name": str(place_text).strip(),
        "address": str(place_text).strip(),
    }


def _merge_list_values(existing_values: Any, updates: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in list(existing_values or []) + list(updates or []):
        text = str(item or "").strip()
        if not text:
            continue
        key = normalize_search_text(text)
        if key in seen:
            continue
        seen.add(key)
        merged.append(text)
    return merged


def _merge_comments(existing_comments: Any, updates: dict[str, Any]) -> str | None:
    parts: list[str] = []
    current = str(existing_comments or "").strip()
    if current:
        parts.append(current)
    comment = str(updates.get("comments") or "").strip()
    if comment and normalize_search_text(comment) not in normalize_search_text(current):
        parts.append(comment)
    profession = str(updates.get("profession") or "").strip()
    if profession:
        profession_line = f"Profession: {profession}"
        if normalize_search_text(profession_line) not in normalize_search_text("\n".join(parts)):
            parts.append(profession_line)
    combined = "\n\n".join(part for part in parts if part.strip()).strip()
    return combined or None


def _merge_contact_updates(existing: dict[str, Any] | None, updates: dict[str, Any]) -> dict[str, Any]:
    base = dict(existing or {})
    return {
        "contact_id": str(base.get("contact_id") or ""),
        "display_name": str(base.get("display_name") or updates.get("display_name") or "").strip(),
        "aliases": _merge_list_values(base.get("aliases"), updates.get("aliases")),
        "birthday": updates.get("birthday") or base.get("birthday"),
        "emails": _merge_list_values(base.get("emails"), updates.get("emails")),
        "phones": _merge_list_values(base.get("phones"), updates.get("phones")),
        "links": _merge_list_values(base.get("links"), updates.get("links")),
        "tags": _merge_list_values(base.get("tags"), updates.get("tags")),
        "comments": _merge_comments(base.get("comments"), updates),
        "external_id": base.get("external_id"),
    }


def _append_unique_change(lines: list[str], text: str) -> None:
    normalized = text.strip()
    if normalized and normalized not in lines:
        lines.append(normalized)


def _proposal_id(prefix: str) -> str:
    return f"{prefix}:{uuid4().hex[:8]}"


def _dict_items(raw_value: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_value, list):
        return []
    return [item for item in raw_value if isinstance(item, dict)]


def _contact_reference_value(ref: dict[str, Any] | None) -> str:
    if not ref:
        return ""
    return str(ref.get("contact_id") or ref.get("temp_id") or "").strip()


def _ensure_contact_create(proposal: dict[str, Any], ref: dict[str, Any]) -> None:
    if ref.get("status") != "new":
        return
    reference = _contact_reference_value(ref)
    if not reference:
        return
    for item in proposal["contacts"]:
        if str(item.get("reference") or "") == reference and str(item.get("operation") or "") == "create":
            return
    proposal["contacts"].append(
        {
            "proposal_id": _proposal_id("contact_create"),
            "source": "explicit",
            "operation": "create",
            "reference": reference,
            "display_name": ref.get("display_name"),
        }
    )
    _append_unique_change(proposal["explicit_change_lines"], f"Create contact: {ref.get('display_name')}")


def _ensure_place_create(proposal: dict[str, Any], ref: dict[str, Any]) -> None:
    if ref.get("status") != "new":
        return
    reference = str(ref.get("temp_id") or "").strip()
    if not reference:
        return
    for item in proposal["places"]:
        if str(item.get("reference") or "") == reference and str(item.get("operation") or "") == "create":
            return
    proposal["places"].append(
        {
            "proposal_id": _proposal_id("place_create"),
            "source": "explicit",
            "operation": "create",
            "reference": reference,
            "name": ref.get("name"),
            "address": ref.get("address"),
        }
    )
    _append_unique_change(proposal["explicit_change_lines"], f"Create place: {ref.get('name')}")


def _ensure_contact_update_entry(proposal: dict[str, Any], reference: str, display_name: str) -> dict[str, Any]:
    for item in proposal["contacts"]:
        if str(item.get("reference") or "") == reference and str(item.get("operation") or "") == "update":
            return item
    update_item = {
        "proposal_id": _proposal_id("contact_update"),
        "source": "explicit",
        "operation": "update",
        "reference": reference,
        "display_name": display_name,
        "fields": {},
        "merged": {
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
        },
    }
    proposal["contacts"].append(update_item)
    return update_item


def _apply_contact_fields_to_proposal(
    proposal: dict[str, Any],
    *,
    target_ref: dict[str, Any],
    updates: dict[str, Any],
    label_name: str,
) -> None:
    normalized_updates = {
        "display_name": target_ref.get("display_name"),
        "birthday": updates.get("birthday"),
        "comments": updates.get("comments"),
        "profession": updates.get("profession"),
        "aliases": _string_list(updates.get("aliases")),
        "emails": _string_list(updates.get("emails")),
        "phones": _string_list(updates.get("phones")),
        "links": _string_list(updates.get("links")),
        "tags": _string_list(updates.get("tags")),
    }
    meaningful = any(
        normalized_updates.get("birthday")
        or normalized_updates.get("comments")
        or normalized_updates.get("profession")
        or normalized_updates.get(field)
        for field in _CONTACT_LIST_FIELDS
    )
    if not meaningful:
        return

    target_contact_id = str(target_ref.get("contact_id") or "").strip()
    target_existing = None
    if target_contact_id:
        target_existing = contacts_service.get_contact(str(target_contact_id))
    target_reference = str(target_ref.get("contact_id") or target_ref.get("temp_id") or "").strip()
    update_item = _ensure_contact_update_entry(
        proposal,
        target_reference,
        str(target_ref.get("display_name") or label_name),
    )
    merged = _merge_contact_updates(target_existing or update_item.get("merged"), normalized_updates)
    update_item["display_name"] = str(target_ref.get("display_name") or label_name)
    update_item["fields"] = {k: v for k, v in normalized_updates.items() if v not in (None, [], "")}
    update_item["merged"] = merged

    if normalized_updates.get("birthday"):
        _append_unique_change(
            proposal["explicit_change_lines"],
            f"Set birthday for {label_name} to {normalized_updates['birthday']}",
        )
    if normalized_updates.get("comments"):
        _append_unique_change(
            proposal["explicit_change_lines"],
            f"Add note to {label_name}: {normalized_updates['comments']}",
        )
    if normalized_updates.get("profession"):
        _append_unique_change(
            proposal["explicit_change_lines"],
            f"Add profession for {label_name}: {normalized_updates['profession']}",
        )
    for field_name in _CONTACT_LIST_FIELDS:
        values = _string_list(normalized_updates.get(field_name))
        if values:
            _append_unique_change(
                proposal["explicit_change_lines"],
                f"Update {field_name} for {label_name}: {', '.join(values)}",
            )


def _append_relationship_to_proposal(
    proposal: dict[str, Any],
    *,
    from_ref: dict[str, Any],
    to_ref: dict[str, Any],
    relationship_type: str,
) -> dict[str, Any]:
    relationship = {
        "proposal_id": _proposal_id("relationship"),
        "source": "explicit",
        "from_reference": _contact_reference_value(from_ref),
        "from_display_name": from_ref.get("display_name"),
        "to_reference": _contact_reference_value(to_ref),
        "to_display_name": to_ref.get("display_name"),
        "relationship_type": relationship_type,
        "reciprocal_type": _reciprocal_relationship_type(relationship_type),
    }
    proposal["relationships"].append(relationship)
    _append_unique_change(
        proposal["explicit_change_lines"],
        f"Add relationship: {from_ref.get('display_name')} -> {relationship_type} -> {to_ref.get('display_name')}",
    )
    return relationship


def _append_parent_derived_relationships(
    proposal: dict[str, Any],
    *,
    parent_ref: dict[str, Any],
    child_ref: dict[str, Any],
    relationship_type: str,
) -> None:
    if relationship_type not in _PARENT_TYPES or not parent_ref.get("contact_id"):
        return
    child_reference = _contact_reference_value(child_ref)
    child_display_name = str(child_ref.get("display_name") or "").strip()
    parent_contact = contacts_service.get_contact(str(parent_ref.get("contact_id") or "")) or {}
    for rel in parent_contact.get("relationships") or []:
        rel_type = str(rel.get("type") or "").strip().lower()
        related_contact_id = str(rel.get("contact_id") or "").strip()
        if related_contact_id and rel_type in _SPOUSE_TYPES:
            spouse_contact = contacts_service.get_contact(related_contact_id)
            if spouse_contact:
                proposal["derived_relationships"].append(
                    {
                        "proposal_id": _proposal_id("derived_relationship"),
                        "source": "derived",
                        "reason": "co_parent_from_spouse",
                        "from_reference": spouse_contact.get("contact_id"),
                        "from_display_name": spouse_contact.get("display_name"),
                        "to_reference": child_reference,
                        "to_display_name": child_display_name,
                        "relationship_type": "parent",
                        "reciprocal_type": "child",
                    }
                )
                _append_unique_change(
                    proposal["derived_change_lines"],
                    f"Infer co-parent link: {spouse_contact.get('display_name')} -> parent -> {child_display_name}",
                )
        if related_contact_id and rel_type in _PARENT_TYPES:
            grandparent_contact = contacts_service.get_contact(related_contact_id)
            if grandparent_contact:
                derived_type = _GRANDPARENT_MAP.get(rel_type, "grandparent")
                proposal["derived_relationships"].append(
                    {
                        "proposal_id": _proposal_id("derived_relationship"),
                        "source": "derived",
                        "reason": "grandparent_from_parent_graph",
                        "from_reference": grandparent_contact.get("contact_id"),
                        "from_display_name": grandparent_contact.get("display_name"),
                        "to_reference": child_reference,
                        "to_display_name": child_display_name,
                        "relationship_type": derived_type,
                        "reciprocal_type": "grandchild",
                    }
                )
                _append_unique_change(
                    proposal["derived_change_lines"],
                    f"Infer grandparent link: {grandparent_contact.get('display_name')} -> {derived_type} -> {child_display_name}",
                )


def _build_proposal(extracted: dict[str, Any], *, original_message: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    main_name = str(extracted.get("main_contact_name") or "").strip()
    related_name = str(extracted.get("related_contact_name") or "").strip()
    legacy_relationship_type = _normalize_relationship_type(extracted.get("relationship_type"))
    birth_date_text = str(extracted.get("birth_date_text") or "").strip()
    legacy_place_text = str(extracted.get("place_text") or "").strip()
    legacy_place_role = str(extracted.get("place_role") or "").strip().lower() or None

    contact_updates = _dict_items(extracted.get("contacts"))
    legacy_contact_updates = _dict_items(extracted.get("contact_updates"))
    if not contact_updates:
        contact_updates = legacy_contact_updates
    elif legacy_contact_updates:
        contact_updates.extend(legacy_contact_updates)

    relationship_specs = _dict_items(extracted.get("relationships"))
    if not relationship_specs and legacy_relationship_type and main_name and related_name:
        relationship_specs.append(
            {
                "from_contact_name": main_name,
                "to_contact_name": related_name,
                "relationship_type": legacy_relationship_type,
            }
        )

    place_link_specs = _dict_items(extracted.get("contact_place_links"))
    if not place_link_specs and legacy_place_text and main_name:
        place_link_specs.append(
            {
                "contact_name": main_name,
                "place_text": legacy_place_text,
                "place_role": legacy_place_role,
            }
        )

    if not main_name and contact_updates:
        main_name = str((contact_updates[0] or {}).get("contact_name") or "").strip()

    parsed_birth_date, birth_date_error = _parse_birth_date(birth_date_text)
    if birth_date_error:
        return None, build_need_user_input(
            prompt=birth_date_error,
            questions=[birth_date_error],
            fields=_clarification_fields(["birth_date"]),
            kind="clarification",
            source="contact_command",
            submission_mode="ui_submission",
        )

    proposal: dict[str, Any] = {
        "contacts": [],
        "relationships": [],
        "derived_relationships": [],
        "places": [],
        "contact_place_links": [],
        "summary_lines": [],
        "explicit_change_lines": [],
        "derived_change_lines": [],
        "original_message": original_message,
        "edit_context": {},
    }
    refs: dict[str, dict[str, Any]] = {}
    places_by_text: dict[str, dict[str, Any]] = {}

    def resolve_contact(name: str, field_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return None, build_need_user_input(
                prompt="Which contact should I update?",
                questions=["Which contact should I update?"],
                fields=_clarification_fields([field_id]),
                kind="clarification",
                source="contact_command",
                submission_mode="ui_submission",
            )
        key = normalize_search_text(normalized_name)
        if key in refs:
            return refs[key], None
        ref = _resolve_contact_reference(normalized_name)
        if ref.get("status") == "ambiguous":
            return None, build_need_user_input(
                prompt=f"I found multiple contacts for {normalized_name}. Which one did you mean?",
                questions=[f"I found multiple contacts for {normalized_name}. Which one did you mean?"],
                fields=_clarification_fields([field_id]),
                kind="disambiguation",
                source="contact_command",
                submission_mode="ui_submission",
            )
        refs[key] = ref
        _ensure_contact_create(proposal, ref)
        return ref, None

    def resolve_place(place_text: str) -> dict[str, Any] | None:
        normalized_place = str(place_text or "").strip()
        if not normalized_place:
            return None
        key = normalize_search_text(normalized_place)
        if key not in places_by_text:
            place_ref = _resolve_place(normalized_place)
            if place_ref:
                places_by_text[key] = place_ref
                _ensure_place_create(proposal, place_ref)
        return places_by_text.get(key)

    main_ref: dict[str, Any] | None = None
    if main_name:
        main_ref, need_user_input = resolve_contact(main_name, "main_contact_name")
        if need_user_input:
            return None, need_user_input
    related_ref: dict[str, Any] | None = None
    if related_name:
        related_ref, need_user_input = resolve_contact(related_name, "related_contact_name")
        if need_user_input:
            return None, need_user_input

    applied_update_refs: set[str] = set()
    for raw_update in contact_updates:
        target_name = str(
            raw_update.get("contact_name")
            or raw_update.get("name")
            or raw_update.get("display_name")
            or main_name
            or ""
        ).strip()
        target_ref, need_user_input = resolve_contact(target_name, "main_contact_name")
        if need_user_input:
            return None, need_user_input
        if not target_ref:
            continue

        raw_birthday = raw_update.get("birthday")
        if raw_birthday is None and main_ref and _contact_reference_value(target_ref) == _contact_reference_value(main_ref):
            raw_birthday = parsed_birth_date
        parsed_update_birthday, update_birth_date_error = _parse_birth_date(raw_birthday)
        if update_birth_date_error:
            return None, build_need_user_input(
                prompt=update_birth_date_error,
                questions=[update_birth_date_error],
                fields=_clarification_fields(["birth_date"]),
                kind="clarification",
                source="contact_command",
                submission_mode="ui_submission",
            )

        updates = {
            "birthday": parsed_update_birthday,
            "comments": raw_update.get("comments"),
            "profession": raw_update.get("profession"),
            "aliases": raw_update.get("aliases"),
            "emails": raw_update.get("emails"),
            "phones": raw_update.get("phones"),
            "links": raw_update.get("links"),
            "tags": raw_update.get("tags"),
        }
        _apply_contact_fields_to_proposal(
            proposal,
            target_ref=target_ref,
            updates=updates,
            label_name=str(target_ref.get("display_name") or target_name),
        )
        if any(value not in (None, [], "") for value in updates.values()):
            applied_update_refs.add(_contact_reference_value(target_ref))

    if parsed_birth_date and main_ref and _contact_reference_value(main_ref) not in applied_update_refs:
        _apply_contact_fields_to_proposal(
            proposal,
            target_ref=main_ref,
            updates={"birthday": parsed_birth_date},
            label_name=str(main_ref.get("display_name") or main_name),
        )

    first_relationship: dict[str, Any] | None = None
    for spec in relationship_specs:
        from_name = str(spec.get("from_contact_name") or spec.get("from_name") or spec.get("source_contact_name") or "").strip()
        to_name = str(spec.get("to_contact_name") or spec.get("to_name") or spec.get("target_contact_name") or "").strip()
        relationship_type = _normalize_relationship_type(spec.get("relationship_type") or spec.get("type"))
        if not relationship_type:
            return None, build_need_user_input(
                prompt="What relationship should I add between these contacts?",
                questions=["What relationship should I add between these contacts?"],
                fields=_clarification_fields(["relationship_type"]),
                kind="clarification",
                source="contact_command",
                submission_mode="ui_submission",
            )
        from_ref, need_user_input = resolve_contact(from_name, "main_contact_name")
        if need_user_input:
            return None, need_user_input
        to_ref, need_user_input = resolve_contact(to_name, "related_contact_name")
        if need_user_input:
            return None, need_user_input
        if not from_ref or not to_ref:
            continue
        relationship = _append_relationship_to_proposal(
            proposal,
            from_ref=from_ref,
            to_ref=to_ref,
            relationship_type=relationship_type,
        )
        if first_relationship is None:
            first_relationship = relationship
        _append_parent_derived_relationships(
            proposal,
            parent_ref=from_ref,
            child_ref=to_ref,
            relationship_type=relationship_type,
        )

    first_place_ref: dict[str, Any] | None = None
    for spec in place_link_specs:
        contact_name = str(spec.get("contact_name") or spec.get("name") or main_name or "").strip()
        place_text = str(spec.get("place_text") or spec.get("place_name") or spec.get("address") or "").strip()
        link_role = str(spec.get("place_role") or spec.get("role") or "home").strip().lower() or "home"
        contact_ref, need_user_input = resolve_contact(contact_name, "main_contact_name")
        if need_user_input:
            return None, need_user_input
        place_ref = resolve_place(place_text)
        if not contact_ref or not place_ref:
            continue
        if first_place_ref is None:
            first_place_ref = place_ref
        proposal["contact_place_links"].append(
            {
                "proposal_id": _proposal_id("contact_place_link"),
                "source": "explicit",
                "contact_reference": _contact_reference_value(contact_ref),
                "contact_display_name": contact_ref.get("display_name"),
                "place_reference": place_ref.get("place_id") or place_ref.get("temp_id"),
                "place_name": place_ref.get("name"),
                "role": link_role,
            }
        )
        _append_unique_change(
            proposal["explicit_change_lines"],
            f"Link {contact_ref.get('display_name')} to {place_ref.get('name')} as {link_role}",
        )

    proposal["edit_context"] = {
        "main_contact_reference": _contact_reference_value(main_ref),
        "related_contact_reference": _contact_reference_value(related_ref),
        "primary_relationship_id": (first_relationship or {}).get("proposal_id"),
        "place_reference": (first_place_ref or {}).get("place_id") or (first_place_ref or {}).get("temp_id"),
    }

    for line in proposal["explicit_change_lines"]:
        _append_unique_change(proposal["summary_lines"], line)
    for line in proposal["derived_change_lines"]:
        _append_unique_change(proposal["summary_lines"], line)

    if not proposal["summary_lines"]:
        return None, build_need_user_input(
            prompt="I couldn't determine a concrete contact update from that message. What should I change?",
            questions=["I couldn't determine a concrete contact update from that message. What should I change?"],
            fields=_clarification_fields(["details"]),
            kind="clarification",
            source="contact_command",
            submission_mode="ui_submission",
        )

    return proposal, None


def _contact_merged_by_reference(proposal: dict[str, Any], reference: str) -> dict[str, Any] | None:
    contacts_raw = proposal.get("contacts")
    contacts = contacts_raw if isinstance(contacts_raw, list) else []
    for item in contacts:
        if str(item.get("reference") or "") == reference and str(item.get("operation") or "") == "update":
            merged = item.get("merged")
            if isinstance(merged, dict):
                return merged
    return None


def _build_edit_fields(proposal: dict[str, Any], preview_id: str) -> list[dict[str, Any]]:
    edit_context = proposal.get("edit_context") if isinstance(proposal.get("edit_context"), dict) else {}
    fields: list[dict[str, Any]] = []
    main_ref = str(edit_context.get("main_contact_reference") or "")
    related_ref = str(edit_context.get("related_contact_reference") or "")
    main_merged = _contact_merged_by_reference(proposal, main_ref) or {}

    if main_merged.get("display_name"):
        fields.append(
            {
                "id": "main_display_name",
                "kind": "short_text",
                "label": "Primary contact name",
                "value": str(main_merged.get("display_name") or ""),
            }
        )
    if related_ref:
        related_name = ""
        for rel in proposal.get("relationships", []) + proposal.get("derived_relationships", []):
            if str(rel.get("to_reference") or "") == related_ref:
                related_name = str(rel.get("to_display_name") or "")
                break
        if related_name:
            fields.append(
                {
                    "id": "related_display_name",
                    "kind": "short_text",
                    "label": "Related contact name",
                    "value": related_name,
                }
            )

    first_relationship = next(iter(proposal.get("relationships") or []), None)
    if isinstance(first_relationship, dict) and first_relationship.get("relationship_type"):
        fields.append(
            {
                "id": "relationship_type",
                "kind": "short_text",
                "label": "Relationship type",
                "value": str(first_relationship.get("relationship_type") or ""),
            }
        )

    if main_merged.get("birthday"):
        fields.append(
            {
                "id": "birth_date",
                "kind": "date",
                "label": "Birth date",
                "value": str(main_merged.get("birthday") or ""),
            }
        )

    for field_name, label, kind in [
        ("aliases", "Aliases", "short_text"),
        ("emails", "Emails", "email"),
        ("phones", "Phones", "short_text"),
        ("links", "Links", "url"),
        ("tags", "Tags", "short_text"),
    ]:
        values = main_merged.get(field_name) or []
        if values:
            fields.append(
                {
                    "id": field_name,
                    "kind": kind,
                    "label": label,
                    "value": ", ".join(str(value) for value in values),
                }
            )
    if main_merged.get("comments"):
        fields.append(
            {
                "id": "comments",
                "kind": "textarea",
                "label": "Notes",
                "value": str(main_merged.get("comments") or ""),
            }
        )

    first_link = next(iter(proposal.get("contact_place_links") or []), None)
    if isinstance(first_link, dict):
        if first_link.get("place_name"):
            fields.append(
                {
                    "id": "place_name",
                    "kind": "short_text",
                    "label": "Place name",
                    "value": str(first_link.get("place_name") or ""),
                }
            )
        if first_link.get("role"):
            fields.append(
                {
                    "id": "place_role",
                    "kind": "short_text",
                    "label": "Place role",
                    "value": str(first_link.get("role") or ""),
                }
            )

    for rel in proposal.get("derived_relationships") or []:
        if not isinstance(rel, dict):
            continue
        rel_id = str(rel.get("proposal_id") or "").strip()
        if not rel_id:
            continue
        from_name = str(rel.get("from_display_name") or "").strip()
        rel_type = str(rel.get("relationship_type") or "").strip()
        to_name = str(rel.get("to_display_name") or "").strip()
        fields.append(
            {
                "id": f"derived_{rel_id}",
                "kind": "select",
                "label": f"Apply inferred link: {from_name} -> {rel_type} -> {to_name}",
                "options": copy.deepcopy(_YES_NO_OPTIONS),
                "value": "yes",
            }
        )

    if not fields:
        return []
    return fields


def handle_contact(parsed: ParsedCommand, context: dict[str, Any]) -> dict[str, Any]:
    logger.info("[handle_contact] NEW CONTACT COMMAND")
    _emit_progress(context, "Parsing contact update...")

    if not parsed.args:
        return {
            "type": "error",
            "message": "Please provide a contact update. Example: /contact Dana is married to Sage",
        }

    raw_message, clarification_id = _extract_clarification_token(parsed.args)
    user_email = str(context.get("user_email") or "")

    clarification_context = None
    if clarification_id:
        from commands.storage import delete_command_data, get_command_data

        clarification_context = get_command_data(clarification_id)
        delete_command_data(clarification_id)

    original_message = str((clarification_context or {}).get("original_message") or raw_message).strip()
    combined_message = original_message
    if clarification_context:
        _, follow_up = _extract_additional_details(raw_message)
        if follow_up:
            combined_message = f"{original_message}. Additional details: {follow_up}"
        elif raw_message and raw_message != original_message:
            combined_message = f"{original_message}. Additional details: {raw_message}"

    extracted = _llm_extract_contact_changes(combined_message, user_email=user_email)
    explicit_need_user_input = normalize_need_user_input(extracted.get("need_user_input"))
    if explicit_need_user_input:
        clarification_preview_id = f"contact:clarification:{uuid4().hex[:8]}"
        from commands.storage import store_command_data, store_pending_event

        store_command_data(
            clarification_preview_id,
            {
                "command_name": "contact",
                "original_message": original_message,
                "thread_id": context.get("thread_id"),
            },
        )
        pending_key = context.get("event_pending_key")
        if pending_key:
            store_pending_event(pending_key, clarification_preview_id)
        return {
            "type": "need_user_input",
            "clarification_id": clarification_preview_id,
            "need_user_input": {
                **explicit_need_user_input,
                "source": "contact_command",
                "action_id": explicit_need_user_input.get("action_id")
                or _clarification_action_id(clarification_preview_id),
                "submission_mode": "ui_submission",
                "context": {"clarification_id": clarification_preview_id},
            },
        }

    proposal, proposal_need_user_input = _build_proposal(extracted, original_message=original_message)
    if proposal_need_user_input:
        clarification_preview_id = f"contact:clarification:{uuid4().hex[:8]}"
        from commands.storage import store_command_data, store_pending_event

        store_command_data(
            clarification_preview_id,
            {
                "command_name": "contact",
                "original_message": original_message,
                "thread_id": context.get("thread_id"),
            },
        )
        pending_key = context.get("event_pending_key")
        if pending_key:
            store_pending_event(pending_key, clarification_preview_id)
        return {
            "type": "need_user_input",
            "clarification_id": clarification_preview_id,
            "need_user_input": {
                **proposal_need_user_input,
                "action_id": proposal_need_user_input.get("action_id")
                or _clarification_action_id(clarification_preview_id),
                "context": {"clarification_id": clarification_preview_id},
            },
        }

    preview_id = f"contact:preview:{uuid4().hex[:8]}"
    if proposal is None:
        return {
            "type": "error",
            "message": "I couldn't build a contact proposal from that request.",
        }

    edit_fields = _build_edit_fields(proposal, preview_id)

    from commands.storage import store_command_data, store_pending_event

    store_command_data(
        preview_id,
        {
            "command_name": "contact",
            "proposal": proposal,
            "original_message": original_message,
            "thread_id": context.get("thread_id"),
        },
    )
    pending_key = context.get("event_pending_key")
    if pending_key:
        store_pending_event(pending_key, preview_id)

    return {
        "type": "contact_confirmation",
        "preview_id": preview_id,
        "proposal": proposal,
        "summary_lines": proposal.get("summary_lines", []),
        "explicit_change_lines": proposal.get("explicit_change_lines", []),
        "derived_change_lines": proposal.get("derived_change_lines", []),
        "edit_fields": edit_fields,
        "edit_action_id": _edit_action_id(preview_id),
        "requires_confirmation": True,
        "message": "I drafted these contact graph changes. Please review, edit if needed, and confirm.",
    }


def register(registry: CommandRegistry) -> None:
    registry.register(
        name="contact",
        handler=handle_contact,
        description="Create or update contacts, relationships, and contact places",
        requires_args=True,
    )
