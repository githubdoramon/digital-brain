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

from .state import AgentState, ToolCallRecord
from .limits import LimitChecker, LimitViolation, AgentConfig
from .router import IntentRouter, IntentClassification, IntentType
from .controller import AgentController, get_controller

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
    "get_controller",
]
