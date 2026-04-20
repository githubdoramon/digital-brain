"""
Tool validation for the bounded agent.

Provides:
- PreExecutionValidator: Validates tool calls before execution
- PostExecutionValidator: Checks goal coverage after execution
- GoalCompletionValidator: Validates that the user's actual goal was achieved
"""

from .post_execution import (
    GoalCompletionValidator,
    GoalCoverage,
    PostExecutionResult,
    PostExecutionValidator,
)
from .pre_execution import PreExecutionValidator, ValidationResult

__all__ = [
    "GoalCompletionValidator",
    "GoalCoverage",
    "PostExecutionResult",
    "PostExecutionValidator",
    "PreExecutionValidator",
    "ValidationResult",
]
