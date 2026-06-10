"""
Handler for the /event command.

The /event command allows users to add new memories/events to the database.
It extracts entities, checks for existing ones, and asks for confirmation.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import contacts as contacts_service
import events as events_service
import places as places_service
import retrieval
from chat_media import merge_staged_chat_media_attachments, summarize_staged_chat_media_attachments
from commands.event_datetime import normalize_event_datetime, parse_event_datetime
from commands.handlers.clarification_utils import (
    build_clarification_result,
    build_clarification_storage_payload,
    create_clarification_preview_id,
    store_clarification_preview,
)
from commands.handlers.clarification_utils import (
    extract_clarification_detail as _extract_clarification_detail,
)
from commands.handlers.clarification_utils import (
    extract_clarification_token as _extract_clarification_token,
)
from commands.parser import ParsedCommand
from commands.registry import CommandRegistry
from db import fetch_event_people, get_conn
from llm_helpers import LLMUnavailableError, build_json_schema_response_format
from llm_json_schemas import (
    EVENT_EXTRACTION_RESPONSE_SCHEMA,
    EVENT_FIELD_INFERENCE_RESPONSE_SCHEMA,
    EVENT_FOLLOWUP_STRATEGY_RESPONSE_SCHEMA,
    EVENT_MATCH_INTENT_RESPONSE_SCHEMA,
    EVENT_RELATIONSHIP_SUGGESTION_RESPONSE_SCHEMA,
)
from location_inference import geocode_place_name, infer_current_place
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_text
from ui_dsl.clarification import (
    build_need_user_input,
    build_need_user_input_prompt_guidance,
    clarification_fields_from_ambiguous_contacts,
    default_clarification_details_field,
    derive_clarification_questions_from_fields,
    normalize_clarification_fields,
    normalize_need_user_input,
)
from user_fact_rules import RuleScope

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

_PLACE_ROLE_DEFINITIONS = {
    # Home-like
    "home": {"label": "Home", "aliases": {"home", "house", "place", "residence", "apartment", "apt", "condo", "flat"}},
    "family_home": {"label": "Family Home", "aliases": {"parents", "family"}},
    "partner_home": {"label": "Partner Home", "aliases": {"partner"}},
    "secondary_home": {"label": "Secondary Home", "aliases": {"secondary"}},
    # Work-like
    "work": {"label": "Work", "aliases": {"work", "office", "workplace", "job", "company"}},
    "hq": {"label": "HQ", "aliases": {"hq", "headquarters"}},
    "branch_office": {"label": "Branch Office", "aliases": {"branch"}},
    "coworking": {"label": "Coworking", "aliases": {"coworking", "cowork"}},
    "client_site": {"label": "Client Site", "aliases": {"client"}},
    # Education
    "school": {"label": "School", "aliases": {"school", "college", "university"}},
    "campus": {"label": "Campus", "aliases": {"campus"}},
    # Other common categories
    "gym": {"label": "Gym", "aliases": {"gym"}},
    "club": {"label": "Club", "aliases": {"club"}},
    "community_space": {"label": "Community Space", "aliases": {"community"}},
    "worship_place": {"label": "Place of Worship", "aliases": {"church", "temple", "mosque"}},
    "healthcare": {"label": "Healthcare", "aliases": {"hospital", "clinic", "doctor"}},
    "favorite_spot": {"label": "Favorite Spot", "aliases": {"favorite"}},
    "frequent_spot": {"label": "Frequent Spot", "aliases": {"spot", "frequent"}},
    "other": {"label": "Place", "aliases": {"other"}},
}

_PLACE_ROLE_SYNONYMS = {
    alias: role
    for role, config in _PLACE_ROLE_DEFINITIONS.items()
    for alias in config["aliases"]
}

_GENERIC_PLACE_ALIAS_TERMS = {
    # generic role words from synonyms map (both source tokens and canonical targets)
    *set(_PLACE_ROLE_SYNONYMS.keys()),
    *set(_PLACE_ROLE_SYNONYMS.values()),
    # broad non-entity fallback terms
    "place",
}

_INFERRED_PLACE_AUTO_MATCH_MAX_DISTANCE_M = 60.0
_INFERRED_PLACE_AUTO_MATCH_CONFIDENCE = {"high"}
_CONTACT_SCOPED_POSSESSIVE_PATTERN = r"(?:'|’|`|´)s"

# Thresholds for deciding whether an /event invocation should update an
# existing event. Scores come from retrieval.search_memories and are
# normalized to a 0–100 scale below. Tune these together — raising the
# auto-update threshold without raising the floor just means more user
# ambiguity prompts.
EVENT_MATCH_CANDIDATE_FLOOR = 35.0  # below this, ignore the candidate entirely
EVENT_MATCH_AUTO_UPDATE_SCORE = 65.0  # at/above this, propose update confidently
EVENT_MATCH_AMBIGUOUS_GAP = 5.0  # top-2 within this range -> ambiguous
EVENT_MATCH_MAX_CANDIDATES = 5
EVENT_MATCH_SEARCH_LIMIT = 12

_EVENT_MATCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "around",
    "at",
    "be",
    "did",
    "dinner",
    "for",
    "from",
    "go",
    "had",
    "have",
    "i",
    "in",
    "is",
    "it",
    "last",
    "me",
    "met",
    "my",
    "of",
    "on",
    "our",
    "the",
    "their",
    "them",
    "there",
    "this",
    "to",
    "today",
    "tonight",
    "us",
    "was",
    "we",
    "went",
    "were",
    "with",
    "yesterday",
    "wife",
    "husband",
    "daughter",
    "son",
    "kids",
    "family",
    "nothing",
    "special",
    "okay",
    "ok",
    "okish",
    "okayish",
    "well",
}

_EVENT_MATCH_QUERY_OVERLAP_FLOOR = 0.18
_EVENT_MATCH_STRONG_QUERY_OVERLAP_FLOOR = 0.35
_EVENT_MATCH_TITLE_OVERLAP_FLOOR = 0.34
_EVENT_MATCH_PLACE_OVERLAP_FLOOR = 0.6
_EVENT_MATCH_MUTUALLY_EXCLUSIVE_TOKEN_GROUPS = (
    {"breakfast", "brunch", "lunch", "dinner"},
)

_IMMEDIATE_PAST_PATTERNS = (
    re.compile(r"\bjust now\b", re.IGNORECASE),
    re.compile(
        r"\bi just\s+(?:had|finished|got|came|went|returned|ate|met|spoke|talked|wrapped|left|saw|did|completed|attended|visited)\b",
        re.IGNORECASE,
    ),
)
_RELATIVE_PAST_PATTERN = re.compile(
    r"\b(?:(?P<article>a|an)|(?P<amount>\d+))\s+"
    r"(?P<unit>minute|minutes|hour|hours)\s+ago\b",
    re.IGNORECASE,
)


def _infer_recent_event_datetime(message: str) -> datetime | None:
    text = str(message or "").strip()
    if not text:
        return None

    relative_match = _RELATIVE_PAST_PATTERN.search(text)
    if relative_match:
        amount_text = relative_match.group("amount")
        article = relative_match.group("article")
        unit = str(relative_match.group("unit") or "").lower()
        amount = int(amount_text) if amount_text else 1 if article else 0
        if amount > 0:
            delta = timedelta(hours=amount) if unit.startswith("hour") else timedelta(minutes=amount)
            return datetime.now() - delta

    if any(pattern.search(text) for pattern in _IMMEDIATE_PAST_PATTERNS):
        return datetime.now()

    return None


def _need_user_input_is_only_temporal(need_user_input: dict[str, Any] | None) -> bool:
    if not isinstance(need_user_input, dict):
        return False

    fields = normalize_clarification_fields(need_user_input.get("fields"))
    if fields:
        field_ids = {
            str(field.get("id") or "").strip().lower()
            for field in fields
            if isinstance(field, dict)
        }
        field_ids.discard("")
        if field_ids:
            return field_ids <= {"when", "end_when"}

    prompt_parts = [str(need_user_input.get("prompt") or "")]
    prompt_parts.extend(str(question or "") for question in (need_user_input.get("questions") or []))
    prompt_text = " ".join(part.strip().lower() for part in prompt_parts if part and part.strip())
    if not prompt_text:
        return False
    return any(token in prompt_text for token in (" when", "when ", "time", "date", "happen"))


def _classify_event_match_reference(raw_message: str | None) -> str:
    normalized = _normalized_event_match_text(raw_message)
    if not normalized:
        return "neutral"
    if not any(
        cue in normalized
        for cue in (
            "same",
            "different",
            "another",
            "new",
            "existing",
            "update",
            "right event",
            "wrong event",
        )
    ):
        return "neutral"

    from llm_helpers import call_llm_json

    prompt = f"""You classify whether a user message indicates how event matching should behave.

