"""
Canonical state object for the bounded agent.

This state is maintained by the controller (not the model) and
injected into every model call for consistent context.

The controller is the single source of truth for:
- Goal and constraints
- Known facts accumulated from tool results
- Completed actions
- Progress tracking (steps, tool calls, repairs)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class ToolCallRecord:
    """Record of a single tool call execution."""

    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    duration_ms: float
    success: bool
    error: Optional[str] = None
    validation_errors: Optional[list[str]] = None
    was_repaired: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "error": self.error,
            "validation_errors": self.validation_errors,
            "was_repaired": self.was_repaired,
        }


@dataclass
class AgentState:
    """
    Controller-maintained state object.

    This state is the single source of truth, maintained by the controller
    and injected into every model call. The model never modifies state directly.

    Attributes:
        goal: The user's original question/request
        constraints: Restrictions on tool usage (e.g., "read_only")
        known_facts: Facts accumulated from tool results
        completed_actions: Description of actions taken
        pending_questions: Questions to ask the user
        tool_calls: Full record of all tool executions
        step_count: Number of LLM call iterations
        repair_count: Number of validation repair attempts
        intent: Classified intent from router
        allowed_tool_groups: Tool groups allowed for this intent

        # Legacy compatibility fields (from existing AgentState in llm_tools.py)
        resolution: Entity resolution results
        search_results: Accumulated search results
        detailed_events: Full event details retrieved
        activated_skills: Skills activated for this request
    """

    # Core task tracking
    goal: str
    constraints: list[str] = field(default_factory=list)

    # Knowledge accumulation
    known_facts: list[str] = field(default_factory=list)
    completed_actions: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)

    # Progress tracking (controller-managed)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    step_count: int = 0
    repair_count: int = 0

    # Intent routing results
    intent: Optional[str] = None
    allowed_tool_groups: list[str] = field(default_factory=list)
    skill_hints: list[str] = field(default_factory=list)

    # Legacy compatibility (from existing AgentState)
    resolution: dict[str, Any] = field(default_factory=dict)
    search_results: list[dict[str, Any]] = field(default_factory=list)
    detailed_events: list[dict[str, Any]] = field(default_factory=list)
    activated_skills: list[dict[str, Any]] = field(default_factory=list)

    # Timestamps
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def tool_calls_count(self) -> int:
        """Total number of tool calls made."""
        return len(self.tool_calls)

    @property
    def successful_tool_calls(self) -> int:
        """Number of successful tool calls."""
        return sum(1 for tc in self.tool_calls if tc.success)

    @property
    def failed_tool_calls(self) -> int:
        """Number of failed tool calls."""
        return sum(1 for tc in self.tool_calls if not tc.success)

    @property
    def last_tool_call(self) -> Optional[ToolCallRecord]:
        """Get the most recent tool call, if any."""
        return self.tool_calls[-1] if self.tool_calls else None

    def add_fact(self, fact: str) -> None:
        """
        Add a known fact (called by controller after tool execution).

        Facts are deduplicated to avoid redundant context.
        """
        if fact and fact not in self.known_facts:
            self.known_facts.append(fact)

    def add_action(self, action: str) -> None:
        """Record a completed action."""
        if action:
            self.completed_actions.append(action)

    def add_question(self, question: str) -> None:
        """Add a pending question for the user."""
        if question and question not in self.pending_questions:
            self.pending_questions.append(question)

    def clear_questions(self) -> None:
        """Clear pending questions after they've been asked."""
        self.pending_questions.clear()

    def record_tool_call(self, record: ToolCallRecord) -> None:
        """Record a tool call (called by controller after execution)."""
        self.tool_calls.append(record)

    def get_recent_tool_calls(self, n: int = 3) -> list[ToolCallRecord]:
        """Get the N most recent tool calls."""
        return self.tool_calls[-n:] if self.tool_calls else []

    def has_repeated_calls(self, n: int = 3) -> bool:
        """
        Check if the last N tool calls are identical.

        Used for no-progress detection.
        """
        if len(self.tool_calls) < n:
            return False

        recent = self.tool_calls[-n:]
        first = recent[0]
        return all(
            tc.tool_name == first.tool_name and tc.arguments == first.arguments
            for tc in recent
        )

    def has_empty_result_streak(self, n: int = 3) -> bool:
        """
        Check if the last N tool calls returned empty results.

        Used for no-progress detection.
        """
        if len(self.tool_calls) < n:
            return False

        recent = self.tool_calls[-n:]
        return all(self._is_empty_result(tc.result) for tc in recent)

    def _is_empty_result(self, result: dict[str, Any]) -> bool:
        """Check if a tool result is effectively empty."""
        if "error" in result:
            return True
        if "results" in result and len(result.get("results", [])) == 0:
            return True
        if "rows" in result and len(result.get("rows", [])) == 0:
            return True
        if "count" in result and result["count"] == 0:
            return True
        return False

    def to_context_string(self) -> str:
        """
        Generate state context for injection into prompts.

        This provides the model with current state without allowing modification.
        """
        lines = [
            "CURRENT_STATE:",
            f"GOAL: {self.goal}",
            f"STEP: {self.step_count}",
            f"TOOL_CALLS_USED: {self.tool_calls_count}",
        ]

        if self.constraints:
            lines.append(f"CONSTRAINTS: {', '.join(self.constraints)}")

        if self.intent:
            lines.append(f"INTENT: {self.intent}")

        if self.known_facts:
            # Include last 5 facts to limit context size
            recent_facts = self.known_facts[-5:]
            lines.append(f"KNOWN_FACTS: {'; '.join(recent_facts)}")

        if self.completed_actions:
            # Include last 3 actions
            recent_actions = self.completed_actions[-3:]
            lines.append(f"COMPLETED: {'; '.join(recent_actions)}")

        if self.pending_questions:
            lines.append(f"PENDING_QUESTIONS: {'; '.join(self.pending_questions)}")

        return "\n".join(lines)

    def to_metadata(self) -> dict[str, Any]:
        """
        Convert state to metadata for storage/logging.

        Compatible with existing AgentState.to_metadata() method.
        """
        return {
            "goal": self.goal,
            "intent": self.intent,
            "constraints": self.constraints,
            "step_count": self.step_count,
            "tool_calls_count": self.tool_calls_count,
            "repair_count": self.repair_count,
            "known_facts_count": len(self.known_facts),
            "resolution": self.resolution,
            "search_results_count": len(self.search_results),
            "detailed_events_count": len(self.detailed_events),
            "activated_skills": [s.get("name") for s in self.activated_skills],
        }

    def to_dict(self) -> dict[str, Any]:
        """Full serialization for logging/debugging."""
        return {
            "goal": self.goal,
            "constraints": self.constraints,
            "known_facts": self.known_facts,
            "completed_actions": self.completed_actions,
            "pending_questions": self.pending_questions,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "step_count": self.step_count,
            "repair_count": self.repair_count,
            "intent": self.intent,
            "allowed_tool_groups": self.allowed_tool_groups,
            "skill_hints": self.skill_hints,
            "resolution": self.resolution,
            "search_results_count": len(self.search_results),
            "detailed_events_count": len(self.detailed_events),
            "activated_skills": [s.get("name") for s in self.activated_skills],
            "started_at": self.started_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"AgentState(goal={self.goal!r}, intent={self.intent}, "
            f"steps={self.step_count}, tool_calls={self.tool_calls_count})"
        )
