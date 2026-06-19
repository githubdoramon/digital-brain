"""Handler for the /contact command."""

from __future__ import annotations

import copy
import json
import re
from datetime import date
from typing import Any
from uuid import uuid4

import contacts as contacts_service
import places as places_service
from commands.handlers.clarification_utils import (
    append_conversation_message as _append_contact_conversation_message,
)
from commands.handlers.clarification_utils import (
    build_clarification_result,
    build_clarification_storage_payload,
    create_clarification_preview_id,
    store_clarification_preview,
)
from commands.handlers.clarification_utils import (
    extract_additional_details as _extract_additional_details,
)
from commands.handlers.clarification_utils import (
    extract_clarification_token as _extract_clarification_token,
)
from commands.handlers.clarification_utils import (
    need_user_input_prompt as _shared_need_user_input_prompt,
)
from commands.handlers.clarification_utils import (
    strip_clarification_field_labels as _strip_clarification_field_labels,
)
from commands.parser import ParsedCommand
from commands.registry import CommandRegistry
from llm_helpers import build_json_schema_response_format
from llm_json_schemas import CONTACT_UPDATE_RESPONSE_SCHEMA
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_text
from ui_dsl import (
    build_need_user_input,
    build_need_user_input_prompt_guidance,
    normalize_need_user_input,
)

logger = get_runtime_logger(__name__)

_CONTACT_CLARIFICATION_ACTION_PREFIX = "contact_clarification_submit"
_CONTACT_EDIT_ACTION_PREFIX = "contact_edit_submit"

_FIELD_KIND_MAP = {
    "birth_date": "date",
    "place_text": "text",
    "main_contact_name": "text",
    "related_contact_name": "text",
    "relationship_type": "text",
    "details": "textarea",
}

_RELATIONSHIP_LABELS = {
    "married": "Spouse",
    "married to": "Spouse",
    "wife": "Wife",
    "husband": "Husband",
    "spouse": "Spouse",
    "partner": "Partner",
    "father": "Father",
    "mother": "Mother",
    "parent": "Parent",
    "child": "Child",
    "son": "Son",
    "daughter": "Daughter",
    "brother": "Brother",
    "sister": "Sister",
    "sibling": "Sibling",
    "grandfather": "Grandfather",
    "grandmother": "Grandmother",
    "grandparent": "Grandparent",
    "grandson": "Grandson",
    "granddaughter": "Granddaughter",
    "grandchild": "Grandchild",
}

_RELATIONSHIP_RECIPROCALS = {
    "wife": "Husband",
    "husband": "Wife",
    "spouse": "Spouse",
    "partner": "Partner",
    "father": "Child",
    "mother": "Child",
    "parent": "Child",
    "child": "Parent",
    "son": "Parent",
    "daughter": "Parent",
    "brother": "Sibling",
    "sister": "Sibling",
    "sibling": "Sibling",
    "grandfather": "Grandchild",
    "grandmother": "Grandchild",
    "grandparent": "Grandchild",
    "grandson": "Grandparent",
    "granddaughter": "Grandparent",
    "grandchild": "Grandparent",
}

_PARENT_TYPES = {"Father", "Mother", "Parent"}
_SPOUSE_TYPES = {"Spouse", "Partner", "Wife", "Husband"}
_GRANDPARENT_MAP = {"Father": "Grandfather", "Mother": "Grandmother", "Parent": "Grandparent"}
_CONTACT_LIST_FIELDS = ("aliases", "emails", "phones", "links", "tags")
_YES_NO_OPTIONS = [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]


def _emit_progress(context: dict[str, Any], message: str) -> None:
    callback = context.get("progress_callback")
    if callable(callback):
        try:
            callback(message)
        except Exception:
            logger.debug("[contact_command] progress callback failed", exc_info=True)


def _need_user_input_prompt(need_user_input: dict[str, Any] | None) -> str:
    return _shared_need_user_input_prompt(
        need_user_input,
        "Please share the missing contact details so I can continue.",
    )


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
                "kind": _FIELD_KIND_MAP.get(normalized, "text"),
                "label": normalized.replace("_", " ").strip().title(),
                "required": True,
            }
        )
    return output or [{"id": "details", "kind": "textarea", "label": "Details", "required": True}]