User message:
\"{raw_message or ''}\"

Choose exactly one intent:
- create_new: the user is saying this should NOT update an existing event and should be treated as a different/new event
- update_existing: the user is explicitly saying this refers to an existing event that should be updated
- neutral: the message does not clearly express either preference

Important rules:
- Focus on the user's intent, not exact phrases.
- Corrections like saying the previous match was wrong usually mean create_new unless the user clearly says to keep updating the same matched event.
- Do not infer create_new just because the user mentions a time, place, or person.

Return ONLY a JSON object matching the supplied response schema."""

    try:
        result = call_llm_json(
            prompt,
            timeout=15,
            response_format=build_json_schema_response_format(
                name="event_match_intent",
                schema=EVENT_MATCH_INTENT_RESPONSE_SCHEMA,
            ),
        )
    except Exception as exc:
        logger.warning("[handle_event] Event match intent classification failed: %s", exc)
        return "neutral"

    intent = str(result.get("intent") or "").strip().lower()
    confidence = str(result.get("confidence") or "").strip().lower()
    if confidence == "low":
        return "neutral"
    if intent not in {"create_new", "update_existing", "neutral"}:
        return "neutral"
    return intent


def _classify_follow_up_event_strategy(
    follow_up_message: str,
    clarification_context: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    from llm_helpers import call_llm_json
    from prompts.context import get_self_context, get_time_context, get_user_facts_context

    existing_extraction = clarification_context.get("extracted") or {}
    user_email = str(context.get("user_email") or "").strip()
    self_context = get_self_context(user_email) if user_email else None
    self_context_block = f"\n{self_context}\n" if self_context else ""
    user_facts_ctx = (
        get_user_facts_context(user_email, follow_up_message, scope=RuleScope.EVENT_COMMAND)
        if user_email
        else None
    )
    user_facts_block = f"\n{user_facts_ctx}\n" if user_facts_ctx else ""
    extraction_context = _format_field_inference_extraction_context(existing_extraction)
    time_context = get_time_context()
    matched_event = clarification_context.get("matched_event")
    matched_summary = ""
    if isinstance(matched_event, dict):
        matched_summary = (
            "Current matched event candidate:\n"
            f"- id: {matched_event.get('event_id')!r}\n"
            f"- title: {matched_event.get('title')!r}\n"
            f"- when: {matched_event.get('start_date')!r}\n"
        )

    prompt = f"""You decide how an /event follow-up correction should be handled.

Current context:
- Date/time: {time_context}
{self_context_block}
{user_facts_block}
{extraction_context}
{matched_summary}

User follow-up message:
\"{follow_up_message}\"

Choose exactly one action:
- patch_existing: keep the current matched event and update only a small set of fields
- rematch: the current matched event is wrong; rerun extraction/matching from the full user description plus the follow-up correction
- create_new: do not update any existing event; create a new one

If action is patch_existing, include the smallest set of fields to update from:
- title
- summary
- when
- end_when
- where
- tags
- types
- who

Rules:
- Prefer rematch when the user is correcting which event was chosen.
- Prefer patch_existing only when the current matched event is still the right one and the user is correcting details on it.
- Prefer create_new when the user clearly says this is a different/new event.
- Do not rely on exact trigger phrases; reason from meaning.

