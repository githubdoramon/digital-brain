"""Controller-side planning and verification helpers."""

from __future__ import annotations

from typing import Any


def build_execution_plan(goal: str, intent: str | None) -> list[str]:
    """Build a compact execution plan that the controller can track explicitly."""
    normalized_goal = str(goal or "").strip()
    normalized_intent = str(intent or "").strip().upper()
    if not normalized_goal:
        return ["Clarify user request before tool execution"]

    plan: list[str] = ["Clarify scope and key entities"]
    if normalized_intent in {"MEMORY_SEARCH", "DATA_QUERY", "CONTACT_LOOKUP"}:
        plan.append("Collect evidence with memory/resolution tools")
        plan.append("Cross-check top evidence before final answer")
    elif normalized_intent in {"WEB_SEARCH"}:
        plan.append("Gather web evidence from high-confidence sources")
        plan.append("Synthesize answer with citations")
    elif normalized_intent in {"SYSTEM_COMMAND", "HOME_CONTROL"}:
        plan.append("Execute requested action safely")
        plan.append("Verify execution result before reporting")
    else:
        plan.append("Gather relevant evidence with available tools")
        plan.append("Verify answer matches the user goal")

    return plan


def build_verification_retry_prompt(reason: str, missing: list[str]) -> str:
    """Prompt used when verifier rejects a premature final response."""
    missing_text = "; ".join(missing[:2]) if missing else "complete missing evidence"
    return (
        "VERIFICATION_FAILED: The draft response is not yet grounded enough. "
        f"Reason: {reason}. Required next action: {missing_text}. "
        "Do not answer yet. First run the needed tool call(s), then respond."
    )


def verify_final_response(
    *,
    final_content: str,
    goal_check: dict[str, Any],
    completion_evidence: list[str],
    tool_calls_count: int,
) -> tuple[bool, str, list[str]]:
    """Return verifier verdict for final answer grounding and completion."""
    if not goal_check.get("achieved"):
        return (
            False,
            str(goal_check.get("reason") or "Goal not achieved"),
            list(goal_check.get("pending_actions") or []),
        )

    normalized = str(final_content or "").strip()
    if len(normalized) < 20:
        return False, "Final response is too short", ["Provide a complete grounded answer"]

    if tool_calls_count > 0 and not completion_evidence:
        return (
            False,
            "No completion evidence captured from tool execution",
            ["Run a supporting tool call and cite concrete findings"],
        )

    return True, "Verified", []
