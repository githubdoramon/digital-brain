"""
Handler for the /event command.

The /event command allows users to add new memories/events to the database.
It extracts entities, checks for existing ones, and asks for confirmation.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from uuid import uuid4

import places as places_service
from commands.parser import ParsedCommand
from commands.registry import CommandRegistry
from location_inference import geocode_place_name, infer_current_place
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_text
from ui_dsl.clarification import (
    build_need_user_input,
    build_need_user_input_prompt_guidance,
    clarification_fields_from_ambiguous_contacts,
    default_clarification_details_field,
    derive_clarification_questions_from_fields,
    need_user_input_json_property_template,
    normalize_clarification_fields,
    normalize_need_user_input,
)

logger = get_runtime_logger(__name__)

_EVENT_FIELD_RULES: dict[str, dict[str, bool]] = {
    "title": {"extractable": True},
    "summary": {"extractable": True},
    "when": {"extractable": True},
    "end_when": {"extractable": True},
    "where": {"extractable": True},
    "tags": {"extractable": True},
    "types": {"extractable": True},
    "who": {"extractable": False},
}

_PLACE_ROLE_SYNONYMS = {
    # Home-like
    "home": "home",
    "house": "home",
    "residence": "home",
    "apartment": "home",
    "apt": "home",
    "condo": "home",
    "flat": "home",
    "parents": "family_home",
    "family": "family_home",
    "partner": "partner_home",
    "secondary": "secondary_home",
    # Work-like
    "work": "work",
    "office": "work",
    "workplace": "work",
    "job": "work",
    "company": "work",
    "hq": "hq",
    "headquarters": "hq",
    "branch": "branch_office",
    "coworking": "coworking",
    "cowork": "coworking",
    "client": "client_site",
    # Education
    "school": "school",
    "campus": "campus",
    "college": "school",
    "university": "school",
    # Other common categories
    "gym": "gym",
    "club": "club",
    "community": "community_space",
    "church": "worship_place",
    "temple": "worship_place",
    "mosque": "worship_place",
    "hospital": "healthcare",
    "clinic": "healthcare",
    "doctor": "healthcare",
    "favorite": "favorite_spot",
    "spot": "frequent_spot",
    "frequent": "frequent_spot",
    "other": "other",
}

_GENERIC_PLACE_ALIAS_TERMS = {
    # generic role words from synonyms map (both source tokens and canonical targets)
    *set(_PLACE_ROLE_SYNONYMS.keys()),
    *set(_PLACE_ROLE_SYNONYMS.values()),
    # broad non-entity fallback terms
    "place",
}


def _extract_client_location(context: dict[str, Any]) -> dict[str, Any] | None:
    client_context = context.get("client_context")
    if not isinstance(client_context, dict):
        return None
    location = client_context.get("location")
    return location if isinstance(location, dict) else None


def _normalize_role_hint(role_text: str | None) -> str | None:
    normalized = normalize_search_text(role_text or "")
    if not normalized:
        return None
    normalized = normalized.replace("-", " ")
    for token in normalized.split():
        mapped = _PLACE_ROLE_SYNONYMS.get(token)
        if mapped:
            return mapped
    return normalized


def _is_generic_place_alias(alias_text: str) -> bool:
    normalized = normalize_search_text(alias_text)
    if not normalized:
        return True
    tokens = [token for token in normalized.replace("-", " ").split() if token]
    if not tokens:
        return True
    return all(token in _GENERIC_PLACE_ALIAS_TERMS for token in tokens)


def _is_high_confidence_match(match: dict[str, Any] | None) -> bool:
    if not isinstance(match, dict):
        return False
    confidence = str(match.get("confidence") or "").strip().lower()
    if confidence == "high":
        return True
    try:
        score = float(match.get("match_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return score >= 92.0


def _extract_contact_scoped_place_hint(where_text: str) -> dict[str, str] | None:
    text = str(where_text or "").strip()
    if not text:
        return None

    patterns = [
        re.compile(r"^(?P<person>.+?)\s*'s\s+(?P<role>[a-zA-Z\s]+)$", flags=re.IGNORECASE),
        re.compile(r"^(?P<role>[a-zA-Z\s]+)\s+of\s+(?P<person>.+?)$", flags=re.IGNORECASE),
        re.compile(r"^at\s+(?P<person>.+?)\s+(?P<role>[a-zA-Z\s]+)$", flags=re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.match(text)
        if not match:
            continue
        person = str(match.group("person") or "").strip()
        role = str(match.group("role") or "").strip()
        normalized_role = _normalize_role_hint(role)
        if person and normalized_role:
            return {
                "person_text": person,
                "role": normalized_role,
                "raw_role": role,
            }
    return None


def _resolve_contact_id_from_resolution(
    person_text: str,
    resolution: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    normalized_target = normalize_search_text(person_text)
    if not normalized_target:
        return None, None, None, None

    candidates: list[tuple[str, str, str, str | None]] = []
    for contact in resolution.get("contacts", []):
        if not isinstance(contact, dict):
            continue
        contact_id = str(contact.get("contact_id") or "").strip()
        display_name = str(contact.get("display_name") or "").strip()
        query_text = str(contact.get("query") or "").strip()
        confidence = str(contact.get("confidence") or "").strip() or None
        if not contact_id:
            continue
        candidates.append((contact_id, display_name, query_text, confidence))

    for contact_id, display_name, query_text, confidence in candidates:
        if normalize_search_text(display_name) == normalized_target:
            return contact_id, display_name or None, query_text or None, confidence
        if query_text and normalize_search_text(query_text) == normalized_target:
            return contact_id, display_name or None, query_text or None, confidence

    return None, None, None, None


def _emit_progress(context: dict[str, Any], message: str) -> None:
    callback = context.get("progress_callback")
    if callable(callback):
        callback(message)


def _normalize_event_field_ids(raw_fields: Any) -> list[str]:
    if not isinstance(raw_fields, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_fields:
        field_id = str(raw or "").strip().lower()
        if not field_id or field_id not in _EVENT_FIELD_RULES:
            continue
        if field_id in seen:
            continue
        seen.add(field_id)
        normalized.append(field_id)
    return normalized


def _has_assistant_clarification_prompt(
    clarification_messages: list[dict[str, str]] | None,
) -> bool:
    if not clarification_messages:
        return False
    return any(
        str(entry.get("role") or "").strip().lower() == "assistant"
        and bool(str(entry.get("content") or "").strip())
        for entry in clarification_messages
    )


def _format_field_inference_extraction_context(existing_extraction: dict[str, Any]) -> str:
    when_value = existing_extraction.get("when")
    if isinstance(when_value, datetime):
        when_value = when_value.isoformat()
    end_when_value = existing_extraction.get("end_when")
    if isinstance(end_when_value, datetime):
        end_when_value = end_when_value.isoformat()

    return (
        "Current extracted event fields:\n"
        f"- title: {existing_extraction.get('title')!r}\n"
        f"- summary: {existing_extraction.get('summary')!r}\n"
        f"- when: {when_value!r}\n"
        f"- end_when: {end_when_value!r}\n"
        f"- where: {existing_extraction.get('where')!r}\n"
        f"- tags: {existing_extraction.get('tags')!r}\n"
        f"- types: {existing_extraction.get('types')!r}\n"
        f"- who: {existing_extraction.get('who')!r}\n"
    )


def _infer_follow_up_target_fields(
    follow_up_message: str,
    existing_extraction: dict[str, Any],
    context: dict[str, Any],
) -> list[str]:
    from llm_helpers import call_llm_json
    from prompts.context import get_time_context, get_user_facts_context

    user_email = str(context.get("user_email") or "").strip()
    user_facts_ctx = get_user_facts_context(user_email, follow_up_message) if user_email else None
    user_facts_block = f"\n{user_facts_ctx}\n" if user_facts_ctx else ""
    extraction_context = _format_field_inference_extraction_context(existing_extraction)
    time_context = get_time_context()

    prompt = f"""You classify which event fields the user is trying to update in a follow-up message.

