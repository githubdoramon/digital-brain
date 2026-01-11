"""
Tool system with validation for the bounded agent.

This module provides:
- ToolContract: Schema definitions with validation and guards
- ToolRegistry: Registry with tool grouping for intent-based filtering
- Validators: Pre/post execution validation
- Handlers: Extracted tool implementations
"""

from .contracts import ToolContract, ToolParameter
from .registry import TOOL_GROUPS, ToolRegistry, get_registry

__all__ = [
    "ToolContract",
    "ToolParameter",
    "ToolRegistry",
    "get_registry",
    "TOOL_GROUPS",
]