Return ONLY a JSON object matching the supplied response schema."""

    try:
        result = call_llm_json(
            prompt,
            timeout=20,
            response_format=build_json_schema_response_format(
                name="event_followup_strategy",
                schema=EVENT_FOLLOWUP_STRATEGY_RESPONSE_SCHEMA,
            ),
        )
    except Exception as exc:
        logger.warning("[handle_event] Follow-up strategy classification failed: %s", exc)
        return {"action": "patch_existing", "fields": []}

    action = str(result.get("action") or "").strip().lower()
    confidence = str(result.get("confidence") or "").strip().lower()
    if confidence == "low":
        return {"action": "patch_existing", "fields": []}
    if action not in {"patch_existing", "rematch", "create_new"}:
        action = "patch_existing"
    fields = _normalize_event_field_ids(result.get("fields")) if action == "patch_existing" else []
    return {"action": action, "fields": fields}


def _event_match_tokens(value: Any) -> set[str]:
    normalized = normalize_search_text(str(value or ""))
    if not normalized:
        return set()
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9']+", normalized):
        if token in _EVENT_MATCH_STOPWORDS:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        tokens.add(token)
    return tokens


def _token_overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / float(min(len(left), len(right)))


def _normalized_event_match_text(value: Any) -> str:
    return normalize_search_text(str(value or ""))


def _has_mutually_exclusive_token_conflict(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    for group in _EVENT_MATCH_MUTUALLY_EXCLUSIVE_TOKEN_GROUPS:
        left_hits = left & group
        right_hits = right & group
        if left_hits and right_hits and left_hits != right_hits:
            return True
    return False


def _user_explicitly_rejected_same_event(raw_message: str | None) -> bool:
    return _classify_event_match_reference(raw_message) == "create_new"


def _user_explicitly_requested_existing_event(raw_message: str | None) -> bool:
    return _classify_event_match_reference(raw_message) == "update_existing"


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


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _should_autofill_inferred_place(inferred_location: dict[str, Any]) -> bool:
    confidence = str(inferred_location.get("confidence") or "").strip().lower()
    if confidence in _INFERRED_PLACE_AUTO_MATCH_CONFIDENCE:
        return True

    distance_m = _safe_float(inferred_location.get("distance_m"))
    return bool(distance_m is not None and distance_m <= _INFERRED_PLACE_AUTO_MATCH_MAX_DISTANCE_M)


def _extract_contact_scoped_place_hint(where_text: str) -> dict[str, str] | None:
    text = str(where_text or "").strip()
    if not text:
        return None

    patterns = [
        re.compile(
            rf"^(?P<person>.+?)\s*{_CONTACT_SCOPED_POSSESSIVE_PATTERN}\s+(?P<role>[a-zA-Z\s]+)$",
            flags=re.IGNORECASE,
        ),
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


def _build_contact_scoped_place_name(contact_place_hint: dict[str, Any]) -> str | None:
    contact_name = str(
        contact_place_hint.get("contact_display_name")
        or contact_place_hint.get("contact_query")
        or ""
    ).strip()
    role = str(contact_place_hint.get("role") or "").strip()
    if not contact_name or not role:
        return None

    role_config = _PLACE_ROLE_DEFINITIONS.get(role)
    role_label = str((role_config or {}).get("label") or "").strip()
    if not role_label:
        role_label = role.replace("_", " ").strip().title()
    if not role_label:
        return None
    return f"{contact_name} {role_label}".strip()


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

    partial_matches: list[tuple[str, str, str, str | None]] = []
    for contact_id, display_name, query_text, confidence in candidates:
        display_name_norm = normalize_search_text(display_name)
        query_text_norm = normalize_search_text(query_text) if query_text else ""
        if normalized_target and (
            normalized_target in display_name_norm or normalized_target in query_text_norm
        ):
            partial_matches.append((contact_id, display_name, query_text, confidence))

    if len(partial_matches) == 1:
        contact_id, display_name, query_text, confidence = partial_matches[0]
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
    from prompts.context import get_self_context, get_time_context, get_user_facts_context

    user_email = str(context.get("user_email") or "").strip()
    self_context = get_self_context(user_email) if user_email else None
    self_context_block = f"\n{self_context}\n" if self_context else ""
    user_facts_ctx = (
        get_user_facts_context(user_email, follow_up_message, scope=RuleScope.EVENT_COMMAND)
        if user_email
        else None
    )
    user_facts_block = f"\n{user_facts_ctx}\n" if user_facts_ctx else ""
    extraction_context = _format_field_inference_extraction_context(existing_extraction)
    time_context = get_time_context()

    prompt = f"""You classify which event fields the user is trying to update in a follow-up message.

Current context:
- Date/time: {time_context}
{self_context_block}
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

Return ONLY a JSON object matching the supplied response schema."""

    try:
        classification = call_llm_json(
            prompt,
            timeout=20,
            response_format=build_json_schema_response_format(
                name="event_field_inference",
                schema=EVENT_FIELD_INFERENCE_RESPONSE_SCHEMA,
            ),
        )
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
    locked_lines_text = "\n".join(locked_lines)
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
        f"{locked_lines_text}\n"
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
    model: str | None = None,
    timeout: int | None = None,
    llm_request_options: dict[str, Any] | None = None,
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
    from prompts.context import get_self_context, get_time_context, get_user_facts_context
    from tags_manager import MAJOR_TAGS

    logger.info("[event_extraction] Starting extraction for: '%s'", message)

    # Get current time context
    time_context = get_time_context()
    user_email = str(context.get("user_email") or "").strip()
    self_context = get_self_context(user_email) if user_email else None

    # Get user facts for personalization (timezone, preferences, common locations, etc.)
    user_facts_ctx = (
        get_user_facts_context(user_email, message, scope=RuleScope.EVENT_COMMAND)
        if user_email
        else None
    )

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
    conversation_context = (
        f"Conversation messages (JSON array, most recent last):\n{conversation_json}\n\n"
    )

    self_context_block = f"\n{self_context}\n" if self_context else ""
    user_facts_block = f"\n{user_facts_ctx}\n" if user_facts_ctx else ""
    extraction_prompt = f"""You are extracting structured information from a user's event description to create a memory entry.

Current context:
- Date/time: {time_context}\n
{self_context_block}
{user_facts_block}\n
Event description: "{message}"\n

{existing_context}{target_field_context}{conversation_context}{clarification_context}\n

Extract the following information:
1. **What happened**: A brief title (5-10 words) and detailed summary
2. **When**: Parse event start date and time. Time is optional. Return ISO date (YYYY-MM-DD) when only date is known, or ISO datetime when time is known.
   - If the user says the event just happened (for example: "I just had lunch", "just now", "10 minutes ago"), infer an immediate past datetime instead of asking for clarification. But if the sentence doesn't imply it is happening now, ask for clarification.
3. **End**: Parse optional end date/time if present. Return null if not mentioned.
4. **Where**: Location/place name (if mentioned) - only one. Be aware that more than once place might be mentioned, and you should only extract the one where the event took place (For example, "I will start running from the Bakery to my house now", the event is taking place at the Bakery, the starting point, the "from").
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

