"""
UI directive DSL validation helpers.

The model can propose UI directives, but controller-side validation is the
source of truth for what is accepted.
"""

from .clarification import (
    MAX_CLARIFICATION_FIELDS,
    MAX_CLARIFICATION_OPTIONS,
    SUPPORTED_CLARIFICATION_FIELD_KINDS,
    SUPPORTED_NEED_USER_INPUT_KINDS,
    SUPPORTED_NEED_USER_INPUT_SUBMISSION_MODES,
    build_need_user_input,
    build_need_user_input_prompt_guidance,
    clarification_fields_from_ambiguous_contacts,
    default_clarification_details_field,
    derive_clarification_questions_from_fields,
    extract_need_user_input,
    infer_clarification_fields_from_questions,
    need_user_input_json_property_template,
    normalize_clarification_field_id,
    normalize_clarification_fields,
    normalize_clarification_select_options,
    normalize_need_user_input,
)
from .command_adapters import command_result_to_ui_directives
from .validator import (
    UI_DSL_VERSION,
    sanitize_ui_directives_payload,
    sanitize_ui_submission_payload,
    validate_ui_directive_tool_param,
)

__all__ = [
    "UI_DSL_VERSION",
    "command_result_to_ui_directives",
    "SUPPORTED_CLARIFICATION_FIELD_KINDS",
    "SUPPORTED_NEED_USER_INPUT_KINDS",
    "SUPPORTED_NEED_USER_INPUT_SUBMISSION_MODES",
    "MAX_CLARIFICATION_FIELDS",
    "MAX_CLARIFICATION_OPTIONS",
    "build_need_user_input_prompt_guidance",
    "build_need_user_input",
    "clarification_fields_from_ambiguous_contacts",
    "default_clarification_details_field",
    "derive_clarification_questions_from_fields",
    "extract_need_user_input",
    "need_user_input_json_property_template",
    "normalize_clarification_field_id",
    "normalize_need_user_input",
    "normalize_clarification_select_options",
    "normalize_clarification_fields",
    "infer_clarification_fields_from_questions",
    "sanitize_ui_directives_payload",
    "sanitize_ui_submission_payload",
    "validate_ui_directive_tool_param",
]
