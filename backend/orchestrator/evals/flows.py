from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Literal

from agent.router import IntentRouter
from commands.handlers.contact import _llm_extract_contact_changes
from commands.handlers.event import _extract_event_entities_with_llm
from contact_resolution_service import resolve_contacts_request
from evals.types import EvalCase, EvalFlowDefinition, EvalRunConfig
from llm_helpers import LLM_TIMEOUT
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_text
from tags_manager import _suggest_tags

logger = get_runtime_logger(__name__)
EVAL_LLM_TIMEOUT = int(os.getenv("EVAL_LLM_TIMEOUT", str(LLM_TIMEOUT)))

ROUTER_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "confidence": {"type": "number"},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "pre_resolve_contacts": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
    "required": ["intent", "confidence", "constraints", "pre_resolve_contacts", "reasoning"],
    "additionalProperties": True,
}

CONTACT_RESOLUTION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "people_mentioned": {"type": "array", "items": {"type": "string"}},
        "selector_mentions": {"type": "array", "items": {"type": "object"}},
        "resolved_contacts": {"type": "array", "items": {"type": "object"}},
        "ambiguous_contacts": {"type": "array", "items": {"type": "object"}},
        "new_contacts": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["status"],
    "additionalProperties": True,
}

EVENT_EXTRACTION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
        "when": {"type": ["string", "null"]},
        "end_when": {"type": ["string", "null"]},
        "where": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "types": {"type": "array", "items": {"type": "string"}},
        "need_user_input": {"type": ["object", "null"]},
    },
    "required": ["title", "summary", "when", "end_when", "where", "tags", "types"],
    "additionalProperties": True,
}

CONTACT_UPDATE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "contacts": {"type": "array", "items": {"type": "object"}},
        "relationships": {"type": "array", "items": {"type": "object"}},
        "need_user_input": {"type": ["object", "null"]},
    },
    "required": ["contacts", "relationships"],
    "additionalProperties": True,
}

TAG_SUGGESTION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tags"],
    "additionalProperties": True,
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _normalized_text_set(values: list[str] | None) -> set[str]:
    normalized: set[str] = set()
    for value in values or []:
        candidate = normalize_search_text(value)
        if candidate:
            normalized.add(candidate)
    return normalized


def _normalize_optional_text(value: Any) -> str | None:
    text = normalize_search_text(str(value or ""))
    return text or None


def _normalize_relationship_value(value: Any) -> str:
    normalized = normalize_search_text(str(value or "").replace("_", " "))
    alias_map = {
        "married": "spouse",
        "married to": "spouse",
        "wife": "spouse",
        "husband": "spouse",
        "partner": "partner",
    }
    return alias_map.get(normalized, normalized)


def _build_llm_request_options(case: EvalCase, run_config: EvalRunConfig) -> dict[str, Any]:
    request_options = run_config.request_options
    response_json_schema = case.response_json_schema
    response_format = None
    if request_options.strict_json_schema and response_json_schema:
        response_format = {
            "type": "json_schema",
            "json_schema": response_json_schema,
        }
    return {
        "stream": request_options.stream,
        "temperature": request_options.temperature,
        "max_tokens": request_options.max_tokens,
        "reasoning_effort": request_options.reasoning_effort,
        "response_format": response_format,
    }


async def _execute_router_case(case: EvalCase, run_config: EvalRunConfig) -> dict[str, Any]:
    logger.info(
        "[evals.router] case=%s llm_model=%r timeout=%ss",
        case.case_id,
        run_config.llm_model,
        run_config.timeout_seconds,
    )
    router = IntentRouter(
        llm_model=run_config.llm_model,
        llm_timeout=run_config.timeout_seconds,
        enable_llm_routing=True,
        llm_request_options=_build_llm_request_options(case, run_config),
    )
    logger.info(
        "[evals.router] case=%s router.llm_model=%r router.llm_timeout=%s",
        case.case_id,
        router.llm_model,
        router.llm_timeout,
    )
    result = await router.classify(
        str(case.input.get("question") or ""),
        conversation_history=case.input.get("conversation_history"),
    )
    return result.to_dict()