Return ONLY a JSON object matching the supplied response schema."""
    extraction_prompt = append_clarification_guidelines(extraction_prompt)

    try:
        logger.info("[event_extraction] Calling LLM for extraction...")
        request_options = {
            "response_format": build_json_schema_response_format(
                name="event_extraction",
                schema=EVENT_EXTRACTION_RESPONSE_SCHEMA,
            )
        }
        request_options.update(dict(llm_request_options or {}))
        extracted = call_llm_json(
            extraction_prompt,
            timeout=timeout or 60,
            model=model,
            **request_options,
        )

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
                parsed = parse_event_datetime(raw)
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
        if when is None:
            inferred_recent_when = _infer_recent_event_datetime(message)
            if inferred_recent_when is not None:
                logger.info(
                    "[event_extraction] Inferred immediate-past event time from message: %s",
                    inferred_recent_when.isoformat(),
                )
                when = inferred_recent_when
                if _need_user_input_is_only_temporal(need_user_input):
                    need_user_input = None

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
        if isinstance(e, LLMUnavailableError):
            logger.error("[event_extraction] Critical LLM outage during extraction: %s", e)
            raise

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
        if rel_map.get(cleaned):
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
            if rel_map.get(rel_type):
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
    text: str | None,
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
    if text is None:
        return ""

    result = text if isinstance(text, str) else str(text)
    normalized_pairs: list[tuple[str, str]] = []
    for generic, actual in replacements.items():
        generic_term = str(generic or "").strip()
        if not generic_term:
            continue
        replacement_text = "" if actual is None else str(actual)
        if normalize_search_text(generic_term) == normalize_search_text(replacement_text):
            continue
        normalized_pairs.append((generic_term, replacement_text))

    normalized_pairs.sort(key=lambda item: len(item[0]), reverse=True)
    placeholder_map: dict[str, str] = {}
    for index, (generic_term, replacement_text) in enumerate(normalized_pairs):
        escaped = re.escape(generic_term)
        pattern = re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)
        placeholder = f"__EVENT_NAME_REPLACEMENT_{index}__"
        result = pattern.sub(placeholder, result)
        placeholder_map[placeholder] = replacement_text

    for placeholder, replacement_text in placeholder_map.items():
        result = result.replace(placeholder, replacement_text)
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


def _compute_event_match_window(when_value: Any) -> tuple[str | None, str | None]:
    """Return (time_start, time_end) ISO strings for the match query window.

    Wide by design: the LLM often mis-dates natural-language references like
    "last Thursday" (sometimes pointing at the wrong Thursday by a week), and
    humans describe events fuzzily. A tight window turns a recoverable
    near-miss into a hard miss because ``search_memories`` treats the span as
    a hard constraint. Date-only extractions open a ±7 day window; time-
    specific extractions use ±1 day. Post-ranking in ``_find_event_matches``
    still rewards temporal proximity so good matches rise to the top.
    """
    if not isinstance(when_value, datetime):
        return None, None
    is_midnight = when_value.hour == 0 and when_value.minute == 0 and when_value.second == 0
    delta = timedelta(days=7) if is_midnight else timedelta(days=1)
    return (when_value - delta).isoformat(), (when_value + delta).isoformat()


def _matched_event_preview(candidate: dict[str, Any]) -> dict[str, Any]:
    """Trim a search_memories event result down to what the UI needs."""
    place = candidate.get("place") if isinstance(candidate.get("place"), dict) else None
    return {
        "event_id": str(candidate.get("id") or "").strip(),
        "title": str(candidate.get("title") or "").strip(),
        "summary": str(candidate.get("summary") or "").strip(),
        "start_date": candidate.get("start_date"),
        "end_date": candidate.get("end_date"),
        "place": (
            {
                "place_id": str(place.get("place_id") or "").strip(),
                "name": str(place.get("name") or "").strip(),
                "city": str(place.get("city") or "").strip() or None,
                "country": str(place.get("country") or "").strip() or None,
            }
            if isinstance(place, dict) and place.get("place_id")
            else None
        ),
        "match_score": round(float(candidate.get("match_score") or 0.0), 2),
        "match_sources": list(candidate.get("match_sources") or []),
    }


def _merge_existing_event_into_extraction(
    matched_event_id: str,
    extracted: dict[str, Any],
    resolution: dict[str, Any],
) -> None:
    """Rebase the preview on the matched event so update doesn't overwrite.

    When we decide this /event run is updating an existing event, we should
    treat that event as the baseline. The user is almost always adding
    details, not renaming or re-dating the event — so we preserve existing
    scalars (title, when, where) and only merge in additive fields
    (summary, tags, types, participants). The user can still override any
    field explicitly in the editor; on confirm, modifications carry only
    the diff. This prevents the classic failure where the LLM picks the
    wrong Thursday and wipes out the correct stored date.
    """
    existing = events_service.get_event_by_id(matched_event_id)
    if not existing:
        return

    existing_title = str(existing.get("title") or "").strip()
    if existing_title:
        extracted["title"] = existing_title
    if existing.get("start_date"):
        extracted["when"] = parse_event_datetime(existing["start_date"])
    if existing.get("end_date") is not None:
        extracted["end_when"] = parse_event_datetime(existing["end_date"])

    existing_place_id = str(existing.get("place_id") or "").strip()
    if existing_place_id:
        place_row = places_service.get_place(existing_place_id)
        place_name = str((place_row or {}).get("name") or "").strip()
        if place_name:
            extracted["where"] = place_name
        resolution["matched_place"] = {
            "place_id": existing_place_id,
            "name": place_name or str(extracted.get("where") or "").strip(),
            "confidence": "high",
            "matched_via": "existing_event",
        }
        new_entities = resolution.setdefault(
            "new_entities", {"contacts": [], "places": [], "documents": []}
        )
        new_entities["places"] = []

    existing_summary = str(existing.get("summary") or "").strip()
    new_summary = str(extracted.get("summary") or "").strip()
    if existing_summary and new_summary and existing_summary != new_summary:
        if new_summary in existing_summary:
            extracted["summary"] = existing_summary
        elif existing_summary in new_summary:
            extracted["summary"] = new_summary
        else:
            extracted["summary"] = f"{existing_summary}\n\n{new_summary}"
    elif existing_summary and not new_summary:
        extracted["summary"] = existing_summary

    def _union_preserve(existing_list: Any, new_list: Any) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in list(existing_list or []) + list(new_list or []):
            text = str(item or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    extracted["tags"] = _union_preserve(existing.get("tags"), extracted.get("tags"))
    merged_types = _union_preserve(existing.get("types") or [], extracted.get("types") or [])
    extracted["types"] = merged_types or ["generic"]

    existing_people_ids = [
        str(cid).strip() for cid in (existing.get("people") or []) if str(cid or "").strip()
    ]
    if existing_people_ids:
        current_contacts = resolution.setdefault("contacts", [])
        existing_ids_in_resolution = {
            str(entry.get("contact_id") or "").strip()
            for entry in current_contacts
            if isinstance(entry, dict)
        }
        for contact_id in existing_people_ids:
            if contact_id in existing_ids_in_resolution:
                continue
            try:
                contact_row = contacts_service.get_contact(contact_id)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "[handle_event] Failed to fetch existing contact %s: %s", contact_id, exc
                )
                contact_row = None
            display_name = str((contact_row or {}).get("display_name") or "").strip() or contact_id
            current_contacts.append(
                {
                    "contact_id": contact_id,
                    "display_name": display_name,
                    "query": display_name,
                    "confidence": "high",
                    "match_source": "existing_event",
                }
            )


def _search_event_candidates(
    query: str,
    time_start: str | None,
    time_end: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Run search_memories and return only event rows.

    Intentionally does NOT pass people/place filters: ``search_memories``
    turns any structured filter into a hard AND constraint, which wipes out
    near-miss candidates that would otherwise be ideal update targets. We
    apply people/place as soft post-rank boosts below instead.
    """
    try:
        search_result = retrieval.search_memories(
            query=query,
            time_start=time_start,
            time_end=time_end,
            limit=limit,
            sort_order="relevance",
        )
    except Exception as exc:  # pragma: no cover - defensive, retrieval shouldn't raise
        logger.warning("[handle_event] event match search failed: %s", exc)
        return []
    return [
        result for result in (search_result.get("results") or []) if result.get("kind") == "event"
    ]


