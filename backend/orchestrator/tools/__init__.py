"""
Tool system with validation for the bounded agent.

This module provides:
- ToolContract: Schema definitions with validation and guards
- ToolRegistry: Registry with tool grouping metadata and optional filtering helpers
- Validators: Pre/post execution validation
- Handlers: Extracted tool implementations
"""

from .contracts import ToolContract, ToolParameter
from .registry import TOOL_GROUPS, ToolRegistry, get_registry

__all__ = [
    "TOOL_GROUPS",
    "ToolContract",
    "ToolParameter",
    "ToolRegistry",
    "get_registry",
]
