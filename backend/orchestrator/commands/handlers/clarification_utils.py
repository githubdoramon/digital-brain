from __future__ import annotations

import re
from typing import Any
from uuid import uuid4


def extract_clarification_token(text: str) -> tuple[str, str | None]:
    raw = str(text or "")
    match = re.search(r"\[clarification_id:([^\]]+)\]\s*$", raw, flags=re.IGNORECASE)
    if not match:
        return raw.strip(), None
    cleaned = raw[: match.start()].strip()
    return cleaned, match.group(1).strip() or None


def extract_additional_details(text: str) -> tuple[str, str | None]:
    marker = "\n\nAdditional details:"
    raw = str(text or "")
    if marker not in raw:
        return raw.strip(), None
    base, extra = raw.split(marker, 1)
    return base.strip(), extra.strip() or None


def strip_clarification_field_labels(
    detail: str,
    field_labels: list[str] | None = None,
) -> str:
    normalized_detail = (detail or "").strip()
    if not normalized_detail:
        return ""

    normalized_labels = [label.strip() for label in (field_labels or []) if str(label).strip()]
    if not normalized_labels:
        return normalized_detail

    cleaned_lines: list[str] = []
    for raw_line in normalized_detail.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        stripped_line = line
        for label in normalized_labels:
            prefix = f"{label}:"
            if line.lower().startswith(prefix.lower()):
                candidate = line[len(prefix) :].strip()
                if candidate:
                    stripped_line = candidate
                break

        cleaned_lines.append(stripped_line)

    return "\n".join(cleaned_lines).strip() or normalized_detail


def extract_clarification_detail(
    message: str,
    original_message: str,
    field_labels: list[str] | None = None,
) -> str:
    normalized_message = (message or "").strip()
    if not normalized_message:
        return ""

    marker_match = re.search(r"additional details:\s*", normalized_message, flags=re.IGNORECASE)
    if marker_match:
        detail = normalized_message[marker_match.end() :].strip()
        if detail:
            normalized_detail = strip_clarification_field_labels(detail, field_labels)
            if normalized_detail:
                return normalized_detail

    normalized_original = (original_message or "").strip()
    if normalized_original and normalized_message.lower().startswith(normalized_original.lower()):
        detail = normalized_message[len(normalized_original) :].strip(" \n:-")
        if detail:
            normalized_detail = strip_clarification_field_labels(detail, field_labels)
            if normalized_detail:
                return normalized_detail

    return strip_clarification_field_labels(normalized_message, field_labels)


def append_conversation_message(
    messages: list[dict[str, str]],
    role: str,
    content: str,
) -> None:
    normalized_content = str(content or "").strip()
    if role not in {"user", "assistant"} or not normalized_content:
        return
    messages.append({"role": role, "content": normalized_content})


def need_user_input_prompt(
    need_user_input: dict[str, Any] | None,
    fallback_prompt: str,
) -> str:
    if not isinstance(need_user_input, dict):
        return fallback_prompt
    prompt = str(need_user_input.get("prompt") or "").strip()
    if prompt:
        return prompt
    questions = need_user_input.get("questions")
    if isinstance(questions, list):
        combined = " ".join(str(question).strip() for question in questions if str(question).strip())
        if combined:
            return combined
    return fallback_prompt


def build_clarification_storage_payload(
    *,
    original_message: str,
    assistant_prompt: str,
    existing_messages: list[dict[str, str]] | None = None,
    requested_fields: list[dict[str, Any]] | None = None,
    message_key: str = "conversation_messages",
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    messages = list(existing_messages or [])
    if not messages and str(original_message or "").strip():
        append_conversation_message(messages, "user", original_message)
    append_conversation_message(messages, "assistant", assistant_prompt)

    payload = dict(extra_payload or {})
    payload["original_message"] = original_message
    payload[message_key] = messages
    payload["requested_fields"] = list(requested_fields or [])
    return payload


def create_clarification_preview_id(command_name: str) -> str:
    normalized = str(command_name or "").strip().lower() or "command"
    return f"{normalized}:clarification:{uuid4().hex[:8]}"


def store_clarification_preview(
    clarification_id: str,
    payload: dict[str, Any],
    pending_key: str | None = None,
) -> None:
    from commands.storage import store_command_data, store_pending_event

    store_command_data(clarification_id, payload)
    if pending_key:
        store_pending_event(pending_key, clarification_id)


def build_clarification_result(
    clarification_id: str,
    need_user_input: dict[str, Any],
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "type": "need_user_input",
        "clarification_id": clarification_id,
        "need_user_input": need_user_input,
    }
    result.update(extra_payload or {})
    return result
