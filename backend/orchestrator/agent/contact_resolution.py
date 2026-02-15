"""Contact-resolution helpers extracted from the main controller."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable

from observability import trace
from search_normalization import normalize_search_text
from ui_dsl.clarification import (
    build_need_user_input,
    clarification_fields_from_ambiguous_contacts,
    normalize_need_user_input,
)

from .enums import ToolStatus
from .guardrails import sanitize_goal_text

if TYPE_CHECKING:
    from .state import AgentState


PRE_RESOLVE_CONTACT_INTENTS = {
    "memory_search",
    "data_query",
    "contact_lookup",
    "complex",
}


def should_pre_resolve_contacts(
    intent: str | None,
    llm_hint: bool | None = None,
) -> bool:
    """Decide whether to run contact pre-resolution before the main loop."""
    if llm_hint is not None:
        return bool(llm_hint)

    if not intent:
        return False

    return intent in PRE_RESOLVE_CONTACT_INTENTS


def is_contact_referential_memory_query(query_text: str, goal_text: str) -> bool:
    """
    Heuristic check for person-referential memory queries.

    This is the deterministic fallback when model tool planning misses
    an explicit resolve step.
    """
    combined = " ".join(
        part for part in [sanitize_goal_text(goal_text), sanitize_goal_text(query_text)] if part
    ).strip()
    if not combined:
        return False

    lower = combined.lower()
    interaction_verbs = re.search(
        r"\b(meet|met|talk|talked|speak|spoke|chat|chatted|call|called|text|texted|email|emailed|see|saw|visit|visited)\b",
        lower,
    )
    if not interaction_verbs:
        return False

    relation_terms = re.search(
        r"\b(my|our)\s+(mom|mother|dad|father|wife|husband|partner|son|daughter|friend|colleague|coworker|boss|manager|doctor|therapist|teacher|coach|brother|sister)\b",
        lower,
    )
    pronoun_target = re.search(r"\b(him|her|them|he|she|they)\b", lower)
    explicit_name = re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", combined)
    directional = re.search(r"\b(with|to|from)\b", lower)

    return bool(relation_terms or pronoun_target or explicit_name or directional)


def build_contact_clarification_result(
    ambiguous_contacts: list[dict[str, Any]],
    people_mentioned: list[str],
) -> dict[str, Any]:
    """Build a synthetic search result asking for person clarification."""
    prompt = "I found multiple matching people. Please clarify which person you mean."
    if ambiguous_contacts:
        first = ambiguous_contacts[0]
        if isinstance(first, dict):
            original_text = str(first.get("original_text") or "").strip()
            if original_text:
                prompt = f"I found multiple matches for '{original_text}'. Please choose one."

    need_user_input = build_need_user_input(
        kind="disambiguation",
        source="contact_resolution",
        prompt=prompt,
        questions=[prompt],
        fields=clarification_fields_from_ambiguous_contacts(ambiguous_contacts),
        submission_mode="ui_submission",
        context={"people_mentioned": people_mentioned},
    )

    return {
        "status": ToolStatus.NEED_USER_INPUT.value,
        "message": prompt,
        "ambiguous_contacts": ambiguous_contacts,
        "people_mentioned": people_mentioned,
        "need_user_input": need_user_input,
        "results": [],
        "count": 0,
    }


def get_user_clarification_prompt_for_contact_resolution(state: AgentState) -> str | None:
    """Return a user-facing clarification prompt when additional input is required."""
    pending_need_user_input = normalize_need_user_input(
        state.resolution.get("pending_contact_need_user_input")
    )
    prompt = str((pending_need_user_input or {}).get("prompt") or "").strip()
    if not prompt and state.pending_questions:
        prompt = state.pending_questions[-1].strip()
    if not prompt:
        return None

    ambiguous_contacts = state.resolution.get("pending_contact_ambiguous_contacts", [])
    candidate_names: list[str] = []
    if isinstance(ambiguous_contacts, list):
        for item in ambiguous_contacts:
            if not isinstance(item, dict):
                continue
            for candidate in item.get("candidates", []):
                if not isinstance(candidate, dict):
                    continue
                name = str(candidate.get("display_name", "")).strip()
                if name and name not in candidate_names:
                    candidate_names.append(name)

    if candidate_names:
        options_preview = ", ".join(candidate_names[:4])
        norm_prompt = normalize_search_text(prompt)
        if not any(normalize_search_text(name) in norm_prompt for name in candidate_names[:4]):
            prompt = f"{prompt} Options: {options_preview}."

    return prompt


def resolve_contacts_for_text(
    state: AgentState,
    text: str,
    user_email: str | None,
    conversation_history: list[dict[str, str]] | None,
    update_state: Callable[[AgentState, dict[str, Any], dict[str, Any]], None],
) -> dict[str, Any] | None:
    """Resolve contacts for text with cached-state short-circuiting."""
    if not user_email:
        return None

    normalized_text = sanitize_goal_text(text)
    if not normalized_text:
        return None

    cached_status = ToolStatus.from_value(state.resolution.get("last_contact_resolution_status"))
    cached_text = sanitize_goal_text(
        str(state.resolution.get("last_contact_resolution_text", "")).strip()
    )
    if cached_status is ToolStatus.NO_PEOPLE and cached_text.lower() == normalized_text.lower():
        return {
            "status": ToolStatus.NO_PEOPLE.value,
            "people_mentioned": [],
            "resolved_contacts": [],
            "ambiguous_contacts": [],
        }

    try:
        from contact_resolution_service import resolve_contacts_request

        payload: dict[str, Any] = {"text": normalized_text, "user_email": user_email}
        context_messages = list((conversation_history or [])[-8:])
        if (
            not context_messages
            or context_messages[-1].get("role") != "user"
            or sanitize_goal_text(str(context_messages[-1].get("content", ""))) != normalized_text
        ):
            context_messages.append({"role": "user", "content": normalized_text})
        payload["conversation_messages"] = context_messages

        resolution = resolve_contacts_request(payload)
        state.resolution["contact_resolution"] = resolution
        update_state(state, {"text": normalized_text}, resolution)
        return resolution
    except Exception as e:
        trace.trace_tool_error(
            "resolve_contacts",
            f"Contact resolution failed: {e}",
        )
        return None
