"""Validation and sanitization for chat UI directives."""

from __future__ import annotations

from typing import Any

from ui_dsl.clarification import SUPPORTED_CLARIFICATION_FIELD_KINDS

UI_DSL_VERSION = "1.0"

_SUPPORTED_BLOCK_TYPES = {
    "clarification_form",
    "choice_buttons",
    "info_card",
}

_MAX_BLOCKS = 5
_MAX_FIELDS_PER_BLOCK = 8
_MAX_OPTIONS_PER_BLOCK = 10
_MAX_LINKS_PER_BLOCK = 8

_MAX_ID_LENGTH = 64
_MAX_LABEL_LENGTH = 120
_MAX_TITLE_LENGTH = 160
_MAX_TEXT_LENGTH = 2000
_MAX_SHORT_TEXT_LENGTH = 400
_MAX_FALLBACK_TEXT_LENGTH = 1200


def validate_ui_directive_tool_param(value: Any) -> bool:
    """Contract validator hook for `emit_ui_directive.directive`."""
    _, errors = sanitize_ui_directives_payload(value)
    return len(errors) == 0


def sanitize_ui_directives_payload(payload: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate and normalize a UI directives payload."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return None, ["directive must be an object"]

    version = _sanitize_text(
        payload.get("version", UI_DSL_VERSION),
        "version",
        max_length=16,
        required=True,
        errors=errors,
    )
    if version and version != UI_DSL_VERSION:
        errors.append(f"version must be {UI_DSL_VERSION}")

    fallback_text = _sanitize_text(
        payload.get("fallback_text"),
        "fallback_text",
        max_length=_MAX_FALLBACK_TEXT_LENGTH,
        required=True,
        errors=errors,
    )

    blocks_raw = payload.get("blocks")
    if not isinstance(blocks_raw, list):
        errors.append("blocks must be an array")
        blocks_raw = []
    if len(blocks_raw) == 0:
        errors.append("blocks must include at least one block")
    if len(blocks_raw) > _MAX_BLOCKS:
        errors.append(f"blocks cannot exceed {_MAX_BLOCKS}")

    blocks: list[dict[str, Any]] = []
    block_ids: set[str] = set()
    for index, block_raw in enumerate(blocks_raw[:_MAX_BLOCKS]):
        block, block_errors = _sanitize_block(block_raw, index)
        if block_errors:
            errors.extend(block_errors)
            continue
        block_id = block.get("id")
        if block_id in block_ids:
            errors.append(f"blocks[{index}].id duplicates another block id")
            continue
        block_ids.add(block_id)
        blocks.append(block)

    if errors:
        return None, errors

    return {
        "version": version or UI_DSL_VERSION,
        "fallback_text": fallback_text,
        "blocks": blocks,
    }, []


def sanitize_ui_submission_payload(payload: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate and normalize an incoming UI submission payload."""
    if payload is None:
        return None, []

    errors: list[str] = []
    if not isinstance(payload, dict):
        return None, ["ui_submission must be an object"]

    block_id = _sanitize_text(
        payload.get("block_id"),
        "block_id",
        max_length=_MAX_ID_LENGTH,
        required=False,
        errors=errors,
    )
    action_id = _sanitize_text(
        payload.get("action_id"),
        "action_id",
        max_length=_MAX_ID_LENGTH,
        required=False,
        errors=errors,
    )
    text_fallback = _sanitize_text(
        payload.get("text_fallback"),
        "text_fallback",
        max_length=_MAX_FALLBACK_TEXT_LENGTH,
        required=False,
        errors=errors,
    )

    values = payload.get("values")
    if values is None:
        values = {}
    elif not isinstance(values, dict):
        errors.append("values must be an object")
        values = {}

    if not text_fallback and not (block_id and action_id):
        errors.append("ui_submission requires text_fallback or both block_id and action_id")

    if errors:
        return None, errors

    normalized: dict[str, Any] = {"values": values}
    if block_id:
        normalized["block_id"] = block_id
    if action_id:
        normalized["action_id"] = action_id
    if text_fallback:
        normalized["text_fallback"] = text_fallback

    return normalized, []


def _sanitize_block(payload: Any, index: int) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    prefix = f"blocks[{index}]"

    if not isinstance(payload, dict):
        return None, [f"{prefix} must be an object"]

    block_id = _sanitize_text(
        payload.get("id"),
        f"{prefix}.id",
        max_length=_MAX_ID_LENGTH,
        required=True,
        errors=errors,
    )
    block_type = _sanitize_text(
        payload.get("type"),
        f"{prefix}.type",
        max_length=32,
        required=True,
        errors=errors,
    )
    if block_type and block_type not in _SUPPORTED_BLOCK_TYPES:
        errors.append(
            f"{prefix}.type must be one of {sorted(_SUPPORTED_BLOCK_TYPES)}"
        )

    title = _sanitize_text(
        payload.get("title"),
        f"{prefix}.title",
        max_length=_MAX_TITLE_LENGTH,
        required=False,
        errors=errors,
    )
    description = _sanitize_text(
        payload.get("description"),
        f"{prefix}.description",
        max_length=_MAX_TEXT_LENGTH,
        required=False,
        errors=errors,
    )

    block: dict[str, Any] = {}
    if block_id:
        block["id"] = block_id
    if block_type:
        block["type"] = block_type
    if title:
        block["title"] = title
    if description:
        block["description"] = description

    if block_type == "clarification_form":
        fields_raw = payload.get("fields")
        if not isinstance(fields_raw, list):
            errors.append(f"{prefix}.fields must be an array")
            fields_raw = []
        if len(fields_raw) == 0:
            errors.append(f"{prefix}.fields must include at least one field")
        if len(fields_raw) > _MAX_FIELDS_PER_BLOCK:
            errors.append(f"{prefix}.fields cannot exceed {_MAX_FIELDS_PER_BLOCK}")

        fields: list[dict[str, Any]] = []
        seen_field_ids: set[str] = set()
        for field_index, field_raw in enumerate(fields_raw[:_MAX_FIELDS_PER_BLOCK]):
            field, field_errors = _sanitize_field(field_raw, prefix, field_index)
            if field_errors:
                errors.extend(field_errors)
                continue
            field_id = field.get("id")
            if field_id in seen_field_ids:
                errors.append(f"{prefix}.fields[{field_index}].id duplicates another field id")
                continue
            seen_field_ids.add(field_id)
            fields.append(field)

        submit_label = _sanitize_text(
            payload.get("submit_label") or "Submit",
            f"{prefix}.submit_label",
            max_length=_MAX_LABEL_LENGTH,
            required=True,
            errors=errors,
        )

        block["fields"] = fields
        if submit_label:
            block["submit_label"] = submit_label

        action_id = _sanitize_text(
            payload.get("action_id"),
            f"{prefix}.action_id",
            max_length=_MAX_ID_LENGTH,
            required=False,
            errors=errors,
        )
        if action_id:
            block["action_id"] = action_id

    elif block_type == "choice_buttons":
        options, option_errors = _sanitize_options(payload.get("options"), f"{prefix}.options")
        if option_errors:
            errors.extend(option_errors)
        block["options"] = options

        action_id = _sanitize_text(
            payload.get("action_id"),
            f"{prefix}.action_id",
            max_length=_MAX_ID_LENGTH,
            required=False,
            errors=errors,
        )
        if action_id:
            block["action_id"] = action_id

    elif block_type == "info_card":
        body = _sanitize_text(
            payload.get("body"),
            f"{prefix}.body",
            max_length=_MAX_TEXT_LENGTH,
            required=False,
            errors=errors,
        )
        if body:
            block["body"] = body

        if not title and not body:
            errors.append(f"{prefix} requires at least one of title or body")

        links_raw = payload.get("links", [])
        if links_raw is None:
            links_raw = []
        if not isinstance(links_raw, list):
            errors.append(f"{prefix}.links must be an array")
            links_raw = []
        if len(links_raw) > _MAX_LINKS_PER_BLOCK:
            errors.append(f"{prefix}.links cannot exceed {_MAX_LINKS_PER_BLOCK}")

        links: list[dict[str, Any]] = []
        for link_index, link_raw in enumerate(links_raw[:_MAX_LINKS_PER_BLOCK]):
            link, link_errors = _sanitize_link(link_raw, prefix, link_index)
            if link_errors:
                errors.extend(link_errors)
                continue
            links.append(link)
        if links:
            block["links"] = links

    if errors:
        return None, errors
    return block, []


def _sanitize_field(payload: Any, block_prefix: str, index: int) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    prefix = f"{block_prefix}.fields[{index}]"

    if not isinstance(payload, dict):
        return None, [f"{prefix} must be an object"]

    field_id = _sanitize_text(
        payload.get("id"),
        f"{prefix}.id",
        max_length=_MAX_ID_LENGTH,
        required=True,
        errors=errors,
    )
    kind = _sanitize_text(
        payload.get("kind"),
        f"{prefix}.kind",
        max_length=24,
        required=True,
        errors=errors,
    )
    if kind and kind not in SUPPORTED_CLARIFICATION_FIELD_KINDS:
        errors.append(f"{prefix}.kind must be one of {sorted(SUPPORTED_CLARIFICATION_FIELD_KINDS)}")

    label = _sanitize_text(
        payload.get("label"),
        f"{prefix}.label",
        max_length=_MAX_LABEL_LENGTH,
        required=True,
        errors=errors,
    )
    placeholder = _sanitize_text(
        payload.get("placeholder"),
        f"{prefix}.placeholder",
        max_length=_MAX_SHORT_TEXT_LENGTH,
        required=False,
        errors=errors,
    )

    field: dict[str, Any] = {}
    if field_id:
        field["id"] = field_id
    if kind:
        field["kind"] = kind
    if label:
        field["label"] = label
    if placeholder:
        field["placeholder"] = placeholder

    required = payload.get("required")
    if required is not None:
        if isinstance(required, bool):
            field["required"] = required
        else:
            errors.append(f"{prefix}.required must be a boolean")

    if kind == "select":
        options, option_errors = _sanitize_options(payload.get("options"), f"{prefix}.options")
        if option_errors:
            errors.extend(option_errors)
        field["options"] = options

    if errors:
        return None, errors
    return field, []


def _sanitize_options(payload: Any, path: str) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if not isinstance(payload, list):
        return [], [f"{path} must be an array"]
    if len(payload) == 0:
        errors.append(f"{path} must include at least one option")
    if len(payload) > _MAX_OPTIONS_PER_BLOCK:
        errors.append(f"{path} cannot exceed {_MAX_OPTIONS_PER_BLOCK}")

    options: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, option_raw in enumerate(payload[:_MAX_OPTIONS_PER_BLOCK]):
        if not isinstance(option_raw, dict):
            errors.append(f"{path}[{idx}] must be an object")
            continue

        option_id = _sanitize_text(
            option_raw.get("id"),
            f"{path}[{idx}].id",
            max_length=_MAX_ID_LENGTH,
            required=True,
            errors=errors,
        )
        label = _sanitize_text(
            option_raw.get("label"),
            f"{path}[{idx}].label",
            max_length=_MAX_LABEL_LENGTH,
            required=True,
            errors=errors,
        )
        if option_id in seen_ids:
            errors.append(f"{path}[{idx}].id duplicates another option id")
            continue
        seen_ids.add(option_id)
        if option_id and label:
            options.append({"id": option_id, "label": label})

    return options, errors


def _sanitize_link(payload: Any, block_prefix: str, index: int) -> tuple[dict[str, str] | None, list[str]]:
    errors: list[str] = []
    prefix = f"{block_prefix}.links[{index}]"

    if not isinstance(payload, dict):
        return None, [f"{prefix} must be an object"]

    label = _sanitize_text(
        payload.get("label"),
        f"{prefix}.label",
        max_length=_MAX_LABEL_LENGTH,
        required=True,
        errors=errors,
    )
    url = _sanitize_text(
        payload.get("url"),
        f"{prefix}.url",
        max_length=2000,
        required=True,
        errors=errors,
    )
    if url and not url.startswith("https://"):
        errors.append(f"{prefix}.url must use https://")

    if errors:
        return None, errors

    return {
        "label": label,
        "url": url,
    }, []


def _sanitize_text(
    value: Any,
    field_name: str,
    *,
    max_length: int,
    required: bool,
    errors: list[str],
) -> str:
    if value is None:
        if required:
            errors.append(f"{field_name} is required")
        return ""

    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string")
        return ""

    trimmed = value.strip()
    if required and not trimmed:
        errors.append(f"{field_name} cannot be empty")
    if len(trimmed) > max_length:
        errors.append(f"{field_name} exceeds max length {max_length}")
    return trimmed
