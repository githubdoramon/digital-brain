"""Shared controller helpers for contact-scope reuse and contact-resolution state."""

from __future__ import annotations

from typing import Any, Callable

from ui_dsl.clarification import extract_need_user_input
from ui_dsl.command_adapters import command_result_to_ui_directives

from .contact_resolution import (
    build_contact_clarification_result,
    is_contact_referential_memory_query,
    resolve_contacts_for_text,
)
from .enums import ToolStatus
from .guardrails import sanitize_goal_text


def ensure_contact_scope(
    *,
    state: Any,
    text: str,
    user_email: str | None,
    conversation_history: list[dict[str, str]] | None,
    require_person_query: bool,
    normalize_tool_status: Callable[[dict[str, Any], str], ToolStatus | None],
    update_state: Callable[[Any, dict[str, Any], dict[str, Any]], None],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None, bool]:
    """Reuse existing contact scope or resolve it once for the supplied text.

    This helper centralizes the controller's contact-scope policy for both
    pre-resolution and tool-argument normalization:

    - reuse previously resolved `active_contact_scope_ids` when present
    - surface pending clarification instead of re-running resolution
    - optionally skip resolution for non-person-referential text
    - perform at most one on-demand resolution attempt for the given text

    Returns `(active_scope, active_scope_ids, preempt_result, resolution_attempted)`.
    """
    normalized_text = sanitize_goal_text(text)
    active_scope = state.resolution.get("active_contact_scope") or []
    active_scope_ids = list(state.resolution.get("active_contact_scope_ids") or [])

    pending_need_user_input = state.resolution.get("pending_contact_need_user_input")
    pending_prompt = ""
    if isinstance(pending_need_user_input, dict):
        pending_prompt = str(pending_need_user_input.get("prompt") or "").strip()

    if pending_prompt:
        preempt = build_contact_clarification_result(
            ambiguous_contacts=state.resolution.get("pending_contact_ambiguous_contacts", []),
            people_mentioned=state.resolution.get("pending_contact_people", []),
        )
        return active_scope, active_scope_ids, preempt, False

    if active_scope_ids:
        return active_scope, active_scope_ids, None, False
    if not user_email or not normalized_text:
        return active_scope, active_scope_ids, None, False
    if require_person_query and not is_contact_referential_memory_query(
        normalized_text,
        normalized_text,
    ):
        return active_scope, active_scope_ids, None, False

    resolution = resolve_contacts_for_text(
        state=state,
        text=normalized_text,
        user_email=user_email,
        conversation_history=conversation_history,
        update_state=update_state,
    )
    if not resolution:
        return active_scope, active_scope_ids, None, False

    status = normalize_tool_status(resolution, "resolve_contacts")
    if status is ToolStatus.NEED_USER_INPUT:
        preempt = build_contact_clarification_result(
            ambiguous_contacts=state.resolution.get("pending_contact_ambiguous_contacts", []),
            people_mentioned=state.resolution.get("pending_contact_people", []),
        )
        return [], [], preempt, True

    active_scope = state.resolution.get("active_contact_scope") or []
    active_scope_ids = list(state.resolution.get("active_contact_scope_ids") or [])
    return active_scope, active_scope_ids, None, True


def block_redundant_contact_resolution(
    *,
    state: Any,
    args: dict[str, Any],
    normalize_tool_status: Callable[[dict[str, Any], str], ToolStatus | None],
) -> tuple[dict[str, Any] | None, str | None]:
    """Return cached clarification/no-progress results for repeated resolution calls."""
    text = sanitize_goal_text(str(args.get("text", "")).strip())
    if not text:
        return None, None

    scoped_text = sanitize_goal_text(str(state.resolution.get("active_contact_scope_text", "")).strip())
    scoped_ids = state.resolution.get("active_contact_scope_ids", [])
    if scoped_text and scoped_text.lower() == text.lower() and scoped_ids:
        cached_result = state.resolution.get("contact_resolution") or {}
        return (
            {
                **cached_result,
                "status": ToolStatus.SUCCESS.value,
                "message": "Contact scope is already resolved for this request.",
            },
            None,
        )

    pending_text = sanitize_goal_text(str(state.resolution.get("pending_contact_scope_text", "")).strip())
    pending_need_user_input = state.resolution.get("pending_contact_need_user_input")
    pending_prompt = ""
    if isinstance(pending_need_user_input, dict):
        pending_prompt = str(pending_need_user_input.get("prompt") or "").strip()
    if pending_prompt and pending_text and pending_text.lower() == text.lower():
        return (
            {
                "status": ToolStatus.NEED_USER_INPUT.value,
                "ambiguous_contacts": state.resolution.get("pending_contact_ambiguous_contacts", []),
                "people_mentioned": state.resolution.get("pending_contact_people", []),
                "message": "Contact resolution already requires clarification.",
                "need_user_input": pending_need_user_input,
            },
            None,
        )

    last_call = state.last_tool_call
    if not last_call or last_call.tool_name != "resolve_contacts":
        return None, None

    last_text = sanitize_goal_text(str(last_call.arguments.get("text", "")).strip())
    last_result = last_call.result or {}
    last_status = normalize_tool_status(last_result, "resolve_contacts")
    if last_text.lower() != text.lower():
        return None, None
    if last_status not in {ToolStatus.NEED_USER_INPUT, ToolStatus.NO_PEOPLE}:
        return None, None
    resolved_last_status = last_status or ToolStatus.UNKNOWN

    reason = (
        "Contact resolution already returned ambiguity for this exact text. "
        "Ask the user to clarify instead of retrying the same call."
        if last_status is ToolStatus.NEED_USER_INPUT
        else "No people were detected for this text in the previous attempt."
    )
    return (
        {
            **last_result,
            "status": resolved_last_status.value,
            "message": reason,
        },
        reason,
    )