def _ambiguous_contact_field(
    *,
    field_id: str,
    label: str,
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    options: list[dict[str, str]] = []
    seen_labels: set[str] = set()
    for idx, match in enumerate(matches[:5]):
        display_name = str(match.get("display_name") or "").strip()
        if not display_name or display_name in seen_labels:
            continue
        seen_labels.add(display_name)
        options.append({"id": f"match_{idx + 1}", "label": display_name})

    if options:
        options.append({"id": "none_of_these", "label": "None of these - create a new contact"})
        return [
            {
                "id": field_id,
                "kind": "select",
                "label": label,
                "required": True,
                "options": options,
            }
        ]

    return _clarification_fields([field_id])


def _format_contact_command_conversation(messages: list[dict[str, str]] | None, fallback_message: str) -> str:
    conversation: list[dict[str, str]] = []
    for item in messages or []:
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            conversation.append({"role": role, "content": content})
    if not conversation and str(fallback_message or "").strip():
        conversation.append({"role": "user", "content": str(fallback_message).strip()})
    return json.dumps(conversation, ensure_ascii=False)


def _format_existing_contact_extraction(existing: dict[str, Any] | None) -> str:
    if not existing:
        return ""
    try:
        serialized = json.dumps(existing, ensure_ascii=False, default=str, indent=2)
    except TypeError:
        serialized = str(existing)
    return (
        "Existing contact extraction from earlier turns (use as base, update only if new details override):\n"
        f"{serialized}\n\n"
    )


def _llm_extract_contact_changes(
    message: str,
    *,
    user_email: str,
    model: str | None = None,
    timeout: int | None = None,
    conversation_messages: list[dict[str, str]] | None = None,
    existing_extraction: dict[str, Any] | None = None,
    llm_request_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from llm_helpers import call_llm_json
    from prompts.context import get_self_context, get_user_facts_context
    from user_fact_rules import RuleScope

    need_user_input_guidance = build_need_user_input_prompt_guidance(exclude_people=False)
    self_context = get_self_context(user_email) if user_email else None
    self_context_block = f"\n{self_context}\n" if self_context else ""
    user_facts_ctx = (
        get_user_facts_context(user_email, message, scope=RuleScope.CONTACT_RESOLUTION)
        if user_email
        else None
    )
    user_facts_block = f"\n{user_facts_ctx}\n" if user_facts_ctx else ""
    conversation_json = _format_contact_command_conversation(conversation_messages, message)
    existing_extraction_context = _format_existing_contact_extraction(existing_extraction)
    prompt = f"""You need to extract a structured proposal of information about contacts and their relationships.

{self_context_block}
{user_facts_block}
Current user message: \"{message}\"

Conversation messages (JSON array, most recent last):
{conversation_json}

{existing_extraction_context}
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

Return ONLY a JSON object matching the supplied response schema.

Relationship type rules:
- Prefer specific Title Case labels when context supports them: Husband/Wife, Father/Daughter, Father/Son, Mother/Daughter, Mother/Son, Brother/Sister.
- Avoid generic labels like spouse/spouse or parent/child when the sentence gives enough information for a specific pair.
- If the reciprocal side is unknown, use a reasonable generic Title Case reciprocal such as Child, Parent, Sibling, Spouse, or Partner.
"""
    request_options = {
        "response_format": build_json_schema_response_format(
            name="contact_update_extraction",
            schema=CONTACT_UPDATE_RESPONSE_SCHEMA,
        ),
        "use_fast_model": False,
        "reasoning_effort": "high",
    }
    request_options.update(dict(llm_request_options or {}))
    extracted = call_llm_json(
        prompt,
        timeout=timeout or 45,
        model=model,
        **request_options,
    )
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
        result = {
            "status": "new",
            "display_name": normalized_name,
            "temp_id": f"new_contact:{_safe_entity_slug(normalized_name) or 'contact'}",
        }
        logger.debug(
            "[handle_contact] resolve_contact_reference query=%r status=%s",
            normalized_name,
            result["status"],
        )
        return result
    top = matches[0]
    top_score = float(top.get("match_score") or 0)
    if len(matches) > 1:
        second_score = float(matches[1].get("match_score") or 0)
        if top_score < 98 and abs(top_score - second_score) <= 3:
            result = {"status": "ambiguous", "query": normalized_name, "matches": matches[:3]}
            logger.info(
                "[handle_contact] resolve_contact_reference query=%r status=ambiguous matches=%s",
                normalized_name,
                [
                    {
                        "display_name": str(match.get("display_name") or ""),
                        "match_score": float(match.get("match_score") or 0),
                    }
                    for match in matches[:3]
                ],
            )
            return result
    result = {
        "status": "existing",
        "contact_id": str(top.get("contact_id") or ""),
        "display_name": str(top.get("display_name") or normalized_name),
        "match_score": top_score,
    }
    logger.debug(
        "[handle_contact] resolve_contact_reference query=%r status=%s display_name=%r score=%.1f",
        normalized_name,
        result["status"],
        result.get("display_name"),
        top_score,
    )
    return result


def _build_new_contact_reference(name: str) -> dict[str, Any]:
    normalized_name = str(name or "").strip()
    return {
        "status": "new",
        "display_name": normalized_name,
        "temp_id": f"new_contact:{_safe_entity_slug(normalized_name) or 'contact'}",
    }


def _infer_forced_new_contact_resolution(
    conversation_messages: list[dict[str, str]] | None,
    extracted: dict[str, Any] | None,
) -> tuple[set[str], bool, str | None]:
    transcript = [
        {
            "role": str(item.get("role") or "").strip(),
            "content": str(item.get("content") or "").strip(),
        }
        for item in (conversation_messages or [])
        if str(item.get("role") or "").strip() in {"user", "assistant"}
        and str(item.get("content") or "").strip()
    ]
    user_messages = [item["content"] for item in transcript if item["role"] == "user"]
    if len(user_messages) < 2:
        return set(), False, None

    latest_detail = user_messages[-1]
    latest_detail_normalized = normalize_search_text(latest_detail)
    if not latest_detail_normalized:
        return set(), False, None

    explicit_new_phrases = (
        "new",
        "doesnt exist",
        "doesn't exist",
        "none of these",
        "not found",
        "not existing",
    )

    def _contains_explicit_new_intent(text: str) -> bool:
        normalized_text = normalize_search_text(text)
        return any(phrase in normalized_text for phrase in explicit_new_phrases)

    contact_names = [
        str(contact.get("contact_name") or "").strip()
        for contact in _dict_items((extracted or {}).get("contacts"))
        if str(contact.get("contact_name") or "").strip()
    ]
    normalized_contact_names = {
        normalize_search_text(name): name for name in contact_names if normalize_search_text(name)
    }

    def _matched_contact_names(detail_normalized: str) -> set[str]:
        matches: set[str] = set()
        for normalized_name in normalized_contact_names:
            if normalized_name in detail_normalized:
                matches.add(normalized_name)
                continue
            first_name = normalized_name.split()[0] if normalized_name.split() else ""
            if first_name and re.search(rf"\b{re.escape(first_name)}\b", detail_normalized):
                matches.add(normalized_name)
        return matches

    if not _contains_explicit_new_intent(latest_detail):
        previous_user_messages = user_messages[:-1]
        prior_new_intent = next(
            (message for message in reversed(previous_user_messages) if _contains_explicit_new_intent(message)),
            None,
        )
        if not prior_new_intent:
            return set(), False, latest_detail

        latest_assistant_before_user = next(
            (
                item["content"]
                for item in reversed(transcript[:-1])
                if item["role"] == "assistant"
            ),
            "",
        )
        assistant_prompt_normalized = normalize_search_text(latest_assistant_before_user)
        is_new_contact_naming_step = any(
            phrase in assistant_prompt_normalized
            for phrase in (
                "which new contact",
                "name of the new contact",
                "contact name",
                "what is the name of the new contact",
            )
        )
        if not is_new_contact_naming_step:
            return set(), False, latest_detail

        matched_names = _matched_contact_names(latest_detail_normalized)
        return matched_names, False, latest_detail

    forced_specific_names: set[str] = set()
    forced_specific_names.update(_matched_contact_names(latest_detail_normalized))

    latest_assistant = next(
        (
            str(item.get("content") or "").strip()
            for item in reversed(conversation_messages or [])
            if str(item.get("role") or "").strip() == "assistant"
            and str(item.get("content") or "").strip()
        ),
        "",
    )
    latest_assistant_normalized = normalize_search_text(latest_assistant)
    if not forced_specific_names and latest_assistant_normalized:
        assistant_matches = [
            normalized_name
            for normalized_name in normalized_contact_names
            if normalized_name and normalized_name in latest_assistant_normalized
        ]
        if len(assistant_matches) == 1:
            forced_specific_names.add(assistant_matches[0])

    force_all_ambiguous = any(
        token in latest_detail_normalized for token in ("both", "all", "them", "those contacts")
    )
    return forced_specific_names, force_all_ambiguous, latest_detail


def _normalize_relationship_type(value: str | None) -> str | None:
    normalized = normalize_search_text(value or "")
    if not normalized:
        return None
    if normalized in _RELATIONSHIP_LABELS:
        return _RELATIONSHIP_LABELS[normalized]
    return " ".join(
        "-".join(segment.capitalize() for segment in part.replace("_", "-").split("-") if segment)
        for part in normalized.split()
        if part
    )


def _reciprocal_relationship_type(relationship_type: str) -> str | None:
    normalized = normalize_search_text(relationship_type or "")
    return _RELATIONSHIP_RECIPROCALS.get(normalized)


def _normalize_relationship_pair(
    relationship_type: Any,
    reciprocal_type: Any = None,
) -> tuple[str | None, str | None]:
    normalized_type = _normalize_relationship_type(str(relationship_type or ""))
    normalized_reciprocal = _normalize_relationship_type(str(reciprocal_type or ""))
    if normalized_type and not normalized_reciprocal:
        normalized_reciprocal = _reciprocal_relationship_type(normalized_type)
    return normalized_type, normalized_reciprocal


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
    reciprocal_type: str | None = None,
) -> dict[str, Any]:
    relationship = {
        "proposal_id": _proposal_id("relationship"),
        "source": "explicit",
        "from_reference": _contact_reference_value(from_ref),
        "from_display_name": from_ref.get("display_name"),
        "to_reference": _contact_reference_value(to_ref),
        "to_display_name": to_ref.get("display_name"),
        "relationship_type": relationship_type,
        "reciprocal_type": reciprocal_type or _reciprocal_relationship_type(relationship_type),
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
        rel_type = _normalize_relationship_type(str(rel.get("type") or "").strip())
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
                        "relationship_type": "Parent",
                        "reciprocal_type": "Child",
                    }
                )
                _append_unique_change(
                    proposal["derived_change_lines"],
                    f"Infer co-parent link: {spouse_contact.get('display_name')} -> Parent -> {child_display_name}",
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
                        "reciprocal_type": "Grandchild",
                    }
                )
                _append_unique_change(
                    proposal["derived_change_lines"],
                    f"Infer grandparent link: {grandparent_contact.get('display_name')} -> {derived_type} -> {child_display_name}",
                )


