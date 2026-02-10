"""Shared clarification field schema helpers for UI DSL and agent prompts."""

from __future__ import annotations

import re
from typing import Any

SUPPORTED_CLARIFICATION_FIELD_KINDS = {
    "text",
    "textarea",
    "number",
    "date",
    "time",
    "datetime",
    "email",
    "url",
    "select",
}

SUPPORTED_NEED_USER_INPUT_KINDS = {
    "clarification",
    "disambiguation",
    "confirmation",
    "selection",
}

SUPPORTED_NEED_USER_INPUT_SUBMISSION_MODES = {
    "text",
    "ui_submission",
}

MAX_CLARIFICATION_FIELDS = 6
MAX_CLARIFICATION_OPTIONS = 8
MAX_CLARIFICATION_QUESTIONS = 8


def build_need_user_input_prompt_guidance(
    *,
    exclude_people: bool = False,
) -> str:
    """Return shared prompt guidance for the need_user_input envelope."""
    field_kinds = ", ".join(sorted(SUPPORTED_CLARIFICATION_FIELD_KINDS))
    lines = [
        'If ANY critical information is missing or ambiguous, set "need_user_input" with only the missing details.',
        'If all required details are clear, set "need_user_input": null.',
        'Use concise user-facing wording and avoid repeating already answered questions.',
        f'Allowed field "kind" values: {field_kinds}.',
        'Set "submission_mode" to "ui_submission" whenever you include structured "fields"; otherwise use "text".',
    ]
    if exclude_people:
        lines.append(
            "Do NOT ask clarification questions about people; contact resolution handles that separately."
        )
    return "\n".join(lines)


def need_user_input_json_property_template(*, indent: int = 4, trailing_comma: bool = True) -> str:
    """Return a reusable JSON template line block for the need_user_input property."""
    base = " " * indent
    child = " " * (indent + 4)
    grandchild = " " * (indent + 8)
    leaf = " " * (indent + 12)
    lines = [
        f'{base}"need_user_input": null or {{',
        f'{child}"kind": "clarification",',
        f'{child}"prompt": "short user-facing prompt",',
        f'{child}"questions": ["short question 1"],',
        f'{child}"fields": [',
        f"{grandchild}{{",
        f'{leaf}"id": "details",',
        f'{leaf}"kind": "textarea",',
        f'{leaf}"label": "What happened?",',
        f'{leaf}"placeholder": "Share what is missing",',
        f'{leaf}"required": true',
        f"{grandchild}}}",
        f"{child}],",
        f'{child}"submission_mode": "ui_submission"',
        f"{base}}}{',' if trailing_comma else ''}",
    ]
    return "\n".join(lines)


def default_clarification_details_field() -> dict[str, Any]:
    return {
        "id": "details",
        "kind": "textarea",
        "label": "Add details",
        "placeholder": "Share what is missing",
        "required": True,
    }


def normalize_clarification_field_id(raw: Any, fallback: str) -> str:
    text = str(raw).strip() if raw is not None else ""
    base = text or fallback
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", base).strip("_").lower()
    return normalized or fallback


def normalize_clarification_select_options(raw_options: Any) -> list[dict[str, str]]:
    if not isinstance(raw_options, list):
        return []

    options: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(raw_options[:MAX_CLARIFICATION_OPTIONS]):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        option_id = normalize_clarification_field_id(item.get("id"), f"option_{idx + 1}")
        if option_id in seen_ids:
            continue
        seen_ids.add(option_id)
        options.append({"id": option_id, "label": label})
    return options