def apply_contact_resolution_result(
    *,
    state: Any,
    args: dict[str, Any],
    result: dict[str, Any],
    normalize_tool_status: Callable[[dict[str, Any], str], ToolStatus | None],
) -> None:
    """Persist contact-resolution outcomes into controller-owned request state."""
    need_user_input = extract_need_user_input(
        result,
        default_source="resolve_contacts",
    )
    status = normalize_tool_status(result, "resolve_contacts")
    resolved_status = status or ToolStatus.UNKNOWN

    state.resolution["last_contact_resolution_text"] = args.get("text", "")
    state.resolution["last_contact_resolution_status"] = resolved_status.value
    if resolved_status is ToolStatus.SUCCESS:
        resolved_contacts = result.get("resolved_contacts", [])
        contact_ids = [
            c.get("contact_id")
            for c in resolved_contacts
            if isinstance(c, dict) and c.get("contact_id")
        ]
        deduped_ids = list(dict.fromkeys(contact_ids))
        if deduped_ids:
            scope_entries: list[dict[str, Any]] = []
            seen_scope_ids: set[str] = set()
            for item in resolved_contacts:
                if not isinstance(item, dict):
                    continue
                contact_id = str(item.get("contact_id") or "").strip()
                if not contact_id or contact_id in seen_scope_ids:
                    continue
                seen_scope_ids.add(contact_id)
                scope_entries.append(
                    {
                        "mention_text": str(item.get("original_text") or "").strip(),
                        "display_name": str(item.get("display_name") or "").strip(),
                        "contact_id": contact_id,
                        "confidence": item.get("confidence"),
                        "matched_via": item.get("matched_via"),
                    }
                )
            state.resolution["active_contact_scope_ids"] = deduped_ids
            state.resolution["active_contact_scope"] = scope_entries
            state.resolution["active_contact_scope_text"] = args.get("text", "")
            state.resolution.pop("pending_contact_need_user_input", None)
            state.resolution.pop("pending_contact_ambiguous_contacts", None)
            state.resolution.pop("pending_contact_people", None)
            state.resolution.pop("pending_contact_scope_text", None)
            if state.ui_directives:
                state.ui_directives = None
        else:
            state.resolution.pop("active_contact_scope", None)
        return

    if resolved_status is ToolStatus.NEED_USER_INPUT:
        ambiguous_contacts = result.get("ambiguous_contacts", [])
        if not need_user_input:
            fallback = build_contact_clarification_result(
                ambiguous_contacts=ambiguous_contacts,
                people_mentioned=result.get("people_mentioned", []),
            )
            need_user_input = extract_need_user_input(
                fallback,
                default_source="resolve_contacts",
            )
        if not need_user_input:
            prompt = "I found multiple matching people. Please clarify which one you mean."
            need_user_input = {
                "kind": "disambiguation",
                "prompt": prompt,
                "questions": [prompt],
                "submission_mode": "ui_submission",
            }
        state.resolution["pending_contact_need_user_input"] = need_user_input
        state.resolution["pending_contact_ambiguous_contacts"] = ambiguous_contacts
        state.resolution["pending_contact_people"] = result.get("people_mentioned", [])
        state.resolution["pending_contact_scope_text"] = args.get("text", "")
        state.resolution.pop("active_contact_scope_ids", None)
        state.resolution.pop("active_contact_scope_text", None)
        state.resolution.pop("active_contact_scope", None)

        directive = command_result_to_ui_directives(
            {
                "type": "need_user_input",
                "need_user_input": state.resolution.get("pending_contact_need_user_input"),
            }
        )
        if directive:
            state.ui_directives = directive
        return

    if resolved_status is ToolStatus.NO_PEOPLE:
        state.resolution.pop("active_contact_scope_ids", None)
        state.resolution.pop("active_contact_scope_text", None)
        state.resolution.pop("active_contact_scope", None)
        state.resolution.pop("pending_contact_need_user_input", None)
        state.resolution.pop("pending_contact_ambiguous_contacts", None)
        state.resolution.pop("pending_contact_people", None)
        state.resolution.pop("pending_contact_scope_text", None)
        if state.ui_directives:
            state.ui_directives = None


def record_pre_resolution_outcome(*, state: Any) -> str | None:
    """Record facts from the latest pre-resolution attempt and return clarification prompt."""
    pending_need_user_input = state.resolution.get("pending_contact_need_user_input")
    if pending_need_user_input:
        prompt = str(pending_need_user_input.get("prompt") or "").strip()
        if prompt:
            state.add_question(prompt)
            return prompt
        return None

    status = ToolStatus.from_value(state.resolution.get("last_contact_resolution_status"))
    if status is not ToolStatus.SUCCESS:
        resolution = state.resolution.get("contact_resolution") or {}
        new_contacts = resolution.get("new_contacts", [])
        people = resolution.get("people_mentioned", [])
        state.resolution["pre_resolution_attempted"] = True
        state.resolution["pre_resolution_people"] = people
        state.resolution["pre_resolution_new_contacts"] = [
            str(c.get("display_name") or c.get("original_text", ""))
            for c in new_contacts
            if isinstance(c, dict)
        ]
        if people:
            state.add_fact(f"Pre-resolved contacts for {people}: no existing contacts found")
        return None

    scope_ids = state.resolution.get("active_contact_scope_ids", [])
    if scope_ids:
        state.add_fact(f"Pre-resolved {len(scope_ids)} contact(s) from user question")
    return None