Current context:
- Date/time: {time_context}
- User: {user_email}
{user_facts_block}
{extraction_context}

User follow-up message:
\"{follow_up_message}\"

Choose only from these fields:
- title
- summary
- when
- end_when
- where
- tags
- types
- who

Rules:
- Select the smallest set of fields that should change.
- If user only changes location (e.g. "it happened at my office"), return ["where"].
- If user clarifies people/participants, include "who".
- If unsure, return an empty list with low confidence.

Return ONLY valid JSON with this exact shape:
{{
  "fields": ["where"],
  "confidence": "high"
}}"""

    try:
        classification = call_llm_json(prompt, timeout=20)
    except Exception as exc:
        logger.warning(
            "[handle_event] Failed to infer follow-up target fields: %s",
            exc,
            exc_info=exc,
        )
        return []

    fields = _normalize_event_field_ids(classification.get("fields"))
    confidence = str(classification.get("confidence") or "").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    if confidence == "low":
        return []
    return fields


def _format_target_field_context_for_prompt(
    target_fields: list[str],
    existing_extraction: dict[str, Any] | None,
    lock_existing_fields: bool,
) -> str:
    if not target_fields or not existing_extraction:
        return ""

    extraction_fields = [
        field for field, rules in _EVENT_FIELD_RULES.items() if bool(rules.get("extractable"))
    ]
    locked_fields = [field for field in extraction_fields if field not in target_fields]
    locked_lines: list[str] = []
    for field in locked_fields:
        value = existing_extraction.get(field)
        if isinstance(value, datetime):
            value = value.isoformat()
        locked_lines.append(f"- {field}: {value!r}")

    update_lines = "\n".join(f"- {field}" for field in target_fields)
    lock_instruction = (
        "Preserve locked fields exactly unless the user explicitly corrects them in this turn."
        if lock_existing_fields
        else "Prefer preserving locked fields when the user did not mention them."
    )

    return (
        "Follow-up update scope:\n"
        "Fields to update this turn:\n"
        f"{update_lines}\n"
        "Locked fields:\n"
        f"{'\\n'.join(locked_lines)}\n"
        f"{lock_instruction}\n\n"
    )


def _format_existing_extraction_for_prompt(existing: dict[str, Any] | None) -> str:
    if not existing:
        return ""

    when_value = existing.get("when")
    if isinstance(when_value, datetime):
        when_value = when_value.isoformat()
    end_when_value = existing.get("end_when")
    if isinstance(end_when_value, datetime):
        end_when_value = end_when_value.isoformat()

    return (
        "Existing extraction (use as base, update only if new details override):\n"
        f"- title: {existing.get('title')!r}\n"
        f"- summary: {existing.get('summary')!r}\n"
        f"- when: {when_value!r}\n"
        f"- end_when: {end_when_value!r}\n"
        f"- where: {existing.get('where')!r}\n"
        f"- documents: {existing.get('documents')!r}\n"
        f"- tags: {existing.get('tags')!r}\n"
        f"- types: {existing.get('types')!r}\n"
        "\n"
    )


def _format_clarification_history(
    clarification_messages: list[dict[str, str]] | None,
) -> str:
    if not clarification_messages:
        return ""

    timeline_lines: list[str] = []
    for entry in clarification_messages:
        role = (entry.get("role") or "").strip().lower()
        content = (entry.get("content") or "").strip()
        if not role or not content:
            continue
        if role not in {"assistant", "user"}:
            continue
        timeline_lines.append(f"- {role}: {content}")

    if not timeline_lines:
        return ""

    return (
        "Clarification transcript (chronological, oldest first):\n"
        + "\n".join(timeline_lines)
        + "\n\n"
    )


def _format_conversation_json(
    original_message: str,
    clarification_messages: list[dict[str, str]] | None,
) -> str:
    import json

    messages: list[dict[str, str]] = [{"role": "user", "content": original_message}]
    if clarification_messages:
        for entry in clarification_messages:
            role = entry.get("role")
            content = entry.get("content")
            if not role or not content:
                continue
            if role == "user" and content.strip().lower() == original_message.strip().lower():
                continue
            messages.append({"role": role, "content": content})

    return json.dumps(messages, ensure_ascii=True)


def _build_contact_context_message(
    original_message: str,
    clarification_messages: list[dict[str, str]] | None,
) -> str:
    normalized_original = (original_message or "").strip()
    user_messages: list[str] = []
    if clarification_messages:
        for entry in clarification_messages:
            if entry.get("role") != "user":
                continue
            content = entry.get("content")
            if not content:
                continue
            if normalized_original and content.strip().lower() == normalized_original.lower():
                continue
            user_messages.append(content)

    combined: list[str] = []
    for msg in user_messages:
        normalized = msg.strip()
        if not normalized:
            continue
        if any(normalized.lower() == existing.lower() for existing in combined):
            continue
        combined.append(normalized)

    if not combined:
        return normalized_original

    lines = [f"Original event description: {normalized_original}", ""]
    lines.append("Clarification details (chronological, oldest first):")
    for msg in combined:
        lines.append(f"- {msg}")
    return "\n".join(lines).strip()


def _extract_clarification_detail(message: str, original_message: str) -> str:
    normalized_message = (message or "").strip()
    if not normalized_message:
        return ""

    marker_match = re.search(r"additional details:\s*", normalized_message, flags=re.IGNORECASE)
    if marker_match:
        detail = normalized_message[marker_match.end() :].strip()
        if detail:
            return detail

    normalized_original = (original_message or "").strip()
    if normalized_original and normalized_message.lower().startswith(normalized_original.lower()):
        detail = normalized_message[len(normalized_original) :].strip(" \n:-")
        if detail:
            return detail

    return normalized_message


def _resolve_ambiguous_contacts_from_answer(
    ambiguous_contacts: list[dict[str, Any]],
    answer: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    answer_lower = answer.lower()

    for item in ambiguous_contacts:
        candidates = item.get("candidates", [])
        matches = [
            candidate
            for candidate in candidates
            if (candidate.get("display_name") or "").lower() in answer_lower
        ]
        if len(matches) == 1:
            candidate = matches[0]
            resolved.append(
                {
                    "original_text": item.get("original_text"),
                    "contact_id": candidate.get("contact_id"),
                    "display_name": candidate.get("display_name"),
                    "matched_via": "clarification",
                    "confidence": "high",
                }
            )
        else:
            remaining.append(item)

    return resolved, remaining


def _should_skip_contact_resolution(
    answer: str,
    ambiguous_contacts: list[dict[str, Any]],
) -> bool:
    if not ambiguous_contacts:
        return False

    answer_lower = answer.lower()
    candidate_names: list[str] = []
    for item in ambiguous_contacts:
        for candidate in item.get("candidates", []):
            name = (candidate.get("display_name") or "").strip()
            if name:
                candidate_names.append(name.lower())

    if not candidate_names:
        return False

    has_candidate = any(name in answer_lower for name in candidate_names)
    short_answer = len(answer.strip()) <= 48
    return has_candidate and short_answer


def _normalize_group_confirmation_token(value: str) -> bool | None:
    token = (value or "").strip().lower()
    if not token:
        return None
    if token in {"yes", "y", "true", "confirm", "confirmed", "save", "keep"}:
        return True
    if token in {"no", "n", "false", "skip", "dont", "don't", "ignore", "discard"}:
        return False
    return None


def _apply_group_confirmation_from_answer(
    groups: list[dict[str, Any]],
    answer: str,
) -> tuple[list[dict[str, Any]], bool]:
    if not groups:
        return groups, False

    text = (answer or "").strip()
    if not text:
        return groups, False

    updated_groups: list[dict[str, Any]] = []
    any_updates = False
    answer_lower = text.lower()

    # Global shortcut for single-group flows.
    global_decision = _normalize_group_confirmation_token(answer_lower)

    for group in groups:
        current = dict(group)
        if isinstance(current.get("confirmed"), bool):
            updated_groups.append(current)
            continue

        group_name = str(current.get("name") or "").strip()
        if not group_name:
            updated_groups.append(current)
            continue

        decision: bool | None = None
        escaped = re.escape(group_name.lower())

        # Prefer explicit "<group>: yes/no" style.
        explicit = re.search(
            rf"{escaped}\s*[:=-]\s*(yes|no|y|n|true|false|save|skip|confirm|ignore)",
            answer_lower,
        )
        if explicit:
            decision = _normalize_group_confirmation_token(explicit.group(1))

        # Fallback: local phrase with group mention + positive/negative cue.
        if decision is None and group_name.lower() in answer_lower:
            window = answer_lower
            if re.search(
                rf"(?:save|confirm|keep)\s+{escaped}|{escaped}\s+(?:yes|save|confirm|keep)", window
            ):
                decision = True
            elif re.search(
                rf"(?:do\s+not\s+save|dont\s+save|don't\s+save|skip|ignore)\s+{escaped}|{escaped}\s+(?:no|skip|ignore)",
                window,
            ):
                decision = False

        # Final fallback: single-group answer with global yes/no token.
        if decision is None and len(groups) == 1:
            decision = global_decision
        if decision is None and len(groups) == 1:
            if re.search(r"\b(yes|save|confirm|keep)\b", answer_lower):
                decision = True
            elif re.search(r"\b(no|skip|ignore|dont|don't)\b", answer_lower):
                decision = False

        if decision is not None:
            current["confirmed"] = decision
            any_updates = True

        updated_groups.append(current)

    return updated_groups, any_updates


def _clarification_fields_from_proposed_groups(
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for idx, group in enumerate(groups[:6]):
        name = str(group.get("name") or "").strip()
        if not name:
            continue
        field_id = f"group_confirm_{idx + 1}"
        fields.append(
            {
                "id": field_id,
                "kind": "select",
                "label": f'Save reusable group "{name}"?',
                "required": True,
                "options": [
                    {"id": "yes", "label": "Yes"},
                    {"id": "no", "label": "No"},
                ],
            }
        )
    return fields


def _extract_event_entities_with_llm(
    message: str,
    context: dict,
    existing_extraction: dict[str, Any] | None = None,
    clarification_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Use the existing LLM infrastructure to extract event entities.

    Args:
        message: The event description from the user
        context: Context dict with user info and time context

    Returns:
        Dict with extracted entities and optional need_user_input envelope
    """
    from llm_helpers import call_llm_json
    from prompts.clarification import append_clarification_guidelines
    from prompts.context import get_time_context, get_user_facts_context
    from tags_manager import MAJOR_TAGS

    logger.info("[event_extraction] Starting extraction for: '%s'", message)

    # Get current time context
    time_context = get_time_context()
    user_email = context.get("user_email", "")

    # Get user facts for personalization (timezone, preferences, common locations, etc.)
    user_facts_ctx = get_user_facts_context(user_email, message) if user_email else None

    logger.debug("[event_extraction] Time context: %s", time_context)
    logger.debug("[event_extraction] User: %s", user_email)
    logger.debug("[event_extraction] User facts: %s", "yes" if user_facts_ctx else "none")

    # Build tag context
    tag_examples = ", ".join(MAJOR_TAGS[:5])  # Show first 5 major tags as examples

    target_fields = _normalize_event_field_ids(context.get("event_target_fields"))
    extraction_target_fields = [
        field
        for field in target_fields
        if bool(_EVENT_FIELD_RULES.get(field, {}).get("extractable"))
    ]
    lock_existing_fields = bool(context.get("event_lock_existing_fields"))

    existing_context = _format_existing_extraction_for_prompt(existing_extraction)
    target_field_context = _format_target_field_context_for_prompt(
        extraction_target_fields,
        existing_extraction,
        lock_existing_fields,
    )
    clarification_context = _format_clarification_history(clarification_messages)
    conversation_json = _format_conversation_json(message, clarification_messages)
    need_user_input_guidance = build_need_user_input_prompt_guidance(exclude_people=True)
    need_user_input_template = need_user_input_json_property_template(indent=4)
    conversation_context = (
        f"Conversation messages (JSON array, most recent last):\n{conversation_json}\n\n"
    )

    user_facts_block = f"\n{user_facts_ctx}\n" if user_facts_ctx else ""
    extraction_prompt = f"""You are extracting structured information from a user's event description to create a memory entry.

Current context:
- Date/time: {time_context}
- User: {user_email}
{user_facts_block}
Event description: "{message}"

{existing_context}{target_field_context}{conversation_context}{clarification_context}

Extract the following information:
1. **What happened**: A brief title (5-10 words) and detailed summary
2. **When**: Parse event start date/time. Time is optional. Return ISO date (YYYY-MM-DD) when only date is known, or ISO datetime when time is known.
3. **End**: Parse optional end date/time if present. Return null if not mentioned.
4. **Where**: Location/place name (if mentioned)
5. **Documents**: References to documents/files (if mentioned)
6. **Tags**: Relevant tags for categorization. Consider major categories like: {tag_examples}, etc.
7. **Event types**: Choose from: generic, meeting, communication, task, creation, consumption, travel, personal, system, financial, observation, interaction, education, celebration, purchase, health

People extraction is handled separately. Do NOT include any people/person list.

Prefer specific types over general terms WHEN POSSIBLE (e.g., "Electric Engineer" over "Engineer", "Orthopedist" over "Doctor").

{need_user_input_guidance}

Use the clarification history to avoid repeating questions that were already answered.
Never drop previously confirmed facts from the existing extraction or clarification history; only override if the user explicitly corrects them.
Assistant questions are prompts only and are NOT facts; only treat user-provided details as facts.
If you think there are not enough information to build a valuable event, return a clarification to the user.

Return ONLY valid JSON in this exact format:
{{
{need_user_input_template}
    "title": "Brief title",
    "summary": "Detailed description",
    "when": "ISO 8601 date/datetime or null",
    "end_when": "ISO 8601 datetime or null",
    "where": "Location name or null",
    "documents": [],
    "tags": ["tag1", "tag2"],
    "types": ["generic"]
}}"""
    extraction_prompt = append_clarification_guidelines(extraction_prompt)

    try:
        logger.info("[event_extraction] Calling LLM for extraction...")
        extracted = call_llm_json(extraction_prompt, timeout=60)

        logger.debug("[event_extraction] Raw LLM response")
        logger.debug("[event_extraction]   - Title: %s", extracted.get("title"))
        logger.debug("[event_extraction]   - Summary: %s", extracted.get("summary"))
        logger.debug("[event_extraction]   - When: %s", extracted.get("when"))
        logger.debug("[event_extraction]   - Where: %s", extracted.get("where"))
        logger.debug("[event_extraction]   - Tags: %s", extracted.get("tags"))
        logger.debug("[event_extraction]   - Types: %s", extracted.get("types"))
        logger.debug(
            "[event_extraction]   - Needs user input: %s",
            bool(extracted.get("need_user_input")),
        )
        logger.debug(
            "[event_extraction]   - Clarification fields: %s",
            len(
                (extracted.get("need_user_input") or {}).get("fields", [])
                if isinstance(extracted.get("need_user_input"), dict)
                else []
            ),
        )

        def _parse_optional_datetime(raw: Any, field_name: str) -> datetime | None:
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                logger.debug("[event_extraction] Parsed %s: %s", field_name, parsed)
                return parsed
            except (ValueError, AttributeError) as exc:
                logger.warning(
                    "[event_extraction] Failed to parse %s '%s': %s",
                    field_name,
                    raw,
                    exc,
                    exc_info=exc,
                )
                return None

        when = _parse_optional_datetime(extracted.get("when"), "when")
        end_when = _parse_optional_datetime(extracted.get("end_when"), "end_when")

        need_user_input = normalize_need_user_input(extracted.get("need_user_input"))

        result = {
            "need_user_input": need_user_input,
            "title": extracted.get("title", message[:100]),
            "summary": extracted.get("summary", message),
            "when": when,
            "end_when": end_when,
            "where": extracted.get("where"),
            "who": [],
            "documents": extracted.get("documents", []),
            "tags": extracted.get("tags", []),
            "types": extracted.get("types", ["generic"]),
        }

        if existing_extraction:
            for key in [
                "title",
                "summary",
                "when",
                "end_when",
                "where",
                "documents",
                "tags",
                "types",
            ]:
                if extraction_target_fields and key not in extraction_target_fields:
                    result[key] = existing_extraction.get(key)
                    continue
                if result.get(key) in (None, "", [], ["generic"]) and existing_extraction.get(key):
                    result[key] = existing_extraction[key]

        logger.info("[event_extraction] Extraction complete")
        return result

    except Exception as e:
        logger.exception("[event_extraction] LLM extraction failed: %s", e)

        # Fallback to basic extraction
        return {
            "need_user_input": build_need_user_input(
                kind="clarification",
                source="event_extraction",
                prompt="Could you provide more details about what happened?",
                questions=["Could you provide more details about what happened?"],
                fields=[default_clarification_details_field()],
                submission_mode="ui_submission",
            ),
            "title": message[:100],
            "summary": message,
            "when": None,
            "end_when": None,
            "where": None,
            "who": [],
            "documents": [],
            "tags": [],
            "types": ["generic"],
        }


