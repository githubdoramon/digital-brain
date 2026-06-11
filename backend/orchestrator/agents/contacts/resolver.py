"""
Contact resolution module.

This module resolves person mentions in text to specific contacts in the database.
It supports:
- Direct name matching (fuzzy search)
- Relationship resolution ("my daughter" → Emma)
- Nested relationships ("my daughter's doctor" → Dr. Smith via Emma)
- LLM disambiguation when multiple matches exist

CRITICAL: This module NEVER hallucinates. It only returns:
- Resolved contacts that exist in the database
- Candidates when multiple matches exist
- "new" status when no match is found

Design principles:
1. No database writes - only reads for matching
2. Returns resolution candidates, not decisions
3. Uses LLM only for disambiguation, never creation
4. Clear confidence scoring
"""

import inspect
import os
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional

import contact_groups as contact_groups_service
import contacts as contacts_service
from agents.contacts.participant_filter import (
    build_event_participant_filter_prompt,
    parse_event_participant_filter_result,
)
from agents.contacts.prompt_builders import (
    build_collective_selector_prompt,
    build_contact_disambiguation_prompt,
    build_nested_relationship_selection_prompt,
    build_people_extraction_prompt,
    build_profession_inference_prompt,
    build_relationship_pairs_prompt,
    build_relationship_types_prompt,
)
from llm_helpers import LLMUnavailableError, build_json_schema_response_format, call_llm_json
from llm_json_schemas import (
    COLLECTIVE_SELECTOR_RESPONSE_SCHEMA,
    CONTACT_DISAMBIGUATION_RESPONSE_SCHEMA,
    EVENT_PARTICIPANT_FILTER_RESPONSE_SCHEMA,
    NESTED_RELATIONSHIP_SELECTION_RESPONSE_SCHEMA,
    PEOPLE_EXTRACTION_RESPONSE_SCHEMA,
    PROFESSION_INFERENCE_RESPONSE_SCHEMA,
    RELATIONSHIP_PAIRS_RESPONSE_SCHEMA,
    RELATIONSHIP_TYPES_RESPONSE_SCHEMA,
)
from observability import trace
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_text
from ui_dsl.clarification import (
    build_need_user_input,
    clarification_fields_from_ambiguous_contacts,
)
from user_fact_rules import RuleScope, RuleType

logger = get_runtime_logger(__name__)

