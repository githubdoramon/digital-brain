"""Shared contact-resolution service used by tools, API endpoints, and agents."""

from __future__ import annotations

from typing import Any

from agents.contacts.resolver import resolve_contacts_from_text
from observability.logger import get_runtime_logger
from ui_dsl.clarification import (
    build_need_user_input,
    clarification_fields_from_ambiguous_contacts,
    normalize_need_user_input,
)

logger = get_runtime_logger(__name__)


def _build_disambiguation_prompt(ambiguous_contacts: list[dict[str, Any]]) -> str:
    prompt = "I found multiple matching contacts. Please choose who you meant."
    if ambiguous_contacts:
        first = ambiguous_contacts[0]
        if isinstance(first, dict):
            original_text = str(first.get("original_text") or "").strip()
            if original_text:
                prompt = f"I found multiple matches for '{original_text}'. Please choose one."
    return prompt


def _ensure_need_user_input(result: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize or synthesize need_user_input using repository standards."""
    existing = normalize_need_user_input(result.get("need_user_input"))
    if existing:
        return existing

    ambiguous = result.get("ambiguous_contacts")
    if not isinstance(ambiguous, list) or not ambiguous:
        return None

    prompt = _build_disambiguation_prompt(ambiguous)
    people = result.get("people_mentioned")
    if not isinstance(people, list):
        people = []

    return build_need_user_input(
        kind="disambiguation",
        source="contact_resolution",
        prompt=prompt,
        questions=[prompt],
        fields=clarification_fields_from_ambiguous_contacts(ambiguous),
        submission_mode="ui_submission",
        context={"people_mentioned": people},
    )


def resolve_contacts_request(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve contacts for request payload and enforce output contract."""
    text = data.get("text")
    user_email = data.get("user_email")

    if not text:
        return {"status": "error", "message": "Missing required field: text"}

    if not user_email:
        return {"status": "error", "message": "Missing required field: user_email"}

    try:
        result = resolve_contacts_from_text(
            str(text),
            str(user_email),
            conversation_messages=data.get("conversation_messages"),
        )

        if not isinstance(result, dict):
            return {"status": "error", "message": "Invalid contact resolution response"}

        status = str(result.get("status") or "").strip().lower()
        need_user_input = _ensure_need_user_input(result)

        if not status:
            if need_user_input:
                status = "need_user_input"
            elif not result.get("people_mentioned"):
                status = "no_people"
            else:
                status = "success"

        if need_user_input:
            status = "need_user_input"
            result["need_user_input"] = need_user_input

        result["status"] = status
        return result
    except Exception as e:
        logger.exception("[contact_resolution_service] Error: %s", e)
        return {"status": "error", "message": f"Internal error: {str(e)}"}


__all__ = ["resolve_contacts_request"]