def _extract_clarification_token(message: str) -> tuple[str, str | None]:
    token_pattern = re.compile(r"\[clarification_id:(?P<id>[\w:-]+)\]", re.IGNORECASE)
    match = token_pattern.search(message)
    if not match:
        return message, None

    cleaned = token_pattern.sub("", message).strip()
    return cleaned, match.group("id")


def _resolve_generic_terms_with_relationships(
    terms: list[str],
    user_email: str,
) -> dict[str, str]:
    """
    Resolve generic relational terms to actual contact names using relationship data.

    Examples:
    - "my daughter" -> "Emma" (if user has daughter relationship)
    - "the doctor" -> "Dr. Smith" (if user has doctor relationship)
    - "my wife" -> "Sarah" (if user has spouse relationship)

    Args:
        terms: List of terms that might be generic (e.g., ["my daughter", "the doctor"])
        user_email: User's email to find their contact and relationships

    Returns:
        Dict mapping generic terms to actual names (e.g., {"my daughter": "Emma"})
    """
    import contacts as contacts_service

    logger.info("[generic_resolution] Attempting to resolve %s terms: %s", len(terms), terms)

    resolved = {}

    # Find user's contact record
    user_contact = contacts_service.find_self_contact(user_email)
    if not user_contact:
        logger.warning("[generic_resolution] User contact not found for: %s", user_email)
        return resolved

    user_id = user_contact["contact_id"]
    logger.debug(
        "[generic_resolution] User contact ID: %s (%s)",
        user_id,
        user_contact.get("display_name"),
    )

    # Get all relationships for the user
    relationships_result = contacts_service.get_contact_relationships(
        user_id,
        include_contact_details=True,
    )

    relationships = relationships_result.get("relationships", [])
    logger.debug("[generic_resolution] Found %s relationships", len(relationships))

    # Build a map of relationship types to contacts
    rel_map: dict[str, list[dict]] = {}
    for rel in relationships:
        rel_type = (rel.get("type") or "").lower()
        if rel_type and "related_contact" in rel:
            if rel_type not in rel_map:
                rel_map[rel_type] = []
            rel_map[rel_type].append(rel["related_contact"])

    if rel_map:
        logger.debug(
            "[generic_resolution] Relationship types available: %s",
            list(rel_map.keys()),
        )
    else:
        logger.info("[generic_resolution] No relationships with contact details found")

    # Try to resolve each term
    for term in terms:
        term_lower = term.lower().strip()
        logger.debug("[generic_resolution] Processing term: '%s'", term)

        # Extract relationship type from phrases like "my daughter", "the doctor", "user's daughter"
        # Remove possessives, articles, and "user's"
        cleaned = (
            term_lower.replace("user's ", "")
            .replace("my ", "")
            .replace("the ", "")
            .replace("a ", "")
            .replace("an ", "")
            .strip()
        )
        logger.debug("[generic_resolution]   Cleaned to: '%s'", cleaned)

        # Direct match first
        if cleaned in rel_map and rel_map[cleaned]:
            contact = rel_map[cleaned][0]
            resolved_name = contact.get("display_name", term)
            resolved[term] = resolved_name
            logger.info(
                "[generic_resolution] Direct match: '%s' -> '%s'",
                term,
                resolved_name,
            )
            continue

        # Smart matching: look for related relationship types
        # For example: "daughter" should match "child", "father" should match "parent"
        # Use the shared relationship type mappings from contacts module
        possible_types = contacts_service.find_related_types(cleaned)
        logger.debug(
            "[generic_resolution]   Trying relationship types: %s",
            possible_types,
        )

        for rel_type in possible_types:
            if rel_type in rel_map and rel_map[rel_type]:
                contact = rel_map[rel_type][0]
                resolved_name = contact.get("display_name", term)
                resolved[term] = resolved_name
                logger.info(
                    "[generic_resolution] Smart match via '%s': '%s' -> '%s'",
                    rel_type,
                    term,
                    resolved_name,
                )
                break
        else:
            logger.info(
                "[generic_resolution] No match for '%s' or related types",
                cleaned,
            )

    logger.info(
        "[generic_resolution] Resolution complete. Resolved %s/%s terms",
        len(resolved),
        len(terms),
    )
    return resolved