def _search_event_candidates_structured(
    query: str,
    time_start: str | None,
    time_end: str | None,
    people_ids: list[str],
    place_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        search_result = retrieval.search_memories(
            query=query,
            people=people_ids or None,
            place_ids=[place_id] if place_id else None,
            time_start=time_start,
            time_end=time_end,
            limit=limit,
            sort_order="relevance",
        )
    except Exception as exc:  # pragma: no cover - defensive, retrieval shouldn't raise
        logger.warning("[handle_event] structured event match search failed: %s", exc)
        return []
    return [
        result for result in (search_result.get("results") or []) if result.get("kind") == "event"
    ]


def _load_time_bounded_event_candidates(
    time_start: str,
    time_end: str,
    *,
    anchor_when: datetime | None,
    limit: int,
) -> list[dict[str, Any]]:
    if not time_start or not time_end:
        return []
    anchor_value = anchor_when or parse_event_datetime(time_start)
    if anchor_value is None:
        return []

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  e.id,
                  e.start_date,
                  e.end_date,
                  e.place_id,
                  e.tags,
                  e.types,
                  e.title,
                  e.summary,
                  p.name AS place_name
                FROM events AS e
                LEFT JOIN places AS p ON p.place_id = e.place_id
                WHERE e.start_date >= %s
                  AND e.start_date < %s
                ORDER BY ABS(EXTRACT(EPOCH FROM (e.start_date - %s::timestamptz))), e.start_date ASC
                LIMIT %s
                """,
                (time_start, time_end, anchor_value.isoformat(), limit),
            )
            rows = cur.fetchall()
            if not rows:
                return []

            event_ids = [
                str(row.get("id") or "").strip() for row in rows if str(row.get("id") or "").strip()
            ]
            people_map = fetch_event_people(cur, event_ids)
    except Exception as exc:
        logger.warning("[handle_event] Time-bounded event candidate load failed: %s", exc)
        return []

    candidates: list[dict[str, Any]] = []
    for row in rows:
        event_id = str(row.get("id") or "").strip()
        if not event_id:
            continue
        place_id = str(row.get("place_id") or "").strip()
        place_name = str(row.get("place_name") or "").strip()
        candidates.append(
            {
                "id": event_id,
                "kind": "event",
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "title": row.get("title"),
                "summary": row.get("summary"),
                "tags": row.get("tags") or [],
                "types": row.get("types") or [],
                "people": people_map.get(event_id, []),
                "place": (
                    {"place_id": place_id, "name": place_name or place_id}
                    if place_id
                    else None
                ),
                "score": 0.0,
            }
        )
    return candidates


def _build_structured_event_match_plans(
    people_ids: list[str],
    place_id: str | None,
) -> list[tuple[list[str], str | None, str]]:
    plans: list[tuple[list[str], str | None, str]] = []
    seen: set[tuple[tuple[str, ...], str | None]] = set()

    def _add_plan(plan_people: list[str], plan_place: str | None, label: str) -> None:
        normalized_people = [str(contact_id).strip() for contact_id in plan_people if str(contact_id).strip()]
        key = (tuple(normalized_people), plan_place)
        if key in seen:
            return
        seen.add(key)
        plans.append((normalized_people, plan_place, label))

    if people_ids or place_id:
        _add_plan(people_ids, place_id, "strict")
    if place_id and people_ids:
        _add_plan([], place_id, "place_only")
    for contact_id in people_ids:
        _add_plan([contact_id], place_id, "single_person_with_place" if place_id else "single_person")
    if len(people_ids) > 1:
        _add_plan([], None, "exact_day_text_only")
    return plans


_EVENT_MATCH_QUERY_CHAR_LIMIT = 240


def _build_event_match_query(
    raw_message: str | None,
    extracted: dict[str, Any],
) -> str:
    """Pick a short, stable query for finding a matching event.

    We prefer the user's own words (``raw_message``) because they tend to
    reuse the same short phrases when referring to the same event ("physio
    at Monserrate"), and because the LLM-generated summary describes the
    *new* details being added — which by definition won't appear on the
    stored event yet. Fall back to the title, then to the first sentence
    of the summary. Always cap to avoid blowing up the vector/BM25 query.
    """
    for candidate in (raw_message, extracted.get("title"), extracted.get("summary")):
        text = str(candidate or "").strip()
        if not text:
            continue
        if len(text) > _EVENT_MATCH_QUERY_CHAR_LIMIT:
            text = text[:_EVENT_MATCH_QUERY_CHAR_LIMIT].rsplit(" ", 1)[0]
        return text
    return ""


def _build_event_match_queries(
    raw_message: str | None,
    extracted: dict[str, Any],
    resolution: dict[str, Any] | None,
) -> list[str]:
    queries: list[str] = []
    primary_query = _build_event_match_query(raw_message, extracted)
    if primary_query:
        queries.append(primary_query)

    focused_parts: list[str] = []
    for candidate in (extracted.get("title"), extracted.get("where")):
        text = str(candidate or "").strip()
        if text:
            focused_parts.append(text)
    if isinstance(resolution, dict):
        for contact in resolution.get("contacts") or []:
            if not isinstance(contact, dict):
                continue
            display_name = str(contact.get("display_name") or "").strip()
            if display_name:
                focused_parts.append(display_name)
            if len(focused_parts) >= 5:
                break
    focused_query = " ".join(focused_parts).strip()
    if focused_query:
        queries.append(focused_query)

    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = _normalized_event_match_text(query)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(query[:_EVENT_MATCH_QUERY_CHAR_LIMIT])
    return deduped


def _compute_exact_day_match_window(when_value: Any) -> tuple[str | None, str | None]:
    if not isinstance(when_value, datetime):
        return None, None
    day_start = when_value.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return day_start.isoformat(), day_end.isoformat()


def _merge_event_candidate_lists(*candidate_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for candidate_list in candidate_lists:
        for candidate in candidate_list:
            candidate_id = str(candidate.get("id") or "").strip()
            if not candidate_id or candidate_id in seen_ids:
                continue
            seen_ids.add(candidate_id)
            merged.append(candidate)
    return merged


def _find_event_matches(
    raw_message: str | None,
    extracted: dict[str, Any],
    resolution: dict[str, Any] | None,
    *,
    excluded_event_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Classify the current extraction as create / update / ambiguous.

    Matching is intentionally forgiving: LLM date extraction is noisy and
    natural-language references ("last Thursday") are ambiguous. The
    ``search_memories`` call uses a wide time window with no people/place
    filters so that near-match candidates survive; we then post-rank with
    time-proximity, participant-overlap and place-match boosts. If the
    time-windowed pass returns nothing we retry without a time filter so a
    badly mis-dated extraction can still surface the right event.
    """
    queries = _build_event_match_queries(raw_message, extracted, resolution)
    if not queries:
        return {"operation": "create", "candidates": []}

    if _user_explicitly_rejected_same_event(raw_message):
        logger.info("[handle_event] Event match skipped: user explicitly said this is a new event")
        return {"operation": "create", "candidates": []}
    explicit_existing_event = _user_explicitly_requested_existing_event(raw_message)

    time_start, time_end = _compute_event_match_window(extracted.get("when"))
    end_when = normalize_event_datetime(
        extracted.get("end_when") if isinstance(extracted.get("end_when"), datetime) else None
    )
    if end_when is not None and time_end:
        try:
            parsed_end = parse_event_datetime(time_end)
            if parsed_end and end_when > parsed_end:
                time_end = end_when.isoformat()
        except ValueError:
            pass

    place_id: str | None = None
    if isinstance(resolution, dict):
        matched_place = resolution.get("matched_place")
        if isinstance(matched_place, dict):
            candidate_place_id = str(matched_place.get("place_id") or "").strip()
            if candidate_place_id:
                place_id = candidate_place_id

    people_ids: list[str] = []
    if isinstance(resolution, dict):
        for contact in resolution.get("contacts") or []:
            if not isinstance(contact, dict):
                continue
            contact_id = str(contact.get("contact_id") or "").strip()
            if contact_id:
                people_ids.append(contact_id)

    extracted_when = normalize_event_datetime(
        extracted.get("when") if isinstance(extracted.get("when"), datetime) else None
    )
    people_id_set = set(people_ids)
    all_query_tokens = _event_match_tokens(" ".join(queries))
    title_tokens = _event_match_tokens(extracted.get("title"))
    where_tokens = _event_match_tokens(extracted.get("where"))

    excluded_ids = {str(event_id).strip() for event_id in (excluded_event_ids or set()) if str(event_id).strip()}

    raw_results: list[dict[str, Any]] = []
    exact_day_start, exact_day_end = _compute_exact_day_match_window(extracted.get("when"))
    if exact_day_start and exact_day_end:
        bounded_results = _load_time_bounded_event_candidates(
            exact_day_start,
            exact_day_end,
            anchor_when=extracted_when,
            limit=EVENT_MATCH_SEARCH_LIMIT * 4,
        )
        if bounded_results:
            logger.info(
                "[handle_event] Event match time-bounded candidate set loaded: %d events",
                len(bounded_results),
            )
            raw_results = _merge_event_candidate_lists(bounded_results)

    if not raw_results and exact_day_start and exact_day_end:
        structured_results: list[dict[str, Any]] = []
        structured_plans = _build_structured_event_match_plans(people_ids, place_id)
        for query in queries:
            for plan_people_ids, plan_place_id, plan_label in structured_plans:
                plan_results = _search_event_candidates_structured(
                    query,
                    exact_day_start,
                    exact_day_end,
                    plan_people_ids,
                    plan_place_id,
                    EVENT_MATCH_SEARCH_LIMIT,
                )
                if plan_results:
                    logger.info(
                        "[handle_event] Event match structured pass hit: label=%s query=%r people=%d place=%s results=%d",
                        plan_label,
                        query,
                        len(plan_people_ids),
                        plan_place_id,
                        len(plan_results),
                    )
                    structured_results.extend(plan_results)
        raw_results = _merge_event_candidate_lists(structured_results)

    if not raw_results:
        semantic_results: list[dict[str, Any]] = []
        for query in queries:
            semantic_results.extend(
                _search_event_candidates(query, time_start, time_end, EVENT_MATCH_SEARCH_LIMIT)
            )
        raw_results = _merge_event_candidate_lists(semantic_results)

    if not raw_results and (time_start or time_end):
        logger.info("[handle_event] Event match: no hits in time window, retrying text-only")
        text_only_results: list[dict[str, Any]] = []
        for query in queries:
            text_only_results.extend(
                _search_event_candidates(query, None, None, EVENT_MATCH_SEARCH_LIMIT)
            )
        raw_results = _merge_event_candidate_lists(text_only_results)

    if explicit_existing_event and not raw_results:
        logger.info("[handle_event] Event match: explicit existing-event request, widening search")
        widened_results: list[dict[str, Any]] = []
        for query in queries:
            widened_results.extend(
                _search_event_candidates(query, None, None, EVENT_MATCH_SEARCH_LIMIT * 2)
            )
        raw_results = _merge_event_candidate_lists(widened_results)

    if not raw_results:
        return {"operation": "create", "candidates": []}

    scored: list[dict[str, Any]] = []
    for result in raw_results:
        result_id = str(result.get("id") or "").strip()
        if result_id and result_id in excluded_ids:
            logger.info("[handle_event] Skipping excluded matched event candidate: %s", result_id)
            continue
        raw_score = float(result.get("score") or 0.0)
        normalized_score = max(0.0, min(100.0, raw_score * 100.0))
        result_place = (
            str(result.get("place", {}).get("place_id") or "").strip()
            if isinstance(result.get("place"), dict)
            else ""
        )
        result_place_name = (
            str(result.get("place", {}).get("name") or "").strip()
            if isinstance(result.get("place"), dict)
            else ""
        )
        candidate_title_tokens = _event_match_tokens(result.get("title"))
        candidate_place_tokens = _event_match_tokens(result_place_name)
        candidate_text_tokens = _event_match_tokens(
            " ".join(
                [
                    str(part).strip()
                    for part in (
                        result.get("title"),
                        result.get("summary"),
                        result_place_name,
                    )
                    if str(part or "").strip()
                ]
            )
        )
        query_overlap = _token_overlap_ratio(all_query_tokens, candidate_text_tokens)
        title_overlap = _token_overlap_ratio(
            title_tokens,
            candidate_title_tokens or candidate_text_tokens,
        )
        place_overlap = _token_overlap_ratio(where_tokens, candidate_place_tokens)
        place_match = bool(place_id and result_place and result_place == place_id)
        result_people = {str(cid) for cid in (result.get("people") or []) if cid}
        people_overlap_count = len(result_people & people_id_set) if people_id_set else 0
        has_title_conflict = (
            bool(title_tokens)
            and bool(candidate_title_tokens)
            and title_overlap == 0.0
            and query_overlap < _EVENT_MATCH_STRONG_QUERY_OVERLAP_FLOOR
            and (place_match or people_overlap_count >= 2)
        )
        meal_conflict = _has_mutually_exclusive_token_conflict(
            all_query_tokens | title_tokens,
            candidate_title_tokens | candidate_text_tokens,
        )
        has_content_anchor = (
            query_overlap >= _EVENT_MATCH_QUERY_OVERLAP_FLOOR
            or title_overlap >= _EVENT_MATCH_TITLE_OVERLAP_FLOOR
            or place_overlap >= _EVENT_MATCH_PLACE_OVERLAP_FLOOR
            or place_match
        )
        if not has_content_anchor:
            continue
        if not explicit_existing_event and has_title_conflict:
            logger.info(
                "[handle_event] Rejecting candidate %s due to weak lexical overlap with conflicting title",
                result.get("id"),
            )
            continue
        if not explicit_existing_event and meal_conflict and query_overlap < 0.5:
            logger.info(
                "[handle_event] Rejecting candidate %s due to mutually exclusive meal semantics",
                result.get("id"),
            )
            continue
        match_sources: list[str] = []
        if query_overlap >= _EVENT_MATCH_QUERY_OVERLAP_FLOOR:
            normalized_score += min(18.0, query_overlap * 25.0)
            match_sources.append("query_overlap")
        if title_overlap >= _EVENT_MATCH_TITLE_OVERLAP_FLOOR:
            normalized_score += min(15.0, title_overlap * 20.0)
            match_sources.append("title_overlap")
        if place_overlap >= _EVENT_MATCH_PLACE_OVERLAP_FLOOR and not place_match:
            normalized_score += min(8.0, place_overlap * 10.0)
            match_sources.append("place_overlap")

        # Date is the dominant signal when the user supplied one. If two
        # candidates have similar text scores but different dates, the
        # correctly-dated one must win unambiguously — otherwise "last
        # Thursday" (23rd) can snap onto "last Tuesday" (21st) just because
        # participants and place overlap. Weights chosen to exceed the
        # ambiguous-gap threshold (5pt) so a clean date match can't be
        # cancelled by a slightly more similar summary on another day.
        if extracted_when and result.get("start_date"):
            try:
                result_when = parse_event_datetime(result["start_date"])
                comparable_extracted_when = normalize_event_datetime(extracted_when)
                if result_when is None or comparable_extracted_when is None:
                    continue
                if (
                    result_when.date() != comparable_extracted_when.date()
                    and not explicit_existing_event
                ):
                    logger.info(
                        "[handle_event] Rejecting candidate %s due to calendar-date mismatch: %s vs %s",
                        result.get("id"),
                        result_when.date(),
                        comparable_extracted_when.date(),
                    )
                    continue
                days_apart = abs((result_when - comparable_extracted_when).total_seconds()) / 86400.0
                if days_apart < 0.5:
                    normalized_score += 45.0
                    match_sources.append("same_day")
                    minutes_apart = abs(
                        (result_when - comparable_extracted_when).total_seconds()
                    ) / 60.0
                    if minutes_apart <= 20:
                        normalized_score += 14.0
                        match_sources.append("exact_time")
                    elif minutes_apart <= 90:
                        normalized_score += 7.0
                        match_sources.append("near_time")
                    elif minutes_apart >= 360 and not explicit_existing_event:
                        normalized_score -= 8.0
                elif days_apart < 2:
                    normalized_score += 8.0
                    match_sources.append("near_day")
                elif explicit_existing_event:
                    normalized_score -= min(12.0, days_apart * 1.5)
                    match_sources.append("cross_day_existing")
                elif days_apart >= 7:
                    # User explicitly dated the event; a week+ off is almost
                    # certainly not the one they meant. Penalize hard so text
                    # similarity alone can't resurrect it.
                    normalized_score -= 25.0
                    if not (place_match or query_overlap >= 0.45 or title_overlap >= 0.6):
                        continue
            except (TypeError, ValueError):
                pass

        if people_id_set:
            if people_overlap_count:
                normalized_score += min(15.0, 5.0 * people_overlap_count)
                match_sources.append("people_overlap")

        if place_id:
            if place_match:
                normalized_score += 10.0
                match_sources.append("place_match")

        normalized_score = max(0.0, min(100.0, normalized_score))
        if normalized_score < EVENT_MATCH_CANDIDATE_FLOOR:
            continue
        scored.append(
            {
                **result,
                "match_score": normalized_score,
                "match_sources": _dedupe_preserve_order(match_sources),
            }
        )

    if not scored:
        return {"operation": "create", "candidates": []}

    scored.sort(key=lambda r: -float(r.get("match_score") or 0.0))
    scored = scored[:EVENT_MATCH_MAX_CANDIDATES]
    candidates = [_matched_event_preview(candidate) for candidate in scored]
    top = scored[0]
    top_score = float(top.get("match_score") or 0.0)

    if len(scored) >= 2:
        second_score = float(scored[1].get("match_score") or 0.0)
        if (
            top_score < EVENT_MATCH_AUTO_UPDATE_SCORE
            and (top_score - second_score) <= EVENT_MATCH_AMBIGUOUS_GAP
        ):
            logger.info(
                "[handle_event] Event match ambiguous: top=%.2f second=%.2f",
                top_score,
                second_score,
            )
            return {"operation": "ambiguous", "candidates": candidates}

    if top_score < EVENT_MATCH_AUTO_UPDATE_SCORE:
        logger.info(
            "[handle_event] Event match below auto-update threshold: id=%s score=%.2f",
            top.get("id"),
            top_score,
        )
        return {"operation": "create", "candidates": []}

    logger.info(
        "[handle_event] Event match found: id=%s score=%.2f (auto_update=%s)",
        top.get("id"),
        top_score,
        top_score >= EVENT_MATCH_AUTO_UPDATE_SCORE,
    )
    return {
        "operation": "update",
        "existing_event_id": str(top.get("id") or "").strip(),
        "matched_event": candidates[0],
        "candidates": candidates,
    }


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
        participant_focus=True,
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
        matched_via = str(resolved.get("matched_via") or "").strip().lower()
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
        if matched_via.startswith("selector_"):
            continue
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

    user_facts_ctx = (
        get_user_facts_context(user_email, message, scope=RuleScope.EVENT_COMMAND)
        if user_email
        else None
    )
    user_facts_section = f"\n{user_facts_ctx}" if user_facts_ctx else ""

    prompt = f"""Analyze this event description and identify any implied relationships between the people mentioned.

Event: "{message}"
People involved: {contact_list}{user_facts_section}

Common relationship types:
- Family: parent, child, sibling, spouse, partner, grandparent, grandchild, cousin, uncle, aunt, nephew, niece
- Professional: colleague, manager, employee, client, vendor, doctor, patient, lawyer, therapist, teacher, student
- Social: friend, neighbor, acquaintance

Return ONLY a JSON object matching the supplied response schema. If no clear relationships exist, return an empty relationships array."""

    try:
        logger.info("[relationship_suggestion] Calling LLM for relationship analysis...")
        result = call_llm_json(
            prompt,
            timeout=15,
            response_format=build_json_schema_response_format(
                name="event_relationship_suggestion",
                schema=EVENT_RELATIONSHIP_SUGGESTION_RESPONSE_SCHEMA,
            ),
        )
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
    previous_media_attachments: list[dict[str, Any]] = []
    target_field_ids: list[str] = []
    skip_contact_resolution = False
    original_message_to_store = raw_message
    extraction_base: dict[str, Any] | None = None
    excluded_event_ids: set[str] = set()
    force_create_after_follow_up = False
    event_match_message = raw_message
    if clarification_context:
        clarification_messages = clarification_context.get("clarification_messages")
        original_message = clarification_context.get("original_message") or raw_message
        requested_fields = clarification_context.get("requested_fields") or []
        clarification_field_labels = [
            str(field.get("label") or "").strip()
            for field in requested_fields
            if isinstance(field, dict) and str(field.get("label") or "").strip()
        ]
        clarification_detail = _extract_clarification_detail(
            raw_message,
            original_message,
            clarification_field_labels,
        )
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
        previous_media_attachments = [
            attachment
            for attachment in (clarification_context.get("media_attachments") or [])
            if isinstance(attachment, dict)
        ]
        target_field_ids = _normalize_event_field_ids(
            clarification_context.get("requested_field_ids")
        )
        extraction_base = clarification_context.get("extracted") or None
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
                strategy = _classify_follow_up_event_strategy(
                    raw_message,
                    clarification_context,
                    context,
                )
                strategy_action = str(strategy.get("action") or "patch_existing").strip().lower()
                if strategy_action == "patch_existing":
                    inferred_fields = strategy.get("fields") or []
                    if not inferred_fields:
                        inferred_fields = _infer_follow_up_target_fields(
                            raw_message,
                            clarification_context.get("extracted") or {},
                            context,
                        )
                    if inferred_fields:
                        target_field_ids = _normalize_event_field_ids(inferred_fields)
                        logger.info(
                            "[handle_event] Inferred follow-up target fields: %s",
                            target_field_ids,
                        )
                elif strategy_action == "rematch":
                    extraction_base = None
                    previous_resolution = {}
                    previous_contact_result = {}
                    resolution = None
                    contact_result = None
                    skip_contact_resolution = False
                    event_message = _build_contact_context_message(
                        original_message,
                        clarification_messages,
                    )
                    contact_message = event_message
                    event_match_message = event_message
                    existing_event_id = str(clarification_context.get("existing_event_id") or "").strip()
                    if existing_event_id:
                        excluded_event_ids.add(existing_event_id)
                    logger.info(
                        "[handle_event] Follow-up requested a full rematch; excluded prior event=%s",
                        existing_event_id or None,
                    )
                elif strategy_action == "create_new":
                    extraction_base = None
                    previous_resolution = {}
                    previous_contact_result = {}
                    resolution = None
                    contact_result = None
                    skip_contact_resolution = False
                    force_create_after_follow_up = True
                    event_message = _build_contact_context_message(
                        original_message,
                        clarification_messages,
                    )
                    contact_message = event_message
                    event_match_message = event_message
                    logger.info("[handle_event] Follow-up requested creating a new event")

        if target_field_ids and "who" not in target_field_ids and previous_resolution:
            skip_contact_resolution = True
            resolution = resolution or previous_resolution
            contact_result = contact_result or previous_contact_result
            logger.info(
                "[handle_event] Skipping contact resolution for non-participant follow-up: %s",
                target_field_ids,
            )

    media_attachments = merge_staged_chat_media_attachments(
        previous_media_attachments,
        context.get("media_attachments") if isinstance(context.get("media_attachments"), list) else [],
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
            extraction_base,
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
        clarification_preview_id = create_clarification_preview_id("event")
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

        requested_field_ids = _normalize_event_field_ids(
            [field.get("id") for field in clarification_fields if isinstance(field, dict)]
        )

        store_clarification_preview(
            clarification_preview_id,
            build_clarification_storage_payload(
                original_message=original_message_to_store,
                assistant_prompt=(need_user_input or {}).get("prompt")
                or " ".join(clarification_questions),
                existing_messages=clarification_messages,
                requested_fields=clarification_fields,
                message_key="clarification_messages",
                extra_payload={
                    "extracted": extracted,
                    "resolution": resolution,
                    "contact_result": contact_result,
                    "user_email": user_email,
                    "requested_field_ids": requested_field_ids,
                    "relationship_suggestions": previous_relationship_suggestions,
                    "media_attachments": media_attachments,
                },
            ),
            context.get("event_pending_key"),
        )
        if need_user_input is None:
            return {
                "type": "error",
                "message": "I still need a few details before I can continue.",
            }
        return build_clarification_result(
            clarification_preview_id,
            need_user_input,
            {
                "partial_extraction": extracted,
                "original_message": raw_message,
            },
        )

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
            resolution["inferred_location"] = inferred_location
        if inferred_name and isinstance(inferred_location, dict):
            logger.info(
                "[handle_event] Deferred inferred place autofill: %s "
                "(source=%s confidence=%s distance_m=%s)",
                inferred_name,
                inferred_location.get("source") or "unknown",
                inferred_location.get("confidence") or "unknown",
                inferred_location.get("distance_m"),
            )

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

            suggested_contact_place_name = None
            if contact_place_hint:
                suggested_contact_place_name = _build_contact_scoped_place_name(contact_place_hint)
                if suggested_contact_place_name:
                    extracted["where"] = suggested_contact_place_name
                    new_place_payload["name"] = suggested_contact_place_name

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
            elif where_source == "extracted" and not contact_place_hint:
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

            if (
                contact_place_hint
                and str(contact_place_hint.get("confidence") or "").strip().lower() == "high"
            ):
                resolution["pending_contact_place_link"] = {
                    "contact_id": contact_place_hint.get("contact_id"),
                    "role": contact_place_hint.get("role"),
                    "source": contact_place_hint.get("source") or "event_inference",
                    "confidence": contact_place_hint.get("confidence") or "high",
                }

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

    # Look for an existing event we might be updating instead of creating.
    _emit_progress(context, "Checking for matching events...")
    if force_create_after_follow_up:
        event_match = {"operation": "create", "candidates": []}
    else:
        event_match = _find_event_matches(
            event_match_message,
            extracted,
            resolution,
            excluded_event_ids=excluded_event_ids,
        )
    operation = str(event_match.get("operation") or "create")
    existing_event_id = str(event_match.get("existing_event_id") or "").strip() or None
    matched_event = event_match.get("matched_event")
    candidate_events = list(event_match.get("candidates") or [])
    original_extracted = deepcopy(extracted)
    original_resolution = deepcopy(resolution)

    if operation == "ambiguous":
        # Ambiguous reverts to create by default; the user can pick a candidate
        # in the editor to switch to update. We keep the candidates around so
        # the UI can surface them.
        existing_event_id = None
        matched_event = None
    elif operation == "update" and existing_event_id:
        # Rebase preview on the matched event so confirm doesn't overwrite
        # stored scalars the user didn't mean to change.
        _merge_existing_event_into_extraction(existing_event_id, extracted, resolution)
        # Refresh "who" to reflect any participants we added from the existing event.
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

    media_attachment_summaries = summarize_staged_chat_media_attachments(media_attachments)

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
            "command_name": "event",
            "extracted": extracted,
            "resolution": resolution,
            "user_email": user_email,
            "relationship_suggestions": relationship_suggestions,
            "original_message": original_message_to_store,
            "thread_id": context.get("thread_id"),
            "clarification_messages": clarification_messages,
            "requested_field_ids": [],
            "original_extracted": original_extracted,
            "original_resolution": original_resolution,
            "operation": operation,
            "existing_event_id": existing_event_id,
            "matched_event": matched_event,
            "candidate_events": candidate_events,
            "media_attachments": media_attachments,
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
    logger.info(
        "  - Event match: operation=%s existing_id=%s candidates=%d",
        operation,
        existing_event_id,
        len(candidate_events),
    )

    if operation == "update":
        message = (
            "I found an existing event that looks like a match. Review the update below, "
            "or edit to pick a different event or create a new one."
        )
    elif operation == "ambiguous":
        message = (
            "I found a few existing events that could match. Review the details, and "
            "edit to pick one to update or create a new event."
        )
    else:
        message = "I've extracted the following information from your event. Please review and confirm:"

    return {
        "type": "event_confirmation",
        "preview_id": preview_id,
        "extracted": extracted,
        "resolution": resolution,
        "original_extracted": original_extracted,
        "original_resolution": original_resolution,
        "relationship_suggestions": relationship_suggestions,
        "operation": operation,
        "existing_event_id": existing_event_id,
        "matched_event": matched_event,
        "candidate_events": candidate_events,
        "media_attachments": media_attachment_summaries,
        "requires_confirmation": True,
        "message": message,
    }


def register(registry: CommandRegistry) -> None:
    """Register the /event command."""
    registry.register(
        name="event",
        handler=handle_event,
        description="Add a new memory/event to the database",
        requires_args=True,
    )