def normalize_clarification_fields(raw_fields: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_fields, list):
        return []

    fields: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(raw_fields[:MAX_CLARIFICATION_FIELDS]):
        if not isinstance(item, dict):
            continue

        label = str(item.get("label") or "").strip()
        if not label:
            continue

        kind = str(item.get("kind") or "text").strip().lower()
        if kind not in SUPPORTED_CLARIFICATION_FIELD_KINDS:
            kind = "text"

        field_id = normalize_clarification_field_id(item.get("id"), f"field_{idx + 1}")
        if field_id in seen_ids:
            field_id = f"{field_id}_{idx + 1}"
        seen_ids.add(field_id)

        field: dict[str, Any] = {
            "id": field_id,
            "kind": kind,
            "label": label,
            "required": bool(item.get("required", True)),
        }

        placeholder = str(item.get("placeholder") or "").strip()
        if placeholder:
            field["placeholder"] = placeholder

        if kind == "select":
            options = normalize_clarification_select_options(item.get("options"))
            if options:
                field["options"] = options
            else:
                field["kind"] = "text"

        fields.append(field)

    return fields


def infer_clarification_fields_from_questions(
    questions: list[str],
    partial_extraction: dict[str, Any],
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    has_details = False
    has_when = False
    has_where = False
    has_people = False

    for question in questions:
        lower = question.lower()
        if _contains_any(lower, ["when", "date", "time", "day", "hour", "month"]):
            has_when = True
        elif _contains_any(lower, ["where", "location", "place", "address"]):
            has_where = True
        elif _contains_any(lower, ["who", "which person", "which contact", "which one"]):
            has_people = True
        elif _contains_any(
            lower,
            [
                "what",
                "details",
                "clarify",
                "happened",
                "summary",
                "describe",
                "missing details",
            ],
        ):
            has_details = True

    missing_when = partial_extraction.get("when") in (None, "")
    missing_where = not str(partial_extraction.get("where") or "").strip()

    if has_details or not questions:
        fields.append(default_clarification_details_field())
    if has_when and missing_when:
        fields.append(
            {
                "id": "when",
                "kind": "datetime",
                "label": "When did this happen?",
                "placeholder": "Select date and time",
                "required": True,
            }
        )
    if has_where and missing_where:
        fields.append(
            {
                "id": "where",
                "kind": "text",
                "label": "Where did this happen?",
                "placeholder": "Add location",
                "required": False,
            }
        )
    if has_people:
        fields.append(
            {
                "id": "people",
                "kind": "text",
                "label": "Who did you mean?",
                "placeholder": "Add the exact person name(s)",
                "required": True,
            }
        )

    if not fields:
        fields.append(default_clarification_details_field())
    return fields[:MAX_CLARIFICATION_FIELDS]


def derive_clarification_questions_from_fields(
    fields: list[dict[str, Any]] | None,
) -> list[str]:
    if not fields:
        return []

    questions: list[str] = []
    for field in fields[:MAX_CLARIFICATION_FIELDS]:
        if not isinstance(field, dict):
            continue
        label = str(field.get("label") or "").strip()
        if not label:
            continue
        kind = str(field.get("kind") or "").strip().lower()
        required = bool(field.get("required", True))

        if label.endswith("?"):
            question = label
        elif kind in {"date", "time", "datetime"}:
            question = f"{label}?"
        else:
            question = f"Please provide {label.lower()}."

        if not required:
            question = f"{question} (optional)"
        questions.append(question)

    return questions


def clarification_fields_from_ambiguous_contacts(raw_ambiguous: Any) -> list[dict[str, Any]]:
    """Build dynamic clarification fields from ambiguous contact candidates."""
    if not isinstance(raw_ambiguous, list):
        return []

    fields: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for idx, item in enumerate(raw_ambiguous[:MAX_CLARIFICATION_FIELDS]):
        if not isinstance(item, dict):
            continue

        original_text = str(item.get("original_text") or "").strip() or f"person_{idx + 1}"
        field_id = normalize_clarification_field_id(
            f"who_{original_text}",
            f"person_{idx + 1}",
        )
        if field_id in seen_ids:
            field_id = f"{field_id}_{idx + 1}"
        seen_ids.add(field_id)

        options: list[dict[str, str]] = []
        seen_option_ids: set[str] = set()
        candidates = item.get("candidates")
        if isinstance(candidates, list):
            for c_idx, candidate in enumerate(candidates[:MAX_CLARIFICATION_OPTIONS]):
                if not isinstance(candidate, dict):
                    continue

                label = str(candidate.get("display_name") or "").strip()
                if not label:
                    continue

                option_id = str(candidate.get("contact_id") or "").strip()
                if not option_id:
                    option_id = normalize_clarification_field_id(
                        label,
                        f"candidate_{idx + 1}_{c_idx + 1}",
                    )
                if option_id in seen_option_ids:
                    continue
                seen_option_ids.add(option_id)
                options.append({"id": option_id, "label": label})

        label = f"Who did you mean by '{original_text}'?"
        if len(options) >= 2:
            fields.append(
                {
                    "id": field_id,
                    "kind": "select",
                    "label": label,
                    "required": True,
                    "options": options,
                }
            )
            continue

        fields.append(
            {
                "id": field_id,
                "kind": "text",
                "label": label,
                "placeholder": "Type the exact name",
                "required": True,
            }
        )

    return fields[:MAX_CLARIFICATION_FIELDS]


def normalize_need_user_input(payload: Any) -> dict[str, Any] | None:
    """Normalize a generic need_user_input envelope."""
    if not isinstance(payload, dict):
        return None

    kind = str(payload.get("kind") or "clarification").strip().lower()
    if kind not in SUPPORTED_NEED_USER_INPUT_KINDS:
        kind = "clarification"

    prompt = str(payload.get("prompt") or "").strip()
    questions = _string_list(payload.get("questions"))[:MAX_CLARIFICATION_QUESTIONS]
    fields = normalize_clarification_fields(payload.get("fields"))

    if not questions and prompt:
        questions = [prompt]
    if not prompt and questions:
        prompt = questions[0]
    if not prompt and fields:
        derived = derive_clarification_questions_from_fields(fields)
        if derived:
            prompt = derived[0]
            if not questions:
                questions = derived

    if not prompt:
        return None

    submission_mode = str(payload.get("submission_mode") or "text").strip().lower()
    if submission_mode not in SUPPORTED_NEED_USER_INPUT_SUBMISSION_MODES:
        submission_mode = "text"

    normalized: dict[str, Any] = {
        "kind": kind,
        "prompt": prompt,
        "submission_mode": submission_mode,
    }
    if questions:
        normalized["questions"] = questions
    if fields:
        normalized["fields"] = fields

    source = str(payload.get("source") or "").strip()
    if source:
        normalized["source"] = source

    action_id = str(payload.get("action_id") or "").strip()
    if action_id:
        normalized["action_id"] = action_id

    context = payload.get("context")
    if isinstance(context, dict) and context:
        normalized["context"] = context

    return normalized


def build_need_user_input(
    *,
    prompt: str | None = None,
    questions: list[str] | None = None,
    fields: list[dict[str, Any]] | None = None,
    kind: str = "clarification",
    source: str | None = None,
    action_id: str | None = None,
    submission_mode: str = "text",
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build and normalize a generic need_user_input envelope."""
    payload: dict[str, Any] = {
        "kind": kind,
        "prompt": prompt or "",
        "questions": questions or [],
        "fields": fields or [],
        "submission_mode": submission_mode,
    }
    if source:
        payload["source"] = source
    if action_id:
        payload["action_id"] = action_id
    if context:
        payload["context"] = context
    return normalize_need_user_input(payload)


def extract_need_user_input(
    payload: dict[str, Any] | None,
    *,
    default_source: str | None = None,
) -> dict[str, Any] | None:
    """Extract a normalized explicit need_user_input envelope."""
    if not isinstance(payload, dict):
        return None

    explicit = normalize_need_user_input(payload.get("need_user_input"))
    if not explicit:
        explicit = normalize_need_user_input(payload)

    if explicit:
        if default_source and not explicit.get("source"):
            explicit["source"] = default_source
        return explicit

    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item).strip() if item is not None else ""
        if text:
            output.append(text)
    return output


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)
