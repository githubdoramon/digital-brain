"""Adapters that map command payloads into UI DSL directives."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ui_dsl.clarification import (
    default_clarification_details_field,
    derive_clarification_questions_from_fields,
    extract_need_user_input,
    infer_clarification_fields_from_questions,
    normalize_clarification_fields,
)
from ui_dsl.enums import CommandResultType
from ui_dsl.validator import sanitize_ui_directives_payload

_EVENT_CONFIRM_ACTION_ID = "event_confirmation_action"
_EVENT_CLARIFICATION_ACTION_ID_PREFIX = "event_clarification_submit"
_EVENT_ACTION_CONFIRM_PREFIX = "confirm:"
_EVENT_ACTION_EDIT_PREFIX = "edit:"
_EVENT_ACTION_ADJUST_PREFIX = "adjust:"
_EVENT_ACTION_CANCEL_PREFIX = "cancel:"


def command_result_to_ui_directives(command_result: dict[str, Any]) -> dict[str, Any] | None:
    """Map a command result into sanitized UI directives when supported."""
    if not isinstance(command_result, dict):
        return None

    raw_directive: dict[str, Any] | None = None
    result_type = CommandResultType.from_value(command_result.get("type"))
    if result_type is CommandResultType.EVENT_CONFIRMATION:
        raw_directive = _event_confirmation_directive(command_result)
    else:
        need_user_input = extract_need_user_input(
            command_result,
            default_source="command_result",
        )
        if need_user_input:
            raw_directive = _clarification_directive(command_result, need_user_input)

    if raw_directive is None:
        return None

    directive, errors = sanitize_ui_directives_payload(raw_directive)
    if errors:
        return None
    return directive


def _clarification_directive(
    command_result: dict[str, Any],
    need_user_input: dict[str, Any],
) -> dict[str, Any]:
    clarification_id = _normalized_text(
        command_result.get("clarification_id")
        or (need_user_input.get("context") or {}).get("clarification_id")
    )
    questions = _string_list(need_user_input.get("questions"))
    description_lines = [f"{idx + 1}. {question}" for idx, question in enumerate(questions[:6])]
    if len(questions) > 6:
        description_lines.append("...")

    action_id = _normalized_text(need_user_input.get("action_id"))
    if not action_id:
        action_id = _event_clarification_action_id(clarification_id)

    block_id_suffix = clarification_id or "follow_up"
    fields = normalize_clarification_fields(need_user_input.get("fields"))
    if not fields:
        fields = infer_clarification_fields_from_questions(questions, {})
    if not fields:
        fields = [default_clarification_details_field()]
    if not questions:
        questions = derive_clarification_questions_from_fields(fields)
    fallback_text = _normalized_text(need_user_input.get("prompt")) or (
        questions[0] if questions else "Please share the missing details."
    )
    kind = _normalized_text(need_user_input.get("kind")).lower()
    source = _normalized_text(need_user_input.get("source")).lower()
    title = "A few details are still missing"
    if kind == "disambiguation":
        title = "I need one quick disambiguation"
    elif source.startswith("event"):
        title = "A few event details are still missing"
    submit_label = "Continue" if kind == "disambiguation" else "Submit details"
    block_prefix = "event_clarification" if source.startswith("event") else "clarification"

    return {
        "version": "1.0",
        "fallback_text": fallback_text,
        "blocks": [
            {
                "id": f"{block_prefix}:{block_id_suffix}",
                "type": "clarification_form",
                "title": title,
                "description": "\n".join(description_lines) if description_lines else None,
                "action_id": action_id,
                "fields": fields,
                "submit_label": submit_label,
            }
        ],
    }


def _event_confirmation_directive(command_result: dict[str, Any]) -> dict[str, Any]:
    preview_id = _normalized_text(command_result.get("preview_id"))
    extracted = (
        command_result.get("extracted") if isinstance(command_result.get("extracted"), dict) else {}
    )
    resolution = (
        command_result.get("resolution")
        if isinstance(command_result.get("resolution"), dict)
        else {}
    )
    new_entities = (
        resolution.get("new_entities") if isinstance(resolution.get("new_entities"), dict) else {}
    )
    relationships = _dict_list(command_result.get("relationship_suggestions"))

    preview_lines = [
        f"Title: {_normalized_text(extracted.get('title')) or 'Untitled event'}",
        f"Summary: {_normalized_text(extracted.get('summary')) or 'No summary provided.'}",
        f"When: {_format_when(extracted.get('when'))}",
        f"Where: {_normalized_text(extracted.get('where')) or 'Not specified'}",
        f"Who: {_joined_or_default(_string_list(extracted.get('who')), 'No participants detected')}",
        f"Tags: {_joined_or_default(_string_list(extracted.get('tags')), 'None')}",
        f"Types: {_joined_or_default(_string_list(extracted.get('types')), 'Generic')}",
    ]

    blocks: list[dict[str, Any]] = [
        {
            "id": f"event_preview:{preview_id or 'draft'}",
            "type": "info_card",
            "title": "Event preview",
            "description": "Review this before creating the event.",
            "body": "\n".join(preview_lines),
        }
    ]

    new_contacts = _name_list(new_entities.get("contacts"), name_key="display_name")
    new_places = _name_list(new_entities.get("places"), name_key="name")
    new_documents = _name_list(new_entities.get("documents"), name_key="reference")
    if new_contacts or new_places or new_documents:
        entity_lines: list[str] = []
        if new_contacts:
            entity_lines.append(f"Contacts: {', '.join(new_contacts)}")
        if new_places:
            entity_lines.append(f"Places: {', '.join(new_places)}")
        if new_documents:
            entity_lines.append(f"Documents: {', '.join(new_documents)}")
        blocks.append(
            {
                "id": f"event_new_entities:{preview_id or 'draft'}",
                "type": "info_card",
                "title": "New entities",
                "body": "\n".join(entity_lines),
            }
        )

    if relationships:
        rel_lines: list[str] = []
        for rel in relationships[:6]:
            from_name = _normalized_text(rel.get("from_display_name"))
            to_name = _normalized_text(rel.get("to_display_name"))
            rel_type = _normalized_text(rel.get("relationship_type"))
            if from_name and to_name and rel_type:
                rel_lines.append(f"{from_name} - {rel_type} - {to_name}")
        if rel_lines:
            blocks.append(
                {
                    "id": f"event_relationships:{preview_id or 'draft'}",
                    "type": "info_card",
                    "title": "Suggested relationships",
                    "body": "\n".join(rel_lines),
                }
            )

    if preview_id:
        blocks.append(
            {
                "id": f"event_actions:{preview_id}",
                "type": "choice_buttons",
                "title": "Review and continue",
                "action_id": _EVENT_CONFIRM_ACTION_ID,
                "options": [
                    {
                        "id": f"{_EVENT_ACTION_CONFIRM_PREFIX}{preview_id}",
                        "label": "Create event",
                    },
                    {
                        "id": f"{_EVENT_ACTION_EDIT_PREFIX}{preview_id}",
                        "label": "Edit fields",
                    },
                    {
                        "id": f"{_EVENT_ACTION_ADJUST_PREFIX}{preview_id}",
                        "label": "Ask AI to adjust",
                    },
                    {
                        "id": f"{_EVENT_ACTION_CANCEL_PREFIX}{preview_id}",
                        "label": "Cancel",
                    },
                ],
            }
        )

    return {
        "version": "1.0",
        "fallback_text": _normalized_text(command_result.get("message"))
        or "Please review and confirm this event preview.",
        "blocks": blocks,
    }


def _format_when(value: Any) -> str:
    raw = _normalized_text(value)
    if not raw:
        return "Not specified"
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return raw
    return parsed.strftime("%b %d, %Y %H:%M")


def _joined_or_default(values: list[str], default_text: str) -> str:
    return ", ".join(values) if values else default_text


def _event_clarification_action_id(clarification_id: str) -> str:
    if not clarification_id:
        return _EVENT_CLARIFICATION_ACTION_ID_PREFIX
    return f"{_EVENT_CLARIFICATION_ACTION_ID_PREFIX}:{clarification_id}"


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = _normalized_text(item)
        if text:
            output.append(text)
    return output


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _name_list(value: Any, *, name_key: str) -> list[str]:
    output: list[str] = []
    for item in _dict_list(value):
        text = _normalized_text(item.get(name_key))
        if text:
            output.append(text)
    return output
