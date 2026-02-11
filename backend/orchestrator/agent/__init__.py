"""
Agent orchestration module for bounded, reliable tool usage.

This module implements the core principle:
"The model proposes. The controller validates, executes, and decides when to continue or stop."

Components:
- state: Canonical AgentState object (controller-maintained)
- limits: Stop rules and progress detection
- router: LLM-based intent classification
- controller: Main orchestration logic
"""

from .controller import AgentController
from .limits import AgentConfig, LimitChecker, LimitViolation
from .router import IntentClassification, IntentRouter, IntentType
from .state import AgentState, ToolCallRecord

__all__ = [
    "AgentState",
    "ToolCallRecord",
    "LimitChecker",
    "LimitViolation",
    "AgentConfig",
    "IntentRouter",
    "IntentClassification",
    "IntentType",
    "AgentController",
]
