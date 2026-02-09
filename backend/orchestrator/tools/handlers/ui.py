"""
UI directive tool handlers.

Allows the model to propose structured UI blocks while the controller remains
the source of truth via validation/sanitization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ui_dsl.validator import sanitize_ui_directives_payload

if TYPE_CHECKING:
    from agent.state import AgentState


def handle_emit_ui_directive(
    args: dict[str, Any],
    state: AgentState | None = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Validate and record a model-proposed UI directive payload.

    Args:
        args: Tool arguments, expects {"directive": {...}}
        state: Optional agent state for storing accepted directives
    """
    _ = kwargs
    directive_input = args.get("directive")
    directive, errors = sanitize_ui_directives_payload(directive_input)

    if errors:
        return {
            "success": False,
            "error": "; ".join(errors),
        }

    if directive is None:
        return {
            "success": False,
            "error": "directive validation failed",
        }

    fallback_text = directive.get("fallback_text") or "Please provide more details."

    if state is not None:
        state.ui_directives = directive
        state.add_question(fallback_text)
        state.add_fact("Prepared structured follow-up UI for the user")

    return {
        "success": True,
        "message": fallback_text,
        "needs_user_input": True,
        "directive": directive,
    }