def _replace_generic_terms_in_text(
    text: str,
    replacements: dict[str, str],
) -> str:
    """
    Replace generic terms with actual names in text.

    Args:
        text: Original text with generic terms
        replacements: Dict mapping generic terms to actual names

    Returns:
        Text with generic terms replaced
    """
    result = text
    for generic, actual in replacements.items():
        # Case-insensitive replacement that preserves case structure
        pattern = re.compile(re.escape(generic), re.IGNORECASE)
        result = pattern.sub(actual, result)
    return result


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _dedupe_contacts(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for contact in contacts:
        contact_id = str(contact.get("contact_id") or "")
        display_name = (contact.get("display_name") or "").strip()
        if contact_id and contact_id in seen_ids:
            continue
        if display_name and display_name.lower() in seen_names:
            continue
        if contact_id:
            seen_ids.add(contact_id)
        if display_name:
            seen_names.add(display_name.lower())
        deduped.append(contact)
    return deduped


def _resolve_contacts_with_agent(
    message: str,
    user_email: str,
    conversation_messages: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Resolve contacts for the event using the contact resolution agent.

    Returns:
        Tuple of:
        - resolution dict in /event shape
        - raw contact agent result
    """
    from agents.contacts import resolve_contacts_from_text

    contact_result = resolve_contacts_from_text(
        message,
        user_email,
        conversation_messages=conversation_messages,
    )

    group_upsert_candidates = contact_result.get("group_upsert_candidates", [])
    if isinstance(group_upsert_candidates, list) and group_upsert_candidates:
        try:
            import contact_groups as contact_groups_service

            for candidate in group_upsert_candidates:
                if not isinstance(candidate, dict):
                    continue
                member_contact_ids = [
                    str(contact_id or "").strip()
                    for contact_id in (candidate.get("contact_ids") or [])
                    if str(contact_id or "").strip()
                ]
                if not member_contact_ids:
                    continue
                contact_groups_service.upsert_group_from_selector(
                    user_email=user_email,
                    name=str(candidate.get("name") or "").strip(),
                    member_contact_ids=member_contact_ids,
                    aliases=[
                        str(alias).strip()
                        for alias in (candidate.get("aliases") or [])
                        if str(alias).strip()
                    ],
                    description=str(candidate.get("description") or "").strip() or None,
                    source=str(candidate.get("source") or "deterministic"),
                    confirmed=bool(candidate.get("confirmed", True)),
                    replace_members=bool(candidate.get("replace_members", False)),
                    added_via=str(candidate.get("added_via") or "selector"),
                    confidence=0.9,
                )
        except Exception as exc:
            logger.warning(
                "[handle_event] Failed to upsert inferred contact groups: %s",
                exc,
                exc_info=exc,
            )

    resolution = {
        "contacts": [],
        "places": [],
        "documents": [],
        "new_entities": {
            "contacts": [],
            "places": [],
            "documents": [],
        },
        "name_replacements": {},
        "proposed_contact_groups": [],
    }

    for candidate in contact_result.get("group_confirmation_candidates", []):
        if isinstance(candidate, dict):
            resolution["proposed_contact_groups"].append(candidate)

    for resolved in contact_result.get("resolved_contacts", []):
        resolution["contacts"].append(
            {
                "contact_id": resolved.get("contact_id"),
                "display_name": resolved.get("display_name"),
                "query": resolved.get("original_text"),
                "confidence": resolved.get("confidence", "medium"),
            }
        )

        original_text = resolved.get("original_text")
        display_name = resolved.get("display_name")
        if original_text and display_name and original_text.lower() != display_name.lower():
            if original_text.lower() != "user":
                resolution["name_replacements"][original_text] = display_name

    for new_contact in contact_result.get("new_contacts", []):
        original_text = new_contact.get("original_text")
        display_name = new_contact.get("display_name") or original_text
        resolution["new_entities"]["contacts"].append(
            {
                "display_name": display_name,
                "query": original_text or display_name,
                "inferred_profession": new_contact.get("inferred_profession"),
            }
        )

        if original_text and display_name and original_text.lower() != display_name.lower():
            if original_text.lower() != "user":
                resolution["name_replacements"][original_text] = display_name

    return resolution, contact_result


def _format_relationship_suggestions(
    suggestions: list[dict[str, Any]],
    resolution: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Map contact agent relationship suggestions into /event UI shape.
    """
    if not suggestions:
        return []

    id_by_text: dict[str, str] = {}
    name_by_text: dict[str, str] = {}

    for contact in resolution.get("contacts", []):
        display_name = contact.get("display_name")
        query = contact.get("query")
        contact_id = contact.get("contact_id")
        if query and display_name:
            name_by_text[query] = display_name
        if display_name:
            name_by_text[display_name] = display_name
        if contact_id:
            if query:
                id_by_text[query] = contact_id
            if display_name:
                id_by_text[display_name] = contact_id

    for contact in resolution.get("new_entities", {}).get("contacts", []):
        display_name = contact.get("display_name")
        query = contact.get("query")
        if query and display_name:
            name_by_text[query] = display_name
        if display_name:
            name_by_text[display_name] = display_name

    formatted: list[dict[str, Any]] = []
    for suggestion in suggestions:
        from_text = suggestion.get("from_text")
        to_text = suggestion.get("to_text")
        if not from_text or not to_text:
            continue

        from_display = name_by_text.get(from_text, from_text)
        to_display = name_by_text.get(to_text, to_text)

        formatted.append(
            {
                "from_contact_id": suggestion.get("from_contact_id") or id_by_text.get(from_text),
                "from_display_name": from_display,
                "to_contact_id": suggestion.get("to_contact_id") or id_by_text.get(to_text),
                "to_display_name": to_display,
                "relationship_type": suggestion.get("type") or "",
                "reciprocal_type": suggestion.get("other_type") or "",
                "confidence": "medium",
                "reasoning": suggestion.get("relationship_hint") or "",
            }
        )

    return formatted


def _suggest_relationships_from_context(
    message: str,
    extracted: dict[str, Any],
    resolution: dict[str, Any],
    user_email: str = "",
) -> list[dict[str, Any]]:
    """
    Analyze the event context to suggest relationships between contacts.

    Examples:
    - "took my daughter to the doctor" -> suggest doctor-patient relationship
    - "had lunch with my colleague John" -> suggest colleague relationship

    Args:
        message: Original event message
        extracted: Extracted event data
        resolution: Resolved entities

    Returns:
        List of suggested relationships with from/to contacts and type
    """
    from llm_helpers import call_llm_json
    from prompts.context import get_user_facts_context

    logger.info("[relationship_suggestion] Analyzing event for relationship suggestions")

    suggestions = []

    # Get all resolved contacts
    contacts = resolution.get("contacts", [])
    logger.debug(
        "[relationship_suggestion] Found %s resolved contacts",
        len(contacts),
    )

    if len(contacts) < 2:
        # Need at least 2 contacts to suggest relationships
        logger.info("[relationship_suggestion] Not enough contacts (need at least 2), skipping")
        return suggestions

    # Use LLM to detect implied relationships
    contact_list = ", ".join(c["display_name"] for c in contacts)
    logger.debug(
        "[relationship_suggestion] Analyzing relationships between: %s",
        contact_list,
    )

    user_facts_ctx = get_user_facts_context(user_email, message) if user_email else None
    user_facts_section = f"\n{user_facts_ctx}" if user_facts_ctx else ""

    prompt = f"""Analyze this event description and identify any implied relationships between the people mentioned.

Event: "{message}"
People involved: {contact_list}{user_facts_section}

Common relationship types:
- Family: parent, child, sibling, spouse, partner, grandparent, grandchild, cousin, uncle, aunt, nephew, niece
- Professional: colleague, manager, employee, client, vendor, doctor, patient, lawyer, therapist, teacher, student
- Social: friend, neighbor, acquaintance

Return ONLY valid JSON with suggested relationships. If no clear relationships, return empty array:
{{
    "relationships": [
        {{
            "from_person": "exact name from list",
            "to_person": "exact name from list",
            "relationship_type": "type",
            "reciprocal_type": "type from other perspective",
            "confidence": "high|medium|low",
            "reasoning": "brief explanation"
        }}
    ]
}}"""

    try:
        logger.info("[relationship_suggestion] Calling LLM for relationship analysis...")
        result = call_llm_json(prompt, timeout=15)
        llm_suggestions = result.get("relationships", [])

        logger.debug(
            "[relationship_suggestion] LLM returned %s suggestions",
            len(llm_suggestions),
        )

        # Map names back to contact IDs
        name_to_id = {c["display_name"]: c["contact_id"] for c in contacts}

        for idx, sug in enumerate(llm_suggestions):
            from_name = sug.get("from_person")
            to_name = sug.get("to_person")
            rel_type = sug.get("relationship_type")
            reciprocal = sug.get("reciprocal_type")
            confidence = sug.get("confidence")

            logger.debug("[relationship_suggestion]   Suggestion %s:", idx + 1)
            logger.debug("[relationship_suggestion]     %s -> %s", from_name, to_name)
            logger.debug(
                "[relationship_suggestion]     Type: %s (reciprocal: %s)",
                rel_type,
                reciprocal,
            )
            logger.debug("[relationship_suggestion]     Confidence: %s", confidence)
            logger.debug(
                "[relationship_suggestion]     Reasoning: %s",
                sug.get("reasoning"),
            )

            if from_name in name_to_id and to_name in name_to_id:
                suggestions.append(
                    {
                        "from_contact_id": name_to_id[from_name],
                        "from_display_name": from_name,
                        "to_contact_id": name_to_id[to_name],
                        "to_display_name": to_name,
                        "relationship_type": rel_type or "",
                        "reciprocal_type": reciprocal or "",
                        "confidence": confidence or "medium",
                        "reasoning": sug.get("reasoning", ""),
                    }
                )
                logger.debug("[relationship_suggestion]     Added to suggestions")
            else:
                logger.info("[relationship_suggestion] Names not found in contact list, skipping")

        logger.info(
            "[relationship_suggestion] Suggestion complete. Created %s suggestions",
            len(suggestions),
        )

    except Exception as e:
        logger.exception("[relationship_suggestion] Relationship suggestion failed: %s", e)

    return suggestions


def _resolve_existing_entities(
    entities: dict[str, Any],
    user_email: str,
) -> dict[str, Any]:
    """
    Search database for existing entities using existing resolution tools.
    Also resolves generic terms to actual names using relationship data.

    Args:
        entities: Extracted entities from message
        user_email: User's email for relationship context

    Returns:
        Dict with matched and new entities, plus name replacements
    """
    import contacts as contacts_service

    logger.info("[entity_resolution] Starting entity resolution")

    resolution = {
        "contacts": [],
        "places": [],
        "documents": [],
        "new_entities": {
            "contacts": [],
            "places": [],
            "documents": [],
        },
        "name_replacements": {},  # Maps generic terms to actual names
    }

    # First, try to resolve generic terms using relationships
    who_list = entities.get("who", [])
    logger.debug("[entity_resolution] People to resolve: %s", who_list)

    if who_list:
        replacements = _resolve_generic_terms_with_relationships(who_list, user_email)
        resolution["name_replacements"] = replacements
        if replacements:
            logger.debug("[entity_resolution] Name replacements: %s", replacements)

    # Resolve contacts using existing search_contacts function
    for idx, person_name in enumerate(entities.get("who", []), 1):
        if not person_name or not isinstance(person_name, str):
            logger.info("[entity_resolution]   Person %s: Skipping invalid name", idx)
            continue

        # Use the actual name if we resolved a generic term
        search_name = resolution["name_replacements"].get(person_name, person_name)
        logger.debug(
            "[entity_resolution]   Person %s: '%s' -> searching for '%s'",
            idx,
            person_name,
            search_name,
        )

        matches = contacts_service.search_contacts(
            search_name,
            search_by="any",
            fuzzy_threshold=75,
            limit=3,
        )

        logger.debug("[entity_resolution]     Found %s matches", len(matches))

        if matches:
            # Add first match with confidence
            best_match = matches[0]
            match_score = best_match.get("match_score", 0)
            confidence = "high" if match_score > 90 else "medium"

            resolution["contacts"].append(
                {
                    "contact_id": best_match["contact_id"],
                    "display_name": best_match["display_name"],
                    "query": person_name,  # Original query
                    "confidence": confidence,
                }
            )
            logger.info(
                "[entity_resolution] Matched to existing: %s (ID: %s, score: %s, confidence: %s)",
                best_match["display_name"],
                best_match["contact_id"],
                match_score,
                confidence,
            )
        else:
            # Mark as new contact to create (use resolved name if available)
            display_name = resolution["name_replacements"].get(person_name, person_name)
            resolution["new_entities"]["contacts"].append(
                {
                    "display_name": display_name,
                    "query": person_name,
                }
            )
            logger.info(
                "[entity_resolution] No match, will create new contact: '%s'",
                display_name,
            )

    # Resolve places (simple implementation for now)
    where = entities.get("where")
    if where:
        logger.info("[entity_resolution] Place: '%s' -> creating new place", where)
        # TODO: Search for existing places
        # For now, always create new places
        resolution["new_entities"]["places"].append(
            {
                "name": where,
                "query": where,
            }
        )

    logger.info("[entity_resolution] Resolution complete")
    logger.info(
        "[entity_resolution]   - Matched contacts: %s",
        len(resolution["contacts"]),
    )
    logger.info(
        "[entity_resolution]   - New contacts: %s",
        len(resolution["new_entities"]["contacts"]),
    )
    logger.info(
        "[entity_resolution]   - New places: %s",
        len(resolution["new_entities"]["places"]),
    )

    return resolution


def handle_event(parsed: ParsedCommand, context: dict) -> dict[str, Any]:
    """
    Handle the /event command.

    Flow:
    1. Extract entities using LLM
    2. Check if clarification is needed
    3. Search for existing entities and resolve generic terms
    4. Replace generic terms in titles/summaries with actual names
    5. Suggest relationships between contacts
    6. Store data for confirmation
    7. Return confirmation request or ask for clarification

    Args:
        parsed: Parsed command with event description as args
        context: Context dict with user info

    Returns:
        Dict with event_confirmation or need_user_input type
    """
    logger.info("[handle_event] NEW EVENT COMMAND")
    _emit_progress(context, "Preparing event details...")

    if not parsed.args:
        return {
            "type": "error",
            "message": "Please provide an event description. Example: /event met with John at the cafe yesterday",
        }

    user_email = context.get("user_email", "")
    logger.debug("[handle_event] User: %s", user_email)
    raw_message, clarification_id = _extract_clarification_token(parsed.args)
    logger.debug("[handle_event] Input: '%s'", raw_message)

    clarification_context = None
    if clarification_id:
        from commands.storage import delete_command_data, get_command_data

        clarification_context = get_command_data(clarification_id)
        delete_command_data(clarification_id)
        if clarification_context:
            logger.info(
                "[handle_event] Found clarification context: %s",
                clarification_id,
            )
        else:
            logger.warning(
                "[handle_event] Clarification context missing or expired: %s",
                clarification_id,
            )

    clarification_messages = None
    event_message = raw_message
    contact_message = raw_message
    contact_result = None
    resolution = None
    previous_contact_result: dict[str, Any] = {}
    previous_resolution: dict[str, Any] = {}
    previous_relationship_suggestions: list[dict[str, Any]] = []
    target_field_ids: list[str] = []
    skip_contact_resolution = False
    original_message_to_store = raw_message
    if clarification_context:
        clarification_messages = clarification_context.get("clarification_messages")
        original_message = clarification_context.get("original_message") or raw_message
        clarification_detail = _extract_clarification_detail(raw_message, original_message)
        if clarification_detail:
            clarification_messages = list(clarification_messages or [])
            clarification_messages.append({"role": "user", "content": clarification_detail})
        original_message_to_store = original_message
        event_message = original_message
        contact_message = _build_contact_context_message(
            original_message,
            clarification_messages,
        )
        previous_contact_result = clarification_context.get("contact_result") or {}
        previous_resolution = clarification_context.get("resolution") or {}
        previous_relationship_suggestions = list(
            clarification_context.get("relationship_suggestions") or []
        )
        target_field_ids = _normalize_event_field_ids(
            clarification_context.get("requested_field_ids")
        )
        ambiguous_contacts = previous_contact_result.get("ambiguous_contacts", [])

        if raw_message and ambiguous_contacts:
            resolved_contacts, remaining_contacts = _resolve_ambiguous_contacts_from_answer(
                ambiguous_contacts,
                raw_message,
            )
            if resolved_contacts:
                previous_contact_result = {
                    **previous_contact_result,
                    "resolved_contacts": previous_contact_result.get("resolved_contacts", [])
                    + resolved_contacts,
                    "ambiguous_contacts": remaining_contacts,
                }
                resolved_entries = previous_resolution.get("contacts", [])
                for resolved_contact in resolved_contacts:
                    if not resolved_contact.get("contact_id"):
                        continue
                    resolved_entries.append(
                        {
                            "contact_id": resolved_contact.get("contact_id"),
                            "display_name": resolved_contact.get("display_name"),
                            "query": resolved_contact.get("original_text"),
                            "confidence": resolved_contact.get("confidence", "high"),
                        }
                    )
                previous_resolution["contacts"] = resolved_entries
                resolution = previous_resolution
                contact_result = previous_contact_result
                skip_contact_resolution = _should_skip_contact_resolution(
                    raw_message,
                    ambiguous_contacts,
                )

        if raw_message and previous_resolution.get("proposed_contact_groups"):
            updated_groups, changed = _apply_group_confirmation_from_answer(
                list(previous_resolution.get("proposed_contact_groups") or []),
                raw_message,
            )
            if changed:
                previous_resolution["proposed_contact_groups"] = updated_groups
                resolution = previous_resolution

        if not target_field_ids and raw_message and clarification_context.get("extracted"):
            if not _has_assistant_clarification_prompt(clarification_messages):
                inferred_fields = _infer_follow_up_target_fields(
                    raw_message,
                    clarification_context.get("extracted") or {},
                    context,
                )
                if inferred_fields:
                    target_field_ids = inferred_fields
                    logger.info(
                        "[handle_event] Inferred follow-up target fields: %s",
                        target_field_ids,
                    )

        if target_field_ids and "who" not in target_field_ids and previous_resolution:
            skip_contact_resolution = True
            resolution = resolution or previous_resolution
            contact_result = contact_result or previous_contact_result
            logger.info(
                "[handle_event] Skipping contact resolution for non-participant follow-up: %s",
                target_field_ids,
            )

    # Extract entities using LLM with time context
    logger.info("[handle_event] STEP 1: Extracting entities with LLM...")
    _emit_progress(context, "Extracting event entities...")
    extraction_context = dict(context)
    if target_field_ids:
        extraction_context["event_target_fields"] = target_field_ids
        extraction_context["event_lock_existing_fields"] = True
    with ThreadPoolExecutor(max_workers=2) as executor:
        extraction_future = executor.submit(
            _extract_event_entities_with_llm,
            event_message,
            extraction_context,
            clarification_context.get("extracted") if clarification_context else None,
            clarification_messages,
        )
        contact_future = None
        if not skip_contact_resolution:
            contact_future = executor.submit(
                _resolve_contacts_with_agent,
                contact_message,
                user_email,
                clarification_messages,
            )

        extracted = extraction_future.result()
        if contact_future:
            resolution, contact_result = contact_future.result()
        else:
            resolution = resolution or previous_resolution
            contact_result = contact_result or previous_contact_result

    # Check if clarification is needed
    event_need_user_input = normalize_need_user_input(extracted.get("need_user_input"))
    clarification_questions = list((event_need_user_input or {}).get("questions") or [])
    clarification_fields = normalize_clarification_fields(
        (event_need_user_input or {}).get("fields")
    )

    if event_need_user_input and not clarification_fields:
        clarification_fields = [default_clarification_details_field()]
    if event_need_user_input and not clarification_questions:
        clarification_questions = derive_clarification_questions_from_fields(clarification_fields)
    if event_need_user_input and not clarification_questions:
        fallback_prompt = str(event_need_user_input.get("prompt") or "").strip()
        if fallback_prompt:
            clarification_questions = [fallback_prompt]

    ambiguous_contacts = contact_result.get("ambiguous_contacts", []) if contact_result else []
    contact_need_user_input = None
    if ambiguous_contacts:
        logger.warning("[handle_event] Contact disambiguation needed")
        contact_need_user_input = build_need_user_input(
            kind="disambiguation",
            source="contact_resolution",
            prompt="I found multiple matching contacts. Please choose who you meant.",
            questions=["I found multiple matching contacts. Please choose who you meant."],
            fields=clarification_fields_from_ambiguous_contacts(ambiguous_contacts),
            submission_mode="ui_submission",
        )
        if contact_need_user_input:
            clarification_questions.extend(contact_need_user_input.get("questions", []))
            clarification_fields.extend(contact_need_user_input.get("fields", []))

    proposed_groups = list(resolution.get("proposed_contact_groups") or [])
    unresolved_groups = [
        group for group in proposed_groups if not isinstance(group.get("confirmed"), bool)
    ]
    group_need_user_input = None
    if unresolved_groups:
        group_names = [str(group.get("name") or "").strip() for group in unresolved_groups]
        group_names = [name for name in group_names if name]
        if group_names:
            prompt = (
                "Should I save these participant groups for reuse? " + ", ".join(group_names) + "."
            )
            group_need_user_input = build_need_user_input(
                kind="confirmation",
                source="event_command",
                prompt=prompt,
                questions=[
                    "Should I save these participant groups as reusable contact groups?",
                    "You can answer like 'soccer team: yes' or 'soccer team: no'.",
                ],
                fields=_clarification_fields_from_proposed_groups(unresolved_groups),
                submission_mode="ui_submission",
            )
            if group_need_user_input:
                clarification_questions.extend(group_need_user_input.get("questions", []))
                clarification_fields.extend(group_need_user_input.get("fields", []))

    # Keep order while deduping repeated questions.
    seen_questions: set[str] = set()
    deduped_questions: list[str] = []
    for question in clarification_questions:
        text = str(question).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen_questions:
            continue
        seen_questions.add(key)
        deduped_questions.append(text)
    clarification_questions = deduped_questions

    needs_follow_up = bool(
        event_need_user_input or contact_need_user_input or group_need_user_input
    )
    if needs_follow_up and not clarification_fields:
        clarification_fields = [default_clarification_details_field()]
    if needs_follow_up and not clarification_questions:
        clarification_questions = derive_clarification_questions_from_fields(clarification_fields)
    if needs_follow_up and not clarification_questions:
        clarification_questions = ["Please share the missing event details so I can continue."]

    if needs_follow_up:
        logger.warning("[handle_event] Clarification needed, returning questions to user")
        _emit_progress(context, "Building clarification request...")
        clarification_preview_id = f"event:clarification:{uuid4().hex[:8]}"
        action_id = f"event_clarification_submit:{clarification_preview_id}"
        need_user_input = build_need_user_input(
            kind=(
                "disambiguation"
                if contact_need_user_input and not event_need_user_input
                else "clarification"
            ),
            source="event_command",
            prompt=clarification_questions[0],
            questions=clarification_questions,
            fields=clarification_fields,
            action_id=action_id,
            submission_mode="ui_submission",
            context={
                "clarification_id": clarification_preview_id,
                "command": "event",
            },
        )

        from commands.storage import store_command_data, store_pending_event
        requested_field_ids = _normalize_event_field_ids(
            [field.get("id") for field in clarification_fields if isinstance(field, dict)]
        )

        if clarification_messages is None:
            clarification_messages = [
                {"role": "user", "content": raw_message},
            ]
        clarification_messages.append(
            {
                "role": "assistant",
                "content": (need_user_input or {}).get("prompt")
                or " ".join(clarification_questions),
            }
        )

        store_command_data(
            clarification_preview_id,
            {
                "extracted": extracted,
                "resolution": resolution,
                "contact_result": contact_result,
                "user_email": user_email,
                "original_message": original_message_to_store,
                "clarification_messages": clarification_messages,
                "requested_field_ids": requested_field_ids,
                "relationship_suggestions": previous_relationship_suggestions,
            },
        )
        pending_key = context.get("event_pending_key")
        if pending_key:
            store_pending_event(pending_key, clarification_preview_id)
        return {
            "type": "need_user_input",
            "need_user_input": need_user_input,
            "partial_extraction": extracted,
            "original_message": raw_message,
            "clarification_id": clarification_preview_id,
        }

    # Resolve existing entities and generic terms
    logger.info("[handle_event] STEP 2: Contact resolution complete")
    _emit_progress(context, "Resolving contacts...")

    if not isinstance(resolution, dict):
        resolution = {
            "contacts": [],
            "new_entities": {"contacts": [], "places": [], "documents": []},
        }

    where = str(extracted.get("where") or "").strip()
    client_location = _extract_client_location(context)
    inferred_location: dict[str, Any] | None = None
    where_source = "extracted"
    contact_place_hint: dict[str, Any] | None = None

    if not where:
        inferred_location = infer_current_place(client_location, user_email=user_email)
        inferred_name = ""
        if isinstance(inferred_location, dict):
            inferred_name = str(inferred_location.get("place_name") or "").strip()
        if inferred_name and isinstance(inferred_location, dict):
            where = inferred_name
            where_source = "inferred_location"
            extracted["where"] = inferred_name
            resolution["inferred_location"] = inferred_location
            logger.info(
                "[handle_event] Inferred place from location context: %s (source=%s)",
                inferred_name,
                inferred_location.get("source") or "unknown",
            )
            inferred_place_id = str(inferred_location.get("place_id") or "").strip()
            if inferred_place_id:
                resolution["matched_place"] = {
                    "place_id": inferred_place_id,
                    "name": inferred_name,
                    "confidence": str(inferred_location.get("confidence") or "medium"),
                    "matched_via": "inferred_location",
                }

    if where:
        extracted_contact_hint = _extract_contact_scoped_place_hint(where)
        if extracted_contact_hint:
            contact_id, display_name, matched_query, contact_confidence = (
                _resolve_contact_id_from_resolution(
                    extracted_contact_hint["person_text"],
                    resolution,
                )
            )
            if contact_id:
                contact_place_hint = {
                    "contact_id": contact_id,
                    "contact_display_name": display_name,
                    "contact_query": matched_query,
                    "role": extracted_contact_hint["role"],
                    "source": "event_inference",
                    "confidence": (
                        "high"
                        if str(contact_confidence or "").strip().lower() in {"high", "certain"}
                        else "medium"
                    ),
                }
                resolution["place_contact_hint"] = contact_place_hint

        matched_place = resolution.get("matched_place") if isinstance(resolution, dict) else None
        matched_place_id = (
            str(matched_place.get("place_id") or "").strip()
            if isinstance(matched_place, dict)
            else ""
        )

        if not matched_place_id and contact_place_hint:
            contact_place_match = places_service.resolve_contact_place(
                contact_id=str(contact_place_hint.get("contact_id") or ""),
                role_hint=str(contact_place_hint.get("role") or ""),
                where_text=where,
            )
            if contact_place_match:
                matched_name = str(contact_place_match.get("name") or where).strip() or where
                extracted["where"] = matched_name
                resolution["matched_place"] = {
                    "place_id": str(contact_place_match.get("place_id") or "").strip(),
                    "name": matched_name,
                    "confidence": contact_place_match.get("confidence") or "high",
                    "matched_via": contact_place_match.get("matched_via")
                    or "contact_place_relation",
                }
                where = matched_name
                matched_place_id = str(contact_place_match.get("place_id") or "").strip()

        if not matched_place_id:
            place_match = places_service.find_best_place_match(
                where,
                client_location=client_location,
            )
            if place_match:
                canonical_name = str(place_match.get("name") or where).strip() or where
                extracted["where"] = canonical_name
                resolution["matched_place"] = {
                    "place_id": str(place_match.get("place_id") or "").strip(),
                    "name": canonical_name,
                    "confidence": place_match.get("match_confidence"),
                    "matched_via": place_match.get("matched_via"),
                    "match_score": place_match.get("match_score"),
                }

                if (
                    where_source == "extracted"
                    and str(place_match.get("match_confidence") or "") == "high"
                    and where.casefold() != canonical_name.casefold()
                    and not _is_generic_place_alias(where)
                ):
                    resolution["matched_place"]["pending_alias"] = where
                where = canonical_name

        matched_place = resolution.get("matched_place") if isinstance(resolution, dict) else None
        matched_place_id = (
            str(matched_place.get("place_id") or "").strip()
            if isinstance(matched_place, dict)
            else ""
        )
        if not matched_place_id:
            new_place_payload: dict[str, Any] = {
                "name": where,
                "query": where,
            }

            if where_source == "inferred_location" and isinstance(inferred_location, dict):
                address = str(inferred_location.get("address") or "").strip()
                city = str(inferred_location.get("city") or "").strip()
                country = str(inferred_location.get("country") or "").strip()
                if address:
                    new_place_payload["address"] = address
                if city:
                    new_place_payload["city"] = city
                if country:
                    new_place_payload["country"] = country
                for coordinate in ("lat", "lon"):
                    value = inferred_location.get(coordinate)
                    if value is not None:
                        new_place_payload[coordinate] = value
            elif where_source == "extracted":
                near_lat = None
                near_lon = None
                if isinstance(client_location, dict):
                    near_lat = client_location.get("lat")
                    near_lon = client_location.get("lon")
                geocoded_place = geocode_place_name(where, near_lat=near_lat, near_lon=near_lon)
                if isinstance(geocoded_place, dict):
                    resolution["geocoded_place"] = geocoded_place
                    geocoded_name = str(geocoded_place.get("place_name") or where).strip() or where
                    extracted["where"] = geocoded_name
                    new_place_payload["name"] = geocoded_name
                    for field_name in ("address", "city", "country", "lat", "lon"):
                        field_value = geocoded_place.get(field_name)
                        if field_value is not None:
                            new_place_payload[field_name] = field_value

            resolution["new_entities"]["places"].append(new_place_payload)

        final_matched_place = (
            resolution.get("matched_place") if isinstance(resolution, dict) else None
        )
        final_place_id = (
            str(final_matched_place.get("place_id") or "").strip()
            if isinstance(final_matched_place, dict)
            else ""
        )
        if (
            contact_place_hint
            and final_place_id
            and str(contact_place_hint.get("confidence") or "").strip().lower() == "high"
            and _is_high_confidence_match(final_matched_place)
        ):
            resolution["pending_contact_place_link"] = {
                "contact_id": contact_place_hint.get("contact_id"),
                "role": contact_place_hint.get("role"),
                "source": contact_place_hint.get("source") or "event_inference",
                "confidence": contact_place_hint.get("confidence") or "high",
            }

    # Replace generic terms with actual names in title and summary
    name_replacements = resolution.get("name_replacements", {})
    if name_replacements:
        logger.info("[handle_event] STEP 3: Replacing generic terms in text...")
        original_title = extracted.get("title", "")
        original_summary = extracted.get("summary", "")

        extracted["title"] = _replace_generic_terms_in_text(original_title, name_replacements)
        extracted["summary"] = _replace_generic_terms_in_text(original_summary, name_replacements)

        if extracted["title"] != original_title:
            logger.debug(
                "[handle_event]   Title: '%s' -> '%s'",
                original_title,
                extracted["title"],
            )
        if extracted["summary"] != original_summary:
            logger.debug(
                "[handle_event]   Summary: '%s' -> '%s'",
                original_summary,
                extracted["summary"],
            )
    else:
        logger.info("[handle_event] STEP 3: No generic terms to replace")

    # Suggest relationships between contacts based on context
    logger.info("[handle_event] STEP 4: Suggesting relationships...")
    _emit_progress(context, "Inferring relationships...")
    relationship_suggestions = _format_relationship_suggestions(
        contact_result.get("suggested_relationships", []) if contact_result else [],
        resolution,
    )
    if not relationship_suggestions and previous_relationship_suggestions:
        relationship_suggestions = previous_relationship_suggestions

    # Update extracted "who" from contact agent results
    resolution["contacts"] = _dedupe_contacts(resolution.get("contacts", []))
    resolution["new_entities"]["contacts"] = _dedupe_contacts(
        resolution.get("new_entities", {}).get("contacts", [])
    )

    extracted["who"] = _dedupe_preserve_order(
        [
            contact["display_name"]
            for contact in resolution.get("contacts", [])
            if contact.get("display_name")
        ]
        + [
            contact["display_name"]
            for contact in resolution.get("new_entities", {}).get("contacts", [])
            if contact.get("display_name")
        ]
    )

    # Generate a preview ID and store the data
    preview_id = f"event:preview:{uuid4().hex[:8]}"

    from commands.storage import store_command_data

    logger.info(
        "[handle_event] STEP 5: Storing preview data (ID: %s)",
        preview_id,
    )
    _emit_progress(context, "Preparing confirmation card...")
    store_command_data(
        preview_id,
        {
            "extracted": extracted,
            "resolution": resolution,
            "user_email": user_email,
            "relationship_suggestions": relationship_suggestions,
            "original_message": original_message_to_store,
            "thread_id": context.get("thread_id"),
            "clarification_messages": clarification_messages,
            "requested_field_ids": [],
        },
    )

    pending_key = context.get("event_pending_key")
    if pending_key:
        from commands.storage import store_pending_event

        store_pending_event(pending_key, preview_id)

    logger.info("[handle_event] Event processing complete")
    logger.info("[handle_event] Summary:")
    logger.info("  - Title: %s", extracted.get("title"))
    logger.info("  - Contacts found: %s", len(resolution.get("contacts", [])))
    logger.info(
        "  - New contacts: %s",
        len(resolution.get("new_entities", {}).get("contacts", [])),
    )
    logger.info("  - Relationship suggestions: %s", len(relationship_suggestions))

    return {
        "type": "event_confirmation",
        "preview_id": preview_id,
        "extracted": extracted,
        "resolution": resolution,
        "relationship_suggestions": relationship_suggestions,
        "requires_confirmation": True,
        "message": "I've extracted the following information from your event. Please review and confirm:",
    }


def register(registry: CommandRegistry) -> None:
    """Register the /event command."""
    registry.register(
        name="event",
        handler=handle_event,
        description="Add a new memory/event to the database",
        requires_args=True,
    )