def _score_router_case(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    expected_intent = str(case.expected.get("intent") or "")
    actual_intent = str(output.get("intent") or "")
    notes: list[str] = []
    if actual_intent != expected_intent:
        notes.append(f"Expected intent '{expected_intent}' but got '{actual_intent}'")

    passed = actual_intent == expected_intent
    expected_pre_resolve = case.expected.get("pre_resolve_contacts")
    if expected_pre_resolve is not None:
        actual_pre_resolve = bool(output.get("pre_resolve_contacts"))
        if actual_pre_resolve != bool(expected_pre_resolve):
            passed = False
            notes.append(
                "Expected pre_resolve_contacts="
                f"{bool(expected_pre_resolve)} but got {actual_pre_resolve}"
            )

    return {"passed": passed, "notes": notes}


def _summarize_router_output(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": output.get("intent"),
        "pre_resolve_contacts": bool(output.get("pre_resolve_contacts")),
        "confidence": output.get("confidence"),
        "route_source": output.get("route_source"),
    }


def _execute_contact_resolution_case(case: EvalCase, run_config: EvalRunConfig) -> dict[str, Any]:
    logger.info(
        "[evals.contact_resolution] case=%s llm_model=%r timeout=%ss",
        case.case_id,
        run_config.llm_model,
        run_config.timeout_seconds,
    )
    return _json_safe(
        resolve_contacts_request(
            {
                "text": str(case.input.get("text") or ""),
                "user_email": run_config.user_email,
                "llm_model": run_config.llm_model,
                "timeout": run_config.timeout_seconds,
                "llm_request_options": _build_llm_request_options(case, run_config),
                "mode": str(case.input.get("mode") or "full"),
            }
        )
    )


def _score_contact_resolution_case(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    passed = True

    expected_status = str(case.expected.get("status") or "")
    actual_status = str(output.get("status") or "")
    if expected_status:
        passed = actual_status == expected_status
        if actual_status != expected_status:
            notes.append(f"Expected status '{expected_status}' but got '{actual_status}'")

    expected_mentions = _normalized_text_set(case.expected.get("people_mentioned"))
    actual_mentions = _normalized_text_set(output.get("people_mentioned"))
    if expected_mentions and not expected_mentions.issubset(actual_mentions):
        passed = False
        notes.append(
            "Missing expected mentions: "
            + ", ".join(sorted(expected_mentions.difference(actual_mentions)))
        )

    expected_selector_values = _normalized_text_set(case.expected.get("selector_values"))
    actual_selector_values = {
        normalize_search_text(selector.get("value") or "")
        for selector in output.get("selector_mentions") or []
        if isinstance(selector, dict)
    }
    actual_selector_values.discard("")
    if expected_selector_values and not expected_selector_values.issubset(actual_selector_values):
        passed = False
        notes.append(
            "Missing expected selector values: "
            + ", ".join(sorted(expected_selector_values.difference(actual_selector_values)))
        )

    expected_selector_kinds = _normalized_text_set(case.expected.get("selector_kinds"))
    actual_selector_kinds = {
        normalize_search_text(selector.get("kind") or "")
        for selector in output.get("selector_mentions") or []
        if isinstance(selector, dict)
    }
    actual_selector_kinds.discard("")
    if expected_selector_kinds and not expected_selector_kinds.issubset(actual_selector_kinds):
        passed = False
        notes.append(
            "Missing expected selector kinds: "
            + ", ".join(sorted(expected_selector_kinds.difference(actual_selector_kinds)))
        )

    return {"passed": passed, "notes": notes}


def _summarize_contact_resolution_output(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": output.get("status"),
        "people_mentioned": output.get("people_mentioned") or [],
        "selector_mentions": output.get("selector_mentions") or [],
        "resolved_count": len(output.get("resolved_contacts") or []),
        "new_count": len(output.get("new_contacts") or []),
        "ambiguous_count": len(output.get("ambiguous_contacts") or []),
    }


def _execute_event_extraction_case(case: EvalCase, run_config: EvalRunConfig) -> dict[str, Any]:
    logger.info(
        "[evals.event_extraction] case=%s llm_model=%r timeout=%ss",
        case.case_id,
        run_config.llm_model,
        run_config.timeout_seconds,
    )
    result = _extract_event_entities_with_llm(
        str(case.input.get("message") or ""),
        {
            "user_email": run_config.user_email,
            "event_target_fields": case.input.get("event_target_fields"),
            "event_lock_existing_fields": bool(case.input.get("event_lock_existing_fields")),
        },
        existing_extraction=case.input.get("existing_extraction"),
        clarification_messages=case.input.get("clarification_messages"),
        model=run_config.llm_model,
        timeout=run_config.timeout_seconds,
        llm_request_options=_build_llm_request_options(case, run_config),
    )
    return _json_safe(result)


def _score_event_extraction_case(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    passed = True

    expected_when = case.expected.get("when")
    if expected_when is not None and str(output.get("when") or "") != str(expected_when):
        passed = False
        notes.append(f"Expected when '{expected_when}' but got '{output.get('when')}'")

    expected_end_when = case.expected.get("end_when")
    if expected_end_when is not None and str(output.get("end_when") or "") != str(expected_end_when):
        passed = False
        notes.append(
            f"Expected end_when '{expected_end_when}' but got '{output.get('end_when')}'"
        )

    expected_where = case.expected.get("where")
    if expected_where is not None:
        actual_where = _normalize_optional_text(output.get("where"))
        if actual_where != _normalize_optional_text(expected_where):
            passed = False
            notes.append(f"Expected where '{expected_where}' but got '{output.get('where')}'")

    expected_title = case.expected.get("title")
    if expected_title is not None:
        actual_title = _normalize_optional_text(output.get("title"))
        if actual_title != _normalize_optional_text(expected_title):
            passed = False
            notes.append(f"Expected title '{expected_title}' but got '{output.get('title')}'")

    expected_types = _normalized_text_set(case.expected.get("types"))
    actual_types = _normalized_text_set(output.get("types"))
    if expected_types and not expected_types.issubset(actual_types):
        passed = False
        notes.append(
            "Missing expected event types: "
            + ", ".join(sorted(expected_types.difference(actual_types)))
        )

    expected_tags = _normalized_text_set(case.expected.get("tags"))
    actual_tags = _normalized_text_set(output.get("tags"))
    if expected_tags and not expected_tags.issubset(actual_tags):
        passed = False
        notes.append(
            "Missing expected tags: " + ", ".join(sorted(expected_tags.difference(actual_tags)))
        )

    expected_need_user_input = case.expected.get("needs_clarification")
    if expected_need_user_input is not None:
        actual_need_user_input = bool(output.get("need_user_input"))
        if actual_need_user_input != bool(expected_need_user_input):
            passed = False
            notes.append(
                f"Expected needs_clarification={bool(expected_need_user_input)} but got {actual_need_user_input}"
            )

    return {"passed": passed, "notes": notes}


def _summarize_event_extraction_output(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": output.get("title"),
        "when": output.get("when"),
        "where": output.get("where"),
        "types": output.get("types") or [],
        "needs_clarification": bool(output.get("need_user_input")),
    }


def _execute_contact_update_case(case: EvalCase, run_config: EvalRunConfig) -> dict[str, Any]:
    logger.info(
        "[evals.contact_update] case=%s llm_model=%r timeout=%ss",
        case.case_id,
        run_config.llm_model,
        run_config.timeout_seconds,
    )
    result = _llm_extract_contact_changes(
        str(case.input.get("message") or ""),
        user_email=run_config.user_email,
        model=run_config.llm_model,
        timeout=run_config.timeout_seconds,
        llm_request_options=_build_llm_request_options(case, run_config),
    )
    return _json_safe(result)


def _score_contact_update_case(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    passed = True

    expected_contact_names = _normalized_text_set(case.expected.get("contact_names"))
    actual_contact_names = {
        normalize_search_text(contact.get("contact_name") or "")
        for contact in output.get("contacts") or []
        if isinstance(contact, dict)
    }
    actual_contact_names.discard("")
    if expected_contact_names and not expected_contact_names.issubset(actual_contact_names):
        passed = False
        notes.append(
            "Missing expected contacts: "
            + ", ".join(sorted(expected_contact_names.difference(actual_contact_names)))
        )

    expected_relationships = case.expected.get("relationship_types") or []
    if expected_relationships:
        actual_relationships = {
            _normalize_relationship_value(relationship.get("relationship_type"))
            for relationship in output.get("relationships") or []
            if isinstance(relationship, dict)
        }
        normalized_expected_relationships = {
            _normalize_relationship_value(value) for value in expected_relationships
        }
        if not normalized_expected_relationships.issubset(actual_relationships):
            passed = False
            notes.append(
                "Missing expected relationship types: "
                + ", ".join(sorted(normalized_expected_relationships.difference(actual_relationships)))
            )

    expected_professions = case.expected.get("professions") or {}
    if expected_professions:
        actual_professions = {
            normalize_search_text(contact.get("contact_name") or ""): normalize_search_text(contact.get("profession") or "")
            for contact in output.get("contacts") or []
            if isinstance(contact, dict)
        }
        for name, profession in expected_professions.items():
            normalized_name = normalize_search_text(name)
            normalized_profession = normalize_search_text(profession)
            if actual_professions.get(normalized_name) != normalized_profession:
                passed = False
                notes.append(
                    f"Expected profession '{profession}' for '{name}' but got '{actual_professions.get(normalized_name) or ''}'"
                )

    expected_comments = case.expected.get("comments") or {}
    if expected_comments:
        actual_comments = {
            normalize_search_text(contact.get("contact_name") or ""): normalize_search_text(contact.get("comments") or "")
            for contact in output.get("contacts") or []
            if isinstance(contact, dict)
        }
        for name, comment in expected_comments.items():
            normalized_name = normalize_search_text(name)
            normalized_comment = normalize_search_text(comment)
            actual_comment = actual_comments.get(normalized_name) or ""
            if normalized_comment not in actual_comment:
                passed = False
                notes.append(
                    f"Expected comment for '{name}' to include '{comment}' but got '{actual_comment}'"
                )

    return {"passed": passed, "notes": notes}


def _summarize_contact_update_output(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "contacts": [
            {
                "contact_name": contact.get("contact_name"),
                "profession": contact.get("profession"),
                "comments": contact.get("comments"),
                "emails": contact.get("emails") or [],
            }
            for contact in output.get("contacts") or []
            if isinstance(contact, dict)
        ],
        "relationships": output.get("relationships") or [],
        "needs_clarification": bool(output.get("need_user_input")),
    }


def _execute_tag_suggestion_case(case: EvalCase, run_config: EvalRunConfig) -> dict[str, Any]:
    logger.info(
        "[evals.tag_suggestion] case=%s llm_model=%r timeout=%ss",
        case.case_id,
        run_config.llm_model,
        run_config.timeout_seconds,
    )
    subject = str(case.input.get("subject") or "document")
    normalized_subject: Literal["document", "event"] = (
        "event" if subject == "event" else "document"
    )
    return {
        "tags": _suggest_tags(
            str(case.input.get("content") or ""),
            case.input.get("existing_tags") or [],
            normalized_subject,
            model=run_config.llm_model,
            timeout=run_config.timeout_seconds,
            llm_request_options=_build_llm_request_options(case, run_config),
        )
    }


def _score_tag_suggestion_case(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    expected_tags = _normalized_text_set(case.expected.get("required_tags"))
    actual_tags = _normalized_text_set(output.get("tags"))
    passed = expected_tags.issubset(actual_tags)
    if not passed:
        notes.append(
            "Missing expected tags: " + ", ".join(sorted(expected_tags.difference(actual_tags)))
        )
    return {"passed": passed, "notes": notes}


def _summarize_tag_suggestion_output(output: dict[str, Any]) -> dict[str, Any]:
    return {"tags": output.get("tags") or []}


EVAL_FLOWS: list[EvalFlowDefinition] = [
    EvalFlowDefinition(
        flow_id="router",
        label="Router (LLM Only)",
        description="Runs live intent-routing prompts that should bypass rule-based classification and require LLM intent resolution.",
        cases=[
            EvalCase(
                case_id="router-memory-search-notes",
                title="Memory search from narrative notes request",
                input={"question": "Pull up my notes about the time John and I reviewed the quarterly plan."},
                expected={"intent": "memory_search", "pre_resolve_contacts": True},
                response_json_schema=ROUTER_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="router-memory-search-relationship-history",
                title="Memory search relationship history",
                input={"question": "Pull together the history of interactions I have had with Robin."},
                expected={"intent": "memory_search", "pre_resolve_contacts": True},
                response_json_schema=ROUTER_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="router-web-search-llm",
                title="Web search requiring LLM interpretation",
                input={"question": "Can you check online whether Apple announced anything new at WWDC this week?"},
                expected={"intent": "web_search", "pre_resolve_contacts": False},
                response_json_schema=ROUTER_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="router-data-query-finished-tasks",
                title="Data query count without explicit count keywords",
                input={"question": "Give me the number of todos I finished this week."},
                expected={"intent": "data_query", "pre_resolve_contacts": False},
                response_json_schema=ROUTER_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="router-data-query-roster",
                title="Data query roster without list all shortcut",
                input={"question": "Give me the roster of meetings I had last month."},
                expected={"intent": "data_query", "pre_resolve_contacts": False},
                response_json_schema=ROUTER_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="router-contact-lookup-profile",
                title="Contact lookup profile without who-is rule",
                input={"question": "Tell me more about Sage and how she connects to Dana."},
                expected={"intent": "contact_lookup", "pre_resolve_contacts": True},
                response_json_schema=ROUTER_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="router-home-control-scene",
                title="Home control without obvious trigger keywords",
                input={"question": "Make the living room dark and cooler before the movie starts."},
                expected={"intent": "home_control", "pre_resolve_contacts": False},
                response_json_schema=ROUTER_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="router-system-command-files",
                title="System command from shell-style request",
                input={"question": "Use ls -la to inspect the files in the current directory."},
                expected={"intent": "system_command", "pre_resolve_contacts": False},
                response_json_schema=ROUTER_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="router-conversational-guidance",
                title="Conversational guidance without greeting shortcut",
                input={"question": "I am not sure where to start with this assistant yet."},
                expected={"intent": "conversational", "pre_resolve_contacts": False},
                response_json_schema=ROUTER_RESPONSE_SCHEMA,
            ),
        ],
        execute_case=_execute_router_case,
        score_case=_score_router_case,
        summarize_output=_summarize_router_output,
    ),
    EvalFlowDefinition(
        flow_id="contact_resolution",
        label="Contact Resolution",
        description="Checks live people extraction and resolution behavior on free-form text.",
        cases=[
            EvalCase(
                case_id="contact-resolution-none",
                title="No people mentioned",
                input={"text": "Please summarize the weather forecast for tomorrow."},
                expected={"status": "no_people"},
                response_json_schema=CONTACT_RESOLUTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="contact-resolution-single",
                title="Single person mention",
                input={"text": "Please remember that I met Alyssa Quillstone for coffee yesterday."},
                expected={"status": "success", "people_mentioned": ["Alyssa Quillstone"]},
                response_json_schema=CONTACT_RESOLUTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="contact-resolution-multiple",
                title="Multiple people mention",
                input={"text": "I had lunch with Mateo Riverbend and Nora Vale today."},
                expected={"status": "success", "people_mentioned": ["Mateo Riverbend", "Nora Vale"]},
                response_json_schema=CONTACT_RESOLUTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="contact-resolution-group-team",
                title="Group selector from my team",
                input={"text": "Please remind everyone from my soccer team about dinner on Friday."},
                expected={"selector_values": ["soccer team"], "selector_kinds": ["group"]},
                response_json_schema=CONTACT_RESOLUTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="contact-resolution-group-design-team",
                title="Named team selector",
                input={"text": "I want to message my product design team about the new mockups."},
                expected={"selector_values": ["product design team"], "selector_kinds": ["group"]},
                response_json_schema=CONTACT_RESOLUTION_RESPONSE_SCHEMA,
            ),
        ],
        execute_case=_execute_contact_resolution_case,
        score_case=_score_contact_resolution_case,
        summarize_output=_summarize_contact_resolution_output,
    ),
    EvalFlowDefinition(
        flow_id="event_extraction",
        label="Event Extraction",
        description="Evaluates structured event extraction without persisting preview state.",
        cases=[
            EvalCase(
                case_id="event-extraction-meeting",
                title="Meeting with absolute datetime",
                input={
                    "message": "Project Apollo kickoff on 2026-05-14 14:30 at Porto Office to plan the roadmap.",
                },
                expected={
                    "when": "2026-05-14T14:30:00",
                    "where": "Porto Office",
                    "types": ["meeting"],
                    "needs_clarification": False,
                },
                response_json_schema=EVENT_EXTRACTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="event-extraction-health",
                title="Health appointment",
                input={
                    "message": "Dentist appointment on 2026-06-02 09:00 at Smile Clinic for a routine cleaning.",
                },
                expected={
                    "when": "2026-06-02T09:00:00",
                    "where": "Smile Clinic",
                    "types": ["health"],
                    "needs_clarification": False,
                },
                response_json_schema=EVENT_EXTRACTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="event-extraction-travel",
                title="Travel booking detail",
                input={
                    "message": "Flight to Madrid on 2026-07-01 06:15 from Aurora Airport for a product offsite.",
                },
                expected={
                    "when": "2026-07-01T06:15:00",
                    "where": "Aurora Airport",
                    "types": ["travel"],
                    "needs_clarification": False,
                },
                response_json_schema=EVENT_EXTRACTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="event-extraction-celebration",
                title="Celebration dinner",
                input={
                    "message": "Birthday dinner on 2026-08-12 20:00 at Taberna do Bairro with friends to celebrate my 43rd birthday.",
                },
                expected={
                    "when": "2026-08-12T20:00:00",
                    "where": "Taberna do Bairro",
                    "types": ["celebration"],
                    "needs_clarification": False,
                },
                response_json_schema=EVENT_EXTRACTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="event-extraction-clarification",
                title="Insufficient timing information",
                input={
                    "message": "Coffee with Bruno at Baoba to talk about the move.",
                },
                expected={
                    "needs_clarification": True,
                },
                response_json_schema=EVENT_EXTRACTION_RESPONSE_SCHEMA,
            ),
        ],
        execute_case=_execute_event_extraction_case,
        score_case=_score_event_extraction_case,
        summarize_output=_summarize_event_extraction_output,
    ),
    EvalFlowDefinition(
        flow_id="event_update_extraction",
        label="Event Update Extraction",
        description="Evaluates updating an existing event draft with new corrections or follow-up details.",
        cases=[
            EvalCase(
                case_id="event-update-location",
                title="Add a missing location",
                input={
                    "message": "Actually it was at Ribeira Market, not the office.",
                    "existing_extraction": {
                        "title": "Team lunch",
                        "summary": "Team lunch after the milestone review.",
                        "when": "2026-05-14T12:30:00",
                        "end_when": None,
                        "where": None,
                        "documents": [],
                        "tags": ["Work"],
                        "types": ["meeting"],
                    },
                },
                expected={
                    "where": "Ribeira Market",
                    "when": "2026-05-14T12:30:00",
                    "types": ["meeting"],
                    "needs_clarification": False,
                },
                response_json_schema=EVENT_EXTRACTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="event-update-end-time",
                title="Add an end time",
                input={
                    "message": "It finished at 18:30.",
                    "existing_extraction": {
                        "title": "Project sync",
                        "summary": "Project sync with design and backend.",
                        "when": "2026-06-04T17:00:00",
                        "end_when": None,
                        "where": "Porto Office",
                        "documents": [],
                        "tags": ["Work"],
                        "types": ["meeting"],
                    },
                    "event_target_fields": ["end_when"],
                    "event_lock_existing_fields": True,
                },
                expected={
                    "when": "2026-06-04T17:00:00",
                    "end_when": "2026-06-04T18:30:00",
                    "where": "Porto Office",
                    "types": ["meeting"],
                    "needs_clarification": False,
                },
                response_json_schema=EVENT_EXTRACTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="event-update-reclassification",
                title="Reclassify a generic draft as celebration",
                input={
                    "message": "This was really a birthday celebration at home, not just a generic dinner.",
                    "existing_extraction": {
                        "title": "Dinner at home",
                        "summary": "Dinner with family at home.",
                        "when": "2026-08-12T20:00:00",
                        "end_when": None,
                        "where": "Home",
                        "documents": [],
                        "tags": ["Personal"],
                        "types": ["generic"],
                    },
                },
                expected={
                    "where": "Home",
                    "when": "2026-08-12T20:00:00",
                    "types": ["celebration"],
                    "tags": ["Personal"],
                    "needs_clarification": False,
                },
                response_json_schema=EVENT_EXTRACTION_RESPONSE_SCHEMA,
            ),
        ],
        execute_case=_execute_event_extraction_case,
        score_case=_score_event_extraction_case,
        summarize_output=_summarize_event_extraction_output,
    ),
    EvalFlowDefinition(
        flow_id="contact_update_extraction",
        label="Contact Update Extraction",
        description="Evaluates contact graph extraction before confirmation and persistence.",
        cases=[
            EvalCase(
                case_id="contact-update-profession",
                title="Profession extraction",
                input={"message": "Sage is a lawyer."},
                expected={"contact_names": ["Sage"], "professions": {"Sage": "lawyer"}},
                response_json_schema=CONTACT_UPDATE_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="contact-update-relationship",
                title="Relationship extraction",
                input={"message": "Dana is married to Sage."},
                expected={"contact_names": ["Dana", "Sage"], "relationship_types": ["spouse"]},
                response_json_schema=CONTACT_UPDATE_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="contact-update-description",
                title="Description update extraction",
                input={"message": "Add a note that Sage loves trail running and cooks amazing pasta."},
                expected={
                    "contact_names": ["Sage"],
                    "comments": {"Sage": "loves trail running and cooks amazing pasta"},
                },
                response_json_schema=CONTACT_UPDATE_RESPONSE_SCHEMA,
            ),
        ],
        execute_case=_execute_contact_update_case,
        score_case=_score_contact_update_case,
        summarize_output=_summarize_contact_update_output,
    ),
    EvalFlowDefinition(
        flow_id="tag_suggestion",
        label="Tag Suggestion",
        description="Runs live tag suggestion prompts and checks for required major tags.",
        cases=[
            EvalCase(
                case_id="tag-suggestion-finance",
                title="Finance document",
                input={
                    "subject": "document",
                    "existing_tags": [],
                    "content": "Invoice for quarterly tax preparation and accounting fees from my CPA.",
                },
                expected={"required_tags": ["Finance"]},
                response_json_schema=TAG_SUGGESTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="tag-suggestion-health",
                title="Health document",
                input={
                    "subject": "document",
                    "existing_tags": [],
                    "content": "Blood test results and follow-up notes from my doctor about cholesterol treatment.",
                },
                expected={"required_tags": ["Health"]},
                response_json_schema=TAG_SUGGESTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="tag-suggestion-event-work",
                title="Work event",
                input={
                    "subject": "event",
                    "existing_tags": [],
                    "content": "Weekly engineering planning meeting with the product and backend teams to review milestones and unblock the release.",
                },
                expected={"required_tags": ["Work"]},
                response_json_schema=TAG_SUGGESTION_RESPONSE_SCHEMA,
            ),
            EvalCase(
                case_id="tag-suggestion-event-personal",
                title="Personal event",
                input={
                    "subject": "event",
                    "existing_tags": [],
                    "content": "Dinner with friends before a weekend concert downtown to celebrate my birthday.",
                },
                expected={"required_tags": ["Personal"]},
                response_json_schema=TAG_SUGGESTION_RESPONSE_SCHEMA,
            ),
        ],
        execute_case=_execute_tag_suggestion_case,
        score_case=_score_tag_suggestion_case,
        summarize_output=_summarize_tag_suggestion_output,
    ),
]
