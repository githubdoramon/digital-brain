"""
State injection for the bounded agent.

The controller maintains canonical state, and this module injects
that state into every model call. The model sees the state but
cannot modify it directly - only the controller updates state.

This ensures:
- Consistent state across iterations
- Model awareness of progress and constraints
- Predictable behavior
- Easier debugging
"""

from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.state import AgentState


def build_state_message(state: "AgentState") -> Dict[str, str]:
    """
    Build a system message containing the current state.

    This message is injected into every model call to provide
    context about the agent's progress.

    Args:
        state: The current AgentState

    Returns:
        A system message dict with role and content
    """
    return {
        "role": "system",
        "content": state.to_context_string(),
    }


def inject_state_context(
    messages: List[Dict[str, Any]],
    state: "AgentState",
    position: str = "before_user",
) -> List[Dict[str, Any]]:
    """
    Inject state context into the message list.

    Args:
        messages: The current message list
        state: The current AgentState
        position: Where to inject ("start", "before_user", "end")

    Returns:
        Message list with state context injected
    """
    state_message = build_state_message(state)

    if position == "start":
        return [state_message] + messages

    if position == "end":
        return messages + [state_message]

    if position == "before_user":
        # Insert before the last user message
        result = list(messages)
        for i in range(len(result) - 1, -1, -1):
            if result[i].get("role") == "user":
                result.insert(i, state_message)
                return result
        # No user message found, append at end
        return result + [state_message]

    return messages


def build_validation_feedback_message(
    tool_name: str,
    errors: List[str],
    suggestions: List[str],
    repair_attempt: int,
    max_repairs: int,
) -> Dict[str, str]:
    """
    Build a message providing validation feedback.

    This is sent when a tool call fails validation, asking the
    model to repair its parameters.

    Args:
        tool_name: The tool that failed validation
        errors: List of validation errors
        suggestions: List of suggestions for fixing
        repair_attempt: Current repair attempt number
        max_repairs: Maximum allowed repairs

    Returns:
        A system message dict with feedback
    """
    lines = [
        f"VALIDATION FAILED for tool `{tool_name}`:",
        "",
        "Errors:",
    ]

    for error in errors:
        lines.append(f"  - {error}")

    if suggestions:
        lines.append("")
        lines.append("Suggestions:")
        for suggestion in suggestions:
            lines.append(f"  - {suggestion}")

    lines.append("")
    lines.append(f"Repair attempt {repair_attempt}/{max_repairs}.")

    if repair_attempt >= max_repairs:
        lines.append("WARNING: This is your LAST attempt. Fix the parameters carefully.")
    else:
        lines.append("Please call the tool again with corrected parameters.")

    return {
        "role": "system",
        "content": "\n".join(lines),
    }


def build_tool_result_message(
    tool_call_id: str,
    result: Dict[str, Any],
    tool_name: str,
) -> Dict[str, Any]:
    """
    Build a tool result message.

    Args:
        tool_call_id: The ID of the tool call
        result: The tool execution result
        tool_name: Name of the tool (for logging)

    Returns:
        A tool message dict
    """
    import json

    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(result, ensure_ascii=False, default=str),
    }


def build_limit_hit_message(
    limit_type: str,
    message: str,
    suggestion: Optional[str] = None,
) -> Dict[str, str]:
    """
    Build a message when a limit is hit.

    This informs the model that it must provide a final answer
    with whatever information it has gathered.

    Args:
        limit_type: Type of limit that was hit
        message: Description of the limit
        suggestion: Optional suggestion for the user

    Returns:
        A system message dict
    """
    lines = [
        f"LIMIT REACHED: {limit_type}",
        message,
        "",
        "You must now provide your best answer based on the information gathered.",
        "Acknowledge what you found and what you were unable to determine.",
    ]

    if suggestion:
        lines.append("")
        lines.append(f"Suggestion for user: {suggestion}")

    return {
        "role": "system",
        "content": "\n".join(lines),
    }


def build_no_progress_message(
    reason: str,
    known_facts: List[str],
) -> Dict[str, str]:
    """
    Build a message when no progress is detected.

    This informs the model that it appears to be stuck and should
    either try a different approach or provide a partial answer.

    Args:
        reason: Why no progress was detected
        known_facts: Facts gathered so far

    Returns:
        A system message dict
    """
    lines = [
        "NO PROGRESS DETECTED:",
        reason,
        "",
    ]

    if known_facts:
        lines.append("Facts gathered so far:")
        for fact in known_facts[-5:]:  # Last 5 facts
            lines.append(f"  - {fact}")
        lines.append("")

    lines.extend([
        "Options:",
        "1. Try a completely different approach",
        "2. Ask the user for clarification",
        "3. Provide a partial answer with what you've found",
    ])

    return {
        "role": "system",
        "content": "\n".join(lines),
    }
