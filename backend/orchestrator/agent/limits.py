"""
Stop rules and progress detection for the bounded agent.

This module implements hard limits and no-progress detection:
- max_steps: Maximum LLM call iterations
- max_tool_calls: Maximum tool executions
- max_repairs: Maximum validation repair attempts
- No-progress detection: Repeated calls, empty results streak

When limits are hit, the agent stops cleanly and returns a partial answer.
"""

import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .state import AgentState


class LimitType(str, Enum):
    """Types of limits that can be violated."""

    MAX_STEPS = "max_steps"
    MAX_TOOL_CALLS = "max_tool_calls"
    MAX_REPAIRS = "max_repairs"
    NO_PROGRESS_REPEATED = "no_progress_repeated"
    NO_PROGRESS_EMPTY = "no_progress_empty"


@dataclass
class LimitViolation:
    """Represents a limit that was hit."""

    limit_type: LimitType
    message: str
    partial_answer_allowed: bool = True
    suggestion: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "limit_type": self.limit_type.value,
            "message": self.message,
            "partial_answer_allowed": self.partial_answer_allowed,
            "suggestion": self.suggestion,
        }


@dataclass
class AgentConfig:
    """
    Configuration for agent behavior.

    All values can be overridden via environment variables.
    """

    # Hard limits
    max_steps: int = 15
    max_tool_calls: int = 20
    max_repairs: int = 2

    # No-progress detection thresholds
    repeated_calls_threshold: int = 3
    empty_results_threshold: int = 3

    # Feature flags
    enable_intent_routing: bool = True
    enable_validation: bool = True
    enable_no_progress_detection: bool = True

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """Load configuration from environment variables."""
        return cls(
            max_steps=int(os.getenv("AGENT_MAX_STEPS", "15")),
            max_tool_calls=int(os.getenv("AGENT_MAX_TOOL_CALLS", "20")),
            max_repairs=int(os.getenv("AGENT_MAX_REPAIRS", "2")),
            repeated_calls_threshold=int(
                os.getenv("AGENT_REPEATED_CALLS_THRESHOLD", "3")
            ),
            empty_results_threshold=int(
                os.getenv("AGENT_EMPTY_RESULTS_THRESHOLD", "3")
            ),
            enable_intent_routing=os.getenv(
                "AGENT_ENABLE_INTENT_ROUTING", "true"
            ).lower()
            == "true",
            enable_validation=os.getenv("AGENT_ENABLE_VALIDATION", "true").lower()
            == "true",
            enable_no_progress_detection=os.getenv(
                "AGENT_ENABLE_NO_PROGRESS_DETECTION", "true"
            ).lower()
            == "true",
        )


class LimitChecker:
    """
    Checks stop conditions and progress rules.

    Hard limits:
    - max_steps: Prevents infinite loops
    - max_tool_calls: Prevents runaway tool usage
    - max_repairs: Prevents endless validation cycles

    Soft limits (no-progress detection):
    - Repeated identical tool calls
    - Empty results streak
    """

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig.from_env()

    def check(self, state: "AgentState") -> Optional[LimitViolation]:
        """
        Check all limits and return violation if any.

        Called before each step in the agent loop.

        Args:
            state: Current agent state

        Returns:
            LimitViolation if a limit was hit, None otherwise
        """
        # Check hard limits
        if state.step_count >= self.config.max_steps:
            return LimitViolation(
                limit_type=LimitType.MAX_STEPS,
                message=f"Maximum steps ({self.config.max_steps}) reached",
                suggestion="Try breaking your request into smaller parts",
            )

        if state.tool_calls_count >= self.config.max_tool_calls:
            return LimitViolation(
                limit_type=LimitType.MAX_TOOL_CALLS,
                message=f"Maximum tool calls ({self.config.max_tool_calls}) reached",
                suggestion="The request may be too complex for a single interaction",
            )

        if state.repair_count >= self.config.max_repairs:
            return LimitViolation(
                limit_type=LimitType.MAX_REPAIRS,
                message=f"Maximum repair attempts ({self.config.max_repairs}) reached",
                partial_answer_allowed=True,
                suggestion="There may be a systematic issue with tool parameters",
            )

        return None

    def detect_no_progress(self, state: "AgentState") -> Optional[LimitViolation]:
        """
        Detect if agent is making no progress.

        This is called after tool execution to detect loops.

        Args:
            state: Current agent state

        Returns:
            LimitViolation if no progress detected, None otherwise
        """
        if not self.config.enable_no_progress_detection:
            return None

        # Check for repeated identical calls
        if state.has_repeated_calls(self.config.repeated_calls_threshold):
            last_call = state.last_tool_call
            tool_name = last_call.tool_name if last_call else "unknown"
            return LimitViolation(
                limit_type=LimitType.NO_PROGRESS_REPEATED,
                message=f"Same tool call ({tool_name}) repeated {self.config.repeated_calls_threshold} times",
                suggestion="Try a different approach or rephrase the request",
            )

        # Check for streak of empty results
        if state.has_empty_result_streak(self.config.empty_results_threshold):
            return LimitViolation(
                limit_type=LimitType.NO_PROGRESS_EMPTY,
                message=f"Last {self.config.empty_results_threshold} tool calls returned no results",
                suggestion="The requested information may not exist in the database",
            )

        return None

    def should_stop(self, state: "AgentState") -> tuple[bool, Optional[LimitViolation]]:
        """
        Comprehensive check if the agent should stop.

        Combines hard limit checks and no-progress detection.

        Args:
            state: Current agent state

        Returns:
            Tuple of (should_stop, violation_if_any)
        """
        # Check hard limits first
        violation = self.check(state)
        if violation:
            return True, violation

        # Check for no progress
        violation = self.detect_no_progress(state)
        if violation:
            return True, violation

        return False, None

    def get_remaining_budget(self, state: "AgentState") -> dict:
        """
        Get remaining budget for agent operations.

        Useful for debugging and logging.

        Args:
            state: Current agent state

        Returns:
            Dictionary with remaining counts
        """
        return {
            "steps_remaining": self.config.max_steps - state.step_count,
            "tool_calls_remaining": self.config.max_tool_calls - state.tool_calls_count,
            "repairs_remaining": self.config.max_repairs - state.repair_count,
        }

    def format_stop_message(
        self, state: "AgentState", violation: LimitViolation
    ) -> str:
        """
        Format a user-friendly message when stopping due to a limit.

        Args:
            state: Current agent state
            violation: The limit that was violated

        Returns:
            Formatted message for the user
        """
        parts = [violation.message]

        if violation.suggestion:
            parts.append(f"Suggestion: {violation.suggestion}")

        # Add context about what was accomplished
        if state.known_facts:
            parts.append(f"I found {len(state.known_facts)} relevant facts before stopping.")

        if state.completed_actions:
            parts.append(f"Completed actions: {', '.join(state.completed_actions[-3:])}")

        return " ".join(parts)