def _build_proposal(
    extracted: dict[str, Any],
    *,
    original_message: str,
    forced_new_contact_names: set[str] | None = None,
    force_all_ambiguous_new_contacts: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    main_name = str(extracted.get("main_contact_name") or "").strip()
    related_name = str(extracted.get("related_contact_name") or "").strip()
    legacy_relationship_type, legacy_reciprocal_type = _normalize_relationship_pair(
        extracted.get("relationship_type"),
        extracted.get("reciprocal_type"),
    )
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
                "reciprocal_type": legacy_reciprocal_type,
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
    forced_new_contact_names = set(forced_new_contact_names or set())

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
        if key in forced_new_contact_names:
            ref = _build_new_contact_reference(normalized_name)
            refs[key] = ref
            _ensure_contact_create(proposal, ref)
            logger.info(
                "[handle_contact] forcing new contact from clarification query=%r",
                normalized_name,
            )
            return ref, None
        ref = _resolve_contact_reference(normalized_name)
        if ref.get("status") == "ambiguous" and force_all_ambiguous_new_contacts:
            ref = _build_new_contact_reference(normalized_name)
            refs[key] = ref
            _ensure_contact_create(proposal, ref)
            logger.info(
                "[handle_contact] forcing ambiguous contact to new from clarification query=%r",
                normalized_name,
            )
            return ref, None
        if ref.get("status") == "ambiguous":
            matches = [item for item in (ref.get("matches") or []) if isinstance(item, dict)]
            prompt = f"I found multiple contacts for {normalized_name}. Which one did you mean?"
            return None, build_need_user_input(
                prompt=prompt,
                questions=[prompt],
                fields=_ambiguous_contact_field(
                    field_id=field_id,
                    label=f"Who did you mean by '{normalized_name}'?",
                    matches=matches,
                ),
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
        if not isinstance(raw_update, dict):
            continue
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
        relationship_type, reciprocal_type = _normalize_relationship_pair(
            spec.get("relationship_type") or spec.get("type"),
            spec.get("reciprocal_type") or spec.get("other_type"),
        )
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
            reciprocal_type=reciprocal_type,
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

    first_relationship_data = first_relationship if isinstance(first_relationship, dict) else {}
    first_place_data = first_place_ref if isinstance(first_place_ref, dict) else {}
    proposal["edit_context"] = {
        "main_contact_reference": _contact_reference_value(main_ref),
        "related_contact_reference": _contact_reference_value(related_ref),
        "primary_relationship_id": first_relationship_data.get("proposal_id"),
        "place_reference": first_place_data.get("place_id") or first_place_data.get("temp_id"),
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
                "kind": "text",
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
                    "kind": "text",
                    "label": "Related contact name",
                    "value": related_name,
                }
            )

    first_relationship = next(iter(proposal.get("relationships") or []), None)
    if isinstance(first_relationship, dict) and first_relationship.get("relationship_type"):
        fields.append(
            {
                "id": "relationship_type",
                "kind": "text",
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
        ("aliases", "Aliases", "text"),
        ("emails", "Emails", "email"),
        ("phones", "Phones", "text"),
        ("links", "Links", "url"),
        ("tags", "Tags", "text"),
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
                    "kind": "text",
                    "label": "Place name",
                    "value": str(first_link.get("place_name") or ""),
                }
            )
        if first_link.get("role"):
            fields.append(
                {
                    "id": "place_role",
                    "kind": "text",
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
    previous_extraction: dict[str, Any] | None = None
    if clarification_id:
        from commands.storage import delete_command_data, get_command_data

        clarification_context = get_command_data(clarification_id)
        delete_command_data(clarification_id)
        if isinstance((clarification_context or {}).get("extracted"), dict):
            previous_extraction = dict((clarification_context or {}).get("extracted") or {})

    original_message = str((clarification_context or {}).get("original_message") or raw_message).strip()
    extraction_message = original_message
    conversation_messages = [
        item
        for item in ((clarification_context or {}).get("conversation_messages") or [])
        if isinstance(item, dict)
    ]
    requested_fields = [
        field
        for field in ((clarification_context or {}).get("requested_fields") or [])
        if isinstance(field, dict)
    ]
    clarification_field_labels = [
        str(field.get("label") or "").strip()
        for field in requested_fields
        if str(field.get("label") or "").strip()
    ]
    if not conversation_messages:
        _append_contact_conversation_message(conversation_messages, "user", original_message)
    if clarification_context:
        _, follow_up = _extract_additional_details(raw_message)
        if follow_up:
            _append_contact_conversation_message(
                conversation_messages,
                "user",
                _strip_clarification_field_labels(follow_up, clarification_field_labels),
            )
        elif raw_message and raw_message != original_message:
            _append_contact_conversation_message(
                conversation_messages,
                "user",
                _strip_clarification_field_labels(raw_message, clarification_field_labels),
            )

    extracted = _llm_extract_contact_changes(
        extraction_message,
        user_email=user_email,
        conversation_messages=conversation_messages,
        existing_extraction=previous_extraction,
    )
    logger.info(
        "[handle_contact] extraction complete need_user_input=%s contacts=%d relationships=%d place_links=%d",
        bool(extracted.get("need_user_input")),
        len(_dict_items(extracted.get("contacts"))),
        len(_dict_items(extracted.get("relationships"))),
        len(_dict_items(extracted.get("contact_place_links"))),
    )
    explicit_need_user_input = normalize_need_user_input(extracted.get("need_user_input"))
    if explicit_need_user_input:
        clarification_prompt = str(explicit_need_user_input.get("prompt") or "")
        logger.info(
            "[handle_contact] extraction requested clarification prompt=%r",
            clarification_prompt,
        )
        clarification_preview_id = create_clarification_preview_id("contact")
        clarification_payload = build_clarification_storage_payload(
            original_message=original_message,
            assistant_prompt=_need_user_input_prompt(explicit_need_user_input),
            existing_messages=conversation_messages,
            requested_fields=explicit_need_user_input.get("fields") or [],
            extra_payload={
                "command_name": "contact",
                "extracted": extracted,
                "thread_id": context.get("thread_id"),
            },
        )
        store_clarification_preview(
            clarification_preview_id,
            clarification_payload,
            context.get("event_pending_key"),
        )
        return build_clarification_result(
            clarification_preview_id,
            {
                **explicit_need_user_input,
                "source": "contact_command",
                "action_id": explicit_need_user_input.get("action_id")
                or _clarification_action_id(clarification_preview_id),
                "submission_mode": "ui_submission",
                "context": {"clarification_id": clarification_preview_id},
            },
            {"command_state": clarification_payload},
        )

    forced_new_contact_names, force_all_ambiguous_new_contacts, latest_clarification_detail = (
        _infer_forced_new_contact_resolution(conversation_messages, extracted)
    )
    if forced_new_contact_names or force_all_ambiguous_new_contacts:
        logger.info(
            "[handle_contact] clarification requested new contact resolution detail=%r specific_names=%s force_all_ambiguous=%s",
            latest_clarification_detail,
            sorted(forced_new_contact_names),
            force_all_ambiguous_new_contacts,
        )

    proposal, proposal_need_user_input = _build_proposal(
        extracted,
        original_message=original_message,
        forced_new_contact_names=forced_new_contact_names,
        force_all_ambiguous_new_contacts=force_all_ambiguous_new_contacts,
    )
    if proposal_need_user_input:
        proposal_clarification_prompt = str(proposal_need_user_input.get("prompt") or "")
        logger.info(
            "[handle_contact] proposal requires clarification prompt=%r",
            proposal_clarification_prompt,
        )
        clarification_preview_id = create_clarification_preview_id("contact")
        clarification_payload = build_clarification_storage_payload(
            original_message=original_message,
            assistant_prompt=_need_user_input_prompt(proposal_need_user_input),
            existing_messages=conversation_messages,
            requested_fields=proposal_need_user_input.get("fields") or [],
            extra_payload={
                "command_name": "contact",
                "extracted": extracted,
                "thread_id": context.get("thread_id"),
            },
        )
        store_clarification_preview(
            clarification_preview_id,
            clarification_payload,
            context.get("event_pending_key"),
        )
        return build_clarification_result(
            clarification_preview_id,
            {
                **proposal_need_user_input,
                "source": "contact_command",
                "submission_mode": "ui_submission",
                "action_id": proposal_need_user_input.get("action_id")
                or _clarification_action_id(clarification_preview_id),
                "context": {"clarification_id": clarification_preview_id},
            },
            {"command_state": clarification_payload},
        )

    preview_id = f"contact:preview:{uuid4().hex[:8]}"
    if proposal is None:
        return {
            "type": "error",
            "message": "I couldn't build a contact proposal from that request.",
        }

    logger.info(
        "[handle_contact] proposal ready contacts=%d relationships=%d derived_relationships=%d place_links=%d",
        len(_dict_items(proposal.get("contacts"))),
        len(_dict_items(proposal.get("relationships"))),
        len(_dict_items(proposal.get("derived_relationships"))),
        len(_dict_items(proposal.get("contact_place_links"))),
    )

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