EMAIL_DOMAIN_PATTERN = re.compile(r"@([a-z0-9][a-z0-9.-]*\.[a-z]{2,})", re.IGNORECASE)
MINIMAL_RESOLUTION_MODE = "minimal"
FULL_RESOLUTION_MODE = "full"
_REQUEST_CACHE_MISS = object()
_SHORT_CIRCUIT_RELATIONSHIP_TERMS = (
    "mom",
    "mother",
    "dad",
    "father",
    "wife",
    "husband",
    "partner",
    "son",
    "daughter",
    "friend",
    "colleague",
    "coworker",
    "boss",
    "manager",
    "doctor",
    "therapist",
    "teacher",
    "coach",
    "brother",
    "sister",
)
_USER_SCOPED_FAMILY_RELATIONSHIP_TERMS = {
    "mom",
    "mother",
    "dad",
    "father",
    "wife",
    "husband",
    "partner",
    "son",
    "daughter",
    "brother",
    "sister",
}
_BARE_USER_SCOPED_RELATIONSHIP_PATTERN = re.compile(
    r"\b(?:" + "|".join(sorted(_USER_SCOPED_FAMILY_RELATIONSHIP_TERMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_FIRST_PERSON_PARTICIPANT_PATTERN = re.compile(
    r"\b(i|me|my|we|us|our)\b|^(?:had|met|visited|called|saw|went)\b",
    re.IGNORECASE,
)
_SELF_REFERENTIAL_PERSON_PATTERN = re.compile(r"^(?:i|me|myself|we|us|ourselves)$", re.IGNORECASE)
_EXPLICIT_NEW_CONTACT_MARKERS = (
    "new contact",
    "new contacts",
    "new person",
    "new people",
    "not in the database",
    "not in database",
    "not in memory"
    "wont find in the database",
    "won't find in the database",
    "doesnt exist",
    "doesn't exist",
    "none of these",
)
_LEADING_TEMPORAL_FIRST_PERSON_PATTERN = re.compile(
    r"^(?:today|yesterday|tomorrow|tonight|this\s+(?:morning|afternoon|evening)|last\s+night)\s+i\b",
    re.IGNORECASE,
)
_PROFESSION_INFERENCE_SENTINEL = object()
_PROFESSION_TITLE_CUE_PATTERN = re.compile(
    r"\b(dr\.?|prof\.?|professor|doctor|dentist|therapist|teacher|coach|trainer|lawyer|attorney|engineer|designer|developer|nurse|physician|surgeon)\b",
    re.IGNORECASE,
)
_SHORT_CIRCUIT_RELATIONSHIP_PATTERN = re.compile(
    r"\b(?:my|our)\s+(?:best\s+)?(?:" + "|".join(_SHORT_CIRCUIT_RELATIONSHIP_TERMS) + r")\b",
    re.IGNORECASE,
)
_SHORT_CIRCUIT_NAME_TOKEN = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'-]*"
_SHORT_CIRCUIT_FULL_NAME = (
    rf"(?:Dr\.|Mr\.|Mrs\.|Ms\.|Prof\.)?\s*{_SHORT_CIRCUIT_NAME_TOKEN}"
    rf"(?:\s+{_SHORT_CIRCUIT_NAME_TOKEN}){{1,2}}"
)
_SHORT_CIRCUIT_NAME_PATTERN = re.compile(
    rf"\b(?:Dr\.|Mr\.|Mrs\.|Ms\.|Prof\.)?\s*{_SHORT_CIRCUIT_NAME_TOKEN}(?:\s+{_SHORT_CIRCUIT_NAME_TOKEN}){{0,2}}\b"
)
_RELATIONSHIP_APPOSITIVE_NAME_PATTERN = re.compile(
    r"\b(?P<relationship>(?i:(?:my|our)\s+(?:best\s+)?(?:"
    + "|".join(_SHORT_CIRCUIT_RELATIONSHIP_TERMS)
    + rf")))\s+(?P<name>{_SHORT_CIRCUIT_FULL_NAME})\b",
)
_BLOCKED_RELATIONSHIP_TERMS = {
    "attendee",
    "attendees",
    "co-attendee",
    "guest",
    "guests",
    "host",
    "hosts",
    "participant",
    "participants",
    "visitor",
    "visitors",
}
_SHORT_CIRCUIT_BLOCKERS_PATTERN = re.compile(
    r"\b(her|his|their|him|them|she|he|they)\b|\b(?:my|our)\s+[a-z]+\s*'s\b",
    re.IGNORECASE,
)
_LIKELY_MULTI_PERSON_CONNECTOR_PATTERN = re.compile(
    r",|&|\b(?:and|plus|along with)\b",
    re.IGNORECASE,
)
_SHARED_POSSESSIVE_RELATIONSHIP_PATTERN = re.compile(
    r"\b(?P<prefix>my|our)\s+(?P<body>(?:best\s+)?(?:"
    + "|".join(_SHORT_CIRCUIT_RELATIONSHIP_TERMS)
    + r")(?:\s*(?:,|&|and)\s*(?:best\s+)?(?:"
    + "|".join(_SHORT_CIRCUIT_RELATIONSHIP_TERMS)
    + r"))+)\b",
    re.IGNORECASE,
)
_POSSESSIVE_COLLECTIVE_OWNER_TOKEN = r"[A-Za-zÀ-ÖØ-öø-ÿ'-]+"
_POSSESSIVE_COLLECTIVE_GROUP_PATTERN = (
    r"(?:whole|entire)\s+family"
    r"|family"
    r"|co(?:-|\s)?workers?"
    r"|colleagues"
    r"|friends"
)
_POSSESSIVE_COLLECTIVE_PATTERN = re.compile(
    rf"\b(?P<owner>{_POSSESSIVE_COLLECTIVE_OWNER_TOKEN}(?:\s+{_POSSESSIVE_COLLECTIVE_OWNER_TOKEN}){{0,2}})'s\s+(?P<group>{_POSSESSIVE_COLLECTIVE_GROUP_PATTERN})\b",
    re.IGNORECASE,
)
_LIST_WITH_CONTEXT_PATTERN = re.compile(
    r"\b(?:with|along with)\s+(?P<list>.+?)(?=(?:\s+\b(?:at|from|in|on|near|inside|during|around)\b)|[.!?]|$)",
    re.IGNORECASE,
)
_LIST_SPLIT_PATTERN = re.compile(r"\s*(?:,|&|\band\b|\bplus\b)\s*", re.IGNORECASE)
_PRECEDING_PLACE_PREPOSITION_PATTERN = re.compile(r"(?:^|\s)(?:at|in|near|inside)\s*$", re.IGNORECASE)
_LEADING_SUBJECT_PERSON_PATTERN = re.compile(
    rf"^\s*(?P<subject>(?:Dr\.|Mr\.|Mrs\.|Ms\.|Prof\.)?\s*{_SHORT_CIRCUIT_NAME_TOKEN}(?:\s+{_SHORT_CIRCUIT_NAME_TOKEN}){{0,2}})\s+(?:(?i:is|was)\s+(?:going|meeting|visiting|calling|seeing|having|joining|attending|heading)\b|(?i:went|met|visited|called|saw|joined|attended|headed|left|came|had)\b)"
)
_DIRECT_OBJECT_PERSON_PATTERN = re.compile(
    rf"\b(?:i|we)\s+(?:met|meet|met with|talked to|spoke with|called|texted|emailed|saw|visited|joined)\s+"
    rf"(?P<person>(?:Dr\.|Mr\.|Mrs\.|Ms\.|Prof\.)?\s*{_SHORT_CIRCUIT_NAME_TOKEN}(?:\s+{_SHORT_CIRCUIT_NAME_TOKEN}){{0,2}})"
    rf"(?=(?:\s+\b(?:at|from|in|on|near|inside|during|around|for|about|to)\b)|[,.!?]|$)",
    re.IGNORECASE,
)
_CONTACT_RESOLUTION_MODEL_OVERRIDE: ContextVar[str | None] = ContextVar(
    "contact_resolution_model_override", default=None
)
_CONTACT_RESOLUTION_TIMEOUT_OVERRIDE: ContextVar[int | None] = ContextVar(
    "contact_resolution_timeout_override", default=None
)
_CONTACT_RESOLUTION_REQUEST_OPTIONS_OVERRIDE: ContextVar[dict[str, Any] | None] = ContextVar(
    "contact_resolution_request_options_override", default=None
)


@contextmanager
def use_contact_resolution_model(model: str | None):
    normalized_model = str(model or "").strip() or None
    token = _CONTACT_RESOLUTION_MODEL_OVERRIDE.set(normalized_model)
    try:
        yield
    finally:
        _CONTACT_RESOLUTION_MODEL_OVERRIDE.reset(token)


@contextmanager
def use_contact_resolution_timeout(timeout_seconds: int | None):
    normalized_timeout = timeout_seconds if isinstance(timeout_seconds, int) and timeout_seconds > 0 else None
    token = _CONTACT_RESOLUTION_TIMEOUT_OVERRIDE.set(normalized_timeout)
    try:
        yield
    finally:
        _CONTACT_RESOLUTION_TIMEOUT_OVERRIDE.reset(token)


@contextmanager
def use_contact_resolution_request_options(options: dict[str, Any] | None):
    normalized = dict(options or {}) or None
    token = _CONTACT_RESOLUTION_REQUEST_OPTIONS_OVERRIDE.set(normalized)
    try:
        yield
    finally:
        _CONTACT_RESOLUTION_REQUEST_OPTIONS_OVERRIDE.reset(token)


def _call_contact_resolution_llm_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
    model_override = _CONTACT_RESOLUTION_MODEL_OVERRIDE.get()
    timeout_override = _CONTACT_RESOLUTION_TIMEOUT_OVERRIDE.get()
    request_options_override = _CONTACT_RESOLUTION_REQUEST_OPTIONS_OVERRIDE.get()
    kwargs.setdefault("use_fast_model", True)
    kwargs.setdefault("reasoning_effort", "none")
    if model_override and "model" not in kwargs:
        kwargs["model"] = model_override
    if timeout_override:
        kwargs["timeout"] = timeout_override
    if request_options_override:
        kwargs.update(request_options_override)
    if model_override:
        logger.debug(
            "[contact_resolver] Using model override for LLM call: %s",
            model_override,
        )
    if timeout_override:
        logger.debug("[contact_resolver] Using timeout override for LLM call: %ss", timeout_override)
    return call_llm_json(prompt, **kwargs)


def _schema_response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return build_json_schema_response_format(name=name, schema=schema)


def _build_request_context(
    *,
    user_email: str,
    mode: str,
    full_text: str,
    conversation_messages: list[dict[str, str]] | None,
    participant_focus: bool = False,
) -> dict[str, Any]:
    return {
        "user_email": user_email,
        "mode": mode,
        "full_text": full_text,
        "conversation_messages": conversation_messages,
        "participant_focus": participant_focus,
        "self_contact": _REQUEST_CACHE_MISS,
        "relationship_contexts": {},
        "hard_rule_rows": _REQUEST_CACHE_MISS,
        "hard_rules_context": _REQUEST_CACHE_MISS,
        "soft_facts_context": {},
    }


def _get_request_self_contact(request_context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not request_context:
        return None
    cached = request_context.get("self_contact", _REQUEST_CACHE_MISS)
    if cached is _REQUEST_CACHE_MISS:
        user_email = str(request_context.get("user_email") or "").strip()
        request_context["self_contact"] = (
            contacts_service.find_self_contact(user_email) if user_email else None
        )
    value = request_context.get("self_contact")
    return value if isinstance(value, dict) else None


def _get_relationship_context(
    contact_id: str,
    *,
    include_contact_details: bool,
    request_context: dict[str, Any] | None,
) -> dict[str, Any]:
    cache_key = f"{contact_id}:{'full' if include_contact_details else 'basic'}"
    if request_context is not None:
        cache = request_context.setdefault("relationship_contexts", {})
        if cache_key in cache:
            cached_value = cache[cache_key]
            return cached_value if isinstance(cached_value, dict) else {"relationships": []}

    try:
        relationships = contacts_service.get_contact_relationships(
            contact_id,
            include_contact_details=include_contact_details,
        )
    except Exception:
        relationships = {"relationships": []}

    if request_context is not None:
        cache = request_context.setdefault("relationship_contexts", {})
        cache[cache_key] = relationships
    return relationships


def _get_hard_rule_rows(request_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not request_context:
        return []
    cached = request_context.get("hard_rule_rows", _REQUEST_CACHE_MISS)
    if cached is _REQUEST_CACHE_MISS:
        try:
            import user_facts

            request_context["hard_rule_rows"] = user_facts.get_hard_rules_for_scope(
                str(request_context.get("user_email") or ""),
                scope=RuleScope.CONTACT_RESOLUTION,
                rule_type=RuleType.ENTITY_ALIAS,
                limit=25,
            )
        except Exception:
            logger.exception(
                "[contact_resolver] Failed to load hard rules for user=%s",
                request_context.get("user_email"),
            )
            request_context["hard_rule_rows"] = []
    value = request_context.get("hard_rule_rows")
    return value if isinstance(value, list) else []


def _get_contact_resolution_user_facts_context(
    user_email: str | None,
    query: str,
    *,
    include_soft_facts: bool,
    request_context: dict[str, Any] | None,
) -> str | None:
    if not user_email:
        return None

    sections: list[str] = []
    try:
        import user_facts

        if request_context is not None:
            cached_hard = request_context.get("hard_rules_context", _REQUEST_CACHE_MISS)
            if cached_hard is _REQUEST_CACHE_MISS:
                request_context["hard_rules_context"] = user_facts.get_hard_rules_context(
                    user_email,
                    scope=RuleScope.CONTACT_RESOLUTION,
                )
            hard_rules_context = request_context.get("hard_rules_context")
        else:
            hard_rules_context = user_facts.get_hard_rules_context(
                user_email,
                scope=RuleScope.CONTACT_RESOLUTION,
            )
        if hard_rules_context:
            sections.append(str(hard_rules_context))

        if include_soft_facts:
            if request_context is not None:
                soft_cache = request_context.setdefault("soft_facts_context", {})
                cache_key = normalize_search_text(query)
                if cache_key not in soft_cache:
                    soft_cache[cache_key] = user_facts.get_facts_for_context(user_email, query)
                facts_text = soft_cache.get(cache_key)
            else:
                facts_text = user_facts.get_facts_for_context(user_email, query)
            if facts_text:
                sections.append(f"Known facts about this user:\n{facts_text}")
    except Exception as exc:
        logger.warning("[contact_resolver] Failed to get user facts context: %s", exc)

    if not sections:
        return None
    return "\n\n".join(sections)


def _build_contact_need_user_input(
    ambiguous_contacts: list[dict[str, Any]],
    people_mentioned: list[str],
) -> dict[str, Any] | None:
    prompt = "I found multiple matching contacts. Please choose who you meant."
    if ambiguous_contacts:
        first = ambiguous_contacts[0]
        if isinstance(first, dict):
            original_text = str(first.get("original_text") or "").strip()
            if original_text:
                prompt = f"I found multiple matches for '{original_text}'. Please choose one."

    return build_need_user_input(
        kind="disambiguation",
        source="contact_resolution",
        prompt=prompt,
        questions=[prompt],
        fields=clarification_fields_from_ambiguous_contacts(ambiguous_contacts),
        submission_mode="ui_submission",
        context={"people_mentioned": people_mentioned},
    )


def _is_overly_generic_person_reference(value: str) -> bool:
    """Return True when a person mention is too generic to resolve usefully."""
    normalized = _normalize_entity_for_match(value)
    if not normalized:
        return False

    return normalized in {
        "person",
        "people",
        "someone",
        "somebody",
        "anyone",
        "anybody",
        "someone else",
        "somebody else",
        "anyone else",
        "anybody else",
    }


def _is_unknown_person_aggregate_question(text: str) -> bool:
    """Detect analytical questions that ask for an unknown person (not a concrete contact mention)."""
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False

    if "?" not in normalized or "who" not in normalized:
        return False

    references_generic_person = re.search(
        r"\b(?:the|a|an)?\s*(person|people|someone|somebody|anyone|anybody)\b",
        normalized,
    )
    if not references_generic_person:
        return False

    has_interaction_signal = re.search(
        r"\b(meet|met|meeting|meetings|talk|talked|chat|chatted|call|called|see|saw|visit|visited)\b",
        normalized,
    )
    return bool(has_interaction_signal)


def _format_conversation_for_prompt(conversation_messages: list[dict[str, str]]) -> str:
    import json

    sanitized = []
    for entry in conversation_messages:
        role = entry.get("role")
        content = entry.get("content")
        if not role or not content:
            continue
        sanitized.append({"role": role, "content": content})

    if not sanitized:
        return ""

    return json.dumps(sanitized, ensure_ascii=True)


def _extract_collective_selectors(text: str) -> list[dict[str, str]]:
    """Extract collective participant selectors from free-form text."""
    selectors: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    raw_text = str(text or "")
    normalized_text = raw_text.lower()

    def _append(
        kind: str, value: str, *, raw: str | None = None, deterministic: bool = False
    ) -> None:
        cleaned_value = str(value or "").strip()
        normalized_value = normalize_search_text(cleaned_value)
        if not normalized_value:
            return
        key = (kind, normalized_value)
        if key in seen:
            return
        seen.add(key)
        selectors.append(
            {
                "kind": kind,
                "value": cleaned_value,
                "raw": str(raw or cleaned_value).strip(),
                "deterministic": "true" if deterministic else "false",
            }
        )

    for match in EMAIL_DOMAIN_PATTERN.finditer(normalized_text):
        domain = (match.group(1) or "").strip().lower()
        if domain:
            _append("email_domain", domain, raw=match.group(0), deterministic=True)

    for match in re.finditer(r"@([a-z0-9][a-z0-9.-]{1,80})\s+email\b", normalized_text):
        domain = (match.group(1) or "").strip().lower()
        if domain:
            _append("email_domain", domain, raw=match.group(0), deterministic=False)

    company_patterns = [
        r"(?:all|everyone|everybody)\s+(?:people\s+)?(?:from|at)\s+company\s+([a-z0-9][a-z0-9 .&'\-]{1,80})",
        r"(?:all|everyone|everybody)\s+(?:people\s+)?(?:from|at)\s+([a-z0-9][a-z0-9 .&'\-]{1,80})",
        r"all\s+([a-z0-9][a-z0-9 .&'\-]{1,80})\s+employees",
    ]
    for pattern in company_patterns:
        for match in re.finditer(pattern, normalized_text, flags=re.IGNORECASE):
            candidate = (match.group(1) or "").strip(" .")
            if not candidate:
                continue
            candidate = re.split(
                r"\b(?:about|regarding|concerning|to discuss|for)\b",
                candidate,
                maxsplit=1,
            )[0].strip()
            candidate = re.sub(r"\b(inc|ltd|llc|corp|corporation|company)$", "", candidate).strip()
            if candidate:
                _append("company", candidate, raw=match.group(0), deterministic=True)

    team_patterns = [
        r"(?:all|everyone|everybody)\s+(?:people\s+)?from\s+my\s+([a-z0-9][a-z0-9 '\-]{1,80}team)",
        r"my\s+([a-z0-9][a-z0-9 '\-]{1,80}team)",
    ]
    for pattern in team_patterns:
        for match in re.finditer(pattern, normalized_text, flags=re.IGNORECASE):
            team_name = (match.group(1) or "").strip(" .")
            if team_name:
                _append("group", team_name, raw=match.group(0), deterministic=False)

    if "employees" in normalized_text and not any(s["kind"] == "company" for s in selectors):
        for token in re.findall(r"\b([a-z0-9][a-z0-9\-]{2,})\b", normalized_text):
            if token in {"all", "with", "from", "about", "employees", "everyone", "having"}:
                continue
            if token in {"meeting", "team", "work", "people", "participant", "participants"}:
                continue
            if token == "my":
                continue
            _append("company", token, raw=f"{token} employees", deterministic=True)
            break

    return selectors


def _should_attempt_fast_person_extraction(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if _is_unknown_person_aggregate_question(normalized):
        return True
    return not _SHORT_CIRCUIT_BLOCKERS_PATTERN.search(normalized)


def _extract_shared_possessive_relationship_mentions(text: str) -> list[str]:
    mentions: list[str] = []
    for match in _SHARED_POSSESSIVE_RELATIONSHIP_PATTERN.finditer(text):
        prefix = str(match.group("prefix") or "").strip()
        body = str(match.group("body") or "").strip()
        if not prefix or not body:
            continue
        for raw_term in _LIST_SPLIT_PATTERN.split(body):
            term = str(raw_term or "").strip()
            if not term:
                continue
            mentions.append(f"{prefix} {term}")
    return mentions


def _extract_relationship_appositive_names(text: str) -> tuple[list[str], set[str]]:
    """Collapse mentions like "my wife Dana Lewis" to the explicit named person."""
    names: list[str] = []
    relationship_mentions: set[str] = set()
    seen_names: set[str] = set()
    for match in _RELATIONSHIP_APPOSITIVE_NAME_PATTERN.finditer(text):
        relationship = str(match.group("relationship") or "").strip()
        name = str(match.group("name") or "").strip()
        relationship_key = normalize_search_text(relationship)
        name_key = normalize_search_text(name)
        if relationship_key:
            relationship_mentions.add(relationship_key)
        if name_key and name_key not in seen_names:
            seen_names.add(name_key)
            names.append(name)
    return names, relationship_mentions


def _should_scope_bare_family_mentions_to_user(text: str, people: list[str]) -> bool:
    normalized_people = {normalize_search_text(person) for person in people}
    if "user" in normalized_people:
        return True
    raw_text = str(text or "").strip()
    if not raw_text:
        return False
    return bool(_FIRST_PERSON_PARTICIPANT_PATTERN.search(raw_text))


def _normalize_bare_family_mentions_for_user(text: str, people: list[str]) -> list[str]:
    if not people or not _should_scope_bare_family_mentions_to_user(text, people):
        return people

    normalized_people: list[str] = []
    seen: set[str] = set()
    for person in people:
        cleaned = str(person or "").strip()
        lowered = normalize_search_text(cleaned)
        candidate = cleaned
        if lowered in _USER_SCOPED_FAMILY_RELATIONSHIP_TERMS:
            candidate = f"my {lowered}"
        candidate_key = normalize_search_text(candidate)
        if not candidate_key or candidate_key in seen:
            continue
        seen.add(candidate_key)
        normalized_people.append(candidate)
    return normalized_people


def _extract_possessive_collective_mentions(text: str) -> list[str]:
    mentions: list[str] = []
    for match in _POSSESSIVE_COLLECTIVE_PATTERN.finditer(text):
        owner = str(match.group("owner") or "").strip()
        group = str(match.group("group") or "").strip()
        if owner and group:
            mentions.append(f"{owner}'s {group}")
    return mentions


def _looks_like_list_person_mention(value: str) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return False
    if _SELF_REFERENTIAL_PERSON_PATTERN.fullmatch(candidate):
        return False
    candidate_normalized = normalize_search_text(candidate)
    if candidate_normalized in _USER_SCOPED_FAMILY_RELATIONSHIP_TERMS:
        return True
    if _SHORT_CIRCUIT_RELATIONSHIP_PATTERN.fullmatch(candidate):
        return True
    if _POSSESSIVE_COLLECTIVE_PATTERN.fullmatch(candidate):
        return True
    if not _SHORT_CIRCUIT_NAME_PATTERN.fullmatch(candidate):
        return False
    tokens = [token for token in candidate.replace(".", " ").split() if token]
    return 1 <= len(tokens) <= 3


def _extract_people_from_with_lists(text: str) -> list[str]:
    mentions: list[str] = []
    for match in _LIST_WITH_CONTEXT_PATTERN.finditer(text):
        raw_list = str(match.group("list") or "").strip()
        if not raw_list:
            continue
        for raw_part in _LIST_SPLIT_PATTERN.split(raw_list):
            candidate = str(raw_part or "").strip(" .,!?;:\"'")
            if _looks_like_list_person_mention(candidate):
                mentions.append(candidate)
    return mentions


def _extract_leading_subject_person(text: str) -> list[str]:
    match = _LEADING_SUBJECT_PERSON_PATTERN.search(text)
    if not match:
        return []

    candidate = str(match.group("subject") or "").strip(" .,!?;:\"'")
    candidate_normalized = normalize_search_text(candidate)
    if _LEADING_TEMPORAL_FIRST_PERSON_PATTERN.match(candidate_normalized):
        return []
    if not _looks_like_list_person_mention(candidate):
        return []
    return [candidate]


def _extract_direct_object_people(text: str) -> list[str]:
    mentions: list[str] = []
    for match in _DIRECT_OBJECT_PERSON_PATTERN.finditer(text):
        candidate = str(match.group("person") or "").strip(" .,!?;:\"'")
        if _looks_like_list_person_mention(candidate):
            mentions.append(candidate)
    return mentions


def _has_capitalized_name_tokens(candidate: str) -> bool:
    tokens = [token for token in str(candidate or "").replace(".", " ").split() if token]
    if not tokens:
        return False
    ignored_titles = {"Dr", "Mr", "Mrs", "Ms", "Prof"}
    name_tokens = [token for token in tokens if token.rstrip(".") not in ignored_titles]
    if not name_tokens:
        return False
    return all(re.match(_SHORT_CIRCUIT_NAME_TOKEN + r"$", token) for token in name_tokens)


def _merge_people_mentions(*batches: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for batch in batches:
        for person in batch:
            cleaned = str(person or "").strip()
            normalized = normalize_search_text(cleaned)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(cleaned)
    return merged


def _has_first_person_narration(text: str) -> bool:
    raw_text = str(text or "").strip()
    if not raw_text:
        return False
    return bool(re.search(r"\b(i|me|my|we|us|our)\b", raw_text, flags=re.IGNORECASE))


def _has_relationship_language(text: str) -> bool:
    raw_text = str(text or "")
    if not raw_text:
        return False
    return bool(
        _SHORT_CIRCUIT_RELATIONSHIP_PATTERN.search(raw_text)
        or _BARE_USER_SCOPED_RELATIONSHIP_PATTERN.search(raw_text)
    )


def _count_coordinated_person_like_mentions(text: str) -> int:
    mentions = _extract_people_from_with_lists(text)
    if mentions:
        return len({normalize_search_text(mention) for mention in mentions})
    return 0


def _should_fallback_to_llm_after_fast_path(
    text: str,
    people: list[str],
    selector_mentions: list[dict[str, str]],
) -> bool:
    return bool(_llm_fallback_reasons_after_fast_path(text, people, selector_mentions))


def _llm_fallback_reasons_after_fast_path(
    text: str,
    people: list[str],
    selector_mentions: list[dict[str, str]],
) -> list[str]:
    reasons: list[str] = []
    if not people and not selector_mentions:
        reasons.append("no_fast_path_mentions")
        return reasons

    raw_text = str(text or "")
    normalized_people = {normalize_search_text(person) for person in people}

    if _has_first_person_narration(raw_text) and "user" not in normalized_people:
        reasons.append("missing_user_in_first_person_context")

    if _has_first_person_narration(raw_text) and _has_relationship_language(raw_text):
        has_relationship_person = any(
            person.startswith("my ")
            or normalize_search_text(person) in _USER_SCOPED_FAMILY_RELATIONSHIP_TERMS
            for person in people
        )
        if not has_relationship_person:
            reasons.append("missing_relationship_mentions_in_first_person_context")

    coordinated_mentions = _count_coordinated_person_like_mentions(raw_text)
    if coordinated_mentions > len(normalized_people):
        reasons.append(
            f"coordinated_mentions_exceed_extracted:{coordinated_mentions}>{len(normalized_people)}"
        )

    return reasons


def _fast_extract_people_from_text(
    text: str,
) -> tuple[list[str], list[dict[str, str]], bool]:
    raw_text = str(text or "").strip()
    if not _should_attempt_fast_person_extraction(raw_text):
        return [], [], False

    selector_mentions = _extract_collective_selectors(raw_text)
    collective_people_mentions = _extract_possessive_collective_mentions(raw_text)
    if _is_unknown_person_aggregate_question(raw_text):
        return [], selector_mentions, True

    if selector_mentions or collective_people_mentions:
        return [], selector_mentions, False

    people: list[str] = []
    seen: set[str] = set()
    inferred_user = False
    explicit_people_count = 0
    appositive_names, appositive_relationship_mentions = _extract_relationship_appositive_names(
        raw_text
    )

    def _append_person(value: str) -> bool:
        cleaned = str(value or "").strip(" .,!?;:\"'")
        normalized = normalize_search_text(cleaned)
        if not normalized or normalized in seen:
            return False
        if _is_overly_generic_person_reference(cleaned):
            return False
        seen.add(normalized)
        people.append(cleaned)
        return True

    if (re.search(r"\b(i|me|my|we|us|our)\b", raw_text, flags=re.IGNORECASE) and re.search(
        r"\b(meet|met|talk|talked|spoke|speak|chat|chatted|call|called|text|texted|email|emailed|see|saw|visit|visited|watch|watched|go|went|joined|attended|had lunch|had dinner|had drinks|went with|met with)\b",
        raw_text,
        flags=re.IGNORECASE,
    )) or re.match(
        r"^(had\s+(?:lunch|dinner)\s+with|met\s+with|saw\s+[A-Z]|visited\s+[A-Z]|called\s+[A-Z])",
        raw_text,
        flags=re.IGNORECASE,
    ):
        inferred_user = _append_person("user") or inferred_user

    for match in _SHORT_CIRCUIT_RELATIONSHIP_PATTERN.finditer(raw_text):
        if normalize_search_text(match.group(0)) in appositive_relationship_mentions:
            continue
        if _append_person(match.group(0)):
            explicit_people_count += 1

    for candidate in appositive_names:
        if _append_person(candidate):
            explicit_people_count += 1

    for candidate in _extract_shared_possessive_relationship_mentions(raw_text):
        if _append_person(candidate):
            explicit_people_count += 1

    for candidate in _extract_people_from_with_lists(raw_text):
        if _append_person(candidate):
            explicit_people_count += 1

    for candidate in _extract_leading_subject_person(raw_text):
        if _append_person(candidate):
            explicit_people_count += 1

    for candidate in _extract_direct_object_people(raw_text):
        if _append_person(candidate):
            explicit_people_count += 1

    for match in _SHORT_CIRCUIT_NAME_PATTERN.finditer(raw_text):
        candidate = str(match.group(0) or "").strip()
        if not candidate:
            continue
        if _PRECEDING_PLACE_PREPOSITION_PATTERN.search(raw_text[: match.start()]):
            continue
        candidate_norm = normalize_search_text(candidate)
        candidate_tokens = [token for token in candidate.replace(".", " ").split() if token]
        has_title = bool(re.match(r"^(Dr\.|Mr\.|Mrs\.|Ms\.|Prof\.)\s+", candidate))
        if candidate_tokens and candidate_tokens[-1] == "I":
            continue
        if len(candidate_tokens) < 2 and not has_title:
            continue
        if candidate_norm in {"i", "we", "who", "when", "where", "what", "why", "how"}:
            continue
        if candidate_norm.startswith(("my ", "our ")):
            continue
        if not _has_capitalized_name_tokens(candidate):
            continue
        if _append_person(candidate):
            explicit_people_count += 1

    if inferred_user and explicit_people_count == 0 and not selector_mentions:
        return [], [], False

    if (
        explicit_people_count == 1
        and not selector_mentions
        and _LIKELY_MULTI_PERSON_CONNECTOR_PATTERN.search(raw_text)
    ):
        return [], [], False

    if not people and not selector_mentions:
        return [], [], False

    return people, selector_mentions, True


def _merge_collective_selectors(*batches: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for batch in batches:
        for selector in batch:
            if not isinstance(selector, dict):
                continue
            kind = str(selector.get("kind") or "").strip().lower()
            value = normalize_search_text(str(selector.get("value") or "").strip())
            if not kind or not value:
                continue
            key = (kind, value)
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "kind": kind,
                    "value": str(selector.get("value") or "").strip(),
                    "raw": str(selector.get("raw") or selector.get("value") or "").strip(),
                    "deterministic": "true"
                    if _is_true_flag(selector.get("deterministic"))
                    else "false",
                }
            )
    return merged


def _is_true_flag(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _dedupe_resolved_contacts(resolved_contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for contact in resolved_contacts:
        contact_id = str(contact.get("contact_id") or "").strip()
        if not contact_id or contact_id in seen_ids:
            continue
        seen_ids.add(contact_id)
        deduped.append(contact)
    return deduped


def _resolve_collective_selectors(
    selectors: list[dict[str, str]],
    *,
    user_email: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    resolved_contacts: list[dict[str, Any]] = []
    group_upsert_candidates: list[dict[str, Any]] = []
    group_confirmation_candidates: list[dict[str, Any]] = []

    for selector in selectors:
        kind = str(selector.get("kind") or "").strip().lower()
        value = str(selector.get("value") or "").strip()
        raw_reference = str(selector.get("raw") or value).strip()
        deterministic = _is_true_flag(selector.get("deterministic"))

        matches: list[dict[str, Any]] = []
        group_name = ""
        description = ""
        aliases: list[str] = []
        added_via = f"selector_{kind}"

        if kind == "email_domain":
            domain_value = str(value or "").strip().lstrip("@").lower()
            matches = contacts_service.search_contacts_by_email_domain(domain_value, limit=300)
            # Support shorthand user phrasing like "@acme email" by falling back to
            # broader company/email lexical matching when a full domain is not provided.
            if not matches and "." not in normalize_search_text(domain_value):
                matches = contacts_service.search_contacts_by_company(domain_value, limit=300)
                deterministic = False

            group_name = f"People at @{domain_value}"
            description = f"Contacts matched by email domain @{domain_value}."
            aliases = [f"@{domain_value}", domain_value]

        elif kind == "company":
            matches = contacts_service.search_contacts_by_company(value, limit=300)
            group_name = f"{value} team"
            description = f"Contacts matched for company '{value}'."
            aliases = [value, f"company {value}"]

        elif kind == "group":
            group_lookup = contact_groups_service.resolve_group_members(
                user_email, value, limit=300
            )
            if group_lookup.get("found"):
                matches = group_lookup.get("contacts", [])
            else:
                matches = contacts_service.search_contacts_by_group_hint(value, limit=120)
                if not matches:
                    matches = contacts_service.search_contacts(
                        value, search_by="any", fuzzy_threshold=80, limit=60
                    )
            group_name = value
            description = f"Contacts associated with group '{value}'."
            aliases = [value]
            deterministic = bool(group_lookup.get("found"))

        elif kind == "tag":
            matches = contacts_service.search_contacts_by_group_hint(value, limit=120)
            group_name = value
            description = f"Contacts matched for tag/group hint '{value}'."
            aliases = [value]
            deterministic = False

        if not matches:
            continue

        selector_confidence = "high" if deterministic else "medium"
        for match in matches:
            contact_id = str(match.get("contact_id") or "").strip()
            display_name = str(match.get("display_name") or "").strip()
            if not contact_id or not display_name:
                continue
            resolved_contacts.append(
                {
                    "original_text": raw_reference,
                    "contact_id": contact_id,
                    "display_name": display_name,
                    "matched_via": f"selector_{kind}",
                    "confidence": selector_confidence,
                    "resolution_path": None,
                }
            )

        if deterministic:
            group_upsert_candidates.append(
                {
                    "name": group_name,
                    "description": description,
                    "aliases": aliases,
                    "source": "deterministic",
                    "selector_kind": kind,
                    "added_via": added_via,
                    "contact_ids": [str(match.get("contact_id") or "") for match in matches],
                    "replace_members": True,
                    "confirmed": True,
                }
            )
        elif kind == "group":
            candidate_contact_ids = [
                str(match.get("contact_id") or "").strip()
                for match in matches
                if str(match.get("contact_id") or "").strip()
            ]
            if len(candidate_contact_ids) >= 2:
                group_confirmation_candidates.append(
                    {
                        "name": group_name,
                        "description": description,
                        "aliases": aliases,
                        "source": "inferred",
                        "selector_kind": kind,
                        "added_via": added_via,
                        "contact_ids": candidate_contact_ids,
                        "replace_members": True,
                        "confirmed": False,
                    }
                )

    return (
        _dedupe_resolved_contacts(resolved_contacts),
        group_upsert_candidates,
        group_confirmation_candidates,
    )


def _format_disambiguation_history_for_prompt(
    conversation_messages: list[dict[str, str]] | None,
) -> str:
    if not conversation_messages:
        return ""

    lines: list[str] = []
    for entry in conversation_messages:
        role = (entry.get("role") or "").strip().lower()
        content = (entry.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        lines.append(f"- {role}: {content}")

    if not lines:
        return ""

    return "Disambiguation history (chronological, oldest first):\n" + "\n".join(lines) + "\n\n"


def _extract_explicit_new_contact_names(
    *,
    text: str,
    people: list[str],
    conversation_messages: list[dict[str, str]] | None = None,
) -> set[str]:
    """Return normalized people mentions the user explicitly labelled as new contacts."""
    normalized_people = {
        normalize_search_text(person): person
        for person in people
        if normalize_search_text(person)
        and not _SELF_REFERENTIAL_PERSON_PATTERN.match(str(person).strip())
    }
    if not normalized_people:
        return set()

    fragments = [text or ""]
    fragments.extend(
        str(entry.get("content") or "")
        for entry in (conversation_messages or [])
        if str(entry.get("role") or "").strip().lower() == "user"
    )
    combined = "\n".join(fragment for fragment in fragments if fragment)
    if not combined:
        return set()

    forced_new: set[str] = set()
    for segment in re.split(r"[\n.!?;]+", combined):
        normalized_segment = normalize_search_text(segment)
        if not normalized_segment:
            continue
        if not any(marker in normalized_segment for marker in _EXPLICIT_NEW_CONTACT_MARKERS):
            continue
        for normalized_person in normalized_people:
            if normalized_person and normalized_person in normalized_segment:
                forced_new.add(normalized_person)

    return forced_new


def _normalize_entity_for_match(value: str) -> str:
    normalized = re.sub(r"\s+", " ", normalize_search_text(value))
    for article in ("the ", "a ", "an "):
        if normalized.startswith(article):
            normalized = normalized[len(article) :].strip()
            break
    return normalized


def _is_org_title_phrase(value: str) -> bool:
    text = _normalize_entity_for_match(value)
    if not text:
        return False

    tokens = set(re.findall(r"[a-z]+", text))
    if not tokens:
        return False

    # Corporate/professional titles that usually indicate one person role, not two people.
    org_title_keywords = {
        "ceo",
        "cto",
        "cfo",
        "coo",
        "cio",
        "cmo",
        "chief",
        "president",
        "vice",
        "vp",
        "founder",
        "cofounder",
        "director",
        "head",
        "lead",
        "manager",
        "officer",
        "chair",
        "chairman",
        "chairwoman",
    }

    if tokens & org_title_keywords:
        return True
    return text.startswith(("head of ", "vice president"))


def _detect_split_possessive_title_errors(
    text: str,
    people: list[str],
) -> list[dict[str, str]]:
    """
    Detect extraction errors where "Org's Title" was split into two people.

    Example:
    - Input text: "Acme's CEO"
    - Bad extraction: ["Acme", "CEO"]
    - Desired: ["CEO at Acme"]
    """
    if not text or len(people) < 2:
        return []

    errors: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for owner in people:
        owner_norm = _normalize_entity_for_match(owner)
        if not owner_norm:
            continue
        for title in people:
            title_norm = _normalize_entity_for_match(title)
            if not title_norm or owner_norm == title_norm or not _is_org_title_phrase(title):
                continue

            possessive_pattern = re.compile(
                rf"\b{re.escape(owner)}(?:'s|s')\s+(?:the\s+|a\s+|an\s+)?{re.escape(title)}\b",
                re.IGNORECASE,
            )
            if not possessive_pattern.search(text):
                continue

            key = (owner_norm, title_norm)
            if key in seen:
                continue
            seen.add(key)
            normalized_title = re.sub(r"^(?:the|a|an)\s+", "", title, flags=re.IGNORECASE).strip()
            errors.append(
                {
                    "owner": owner.strip(),
                    "title": normalized_title,
                    "canonical": f"{normalized_title} at {owner.strip()}",
                }
            )

    return errors


def _repair_split_possessive_title_entities(
    text: str,
    people: list[str],
) -> list[str]:
    """
    Repair known extraction mistakes where possessive organization titles are split.
    """
    errors = _detect_split_possessive_title_errors(text, people)
    if not errors:
        return people

    repaired = list(people)
    for error in errors:
        owner_norm = _normalize_entity_for_match(error["owner"])
        title_norm = _normalize_entity_for_match(error["title"])

        owner_removed = False
        title_removed = False
        next_people: list[str] = []
        for person in repaired:
            person_norm = _normalize_entity_for_match(person)
            if not owner_removed and person_norm == owner_norm:
                owner_removed = True
                continue
            if not title_removed and person_norm == title_norm:
                title_removed = True
                continue
            next_people.append(person)
        repaired = next_people

        canonical = error["canonical"]
        canonical_norm = _normalize_entity_for_match(canonical)
        if canonical_norm not in {_normalize_entity_for_match(p) for p in repaired}:
            logger.info(
                "[contact_resolver] Repairing split possessive title '%s's %s' -> '%s'",
                error["owner"],
                error["title"],
                canonical,
            )
            repaired.append(canonical)

    return repaired


def _repair_missing_possessive_collective_mentions(
    text: str,
    people: list[str],
) -> list[str]:
    possessive_mentions = _extract_possessive_collective_mentions(text)
    if not possessive_mentions:
        return people

    repaired = list(people)
    normalized_people = {_normalize_entity_for_match(person) for person in repaired}
    for collective_mention in possessive_mentions:
        mention_norm = _normalize_entity_for_match(collective_mention)
        if mention_norm in normalized_people:
            continue

        owner, _, _group = collective_mention.partition("'s ")
        owner_norm = _normalize_entity_for_match(owner)
        if owner_norm and owner_norm in normalized_people:
            logger.info(
                "[contact_resolver] Restoring possessive collective mention '%s' from source text",
                collective_mention,
            )
            repaired.append(collective_mention)
            normalized_people.add(mention_norm)

    return repaired


def _filter_event_participants_via_llm(
    *,
    text: str,
    people: list[str],
    conversation_block: str,
    user_facts_block: str,
) -> tuple[list[str], list[str]]:
    if not people:
        return [], []

    prompt = build_event_participant_filter_prompt(
        text=text,
        people=people,
        conversation_block=conversation_block,
        user_facts_block=user_facts_block,
    )
    result = _call_contact_resolution_llm_json(
        prompt,
        timeout=45,
        temperature=0.1,
        top_p=0.9,
        use_fast_model=True,
        response_format=_schema_response_format(
            "event_participant_filter",
            EVENT_PARTICIPANT_FILTER_RESPONSE_SCHEMA,
        ),
    )
    return parse_event_participant_filter_result(people=people, result=result)


def _coerce_people_extraction_result(result: Any) -> list[str]:
    """Normalize common non-strict people extraction shapes from local models."""
    raw_people = result.get("people", []) if isinstance(result, dict) else result
    if not isinstance(raw_people, list):
        return []

    people: list[str] = []
    for item in raw_people:
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            value = str(
                item.get("name")
                or item.get("value")
                or item.get("person")
                or item.get("text")
                or ""
            )
        else:
            value = ""
        value = value.strip()
        if value:
            people.append(value)
    return people


def _coerce_collective_selector_result(result: Any) -> list[Any]:
    """Normalize common selector response shapes before selector validation."""
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    raw_selectors = result.get("selectors", result.get("collective_selectors", []))
    return raw_selectors if isinstance(raw_selectors, list) else []


_EXPLICIT_COLLECTIVE_SELECTOR_PATTERN = re.compile(
    r"\b(?:everyone|everybody|all|team|teams|people|folks|staff|employees|employee|"
    r"coworkers|coworker|co-workers|co-worker|colleagues|group|groups|department|"
    r"company|org|organization)\b"
)

_DISALLOWED_RELATIONSHIP_GROUP_SELECTOR_PATTERN = re.compile(
    r"\b(?:family|whole family|children|child|kids|kid|wife|husband|spouse|"
    r"daughter|son|parent|parents|mother|father|brother|sister|mom|dad)\b"
)

_GENERIC_COLLECTIVE_SELECTOR_VALUE_PATTERN = re.compile(
    r"^(?:people|person|folks|group|groups|team|teams|staff|employees|employee|everyone|everybody|all)$"
)

_VAGUE_CROWD_SELECTOR_PATTERN = re.compile(
    r"^(?:lots?\s+of|many|some|several|a\s+bunch\s+of|bunch\s+of|crowd\s+of|the)\s+"
    r"(?:people|person|folks|group|groups|team|teams|staff|employees)"
    r"(?:\s+there)?$"
)

_PARTICIPATION_CUE_PATTERN = re.compile(
    r"\b(?:with|along with|were there|was there|there were|there was|went|came|joined|"
    r"attended|showed up|came along|came over|present|as well)\b"
)


def _selector_has_explicit_collective_intent(
    *,
    text: str,
    kind: str,
    value: str,
    raw: str,
) -> bool:
    normalized_text = normalize_search_text(text)
    normalized_raw = normalize_search_text(raw)
    normalized_value = normalize_search_text(value)

    if kind == "group":
        if _GENERIC_COLLECTIVE_SELECTOR_VALUE_PATTERN.fullmatch(normalized_value):
            logger.debug(
                "[contact_resolver] Rejecting collective selector kind=%s raw=%r value=%r: overly generic group value",
                kind,
                raw,
                value,
            )
            return False
        if _VAGUE_CROWD_SELECTOR_PATTERN.fullmatch(normalized_raw):
            logger.debug(
                "[contact_resolver] Rejecting collective selector kind=%s raw=%r value=%r: vague crowd reference",
                kind,
                raw,
                value,
            )
            return False

    if kind == "group":
        group_text = " ".join(part for part in (normalized_raw, normalized_value) if part).strip()
        if group_text and _DISALLOWED_RELATIONSHIP_GROUP_SELECTOR_PATTERN.search(group_text):
            logger.debug(
                "[contact_resolver] Rejecting collective selector kind=%s raw=%r value=%r: family/relationship-style group",
                kind,
                raw,
                value,
            )
            return False

    if not _EXPLICIT_COLLECTIVE_SELECTOR_PATTERN.search(normalized_text):
        logger.debug(
            "[contact_resolver] Rejecting collective selector kind=%s raw=%r value=%r: no explicit collective intent in text",
            kind,
            raw,
            value,
        )
        return False

    selector_tokens = [normalized_raw, normalized_value]
    if kind == "email_domain" and normalized_value:
        selector_tokens.append(f"@{normalized_value}")

    return any(token and token in normalized_text for token in selector_tokens)


def _collective_mention_has_participation_cue(text: str, mention: str) -> bool:
    normalized_text = normalize_search_text(text)
    normalized_mention = normalize_search_text(mention)
    if not normalized_text or not normalized_mention:
        return False

    start = normalized_text.find(normalized_mention)
    if start < 0:
        return False

    window_start = max(0, start - 80)
    window_end = min(len(normalized_text), start + len(normalized_mention) + 80)
    context_window = normalized_text[window_start:window_end]
    return bool(_PARTICIPATION_CUE_PATTERN.search(context_window))


def _restore_participant_collective_mentions(
    *,
    text: str,
    original_people: list[str],
    filtered_people: list[str],
) -> list[str]:
    if not original_people or not filtered_people:
        return filtered_people

    restored = list(filtered_people)
    normalized_filtered = {_normalize_entity_for_match(person) for person in restored}
    original_collectives = [
        person
        for person in original_people
        if _POSSESSIVE_COLLECTIVE_PATTERN.fullmatch(str(person or "").strip())
    ]
    for collective in original_collectives:
        collective_norm = _normalize_entity_for_match(collective)
        if not collective_norm or collective_norm in normalized_filtered:
            continue

        owner, _, _group = collective.partition("'s ")
        owner_norm = _normalize_entity_for_match(owner)
        if not owner_norm or owner_norm not in normalized_filtered:
            continue
        if not _collective_mention_has_participation_cue(text, collective):
            continue

        logger.info(
            "[contact_resolver] Restoring participant collective mention '%s' after participant filter",
            collective,
        )
        restored.append(collective)
        normalized_filtered.add(collective_norm)

    return restored


def _extract_collective_selectors_via_llm(
    *,
    text: str,
    conversation_block: str,
) -> list[dict[str, str]]:
    prompt = build_collective_selector_prompt(
        text=text,
        conversation_block=conversation_block,
    )
    result = _call_contact_resolution_llm_json(
        prompt,
        timeout=30,
        temperature=0.1,
        top_p=0.9,
        use_fast_model=True,
        response_format=_schema_response_format(
            "collective_selector_extraction",
            COLLECTIVE_SELECTOR_RESPONSE_SCHEMA,
        ),
    )
    raw_selectors = _coerce_collective_selector_result(result)
    logger.info(
        "[contact_resolver] LLM collective selector extraction normalized selector_count=%d",
        len(raw_selectors),
    )
    llm_collective_selectors: list[dict[str, str]] = []
    if isinstance(raw_selectors, list):
        for selector in raw_selectors:
            if not isinstance(selector, dict):
                continue
            kind = str(selector.get("kind") or "").strip().lower()
            if kind not in {"email_domain", "company", "group", "tag"}:
                continue
            value = str(selector.get("value") or "").strip()
            if not value:
                continue
            raw = str(selector.get("raw") or value).strip()
            if not _selector_has_explicit_collective_intent(
                text=text,
                kind=kind,
                value=value,
                raw=raw,
            ):
                continue
            llm_collective_selectors.append(
                {
                    "kind": kind,
                    "value": value,
                    "raw": raw,
                    "deterministic": "true" if bool(selector.get("deterministic")) else "false",
                }
            )
    return llm_collective_selectors


def extract_people_from_text(
    text: str,
    conversation_messages: list[dict[str, str]] | None = None,
    include_collective_selectors: bool = False,
    user_email: str | None = None,
    user_facts_context: str | None = None,
) -> list[str] | tuple[list[str], list[dict[str, str]]]:
    """
    Extract person mentions from text using LLM.

    Args:
        text: The input text to analyze
        conversation_messages: Prior conversation for context
        include_collective_selectors: Whether to also return group selectors
        user_email: User's email for retrieving user facts context

    Returns:
        By default: list of people.
        When include_collective_selectors=True: (people, selectors).
    """
    logger.debug("[contact_resolver] extract_people_from_text: %s", text)
    conversation_block = ""
    if conversation_messages:
        conversation_json = _format_conversation_for_prompt(conversation_messages)
        if conversation_json:
            conversation_block = (
                f"Conversation messages (JSON array, most recent last):\n{conversation_json}\n\n"
            )

    user_facts_block = ""
    facts_ctx = user_facts_context
    if not facts_ctx and user_email:
        facts_ctx = _get_contact_resolution_user_facts_context(
            user_email,
            text,
            include_soft_facts=False,
            request_context=None,
        )
    if facts_ctx:
        user_facts_block = f"\n{facts_ctx}\n\n"
    prompt = build_people_extraction_prompt(
        text=text,
        conversation_block=conversation_block,
        user_facts_block=user_facts_block,
    )

    max_retries = 3
    llm_collective_selectors: list[dict[str, str]] = []
    for attempt in range(max_retries):
        try:
            # Use low temperature for consistent structured output
            result = _call_contact_resolution_llm_json(
                prompt,
                timeout=60,
                temperature=0.1,
                top_p=0.9,
                use_fast_model=True,
                response_format=_schema_response_format(
                    "people_extraction",
                    PEOPLE_EXTRACTION_RESPONSE_SCHEMA,
                ),
            )
            people = _coerce_people_extraction_result(result)
            logger.info(
                "[contact_resolver] LLM people extraction normalized people_count=%d",
                len(people),
            )

            # Validate extraction: check for unresolved pronouns
            invalid_extractions = []
            for person in people:
                person_lower = person.lower().strip()

                # Check for unresolved third-person possessive pronouns at the start
                # These indicate failed extraction since we can only resolve "my/user" context
                if person_lower.startswith(("her ", "his ", "their ")):
                    invalid_extractions.append(person)

            split_possessive_title_errors = _detect_split_possessive_title_errors(text, people)
            if (invalid_extractions or split_possessive_title_errors) and attempt < max_retries - 1:
                logger.warning(
                    "[contact_resolver] Attempt %s: Invalid extractions detected: %s, split_possessive_titles=%s",
                    attempt + 1,
                    invalid_extractions,
                    split_possessive_title_errors,
                )
                logger.info("[contact_resolver] Retrying extraction with stricter guidance...")

                # Add stricter guidance to the prompt
                correction_block = ""
                if invalid_extractions:
                    correction_block += f"""

CRITICAL ERROR CORRECTION:
Your previous extraction contained unresolved pronouns: {", ".join(invalid_extractions)}

These are INVALID because:
- "her X", "his X", "their X" at the start means you failed to identify WHO "her/his/their" refers to
- The ONLY person known in this system is the current user (use "user" token or "my")
- If you see "her mother", you MUST find who "her" refers to in the text and resolve it to "X's mother"
- If you cannot identify the referent, DO NOT include it

Please extract again with proper pronoun resolution or omit unclear references."""
                if split_possessive_title_errors:
                    error_lines = "\n".join(
                        f"- You returned both '{e['owner']}' and '{e['title']}'. "
                        f"Use one entry: '{e['canonical']}'."
                        for e in split_possessive_title_errors
                    )
                    correction_block += f"""

CRITICAL ERROR CORRECTION (POSSESSIVE ORG TITLES):
You split possessive organization titles into two people. This is invalid.
{error_lines}

For possessive org titles, output ONE person mention only, formatted as "<title> at <org>"."""
                prompt += correction_block
                continue

            # Post-process: Filter out first-person pronouns and handle "user" token
            filtered_people = []
            for person in people:
                person_lower = person.lower().strip()

                # Skip invalid third-person pronouns (last safety check)
                if person_lower.startswith(("her ", "his ", "their ")):
                    logger.info("[contact_resolver] Skipping invalid extraction: '%s'", person)
                    continue

                # Keep the special "user" token for direct email resolution later
                if person_lower == "user":
                    filtered_people.append("user")
                    continue

                # Skip standalone first-person pronouns (these should be converted to "user" by LLM)
                if person_lower in ["i", "me", "my", "mine", "myself", "we", "us", "our", "ours"]:
                    logger.info("[contact_resolver] Skipping first-person pronoun: '%s'", person)
                    continue

                # Skip second-person pronouns
                if person_lower in ["you", "your", "yours", "yourself"]:
                    logger.info("[contact_resolver] Skipping second-person pronoun: '%s'", person)
                    continue

                if _LEADING_TEMPORAL_FIRST_PERSON_PATTERN.match(person_lower):
                    logger.info(
                        "[contact_resolver] Skipping malformed temporal first-person extraction: '%s'",
                        person,
                    )
                    continue

                filtered_people.append(person)

            repaired_people = _repair_split_possessive_title_entities(text, filtered_people)
            repaired_people = _repair_missing_possessive_collective_mentions(text, repaired_people)
            cleaned_people = []
            for person in repaired_people:
                if _is_overly_generic_person_reference(person):
                    logger.info(
                        "[contact_resolver] Skipping overly generic person reference: '%s'",
                        person,
                    )
                    continue
                cleaned_people.append(person)

            if _is_unknown_person_aggregate_question(text):
                without_user = [
                    person for person in cleaned_people if person.lower().strip() != "user"
                ]
                if len(without_user) != len(cleaned_people):
                    logger.info(
                        "[contact_resolver] Skipping 'user' token for unknown-person aggregate question"
                    )
                cleaned_people = without_user

            if include_collective_selectors:
                try:
                    llm_collective_selectors = _extract_collective_selectors_via_llm(
                        text=text,
                        conversation_block=conversation_block,
                    )
                except Exception as selector_exc:
                    logger.warning(
                        "[contact_resolver] Failed to extract collective selectors: %s",
                        selector_exc,
                        exc_info=selector_exc,
                    )

            if include_collective_selectors:
                return cleaned_people, llm_collective_selectors
            return cleaned_people
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    "[contact_resolver] Attempt %s failed: %s, retrying...",
                    attempt + 1,
                    e,
                    exc_info=e,
                )
                continue
            logger.warning(
                "[contact_resolver] Failed to extract people after %s attempts: %s",
                max_retries,
                e,
                exc_info=e,
            )
            if include_collective_selectors:
                return [], []
            return []

    if include_collective_selectors:
        return [], []
    return []


def resolve_contact(
    person_text: str,
    user_email: str,
    *,
    event_context: Optional[str] = None,
    resolution_cache: Optional[dict[str, dict[str, Any]]] = None,
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Resolve a person mention to a specific contact.

    This is the main resolution function. It handles all resolution strategies:
    1. Nested relationships ("my daughter's doctor")
    2. Direct relationships ("my daughter")
    3. Fuzzy name search
    4. LLM disambiguation (when multiple matches)

    Args:
        person_text: The person reference to resolve (e.g., "John", "my daughter's doctor")
        user_email: User's email for relationship lookups
        event_context: Optional full event text for LLM disambiguation

    Returns:
        {
            "status": "resolved" | "candidates" | "new",
            "confidence": "high" | "medium" | "low",
            "contact_id": Optional[str],
            "display_name": Optional[str],
            "matched_via": "direct_match" | "relationship" | "nested_relationship" | "llm_disambiguation" | "hard_rule",
            "resolution_path": Optional[List[str]],  # For nested: ["user", "Emma", "Dr. Smith"]
            "candidates": [{"contact_id": str, "display_name": str, "match_score": float}],
        }
    """
    logger.info("[contact_resolver] Resolving: '%s'", person_text)

    result: dict[str, Any] = {
        "status": "new",
        "confidence": "low",
        "contact_id": None,
        "display_name": None,
        "matched_via": None,
        "resolution_path": None,
        "candidates": [],
        "skip_auto_disambiguation": False,
        "auto_resolve_candidates": False,
    }

    # Short-circuit for the current user: resolve directly by email.
    if person_text.lower().strip() == "user":
        user_contact = _get_request_self_contact(request_context)
        if user_contact is None:
            user_contact = contacts_service.find_self_contact(user_email)
        if user_contact:
            result["status"] = "resolved"
            result["confidence"] = "high"
            result["contact_id"] = user_contact["contact_id"]
            result["display_name"] = user_contact.get("display_name")
            result["matched_via"] = "user_email"
            logger.info("[contact_resolver] Resolved user via email: %s", result["display_name"])
            return result
        logger.info("[contact_resolver] User token provided but no contact found by email")
        result["status"] = "new"
        result["display_name"] = "user"
        return result

    hard_rule_resolution = _resolve_contact_via_hard_rules(
        user_email,
        person_text,
        request_context=request_context,
    )
    if hard_rule_resolution:
        result["status"] = "resolved"
        result["confidence"] = "high"
        result["contact_id"] = hard_rule_resolution.get("contact_id")
        result["display_name"] = hard_rule_resolution.get("display_name")
        result["matched_via"] = "hard_rule"
        logger.info(
            "[contact_resolver] Resolved '%s' via hard rule -> %s",
            person_text,
            result["display_name"],
        )
        trace.trace_contact_resolution_outcome(
            "hard_rule_resolved",
            {
                "person_text": person_text,
                "contact_id": result["contact_id"],
            },
        )
        return result

    # Step 1: Check for nested relationships (e.g., "my daughter's doctor")
    nested_parts = _parse_nested_relationship(person_text)
    logger.debug("[contact_resolver] Nested parts: %s", nested_parts)
    logger.debug("[contact_resolver] User email: %s", user_email)
    if nested_parts and len(nested_parts) > 1:
        logger.info("[contact_resolver] Detected nested relationship: %s", nested_parts)
        nested_result = _resolve_nested_relationship(
            nested_parts,
            user_email,
            resolution_cache,
            request_context=request_context,
        )

        if nested_result["found"]:
            result["status"] = "resolved"
            result["confidence"] = nested_result["confidence"]
            result["contact_id"] = nested_result["contact_id"]
            result["display_name"] = nested_result["display_name"]
            result["matched_via"] = "nested_relationship"
            result["resolution_path"] = nested_result["path"]
            logger.info(
                "[contact_resolver] Resolved via nested: %s",
                " -> ".join(nested_result["path"]),
            )
            return result

        if nested_result.get("candidates"):
            result["status"] = "candidates"
            result["candidates"] = nested_result["candidates"]
            result["confidence"] = nested_result.get("confidence", "low")
            result["skip_auto_disambiguation"] = bool(nested_result.get("collective_reference"))
            result["auto_resolve_candidates"] = bool(nested_result.get("auto_resolve_candidates"))
            logger.info(
                "[contact_resolver] Nested: Returning %s related candidates",
                len(result["candidates"]),
            )
            return result

        # Check if first part couldn't be resolved
        if nested_result.get("first_part_unresolved"):
            first_part_status = nested_result.get("first_part_status")
            logger.info(
                "[contact_resolver] Nested: First part unresolved (status: %s)",
                first_part_status,
            )

            # If first part is ambiguous, the whole nested relationship is ambiguous
            if first_part_status == "candidates":
                logger.info(
                    "[contact_resolver] First part '%s' is ambiguous, cannot resolve nested relationship",
                    nested_parts[0],
                )
                result["status"] = "candidates"
                return result
            # If first part is new, the whole nested relationship is unresolvable
            elif first_part_status == "new":
                logger.info(
                    "[contact_resolver] First part '%s' is new, cannot resolve nested relationship",
                    nested_parts[0],
                )
                result["status"] = "new"
                return result

        # Check if this is a user-related nested relationship (starts with "my", "user's", or equals "user")
        first_part_lower = nested_parts[0].lower().strip()
        is_user_nested = (
            first_part_lower.startswith(("my ", "user's ")) or first_part_lower == "user"
        )

        if not is_user_nested:
            # Non-user nested relationship failed (e.g., "Pedro's doctor")
            # Don't fall back to direct search as it will give wrong results
            logger.info(
                "[contact_resolver] Nested resolution failed for non-user relationship, marking as new"
            )
            result["status"] = "new"
            return result

        logger.info("[contact_resolver] Nested resolution failed, falling back")

    # Step 2: Check for simple relationship (e.g., "my daughter")
    relationship_type = _detect_relational_term(person_text)
    logger.debug("[contact_resolver] Relationship type: %s", relationship_type)
    if relationship_type:
        # Get user's relationships
        user_contact = _get_request_self_contact(request_context)
        if user_contact is None:
            user_contact = contacts_service.find_self_contact(user_email)
        if user_contact:
            relationships = _get_relationship_context(
                str(user_contact["contact_id"]),
                include_contact_details=True,
                request_context=request_context,
            )

            rel_result = _resolve_via_relationship(relationship_type, relationships)
            logger.debug("[contact_resolver] Relationship result: %s", rel_result)
            if rel_result["found"]:
                result["status"] = "resolved"
                result["confidence"] = rel_result["confidence"]
                result["contact_id"] = rel_result["contact_id"]
                result["display_name"] = rel_result["display_name"]
                result["matched_via"] = "relationship"
                logger.info(
                    "[contact_resolver] Resolved via relationship: %s",
                    rel_result["display_name"],
                )
                return result
            elif rel_result["candidates"]:
                # Multiple relationship matches - return candidates
                result["status"] = "candidates"
                result["candidates"] = rel_result["candidates"]
                logger.warning(
                    "[contact_resolver] Multiple %ss, returning candidates",
                    relationship_type,
                )
                return result

            phrase_match_result = _resolve_relationship_phrase_against_related_contacts(
                anchor_display_name=str(user_contact.get("display_name") or "user"),
                relationship_phrase=relationship_type,
                relationship_context=relationships,
            )
            if phrase_match_result["status"] == "resolved":
                result["status"] = "resolved"
                result["confidence"] = phrase_match_result.get("confidence", "medium")
                result["contact_id"] = phrase_match_result.get("contact_id")
                result["display_name"] = phrase_match_result.get("display_name")
                result["matched_via"] = "relationship_phrase"
                logger.info(
                    "[contact_resolver] Resolved via relationship phrase: %s",
                    result["display_name"],
                )
                return result

            if phrase_match_result["status"] == "candidates":
                result["status"] = "candidates"
                result["candidates"] = phrase_match_result.get("candidates", [])
                result["confidence"] = phrase_match_result.get("confidence", "low")
                result["skip_auto_disambiguation"] = bool(
                    phrase_match_result.get("collective_reference")
                )
                result["auto_resolve_candidates"] = bool(
                    phrase_match_result.get("auto_resolve_candidates")
                )
                logger.info(
                    "[contact_resolver] Relationship phrase produced %s candidates",
                    len(result["candidates"]),
                )
                return result

    # Step 3: Try direct fuzzy search
    search_name = person_text
    if relationship_type:
        # Strip generic markers for better search
        search_name = _strip_generic_markers(person_text)

    logger.debug("[contact_resolver] Searching for: '%s'", search_name)
    matches = contacts_service.search_contacts(
        search_name,
        search_by="any",
        fuzzy_threshold=75,
        limit=15,
    )

    if len(matches) == 0:
        # No matches - new contact
        logger.info("[contact_resolver] No matches, marking as new")
        result["status"] = "new"
        result["display_name"] = person_text
        return result

    elif len(matches) == 1:
        # Single match - resolved!
        match = matches[0]
        result["status"] = "resolved"
        result["confidence"] = "high" if match.get("match_score", 0) > 90 else "medium"
        result["contact_id"] = match["contact_id"]
        result["display_name"] = match["display_name"]
        result["matched_via"] = "direct_match"
        logger.info(
            "[contact_resolver] Single match: %s (score: %s)",
            match["display_name"],
            match.get("match_score"),
        )
        return result

    else:
        unique_multi_token_match = _select_unique_multi_token_name_match(matches, search_name)
        if unique_multi_token_match is not None:
            result["status"] = "resolved"
            result["confidence"] = "high"
            result["contact_id"] = unique_multi_token_match["contact_id"]
            result["display_name"] = unique_multi_token_match["display_name"]
            result["matched_via"] = "multi_token_name_match"
            logger.info(
                "[contact_resolver] Unique multi-token name match: %s",
                unique_multi_token_match["display_name"],
            )
            return result

        # Multiple matches - need disambiguation
        logger.debug(
            "[contact_resolver] Matches found: %s",
            ", ".join(f"{m['display_name']} ({m.get('match_reason', '')})" for m in matches),
        )
        logger.info(
            "[contact_resolver] Found %s matches, attempting disambiguation",
            len(matches),
        )

        result["candidates"] = [
            {
                "contact_id": m["contact_id"],
                "display_name": m["display_name"],
                "match_score": m.get("match_score", 0),
                "match_reason": m.get("match_reason"),
                "aliases": m.get("aliases") or [],
            }
            for m in matches
        ]

        # Still ambiguous - return candidates
        result["status"] = "candidates"
        logger.warning("[contact_resolver] Ambiguous, returning candidates")
        return result


def resolve_contacts_from_text(
    text: str,
    user_email: str,
    conversation_messages: list[dict[str, str]] | None = None,
    *,
    mode: str = FULL_RESOLUTION_MODE,
    request_context: dict[str, Any] | None = None,
    participant_focus: bool = False,
) -> dict[str, Any]:
    """
    Complete pipeline: extract people from text and resolve them to contacts.

    This is the main entry point. It:
    1. Extracts person mentions from text (LLM)
    2. Resolves each mention to a contact (or marks as new/ambiguous)
    3. Infers professions for new contacts
    4. Infers relationships between contacts and proposes missing ones
    5. Returns structured results

    Args:
        text: The text to analyze
        user_email: User's email for relationship lookups

    Returns:
        {
            "text": str,
            "people_mentioned": List[str],
            "resolved_contacts": [
                {
                    "original_text": str,
                    "contact_id": str,
                    "display_name": str,
                    "matched_via": str,
                    "confidence": str,
                    "resolution_path": Optional[List[str]]
                }
            ],
            "new_contacts": [
                {
                    "original_text": str,
                    "display_name": str,
                    "inferred_profession": Optional[str]
                }
            ],
            "ambiguous_contacts": [
                {
                    "original_text": str,
                    "candidates": List[dict]
                }
            ]
            "suggested_relationships": [
                {
                    "from_text": str,
                    "to_text": str,
                    "from_contact_id": Optional[str],
                    "to_contact_id": Optional[str],
                    "type": str,
                    "other_type": str,
                    "relationship_hint": Optional[str]
                }
            ]
            "ambiguous_text": true | false
        }
    """
    logger.info("[contact_resolver] RESOLVING CONTACTS FROM TEXT")
    logger.debug("[contact_resolver] Text: '%s'", text)
    logger.debug("[contact_resolver] User: %s", user_email)
    resolution_mode = mode if mode in {MINIMAL_RESOLUTION_MODE, FULL_RESOLUTION_MODE} else FULL_RESOLUTION_MODE
    if request_context is None:
        request_context = _build_request_context(
            user_email=user_email,
            mode=resolution_mode,
            full_text=text,
            conversation_messages=conversation_messages,
            participant_focus=participant_focus,
        )
    else:
        request_context.setdefault("participant_focus", participant_focus)

    # Step 1: Extract people
    logger.info("[contact_resolver] Step 1: Extracting people...")
    effective_text = text
    people, selector_mentions, fast_path_applied = _fast_extract_people_from_text(effective_text)
    logger.info(
        "[contact_resolver] Fast-path extraction applied=%s people=%s selectors=%s",
        fast_path_applied,
        people,
        selector_mentions,
    )
    trace.trace_contact_resolution_phase(
        "fast_path",
        {
            "applied": fast_path_applied,
            "people": people,
            "selector_kinds": [selector.get("kind") for selector in selector_mentions],
        },
    )
    llm_selector_mentions: list[dict[str, str]] = []

    fallback_reasons: list[str] = []
    if fast_path_applied:
        fallback_reasons = _llm_fallback_reasons_after_fast_path(
            effective_text,
            people,
            selector_mentions,
        )
    should_run_llm_extraction = (not fast_path_applied) or bool(fallback_reasons)

    if should_run_llm_extraction:
        if fast_path_applied:
            logger.info(
                "[contact_resolver] Running LLM extraction after fast path due to: %s",
                ", ".join(fallback_reasons),
            )
            trace.trace_contact_resolution_phase(
                "llm_fallback",
                {
                    "reasons": fallback_reasons,
                    "people": people,
                    "selector_kinds": [selector.get("kind") for selector in selector_mentions],
                },
            )
        else:
            logger.info("[contact_resolver] Running LLM extraction because fast path did not apply")
            trace.trace_contact_resolution_phase(
                "llm_fallback",
                {"reason": "fast_path_not_applied"},
            )
    else:
        logger.info("[contact_resolver] Skipping LLM extraction; fast path considered sufficient")
        trace.trace_contact_resolution_phase(
            "llm_skip",
            {
                "people": people,
                "selector_kinds": [selector.get("kind") for selector in selector_mentions],
            },
        )

    if should_run_llm_extraction:
        extract_kwargs: dict[str, Any] = {
            "conversation_messages": conversation_messages,
            "user_email": user_email,
            "user_facts_context": _get_contact_resolution_user_facts_context(
                user_email,
                effective_text,
                include_soft_facts=False,
                request_context=request_context,
            ),
        }
        try:
            signature = inspect.signature(extract_people_from_text)
            if "include_collective_selectors" in signature.parameters:
                extract_kwargs["include_collective_selectors"] = True
        except (TypeError, ValueError):
            pass

        extraction_raw = extract_people_from_text(
            effective_text,
            **extract_kwargs,
        )
        if (
            isinstance(extraction_raw, tuple)
            and len(extraction_raw) == 2
            and isinstance(extraction_raw[0], list)
            and isinstance(extraction_raw[1], list)
        ):
            people = _merge_people_mentions(people, extraction_raw[0])
            llm_selector_mentions = extraction_raw[1]
        else:
            llm_people = extraction_raw if isinstance(extraction_raw, list) else []
            people = _merge_people_mentions(people, llm_people)
            llm_selector_mentions = []
        logger.info(
            "[contact_resolver] Post-merge extraction people=%s llm_selectors=%s",
            people,
            llm_selector_mentions,
        )
        trace.trace_contact_resolution_phase(
            "merge_result",
            {
                "people": people,
                "selector_kinds": [selector.get("kind") for selector in llm_selector_mentions],
            },
        )
    extraction_metadata = {
        "fast_path_applied": fast_path_applied,
        "llm_extraction_ran": should_run_llm_extraction,
        "llm_fallback_reasons": fallback_reasons,
        "source": (
            "fast_path_with_llm_fallback"
            if fast_path_applied and should_run_llm_extraction
            else "fast_path"
            if fast_path_applied
            else "llm"
            if should_run_llm_extraction
            else "deterministic"
        ),
    }
    people = _normalize_bare_family_mentions_for_user(effective_text, people)
    if request_context.get("participant_focus") and people:
        conversation_block = ""
        if conversation_messages:
            conversation_json = _format_conversation_for_prompt(conversation_messages)
            if conversation_json:
                conversation_block = (
                    f"Conversation messages (JSON array, most recent last):\n{conversation_json}\n\n"
                )
        user_facts_context = _get_contact_resolution_user_facts_context(
            user_email,
            effective_text,
            include_soft_facts=False,
            request_context=request_context,
        )
        user_facts_block = f"{user_facts_context}\n\n" if user_facts_context else ""
        try:
            filtered_people, excluded_people = _filter_event_participants_via_llm(
                text=effective_text,
                people=people,
                conversation_block=conversation_block,
                user_facts_block=user_facts_block,
            )
            if filtered_people or excluded_people:
                filtered_people = _restore_participant_collective_mentions(
                    text=effective_text,
                    original_people=people,
                    filtered_people=filtered_people,
                )
                logger.info(
                    "[contact_resolver] Participant filter kept=%s excluded=%s",
                    filtered_people,
                    excluded_people,
                )
                people = filtered_people
        except Exception as exc:
            if isinstance(exc, LLMUnavailableError):
                raise
            logger.warning(
                "[contact_resolver] Participant filter failed; continuing with extracted people: %s",
                exc,
                exc_info=exc,
            )
    logger.info("[contact_resolver] Extracted %s people: %s", len(people), people)

    selector_mentions = _merge_collective_selectors(selector_mentions, llm_selector_mentions)
    logger.info(
        "[contact_resolver] Extracted %s collective selectors: %s",
        len(selector_mentions),
        selector_mentions,
    )

    (
        selector_resolved_contacts,
        group_upsert_candidates,
        group_confirmation_candidates,
    ) = _resolve_collective_selectors(selector_mentions, user_email=user_email)
    logger.info(
        "[contact_resolver] Selector-based resolutions: %s",
        len(selector_resolved_contacts),
    )

    if not people and not selector_resolved_contacts:
        return {
            "status": "no_people",
            "text": effective_text,
            "people_mentioned": [],
            "selector_mentions": selector_mentions,
            "resolved_contacts": [],
            "new_contacts": [],
            "ambiguous_contacts": [],
            "extraction_metadata": extraction_metadata,
        }

    # Step 2: Resolve each person (DB lookup or mark as new)
    logger.info("[contact_resolver] Step 2: Resolving %s people...", len(people))
    if people:
        (
            resolved_contacts,
            new_contacts,
            ambiguous_contacts,
            resolution_cache,
        ) = _resolve_people_mentions(
            people,
            user_email,
            effective_text,
            conversation_messages=conversation_messages,
            request_context=request_context,
        )
    else:
        resolved_contacts = []
        new_contacts = []
        ambiguous_contacts = []
        resolution_cache = {}

    if selector_resolved_contacts:
        resolved_contacts = _dedupe_resolved_contacts(
            resolved_contacts + selector_resolved_contacts
        )

    if resolution_mode == MINIMAL_RESOLUTION_MODE:
        result = {
            "status": "success",
            "text": effective_text,
            "people_mentioned": people,
            "selector_mentions": selector_mentions,
            "resolved_contacts": resolved_contacts,
            "new_contacts": new_contacts,
            "ambiguous_contacts": ambiguous_contacts,
            "suggested_relationships": [],
            "group_upsert_candidates": group_upsert_candidates,
            "group_confirmation_candidates": group_confirmation_candidates,
            "extraction_metadata": extraction_metadata,
        }
        if ambiguous_contacts:
            result["status"] = "need_user_input"
            result["need_user_input"] = _build_contact_need_user_input(
                ambiguous_contacts=ambiguous_contacts,
                people_mentioned=people,
            )
        return result

    # Step 3: Infer professions for new contacts
    logger.info("[contact_resolver] Step 3: Inferring professions for new contacts...")
    logger.info("[contact_resolver] Step 4: Inferring relationship pairs...")
    with ThreadPoolExecutor(max_workers=2) as executor:
        profession_future = executor.submit(
            _infer_professions_for_new_contacts,
            new_contacts,
            effective_text,
        )
        relationship_pairs_future = executor.submit(
            _infer_relationship_pairs,
            people,
            effective_text,
        )
        profession_by_text = profession_future.result()
        relationship_pairs = relationship_pairs_future.result()

    # Step 5: Suggest missing relationships (only when none exist yet)
    logger.info("[contact_resolver] Step 5: Suggesting missing relationships...")
    suggested_relationships = _suggest_missing_relationships(
        pairs=relationship_pairs,
        full_text=effective_text,
        user_email=user_email,
        resolution_cache=resolution_cache,
        profession_by_text=profession_by_text,
        request_context=request_context,
    )

    logger.info("[contact_resolver] Resolution complete:")
    logger.info("[contact_resolver]   - Resolved: %s", len(resolved_contacts))
    logger.info("[contact_resolver]   - New: %s", len(new_contacts))
    logger.info("[contact_resolver]   - Ambiguous: %s", len(ambiguous_contacts))
    logger.info(
        "[contact_resolver]   - Suggested relationships: %s",
        len(suggested_relationships),
    )

    result = {
        "text": effective_text,
        "people_mentioned": people,
        "selector_mentions": selector_mentions,
        "resolved_contacts": resolved_contacts,
        "new_contacts": new_contacts,
        "ambiguous_contacts": ambiguous_contacts,
        "suggested_relationships": suggested_relationships,
        "group_upsert_candidates": group_upsert_candidates,
        "group_confirmation_candidates": group_confirmation_candidates,
        "extraction_metadata": extraction_metadata,
    }
    if ambiguous_contacts:
        result["status"] = "need_user_input"
        result["need_user_input"] = _build_contact_need_user_input(
            ambiguous_contacts=ambiguous_contacts,
            people_mentioned=people,
        )
    else:
        result["status"] = "success"

    return result


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _resolve_people_mentions(
    people: list[str],
    user_email: str,
    full_text: str,
    conversation_messages: list[dict[str, str]] | None = None,
    request_context: dict[str, Any] | None = None,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]
]:
    resolved_contacts: list[dict[str, Any]] = []
    new_contacts: list[dict[str, Any]] = []
    ambiguous_contacts: list[dict[str, Any]] = []

    # Cache to avoid re-resolving the same person text multiple times
    # This is especially useful for nested relationships like "my daughter" and "my daughter's doctor"
    resolution_cache: dict[str, dict[str, Any]] = {}
    explicit_new_contact_names = _extract_explicit_new_contact_names(
        text=full_text,
        people=people,
        conversation_messages=conversation_messages,
    )

    for person_text in people:
        normalized_person_text = normalize_search_text(person_text)
        if normalized_person_text in explicit_new_contact_names:
            new_contact = {
                "original_text": person_text,
                "display_name": person_text,
            }
            new_contacts.append(new_contact)
            resolution_cache[person_text] = {
                "status": "new",
                "contact_id": None,
                "display_name": person_text,
                "matched_via": "explicit_new_contact",
                "confidence": "high",
                "resolution_path": None,
            }
            trace.trace_contact_resolution_outcome(
                "explicit_new_contact",
                {
                    "person_text": person_text,
                },
            )
            logger.info(
                "[contact_resolver] '%s' explicitly marked as new contact",
                person_text,
            )
            continue

        # Check cache first
        if person_text in resolution_cache:
            logger.debug("[contact_resolver] Using cached resolution for: '%s'", person_text)
            resolution = resolution_cache[person_text]
        else:
            resolution = resolve_contact(
                person_text,
                user_email,
                event_context=full_text,
                resolution_cache=resolution_cache,
                request_context=request_context,
            )
            resolution_cache[person_text] = resolution

        if resolution["status"] == "resolved":
            resolved_contact = {
                "original_text": person_text,
                "contact_id": resolution["contact_id"],
                "display_name": resolution["display_name"],
                "matched_via": resolution["matched_via"],
                "confidence": resolution["confidence"],
                "resolution_path": resolution.get("resolution_path"),
            }
            resolved_contacts.append(resolved_contact)
            trace.trace_contact_resolution_outcome(
                "resolved",
                {
                    "person_text": person_text,
                    "matched_via": resolution.get("matched_via"),
                },
            )
            logger.info(
                "[contact_resolver] '%s' -> %s",
                person_text,
                resolution["display_name"],
            )

        elif resolution["status"] == "candidates":
            if resolution.get("auto_resolve_candidates"):
                for candidate in resolution["candidates"]:
                    resolved_contact = {
                        "original_text": person_text,
                        "contact_id": candidate.get("contact_id"),
                        "display_name": candidate.get("display_name"),
                        "matched_via": "nested_relationship_group",
                        "confidence": resolution.get("confidence", "medium"),
                        "resolution_path": None,
                    }
                    resolved_contacts.append(resolved_contact)
                trace.trace_contact_resolution_outcome(
                    "auto_group_resolved",
                    {
                        "person_text": person_text,
                        "resolved_count": len(resolution["candidates"]),
                    },
                )
                logger.info(
                    "[contact_resolver] Auto-resolved group mention '%s' to %s related contacts",
                    person_text,
                    len(resolution["candidates"]),
                )
                continue

            llm_result = {"resolved": False, "new_contact": False}
            if not resolution.get("skip_auto_disambiguation"):
                llm_result = _llm_disambiguate_contact(
                    person_text,
                    resolution["candidates"],
                    full_text,
                    conversation_messages=conversation_messages,
                    user_email=user_email,
                    request_context=request_context,
                )
            if llm_result.get("resolved") and _should_accept_llm_disambiguation(
                person_text=person_text,
                candidates=resolution["candidates"],
                full_text=full_text,
                llm_result=llm_result,
                conversation_messages=conversation_messages,
            ):
                resolved_contact = {
                    "original_text": person_text,
                    "contact_id": llm_result.get("contact_id"),
                    "display_name": llm_result.get("display_name"),
                    "matched_via": "llm_disambiguation",
                    "confidence": llm_result.get("confidence", "medium"),
                    "resolution_path": None,
                }
                resolved_contacts.append(resolved_contact)
                resolution_cache[person_text] = {
                    "status": "resolved",
                    "contact_id": llm_result.get("contact_id"),
                    "display_name": llm_result.get("display_name"),
                    "matched_via": "llm_disambiguation",
                    "confidence": llm_result.get("confidence", "medium"),
                    "resolution_path": None,
                    "candidates": resolution.get("candidates", []),
                }
                trace.trace_contact_resolution_outcome(
                    "auto_disambiguated",
                    {
                        "person_text": person_text,
                        "contact_id": llm_result.get("contact_id"),
                    },
                )
                logger.info(
                    "[contact_resolver] LLM resolved: %s",
                    llm_result["display_name"],
                )
                continue
            if llm_result.get("resolved"):
                logger.warning(
                    "[contact_resolver] LLM suggested '%s' but context was insufficient; asking user to clarify instead",
                    llm_result.get("display_name"),
                )

            if llm_result.get("new_contact"):
                new_contact = {
                    "original_text": person_text,
                    "display_name": person_text,
                }
                new_contacts.append(new_contact)
                trace.trace_contact_resolution_outcome(
                    "new_from_disambiguation",
                    {
                        "person_text": person_text,
                    },
                )
                logger.info(
                    "[contact_resolver] '%s' marked as new contact from disambiguation",
                    person_text,
                )
                continue

            ambiguous_contacts.append(
                {
                    "original_text": person_text,
                    "candidates": resolution["candidates"],
                }
            )
            trace.trace_contact_resolution_outcome(
                "ambiguous",
                {
                    "person_text": person_text,
                    "candidate_count": len(resolution.get("candidates", [])),
                },
            )
            logger.warning(
                "[contact_resolver] '%s' -> ambiguous (%s candidates)",
                person_text,
                len(resolution["candidates"]),
            )

        elif resolution["status"] == "new":
            new_contact = {
                "original_text": person_text,
                "display_name": person_text,
            }
            new_contacts.append(new_contact)
            logger.info("[contact_resolver] '%s' -> new contact", person_text)

    return resolved_contacts, new_contacts, ambiguous_contacts, resolution_cache


def _should_accept_llm_disambiguation(
    person_text: str,
    candidates: list[dict[str, Any]],
    full_text: str,
    llm_result: dict[str, Any],
    conversation_messages: list[dict[str, str]] | None = None,
) -> bool:
    """
    Decide whether auto-disambiguation is safe enough to accept without user confirmation.

    Current policy is intentionally conservative:
    - LLM confidence must be high.
    - If there are multiple candidates, context must include non-trivial
      semantic signal beyond temporal boilerplate and the person token itself.
    """
    if not llm_result.get("resolved"):
        return False

    confidence = str(llm_result.get("confidence", "low")).lower()
    if confidence != "high":
        return False

    if len(candidates) <= 1:
        return True

    strictness = _get_disambiguation_strictness()
    if strictness == "lenient":
        return True

    has_context_signal = _has_disambiguating_context(
        person_text=person_text,
        full_text=full_text,
        conversation_messages=conversation_messages,
    )
    if strictness == "strict":
        return has_context_signal

    selected_name = str(llm_result.get("display_name") or "")
    return has_context_signal or _is_name_level_match(person_text, selected_name)


def _get_disambiguation_strictness() -> str:
    """Return disambiguation strictness from env: strict|balanced|lenient."""
    configured = os.getenv("CONTACT_DISAMBIGUATION_STRICTNESS", "strict").strip().lower()
    if configured in {"strict", "balanced", "lenient"}:
        return configured
    return "strict"


def _has_disambiguating_context(
    person_text: str,
    full_text: str,
    conversation_messages: list[dict[str, str]] | None = None,
) -> bool:
    """Return True when context includes meaningful tokens beyond the person mention."""
    person_tokens = set(re.findall(r"[a-z0-9']+", person_text.lower()))
    if not person_tokens:
        return False

    context_fragments = [full_text or ""]
    if conversation_messages:
        context_fragments.extend(
            entry.get("content", "")
            for entry in conversation_messages
            if entry.get("role") == "user"
        )
    context_text = " ".join(fragment for fragment in context_fragments if fragment)
    context_tokens = re.findall(r"[a-z0-9']+", context_text.lower())

    stop_words = {
        "a",
        "an",
        "and",
        "about",
        "did",
        "do",
        "does",
        "event",
        "events",
        "first",
        "i",
        "last",
        "latest",
        "meet",
        "meeting",
        "meetings",
        "met",
        "most",
        "recent",
        "talk",
        "talked",
        "the",
        "time",
        "was",
        "we",
        "when",
        "where",
        "who",
        "with",
        "my",
        "new",
    }

    semantic_tokens = {
        token for token in context_tokens if token not in stop_words and token not in person_tokens
    }

    return len(semantic_tokens) >= 1


def _is_name_level_match(person_text: str, display_name: str) -> bool:
    """
    Return True when mention tokens plausibly map to selected display-name tokens.

    Example: "gio" -> "Giovanni Carter"
    """
    mention_tokens = [
        token
        for token in re.findall(r"[a-z0-9']+", normalize_search_text(person_text))
        if len(token) >= 3
    ]
    name_tokens = re.findall(r"[a-z0-9']+", normalize_search_text(display_name))
    if not mention_tokens or not name_tokens:
        return False

    return all(
        any(
            name_token.startswith(mention_token) or mention_token.startswith(name_token)
            for name_token in name_tokens
        )
        for mention_token in mention_tokens
    )


def _resolve_contact_via_hard_rules(
    user_email: str,
    person_text: str,
    *,
    request_context: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    mention = str(person_text or "").strip()
    if not user_email or not mention:
        return None

    rules = _get_hard_rule_rows(request_context)
    if not rules:
        try:
            import user_facts
        except Exception:
            return None

        try:
            rules = user_facts.get_hard_rules_for_scope(
                user_email,
                scope=RuleScope.CONTACT_RESOLUTION,
                rule_type=RuleType.ENTITY_ALIAS,
                limit=25,
            )
        except Exception:
            logger.exception(
                "[contact_resolver] Failed to load hard rules for user=%s",
                user_email,
            )
            return None
    if not rules:
        return None

    mention_norm = normalize_search_text(mention)
    if not mention_norm:
        return None

    matching_targets: list[str] = []
    for rule in rules:
        payload = rule.get("rule_payload") or {}
        alias_text = str(payload.get("alias_text") or "").strip()
        alias_norm = normalize_search_text(alias_text)
        if alias_norm and alias_norm == mention_norm:
            target_text = str(payload.get("target_text") or "").strip()
            if target_text:
                matching_targets.append(target_text)

    if not matching_targets:
        return None

    dedup_targets: list[str] = []
    seen_targets: set[str] = set()
    for target in matching_targets:
        norm = normalize_search_text(target)
        if not norm or norm in seen_targets:
            continue
        seen_targets.add(norm)
        dedup_targets.append(target)

    if len(dedup_targets) != 1:
        logger.warning(
            "[contact_resolver] Conflicting hard rules for '%s' (%s targets)",
            mention,
            len(dedup_targets),
        )
        trace.trace_contact_resolution_outcome(
            "hard_rule_skipped",
            {
                "person_text": mention,
                "reason": "conflicting_targets",
            },
        )
        return None

    target_text = dedup_targets[0]
    matches = contacts_service.search_contacts(
        target_text,
        search_by="name",
        fuzzy_threshold=80,
        limit=10,
    )
    selected = _select_unique_exact_contact_match(matches, target_text)
    if not selected:
        trace.trace_contact_resolution_outcome(
            "hard_rule_skipped",
            {
                "person_text": mention,
                "target": target_text,
                "reason": "target_not_unique",
            },
        )
        return None

    return {
        "contact_id": str(selected.get("contact_id") or ""),
        "display_name": str(selected.get("display_name") or target_text),
    }


def _select_unique_exact_contact_match(
    matches: list[dict[str, Any]],
    target_text: str,
) -> dict[str, Any] | None:
    if not matches:
        return None
    target_norm = normalize_search_text(target_text)
    if not target_norm:
        return None

    exact_matches: list[dict[str, Any]] = []
    for match in matches:
        display_name = str(match.get("display_name") or "")
        display_norm = normalize_search_text(display_name)
        alias_norms = [
            normalize_search_text(str(alias or ""))
            for alias in (match.get("aliases") or [])
            if str(alias or "").strip()
        ]
        if display_norm == target_norm or target_norm in alias_norms:
            exact_matches.append(match)

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return None
    if len(matches) == 1:
        return matches[0]
    return None


def _select_unique_multi_token_name_match(
    matches: list[dict[str, Any]],
    target_text: str,
) -> dict[str, Any] | None:
    query_tokens = re.findall(r"[a-z0-9']+", normalize_search_text(target_text))
    if len(query_tokens) < 2:
        return None

    matching_candidates: list[dict[str, Any]] = []
    for match in matches:
        candidate_names = [str(match.get("display_name") or "").strip(), *(match.get("aliases") or [])]
        for candidate_name in candidate_names:
            candidate_tokens = set(
                re.findall(r"[a-z0-9']+", normalize_search_text(str(candidate_name or "")))
            )
            if candidate_tokens and all(token in candidate_tokens for token in query_tokens):
                matching_candidates.append(match)
                break

    if len(matching_candidates) == 1:
        return matching_candidates[0]

    return None


def _infer_professions_for_new_contacts(
    new_contacts: list[dict[str, Any]],
    full_text: str,
) -> dict[str, Optional[str]]:
    profession_by_text: dict[str, Optional[str]] = {}
    for new_contact in new_contacts:
        person_text = new_contact["original_text"]
        profession = None
        if _should_attempt_profession_inference(person_text, full_text):
            profession = _infer_profession_from_text(person_text, full_text)
        new_contact["inferred_profession"] = profession
        profession_by_text[person_text] = profession
    return profession_by_text


def _normalize_person_key(text: str) -> str:
    return normalize_search_text(text)


def _unordered_text_pair_key(person_text: str, anchor_text: str) -> Optional[tuple[str, str]]:
    person_key = _normalize_person_key(person_text)
    anchor_key = _normalize_person_key(anchor_text)
    if not person_key or not anchor_key or person_key == anchor_key:
        return None
    sorted_keys = sorted([person_key, anchor_key])
    return (sorted_keys[0], sorted_keys[1])


def _match_cached_resolved_contact_for_nested_first_part(
    first_part: str,
    resolution_cache: Optional[dict[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    if not resolution_cache:
        return None

    first_part_norm = normalize_search_text(first_part)
    if not first_part_norm:
        return None

    for cached_text, cached_resolution in resolution_cache.items():
        if not isinstance(cached_resolution, dict):
            continue
        if cached_resolution.get("status") != "resolved":
            continue

        display_name = str(cached_resolution.get("display_name") or "").strip()
        if display_name and _is_name_level_match(first_part, display_name):
            logger.info(
                "[contact_resolver] Nested: Reusing resolved full-name anchor '%s' for '%s'",
                display_name,
                first_part,
            )
            return dict(cached_resolution)

        cached_text_norm = normalize_search_text(str(cached_text or ""))
        if cached_text_norm and cached_text_norm == first_part_norm:
            return dict(cached_resolution)

    return None


def _parse_nested_relationship(text: str) -> Optional[list[str]]:
    """
    Parse nested relationship like "my daughter's doctor" into ["my daughter", "doctor"].

    Returns:
        List of parts or None if not nested
    """
    text_lower = text.lower().strip()

    if "'s " in text_lower or "s' " in text_lower:
        parts = text.split("'s ", 1) if "'s " in text_lower else text.split("s' ", 1)

        if len(parts) == 2:
            part1 = parts[0].strip()
            part2 = parts[1].strip()

            # Remove articles from second part
            for prefix in ["the ", "a ", "an "]:
                if part2.lower().startswith(prefix):
                    part2 = part2[len(prefix) :].strip()

            return [part1, part2]

    return None


def _resolve_nested_relationship(
    parts: list[str],
    user_email: str,
    resolution_cache: Optional[dict[str, dict[str, Any]]] = None,
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Resolve nested relationship like ["my daughter", "doctor"].

    Process:
    1. Resolve first part ("my daughter") to a contact
    2. Get that contact's relationships
    3. Find the second part ("doctor") in those relationships

    Args:
        parts: List of relationship parts (e.g., ["my daughter", "doctor"])
        user_email: User's email for relationship lookups
        resolution_cache: Optional cache to avoid re-resolving same person text

    Returns:
        {
            "found": bool,
            "contact_id": Optional[str],
            "display_name": Optional[str],
            "confidence": str,
            "path": List[str],
            "first_part_unresolved": bool,  # True if first part couldn't be resolved
            "first_part_status": Optional[str],  # Status of first part resolution
            "candidates": List[dict],  # Candidate contacts for unresolved group mentions
            "collective_reference": bool,
            "auto_resolve_candidates": bool,
        }
    """
    result = {
        "found": False,
        "contact_id": None,
        "display_name": None,
        "confidence": "low",
        "path": [],
        "first_part_unresolved": False,
        "first_part_status": None,
        "candidates": [],
        "collective_reference": False,
        "auto_resolve_candidates": False,
    }

    if len(parts) < 2:
        return result

    # Step 1: Resolve first part (check cache first)
    first_part = parts[0]
    first_resolution: dict[str, Any] | None = None
    cached_anchor_resolution = _match_cached_resolved_contact_for_nested_first_part(
        first_part,
        resolution_cache,
    )
    if cached_anchor_resolution is not None:
        first_resolution = cached_anchor_resolution
        if resolution_cache is not None and first_part not in resolution_cache:
            resolution_cache[first_part] = first_resolution
    if resolution_cache and first_part in resolution_cache:
        logger.debug(
            "[contact_resolver] Nested: Using cached resolution for first part: '%s'",
            first_part,
        )
        first_resolution = resolution_cache[first_part]
    elif cached_anchor_resolution is None:
        first_resolution = resolve_contact(
            first_part,
            user_email,
            resolution_cache=resolution_cache,
            request_context=request_context,
        )
        if resolution_cache is not None:
            resolution_cache[first_part] = first_resolution

    if first_resolution is None:
        return result

    if first_resolution["status"] != "resolved":
        logger.info(
            "[contact_resolver] Nested: Could not resolve first part '%s' (status: %s)",
            parts[0],
            first_resolution["status"],
        )
        # Mark that the first part couldn't be resolved
        result["first_part_unresolved"] = True
        result["first_part_status"] = first_resolution["status"]
        return result

    intermediate_contact_id = first_resolution["contact_id"]
    intermediate_name = first_resolution["display_name"]
    result["path"] = ["user", intermediate_name]

    logger.debug(
        "[contact_resolver] Nested: First part resolved to %s",
        intermediate_name,
    )

    # Step 2: Get intermediate contact's relationships
    try:
        intermediate_rels = _get_relationship_context(
            str(intermediate_contact_id),
            include_contact_details=True,
            request_context=request_context,
        )
    except Exception as e:
        logger.warning(
            "[contact_resolver] Nested: Failed to get relationships: %s",
            e,
            exc_info=e,
        )
        return result

    # Step 3: Resolve second part within those relationships
    # The second part is already the clean relationship type (e.g., "doctor")
    # because _parse_nested_relationship already stripped articles
    second_part = parts[1]
    logger.debug(
        "[contact_resolver] Nested: Second part (relationship type): %s",
        second_part,
    )

    # Try relationship match directly with the second part as the relationship type
    # Use for_nested_resolution=True to ONLY match on 'other_type'
    # This ensures we find what the related contact IS to the intermediate person
    rel_result = _resolve_via_relationship(
        second_part, intermediate_rels, for_nested_resolution=True
    )
    logger.debug("[contact_resolver] Nested: Relationship result: %s", rel_result)

    if not rel_result["found"] and not rel_result["candidates"]:
        rel_result = _resolve_via_relationship(
            second_part,
            intermediate_rels,
            for_nested_resolution=False,
        )
        logger.debug(
            "[contact_resolver] Nested: Fallback direct relationship result: %s",
            rel_result,
        )

    if rel_result["found"]:
        result["found"] = True
        result["contact_id"] = rel_result["contact_id"]
        result["display_name"] = rel_result["display_name"]
        result["confidence"] = "medium"
        result["path"].append(result["display_name"])
        return result

    if rel_result["candidates"]:
        # Multiple matches found - for nested relationships, return first candidate
        # (we could enhance this later to return candidates for disambiguation)
        candidate = rel_result["candidates"][0]
        result["found"] = True
        result["contact_id"] = candidate["contact_id"]
        result["display_name"] = candidate["display_name"]
        result["confidence"] = "low"
        result["path"].append(result["display_name"])
        logger.info(
            "[contact_resolver] Nested: Multiple %ss found, using first: %s",
            second_part,
            candidate["display_name"],
        )
        return result

    # Try fuzzy search among related contacts as fallback
    search_name = second_part
    matches = contacts_service.search_contacts(
        search_name, search_by="any", fuzzy_threshold=75, limit=3
    )

    if matches:
        # Filter to only those in intermediate contact's relationships
        relationships = intermediate_rels.get("relationships", [])
        related_ids = {
            contact_id
            for rel in relationships
            for contact_id in [
                str(
                    (rel.get("related_contact") or {}).get("contact_id")
                    or rel.get("contact_id")
                    or ""
                ).strip()
            ]
            if contact_id
        }

        filtered = [m for m in matches if m["contact_id"] in related_ids]

        if filtered:
            best_match = filtered[0]
            result["found"] = True
            result["contact_id"] = best_match["contact_id"]
            result["display_name"] = best_match["display_name"]
            result["confidence"] = "medium"
            result["path"].append(result["display_name"])
            return result

    phrase_match_result = _resolve_relationship_phrase_against_related_contacts(
        anchor_display_name=intermediate_name,
        relationship_phrase=second_part,
        relationship_context=intermediate_rels,
    )

    if phrase_match_result["status"] == "resolved":
        result["found"] = True
        result["contact_id"] = phrase_match_result.get("contact_id")
        result["display_name"] = phrase_match_result.get("display_name")
        result["confidence"] = phrase_match_result.get("confidence", "medium")
        result["path"].append(result["display_name"])
        logger.info(
            "[contact_resolver] Nested: LLM matched related contact '%s' for phrase '%s'",
            result["display_name"],
            second_part,
        )
        return result

    if phrase_match_result["status"] == "candidates":
        result["candidates"] = phrase_match_result.get("candidates", [])
        result["collective_reference"] = bool(phrase_match_result.get("collective_reference"))
        result["confidence"] = phrase_match_result.get("confidence", "low")
        result["auto_resolve_candidates"] = bool(phrase_match_result.get("auto_resolve_candidates"))
        logger.info(
            "[contact_resolver] Nested: Relationship phrase produced %s candidates",
            len(result["candidates"]),
        )
    return result


def _resolve_relationship_phrase_against_related_contacts(
    *,
    anchor_display_name: str,
    relationship_phrase: str,
    relationship_context: dict[str, Any],
) -> dict[str, Any]:
    """
    Resolve a free-form relationship phrase against known related contacts.

    Used when direct relationship-type matching is insufficient
    (e.g., collective phrases like "whole family").
    """
    related_candidates = _build_related_contact_candidates(relationship_context)
    if not related_candidates:
        return {"status": "no_match", "candidates": [], "confidence": "low"}

    llm_related_result = _llm_match_nested_relationship_candidates(
        anchor_display_name=anchor_display_name,
        relationship_phrase=relationship_phrase,
        related_candidates=related_candidates,
    )
    selected_candidates = llm_related_result.get("candidates") or []
    confidence = str(llm_related_result.get("confidence", "low")).lower().strip()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    if len(selected_candidates) == 1:
        selected = selected_candidates[0]
        return {
            "status": "resolved",
            "contact_id": selected.get("contact_id"),
            "display_name": selected.get("display_name"),
            "confidence": confidence,
            "candidates": [],
            "collective_reference": bool(llm_related_result.get("collective_reference")),
            "auto_resolve_candidates": False,
        }

    if len(selected_candidates) > 1:
        collective_reference = bool(llm_related_result.get("collective_reference"))
        return {
            "status": "candidates",
            "contact_id": None,
            "display_name": None,
            "confidence": confidence,
            "candidates": selected_candidates,
            "collective_reference": collective_reference,
            "auto_resolve_candidates": (collective_reference and confidence in {"high", "medium"}),
        }

    # Fallback: preserve relationship candidates instead of creating a fake new contact.
    return {
        "status": "candidates",
        "contact_id": None,
        "display_name": None,
        "confidence": "low",
        "candidates": related_candidates,
        "collective_reference": False,
        "auto_resolve_candidates": False,
    }


def _build_related_contact_candidates(relationship_context: dict[str, Any]) -> list[dict[str, Any]]:
    relationships = relationship_context.get("relationships", [])
    if not relationships:
        return []

    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()

    for rel in relationships:
        related_contact = rel.get("related_contact") or {}
        contact_id = str(related_contact.get("contact_id") or rel.get("contact_id") or "").strip()
        display_name = str(related_contact.get("display_name") or "").strip()
        if not contact_id or not display_name:
            continue

        if contact_id in seen_ids or normalize_search_text(display_name) in seen_names:
            continue
        seen_ids.add(contact_id)
        seen_names.add(normalize_search_text(display_name))

        rel_type = str(rel.get("type") or "").strip().lower()
        other_type = str(rel.get("other_type") or "").strip().lower()
        reason_parts = []
        if other_type:
            reason_parts.append(f"other_type: {other_type}")
        if rel_type:
            reason_parts.append(f"type: {rel_type}")
        reason = ", ".join(reason_parts) if reason_parts else "relationship candidate"

        candidates.append(
            {
                "contact_id": contact_id,
                "display_name": display_name,
                "match_reason": reason,
                "relationship_type": rel_type,
                "other_type": other_type,
            }
        )

    return candidates


def _llm_match_nested_relationship_candidates(
    *,
    anchor_display_name: str,
    relationship_phrase: str,
    related_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Map an unresolved nested relationship phrase to one or more related contacts.

    This keeps matching generic and model-driven, avoiding hardcoded lexical lists.
    """
    if not related_candidates:
        return {
            "candidates": [],
            "collective_reference": False,
            "confidence": "low",
        }

    candidate_lines: list[str] = []
    for index, candidate in enumerate(related_candidates, start=1):
        rel_type = candidate.get("relationship_type") or "-"
        other_type = candidate.get("other_type") or "-"
        candidate_lines.append(
            f"- {index}. {candidate['display_name']} (ID: {candidate['contact_id']}) "
            f"| relationship_type: {rel_type} | other_type: {other_type}"
        )
    candidate_block = "\n".join(candidate_lines)

    prompt = build_nested_relationship_selection_prompt(
        anchor_display_name=anchor_display_name,
        relationship_phrase=relationship_phrase,
        candidate_block=candidate_block,
    )

    try:
        llm_result = _call_contact_resolution_llm_json(
            prompt,
            timeout=60,
            temperature=0.1,
            top_p=0.9,
            use_fast_model=True,
            response_format=_schema_response_format(
                "nested_relationship_selection",
                NESTED_RELATIONSHIP_SELECTION_RESPONSE_SCHEMA,
            ),
        )
    except Exception:
        return {
            "candidates": [],
            "collective_reference": False,
            "confidence": "low",
        }

    raw_numbers = llm_result.get("candidate_numbers", [])
    if isinstance(raw_numbers, int):
        raw_numbers = [raw_numbers]
    if not isinstance(raw_numbers, list):
        raw_numbers = []

    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for value in raw_numbers:
        if not isinstance(value, int):
            continue
        if value < 1 or value > len(related_candidates):
            continue
        candidate = related_candidates[value - 1]
        candidate_id = str(candidate.get("contact_id") or "")
        if candidate_id and candidate_id in seen_ids:
            continue
        if candidate_id:
            seen_ids.add(candidate_id)
        selected.append(candidate)

    confidence = str(llm_result.get("confidence", "low")).lower().strip()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    return {
        "candidates": selected,
        "collective_reference": bool(llm_result.get("collective_reference", False)),
        "confidence": confidence,
    }


def _detect_relational_term(text: str) -> Optional[str]:
    """
    Detect if text is a relational term like "my daughter", "the doctor".

    Returns:
        relationship_type if detected, None otherwise
    """
    text_lower = text.lower().strip()

    possessive_markers = ["my ", "user's ", "the ", "a ", "an ", "their ", "his ", "her "]
    for marker in possessive_markers:
        if text_lower.startswith(marker):
            rel_type = text_lower.replace(marker, "").strip()
            if rel_type:
                return rel_type

    return None


def _strip_generic_markers(text: str) -> str:
    """Strip markers like 'my', 'the' from text."""
    text_lower = text.lower().strip()
    for prefix in ["my ", "user's ", "the ", "a ", "an ", "their ", "his ", "her "]:
        if text_lower.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _resolve_via_relationship(
    relationship_type: str,
    relationship_context: dict[str, Any],
    for_nested_resolution: bool = False,
) -> dict[str, Any]:
    """
    Try to resolve person via relationship data.

    Args:
        relationship_type: The type of relationship to look for (e.g., "mother", "doctor")
        relationship_context: Dictionary containing relationships data
        for_nested_resolution: If True, only match on 'other_type' (what the related contact IS).
                             If False, match on both 'type' and 'other_type' for flexibility.

    Returns:
        {
            "found": bool,
            "contact_id": Optional[str],
            "display_name": Optional[str],
            "confidence": str,
            "candidates": list[dict]  # Multiple matches if they exist
        }

    Relationship directionality:
        - 'type': What THIS contact is TO the related contact
        - 'other_type': What the RELATED CONTACT is TO this contact

        Example: Jane has relationship {type: "child", other_type: "mother", related_contact: Mary}
        - Jane is a "child" to Mary
        - Mary is a "mother" to Jane
        - When looking for "mother", we match on 'other_type' because Mary IS the mother
    """
    result = {
        "found": False,
        "contact_id": None,
        "display_name": None,
        "confidence": "low",
        "candidates": [],
    }

    def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for candidate in candidates:
            contact_id = str(candidate.get("contact_id") or "")
            display_name = (candidate.get("display_name") or "").strip()
            name_key = normalize_search_text(display_name)
            if contact_id and contact_id in seen_ids:
                continue
            if name_key and name_key in seen_names:
                continue
            if contact_id:
                seen_ids.add(contact_id)
            if name_key:
                seen_names.add(name_key)
            deduped.append(candidate)
        return deduped

    def _build_candidate(contact: dict[str, Any], reason: str) -> dict[str, Any]:
        candidate: dict[str, Any] = {
            "contact_id": contact["contact_id"],
            "display_name": contact["display_name"],
            "match_reason": reason,
        }
        aliases = contact.get("aliases")
        if isinstance(aliases, list) and aliases:
            candidate["aliases"] = aliases
        return candidate

    relationships = relationship_context.get("relationships", [])
    if not relationships:
        return result

    # Build map of relationship types to contacts
    rel_map: dict[str, list[dict]] = {}
    for rel in relationships:
        # For nested resolution, ONLY use 'other_type' because we want to find
        # what the related contact IS to the intermediate person
        if for_nested_resolution:
            other_type = (rel.get("other_type") or "").lower()
            if other_type and "related_contact" in rel:
                if other_type not in rel_map:
                    rel_map[other_type] = []
                contact_data = rel["related_contact"].copy()
                if "contact_id" not in contact_data:
                    contact_data["contact_id"] = rel.get("contact_id")
                rel_map[other_type].append(contact_data)
        else:
            # For direct user relationships, check both type and other_type for flexibility
            rel_type = (rel.get("type") or "").lower()
            if rel_type and "related_contact" in rel:
                if rel_type not in rel_map:
                    rel_map[rel_type] = []
                contact_data = rel["related_contact"].copy()
                if "contact_id" not in contact_data:
                    contact_data["contact_id"] = rel.get("contact_id")
                rel_map[rel_type].append(contact_data)

            other_type = (rel.get("other_type") or "").lower()
            if other_type and "related_contact" in rel:
                if other_type not in rel_map:
                    rel_map[other_type] = []
                contact_data = rel["related_contact"].copy()
                if "contact_id" not in contact_data:
                    contact_data["contact_id"] = rel.get("contact_id")
                rel_map[other_type].append(contact_data)

    # Direct match
    if rel_map.get(relationship_type):
        matches = rel_map[relationship_type]
        logger.debug("[contact_resolver_inner] Direct match found: %s", matches)

        if len(matches) == 1:
            # Single match - resolved
            contact = matches[0]
            result["found"] = True
            result["contact_id"] = contact["contact_id"]
            result["display_name"] = contact["display_name"]
            result["confidence"] = "high"
            return result
        else:
            # Multiple matches - return candidates
            result["found"] = False
            result["candidates"] = _dedupe_candidates(
                [_build_candidate(c, f"relationship match: {relationship_type}") for c in matches]
            )
            result["confidence"] = "low"
            return result

    # Try related types (e.g., "daughter" -> "child")
    # Use the shared mapping from contacts module
    related_types = contacts_service.find_related_types(relationship_type)

    logger.debug("[contact_resolver_inner] Related types: %s", related_types)

    for related_type in related_types:
        if rel_map.get(related_type):
            matches = rel_map[related_type]

            if len(matches) == 1:
                # Single match - resolved
                contact = matches[0]
                result["found"] = True
                result["contact_id"] = contact["contact_id"]
                result["display_name"] = contact["display_name"]
                result["confidence"] = "medium"
                return result
            else:
                # Multiple matches - return candidates
                result["found"] = False
                result["candidates"] = _dedupe_candidates(
                    [
                        _build_candidate(c, f"related relationship match: {related_type}")
                        for c in matches
                    ]
                )
                result["confidence"] = "low"
                return result

    return result


def _llm_disambiguate_contact(
    person_text: str,
    candidates: list[dict[str, Any]],
    event_context: str,
    conversation_messages: list[dict[str, str]] | None = None,
    user_email: str | None = None,
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Use LLM to disambiguate between multiple contact candidates.

    CRITICAL: LLM can ONLY choose from candidates or say it cannot decide.
    It must expose certainty via a structured `new_contact` flag.

    Returns:
        {
            "resolved": bool,
            "new_contact": bool,
            "contact_id": Optional[str],
            "display_name": Optional[str],
            "confidence": str,
        }
    """
    def _build_relationship_summary(candidate: dict[str, Any]) -> str:
        contact_id = str(candidate.get("contact_id") or "").strip()
        if not contact_id:
            return ""

        relationship_context = _get_relationship_context(
            contact_id,
            include_contact_details=True,
            request_context=request_context,
        )
        relationships = relationship_context.get("relationships") or []
        if not isinstance(relationships, list) or not relationships:
            return ""

        fragments: list[str] = []
        seen: set[str] = set()
        for rel in relationships:
            if not isinstance(rel, dict):
                continue
            related_contact = rel.get("related_contact") or {}
            related_name = str(related_contact.get("display_name") or "").strip()
            rel_type = str(rel.get("type") or "").strip().lower()
            other_type = str(rel.get("other_type") or "").strip().lower()

            if rel_type and related_name:
                fragment = f"{rel_type} of {related_name}"
            elif other_type and related_name:
                fragment = f"related to {related_name} as {other_type}"
            elif rel_type:
                fragment = rel_type
            elif other_type:
                fragment = other_type
            else:
                continue

            fragment_key = normalize_search_text(fragment)
            if fragment_key in seen:
                continue
            seen.add(fragment_key)
            fragments.append(fragment)
            if len(fragments) >= 3:
                break

        if not fragments:
            return ""
        return f" | Relationships: {'; '.join(fragments)}"

    formatted_candidates: list[str] = []
    for i, candidate in enumerate(candidates):
        aliases = [
            str(alias).strip() for alias in (candidate.get("aliases") or []) if str(alias).strip()
        ]
        alias_suffix = f" | Aliases: {', '.join(aliases[:5])}" if aliases else ""
        reason = str(candidate.get("match_reason") or "").strip()
        reason_suffix = f" | Match hint: {reason}" if reason else ""
        relationship_suffix = _build_relationship_summary(candidate)
        formatted_candidates.append(
            f"- {i + 1}. {candidate['display_name']} (ID: {candidate['contact_id']})"
            f"{alias_suffix}{reason_suffix}{relationship_suffix}"
        )
    candidate_list = "\n".join(formatted_candidates)

    conversation_block = ""
    disambiguation_history_block = _format_disambiguation_history_for_prompt(conversation_messages)
    if conversation_messages:
        conversation_json = _format_conversation_for_prompt(conversation_messages)
        if conversation_json:
            conversation_block = (
                f"Conversation messages (JSON array, most recent last):\n{conversation_json}\n\n"
            )

    user_facts_block = ""
    if user_email:
        facts_ctx = _get_contact_resolution_user_facts_context(
            user_email,
            f"{person_text} {event_context[:200]}",
            include_soft_facts=True,
            request_context=request_context,
        )
        if facts_ctx:
            user_facts_block = f"{facts_ctx}\n\n"

    prompt = build_contact_disambiguation_prompt(
        person_text=person_text,
        candidate_list=candidate_list,
        event_context=event_context,
        disambiguation_history_block=disambiguation_history_block,
        conversation_block=conversation_block,
        user_facts_block=user_facts_block,
    )

    try:
        # Use low temperature for consistent disambiguation
        llm_response = _call_contact_resolution_llm_json(
            prompt,
            timeout=60,
            temperature=0.1,
            top_p=0.9,
            use_fast_model=True,
            response_format=_schema_response_format(
                "contact_disambiguation",
                CONTACT_DISAMBIGUATION_RESPONSE_SCHEMA,
            ),
        )

        decision = llm_response.get("decision")
        candidate_number = llm_response.get("candidate_number")
        new_contact = bool(llm_response.get("new_contact") is True)

        if decision == "resolved" and candidate_number and 1 <= candidate_number <= len(candidates):
            chosen = candidates[candidate_number - 1]
            return {
                "resolved": True,
                "new_contact": False,
                "contact_id": chosen["contact_id"],
                "display_name": chosen["display_name"],
                "confidence": llm_response.get("confidence", "medium"),
            }

        return {
            "resolved": False,
            "new_contact": new_contact,
            "contact_id": None,
            "display_name": None,
            "confidence": "low",
        }

    except Exception as e:
        if isinstance(e, LLMUnavailableError):
            logger.error(
                "[contact_resolver] Critical LLM outage during contact disambiguation: %s",
                e,
            )
            raise
        logger.warning("[contact_resolver] LLM disambiguation failed: %s", e, exc_info=e)
        return {
            "resolved": False,
            "new_contact": False,
            "contact_id": None,
            "display_name": None,
            "confidence": "low",
        }


def _infer_profession_from_text(person_text: str, full_text: str) -> Optional[str]:
    """
    Infer profession from context if explicitly stated.

    Examples:
    - "Dr. Smith" -> "doctor"
    - "lawyer John" -> "lawyer"
    - "met with teacher" -> "teacher"

    Returns:
        Profession string or None
    """
    prompt = build_profession_inference_prompt(person_text=person_text, full_text=full_text)

    try:
        # Use low temperature for consistent profession inference
        result = _call_contact_resolution_llm_json(
            prompt,
            timeout=20,
            temperature=0.1,
            top_p=0.9,
            use_fast_model=True,
            response_format=_schema_response_format(
                "profession_inference",
                PROFESSION_INFERENCE_RESPONSE_SCHEMA,
            ),
        )
        return result.get("profession")
    except Exception:
        return None


def _should_attempt_profession_inference(
    person_text: str,
    full_text: str,
    *,
    relationship_hint: str | None = None,
) -> bool:
    normalized_person = normalize_search_text(person_text or "")
    if not normalized_person or normalized_person == "user":
        return False
    if normalized_person in _USER_SCOPED_FAMILY_RELATIONSHIP_TERMS:
        return False
    if _POSSESSIVE_COLLECTIVE_PATTERN.fullmatch(str(person_text or "").strip()):
        return False

    normalized_hint = normalize_search_text(relationship_hint or "")
    if normalized_hint:
        if normalized_hint in _USER_SCOPED_FAMILY_RELATIONSHIP_TERMS:
            return False
        if normalized_hint in {
            "spouse",
            "parent",
            "child",
            "family",
            "friend",
            "coworker",
            "co worker",
            "colleague",
            "partner",
        }:
            return False
        if _PROFESSION_TITLE_CUE_PATTERN.search(normalized_hint):
            return True

    person_text_raw = str(person_text or "").strip()
    full_text_raw = str(full_text or "")
    if not person_text_raw or not full_text_raw:
        return False
    if _PROFESSION_TITLE_CUE_PATTERN.search(person_text_raw):
        return True

    escaped_person = re.escape(person_text_raw)
    contextual_patterns = (
        rf"\b(?:dr\.?|prof\.?|professor)\s+{escaped_person}\b",
        rf"\b{escaped_person}\s*,\s*(?:the\s+)?(?:doctor|dentist|therapist|teacher|coach|trainer|lawyer|attorney|engineer|designer|developer|nurse|physician|surgeon)\b",
        rf"\b(?:doctor|dentist|therapist|teacher|coach|trainer|lawyer|attorney|engineer|designer|developer|nurse|physician|surgeon)\s+{escaped_person}\b",
    )
    return any(re.search(pattern, full_text_raw, flags=re.IGNORECASE) for pattern in contextual_patterns)


def _normalize_relationship_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip()
    return cleaned.lower() if cleaned else None


def _is_meaningful_relationship_hint(value: Optional[str]) -> bool:
    normalized = normalize_search_text(value or "")
    if not normalized:
        return False
    tokens = [token for token in normalized.replace("-", " ").split() if token]
    if not tokens:
        return False
    return not all(token in _BLOCKED_RELATIONSHIP_TERMS for token in tokens)


def _is_meaningful_relationship_type(value: Optional[str]) -> bool:
    normalized = _normalize_relationship_type(value)
    if not normalized:
        return False
    tokens = [token for token in normalized.replace("-", " ").split() if token]
    if not tokens:
        return False
    return not all(token in _BLOCKED_RELATIONSHIP_TERMS for token in tokens)


def _infer_relationship_pairs(people: list[str], full_text: str) -> list[dict[str, str]]:
    """
    Infer explicit relationship pairs between people mentioned in text.

    Returns:
        [{"person_text": "John", "anchor_text": "my daughter", "relationship_hint": "eye doctor"}]
    """
    if not people:
        return []

    people_list = "\n".join(f"- {p}" for p in people)
    logger.debug("[contact_resolver_inner] People list: %s", people_list)
    prompt = build_relationship_pairs_prompt(full_text=full_text, people_list=people_list)

    try:
        result = _call_contact_resolution_llm_json(
            prompt,
            timeout=60,
            temperature=0.1,
            top_p=0.9,
            use_fast_model=True,
            response_format=_schema_response_format(
                "relationship_pairs",
                RELATIONSHIP_PAIRS_RESPONSE_SCHEMA,
            ),
        )
    except Exception:
        return []

    relationships = result.get("relationships", [])
    logger.debug("[contact_resolver_inner] Relationships: %s", relationships)
    pairs: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for item in relationships:
        person_text = item.get("person_text")
        anchor_text = item.get("anchor_text")
        relationship_hint = item.get("relationship_hint")
        if not person_text or not anchor_text or not relationship_hint:
            continue
        if not _is_meaningful_relationship_hint(relationship_hint):
            continue
        if person_text == anchor_text:
            continue
        if person_text not in people and person_text.lower().strip() != "user":
            continue
        if anchor_text not in people and anchor_text.lower().strip() != "user":
            continue
        pair_key = _unordered_text_pair_key(person_text, anchor_text)
        if not pair_key or pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        pairs.append(
            {
                "person_text": person_text,
                "anchor_text": anchor_text,
                "relationship_hint": relationship_hint,
            }
        )

    logger.debug("[contact_resolver_inner] Pairs: %s", pairs)
    return pairs


def _infer_relationship_types(
    person_text: str,
    anchor_text: str,
    relationship_hint: str,
    full_text: str,
    *,
    person_profession: Optional[str] = None,
    anchor_profession: Optional[str] = None,
) -> Optional[dict[str, str]]:
    prompt = build_relationship_types_prompt(
        person_text=person_text,
        anchor_text=anchor_text,
        relationship_hint=relationship_hint,
        full_text=full_text,
        person_profession=person_profession,
        anchor_profession=anchor_profession,
    )

    try:
        result = _call_contact_resolution_llm_json(
            prompt,
            timeout=20,
            temperature=0.1,
            top_p=0.9,
            use_fast_model=True,
            response_format=_schema_response_format(
                "relationship_types",
                RELATIONSHIP_TYPES_RESPONSE_SCHEMA,
            ),
        )
    except Exception:
        return None

    logger.debug("[contact_resolver_inner] Result: %s", result)
    rel_type = _normalize_relationship_type(result.get("type"))
    other_type = _normalize_relationship_type(result.get("other_type"))

    invalid_types = {"self", "same", "same person", "identical", "me"}
    if not rel_type or not other_type or rel_type in invalid_types or other_type in invalid_types:
        return None
    if not _is_meaningful_relationship_type(rel_type):
        return None
    if not _is_meaningful_relationship_type(other_type):
        return None

    return {"type": rel_type, "other_type": other_type}


def _relationship_exists_between_contacts(
    from_contact_id: str,
    to_contact_id: str,
    *,
    relationship_edges: set[tuple[str, str]] | None = None,
    request_context: dict[str, Any] | None = None,
) -> bool:
    if relationship_edges is not None:
        sorted_ids = sorted([str(from_contact_id), str(to_contact_id)])
        return (sorted_ids[0], sorted_ids[1]) in relationship_edges
    return _relationship_exists_one_way(
        from_contact_id,
        to_contact_id,
        request_context=request_context,
    ) or _relationship_exists_one_way(
        to_contact_id,
        from_contact_id,
        request_context=request_context,
    )


def _relationship_exists_one_way(
    from_contact_id: str,
    to_contact_id: str,
    *,
    request_context: dict[str, Any] | None = None,
) -> bool:
    rels = _get_relationship_context(
        str(from_contact_id),
        include_contact_details=False,
        request_context=request_context,
    )

    if not rels.get("found"):
        return False

    relationships = rels.get("relationships", [])
    return any(rel.get("contact_id") == to_contact_id for rel in relationships)


def _build_relationship_edge_cache(
    contact_ids: set[str],
    *,
    request_context: dict[str, Any] | None = None,
) -> set[tuple[str, str]]:
    if not contact_ids:
        return set()

    edges: set[tuple[str, str]] = set()

    def _load_edges(contact_id: str) -> None:
        rels = _get_relationship_context(
            contact_id,
            include_contact_details=False,
            request_context=request_context,
        )
        if not rels.get("found"):
            return
        relationships = rels.get("relationships", [])
        for rel in relationships:
            raw_related_contact_id = rel.get("contact_id")
            related_contact_id = (
                str(raw_related_contact_id).strip()
                if isinstance(raw_related_contact_id, str)
                else ""
            )
            if not related_contact_id:
                continue
            sorted_ids = sorted([contact_id, related_contact_id])
            edges.add((sorted_ids[0], sorted_ids[1]))

    max_workers = min(4, len(contact_ids))
    if max_workers <= 1:
        for contact_id in sorted(contact_ids):
            _load_edges(contact_id)
        return edges

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_load_edges, sorted(contact_ids)))
    return edges


def _suggest_missing_relationships(
    *,
    pairs: list[dict[str, str]],
    full_text: str,
    user_email: str,
    resolution_cache: dict[str, dict[str, Any]],
    profession_by_text: dict[str, Optional[str]],
    request_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not pairs:
        return []

    user_contact_id = None
    user_contact = _get_request_self_contact(request_context)
    if user_contact is None:
        user_contact = contacts_service.find_self_contact(user_email)
    if user_contact:
        user_contact_id = user_contact.get("contact_id")

    suggestions: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    relationship_cache: dict[tuple[str, str], bool] = {}
    resolved_contact_ids: set[str] = set()

    def _resolution_for(text: str) -> Optional[dict[str, Any]]:
        if text.lower().strip() == "user":
            if user_contact_id:
                display_name = user_contact.get("display_name") if user_contact else "user"
                return {
                    "status": "resolved",
                    "contact_id": user_contact_id,
                    "display_name": display_name,
                }
            return {"status": "new", "contact_id": None, "display_name": "user"}
        return resolution_cache.get(text)

    def _entity_key(text: str, resolution: dict[str, Any]) -> Optional[str]:
        status = resolution.get("status")
        if status == "resolved" and resolution.get("contact_id"):
            return resolution["contact_id"]
        if status == "new":
            return f"new:{text}"
        return None

    def _relationship_exists_cached(
        person_contact_id: str,
        anchor_contact_id: str,
        *,
        relationship_edges: set[tuple[str, str]] | None = None,
    ) -> bool:
        sorted_ids = sorted([str(person_contact_id), str(anchor_contact_id)])
        key = (sorted_ids[0], sorted_ids[1])
        if key in relationship_cache:
            return relationship_cache[key]
        exists = _relationship_exists_between_contacts(
            person_contact_id,
            anchor_contact_id,
            relationship_edges=relationship_edges,
            request_context=request_context,
        )
        relationship_cache[key] = exists
        return exists

    def _profession_for(text: str, relationship_hint: str) -> Optional[str]:
        cached_value = profession_by_text.get(text, _PROFESSION_INFERENCE_SENTINEL)
        if cached_value is not _PROFESSION_INFERENCE_SENTINEL:
            if isinstance(cached_value, str):
                return cached_value
            if cached_value is None:
                return None
            return None
        if not _should_attempt_profession_inference(
            text,
            full_text,
            relationship_hint=relationship_hint,
        ):
            profession_by_text[text] = None
            return None
        profession_by_text[text] = _infer_profession_from_text(text, full_text)
        return profession_by_text[text]

    for pair in pairs:
        person_text = pair.get("person_text")
        anchor_text = pair.get("anchor_text")
        relationship_hint = pair.get("relationship_hint")
        if not person_text or not anchor_text or not relationship_hint:
            continue

        person_resolution = _resolution_for(person_text)
        anchor_resolution = _resolution_for(anchor_text)
        if not person_resolution or not anchor_resolution:
            continue
        if (
            person_resolution.get("status") == "candidates"
            or anchor_resolution.get("status") == "candidates"
        ):
            continue

        person_key = _entity_key(person_text, person_resolution)
        anchor_key = _entity_key(anchor_text, anchor_resolution)
        if not person_key or not anchor_key or person_key == anchor_key:
            continue

        person_contact_id = str(person_resolution.get("contact_id") or "").strip()
        anchor_contact_id = str(anchor_resolution.get("contact_id") or "").strip()
        if person_contact_id:
            resolved_contact_ids.add(person_contact_id)
        if anchor_contact_id:
            resolved_contact_ids.add(anchor_contact_id)

    relationship_edges = _build_relationship_edge_cache(
        resolved_contact_ids,
        request_context=request_context,
    )

    for pair in pairs:
        person_text = pair.get("person_text")
        anchor_text = pair.get("anchor_text")
        relationship_hint = pair.get("relationship_hint")
        if not person_text or not anchor_text or not relationship_hint:
            continue

        person_resolution = _resolution_for(person_text)
        anchor_resolution = _resolution_for(anchor_text)
        if not person_resolution or not anchor_resolution:
            continue
        if (
            person_resolution.get("status") == "candidates"
            or anchor_resolution.get("status") == "candidates"
        ):
            continue

        person_key = _entity_key(person_text, person_resolution)
        anchor_key = _entity_key(anchor_text, anchor_resolution)
        if not person_key or not anchor_key or person_key == anchor_key:
            continue

        sorted_keys = sorted([person_key, anchor_key])
        pair_key = (sorted_keys[0], sorted_keys[1])
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        person_contact_id = person_resolution.get("contact_id")
        anchor_contact_id = anchor_resolution.get("contact_id")
        if person_contact_id and anchor_contact_id and _relationship_exists_cached(
            person_contact_id,
            anchor_contact_id,
            relationship_edges=relationship_edges,
        ):
            continue

        person_profession = _profession_for(person_text, relationship_hint)
        anchor_profession = _profession_for(anchor_text, relationship_hint)
        rel_types = _infer_relationship_types(
            person_text,
            anchor_text,
            relationship_hint,
            full_text,
            person_profession=person_profession,
            anchor_profession=anchor_profession,
        )
        if not rel_types:
            continue

        suggestions.append(
            {
                "from_text": person_text,
                "to_text": anchor_text,
                "from_contact_id": person_contact_id,
                "to_contact_id": anchor_contact_id,
                "type": rel_types["type"],
                "other_type": rel_types["other_type"],
                "relationship_hint": relationship_hint,
            }
        )

    return suggestions
